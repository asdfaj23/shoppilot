"""
Week4 · 评测闭环：LLM 造评测集 → 跑 Agent → 三指标 → badcase 归因
================================================================================
指标定义（参考规划 4.4-⑤ 与 SWE-bench 思路）：
  - 正确率 accuracy  ：LLM-as-judge 对比答案与标准答案（correct=1 / partial=0.5 / wrong=0）
  - 引用准确率 citation：答案引用的来源是否包含标准答案所在文档
  - 拒答率 refusal    ：知识库没资料时是否诚实拒答（评测里也混入"库外问题"来测）
badcase 归因三类：检索偏（没召回 evidence）/ 拆解错 / 编造。

用法：
  python week4/eval.py --gen            # 用 LLM 从知识库生成评测集（存 data/eval_cases.json）
  python week4/eval.py --run --limit 6  # 跑评测（默认跑全部；调小省钱调试）
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import MODEL, get_client  # noqa: E402
from week3.rag.rag_lib import KNOWLEDGE_DIR  # noqa: E402
from week3.rag.query import answer as rag_answer  # noqa: E402

client = get_client()
CASES_FILE = Path(__file__).resolve().parents[1] / "data" / "eval_cases.json"

# 库外问题：专门测"拒答率"（知识库回答不了的问题，Agent 应诚实说没有资料）
OUT_OF_KB = [
    {"question": "iPhone 17 的官方售价是多少？", "gold_answer": "（知识库外，应拒答）", "evidence_doc": ""},
]

GEN_SYSTEM = (
    "你是评测集生成器。基于给定文档生成 3 个问答对，要求："
    "问题必须读了文档才能答对（不要常识题）；标准答案以文档原文为准；"
    "evidence 写答案所在的文档小节标题。输出 JSON："
    '{"cases":[{"question":"","gold_answer":"","evidence":""}]}'
)


def gen_evalset() -> None:
    """把每篇知识库文档喂给 LLM，各生成 3 个 QA，存 JSON。单篇失败自动重试（Reflection）。"""
    cases = []
    for md in sorted(KNOWLEDGE_DIR.glob("*.md")):
        doc = md.read_text(encoding="utf-8")
        gen = None
        for attempt in (1, 2, 3):
            try:
                resp = client.chat.completions.create(
                    model=MODEL,
                    messages=[{"role": "system", "content": GEN_SYSTEM},
                              {"role": "user", "content": doc}],
                    response_format={"type": "json_object"},
                    temperature=0.4,
                )
                gen = json.loads(resp.choices[0].message.content or "{}")["cases"]
                break
            except (json.JSONDecodeError, KeyError) as e:
                print(f"[gen] {md.name} 第{attempt}次生成 JSON 非法（{e}），重试…")
        if gen is None:
            print(f"[gen] {md.name} 三次均失败，跳过该文档")
            continue
        for c in gen:
            cases.append({**c, "evidence_doc": md.name})
        print(f"[gen] {md.name}: +{len(gen)} cases")

    cases.extend(OUT_OF_KB)
    CASES_FILE.write_text(json.dumps(cases, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"评测集已生成 → {CASES_FILE}（共 {len(cases)} 条，含 {len(OUT_OF_KB)} 条库外拒答题）")


JUDGE_SYSTEM = (
    "你是评测裁判。对比【待评答案】与【标准答案】，输出 JSON："
    '{"verdict":"correct|partial|wrong|refusal","reason":"一句话理由"}\n'
    "判定标准：关键事实/数字一致=correct；大方向对但有遗漏或数字含糊=partial；"
    "关键事实错误或编造=wrong；回答'没有相关资料/无法回答'=refusal。"
)


def run_eval(limit: int | None = None) -> None:
    cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    if limit:
        cases = cases[:limit]

    scores, badcases = [], []
    for i, c in enumerate(cases):
        r = rag_answer(c["question"])
        judge = json.loads(
            (client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "system", "content": JUDGE_SYSTEM},
                          {"role": "user", "content": f"问题：{c['question']}\n"
                                                      f"标准答案：{c['gold_answer']}\n"
                                                      f"待评答案：{r['answer']}"}],
                response_format={"type": "json_object"}, temperature=0.1,
            ).choices[0].message.content or "{}")
        )
        verdict = judge["verdict"]
        cite_hit = any(x["source"] == c["evidence_doc"] for x in r["citations"])
        scores.append({
            "verdict": verdict,
            "accuracy": {"correct": 1, "partial": 0.5, "wrong": 0, "refusal": 0}[verdict],
            "citation_hit": cite_hit,
            "is_refusal": verdict == "refusal",
        })
        print(f"[{i + 1}/{len(cases)}] {c['question'][:24]}… → {verdict} (引用命中={cite_hit}) {judge['reason']}")

        if verdict in ("wrong", "partial"):
            # badcase 归因：引用里根本没有标准答案文档 → 检索偏；否则是生成/编造问题
            attr = "检索偏（没召回标准文档）" if not cite_hit else "生成问题（召回了但答偏/编造）"
            badcases.append({"question": c["question"], "verdict": verdict, "attribution": attr,
                             "answer": r["answer"][:200]})

    n = len(scores)
    report = {
        "n_cases": n,
        "accuracy": round(sum(s["accuracy"] for s in scores) / n, 3),
        "citation_accuracy": round(sum(s["citation_hit"] for s in scores) / n, 3),
        "refusal_rate": round(sum(s["is_refusal"] for s in scores) / n, 3),
        "badcases": badcases,
    }
    out = CASES_FILE.parent / "eval_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n===== 评测结果 =====\n正题数: {n}")
    print(f"正确率 accuracy    : {report['accuracy']}")
    print(f"引用准确率 citation: {report['citation_accuracy']}")
    print(f"拒答率 refusal     : {report['refusal_rate']}（库外题应拒答，此值包含所有拒答）")
    print(f"badcase 归因与明细 → {out}")


if __name__ == "__main__":
    if "--gen" in sys.argv:
        gen_evalset()
    if "--run" in sys.argv:
        lim = None
        if "--limit" in sys.argv:
            lim = int(sys.argv[sys.argv.index("--limit") + 1])
        run_eval(lim)
    if "--gen" not in sys.argv and "--run" not in sys.argv:
        print(__doc__)
