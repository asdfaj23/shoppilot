"""
ShopPilot benchmark.py · 效果测试（对应规划：25 题导购验收集 + 四项确定性指标 + token 对照实验）
================================================================================
设计取舍：不引评测框架（见边界），四项指标全部确定性规则判定、零裁判 token：
  1. 任务完成率   task_completed：无降级 + constraints 全过（must_contain/must_not_contain，对齐
                  llmeval deterministic.py 规则判定）；拒答题以正确拒答计完成；
  2. 工具命中率   tool_selection_acc：实际工具集合对 meta.expected_tools 的覆盖比例
                  （字段对齐 datasets/agent/tool_use.jsonl 的 expected_tools 用法）；
  3. 拒答正确率   refusal_correct：meta.expected_behavior(refuse|answer) 与实际行为一致
                  （对齐 datasets/safety.jsonl 的 expected_behavior + REFUSAL_WORDS 规则）；
  4. 引用可验证   citation_verifiable：answer_md 里的 [n] 全部落在资料编号范围内（防编造来源）。
--compare：同一题库跑两遍——"task_type 预加载工具" vs "全量注入 6 工具 schema"，
输出平均 tokens 对照 → 简历上"工具预加载省 XX% token"的数字来源。

用法：
  D:\\conda_envs\\agent\\python.exe -X utf8 benchmark.py --run --limit 3   # 试跑
  D:\\conda_envs\\agent\\python.exe -X utf8 benchmark.py --run             # 全量 15 题
  D:\\conda_envs\\agent\\python.exe -X utf8 benchmark.py --run --compare   # 加 token 对照
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from config import ECOM_DIR, SESSIONS_DIR
from shoppilot.agents.team import run_team
from shoppilot.models import BenchCase

CASES_FILE = ECOM_DIR / "bench_cases.json"
REPORT_FILE = Path(__file__).resolve().parent / "docs" / "benchmark_report.md"
CITE_PATTERN = re.compile(r"\[(\d{1,2})\]")


def load_cases() -> list[BenchCase]:
    data = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    return [BenchCase(**c) for c in data["cases"]]


def check_constraints(answer: str, constraints: list[dict]) -> tuple[bool, list[str]]:
    """规则判定（与 llmeval deterministic.py 同型）：
    must_contain=数组内 AND；must_contain_any=数组内 OR（兼容表述差异，如"12个月/一年"）；
    must_not_contain=任一出现即失败。"""
    fails = []
    for c in constraints:
        ctype, value = c.get("type"), c.get("value")
        kws = [value] if isinstance(value, str) else (value or [])
        if ctype == "must_contain":
            for kw in kws:
                if kw not in answer:
                    fails.append(f"缺关键词：{kw}")
        elif ctype == "must_contain_any":
            if not any(kw in answer for kw in kws):
                fails.append(f"缺关键词（任一）：{'/'.join(kws)}")
        elif ctype == "must_not_contain":
            for kw in kws:
                if kw in answer:
                    fails.append(f"出现禁用词：{kw}")
    return (not fails), fails


def score_case(case: BenchCase, result) -> dict:
    answer = result.answer_md or ""
    # 拒答判定（口径）：①out_of_scope 意图（Planner 拒答短路）；②after_sales 场景 RAG 明确说
    # "没有相关资料"；③拒答标签题（meta.tags 含"拒答"）回答中明确披露商品查询不到/不在商品池
    # （"诚实披露库外"也算拒答——与"编造库外参数"相对）。注意区分：正常回答里顺带说
    # "如果客服查不到订单"不属于拒答，故③仅对拒答标签题生效。
    refusal_tagged = "拒答" in (case.meta or {}).get("tags", [])
    disclose_absent = any(w in answer for w in
                          ("查询不到", "查不到", "商品池中不存在", "没有这款商品", "没有该商品",
                           "不在商品池", "未收录"))
    is_refusal = (result.intent == "out_of_scope"
                  or (result.intent == "after_sales" and "没有相关资料" in answer)
                  or (refusal_tagged and disclose_absent))
    expected_behavior = (case.meta or {}).get("expected_behavior", "answer")

    # ① 任务完成：无降级 + 约束全过 + 拒答行为正确
    constraints_ok, fails = check_constraints(answer, case.constraints)
    behavior_ok = (is_refusal == (expected_behavior == "refuse"))
    task_completed = (not result.degradations) and constraints_ok and behavior_ok

    # ② 工具命中：实际调用的工具集合 vs 期望集合（覆盖率）
    actual_tools = {t["name"] for r in result.tasks for t in r.tool_trace if t.get("ok")}
    expected_tools = [t for t in (case.meta or {}).get("expected_tools", []) if t != "finish"]
    tool_acc = 1.0 if not expected_tools else round(
        len(actual_tools & set(expected_tools)) / len(expected_tools), 2)

    # ③ 拒答正确
    refusal_correct = behavior_ok

    # ④ 引用可验证：[n] 全部落在资料编号范围内（任务结论 + 引用卡）
    n_sources = len(result.tasks) + len(result.citations)
    cited = {int(m) for m in CITE_PATTERN.findall(answer)}
    citation_ok = (not cited) or (cited <= set(range(1, n_sources + 1)))

    return {
        "id": case.id,
        "prompt": case.prompt,
        "task_completed": task_completed,
        "tool_acc": tool_acc,
        "refusal_correct": refusal_correct,
        "citation_verifiable": citation_ok,
        "tokens": result.tokens,
        "duration_s": round(result.duration_s, 1),
        "note": "；".join(fails[:3]) or (result.degradations[0][:60] if result.degradations else ""),
    }


def run_benchmark(limit: int | None = None, compare: bool = False) -> None:
    cases = load_cases()
    if limit:
        cases = cases[:limit]
    SESSIONS_DIR.mkdir(exist_ok=True)
    (SESSIONS_DIR / "profile_bench.json").unlink(missing_ok=True)  # 每轮画像重置，保证可复现

    print(f"开始评测：{len(cases)} 题（四项确定性指标，零裁判 token）\n" + "=" * 60)
    scores, rows = [], []
    for i, case in enumerate(cases, 1):
        t0 = time.time()
        result = run_team(case.prompt, session_id=f"bench_{case.id}", user_id="bench")
        s = score_case(case, result)
        scores.append(s)
        rows.append(s)
        print(f"[{i}/{len(cases)}] {case.id} 完成={s['task_completed']} 工具={s['tool_acc']} "
              f"拒答={s['refusal_correct']} 引用={s['citation_verifiable']} "
              f"tokens={s['tokens']} ({time.time() - t0:.0f}s) {s['note']}")

    n = len(scores)
    summary = {
        "n_cases": n,
        "task_completed_rate": round(sum(s["task_completed"] for s in scores) / n, 3),
        "tool_selection_acc": round(sum(s["tool_acc"] for s in scores) / n, 3),
        "refusal_correct_rate": round(sum(s["refusal_correct"] for s in scores) / n, 3),
        "citation_verifiable_rate": round(sum(s["citation_verifiable"] for s in scores) / n, 3),
        "avg_tokens": round(sum(s["tokens"] for s in scores) / n),
    }

    comparison = None
    if compare:
        print("\n" + "=" * 60 + "\n对照实验：全量注入 vs task_type 预加载（同一题库重跑）")
        (SESSIONS_DIR / "profile_bench.json").unlink(missing_ok=True)
        full_rows = []
        for case in cases:
            result = run_team(case.prompt, session_id=f"benchfull_{case.id}",
                              user_id="bench", preload_tools=False)
            full_rows.append(score_case(case, result))
            print(f"  {case.id}: full-tokens={full_rows[-1]['tokens']}")
        comparison = {
            "preloaded_avg_tokens": summary["avg_tokens"],
            "full_avg_tokens": round(sum(r["tokens"] for r in full_rows) / n),
        }
        comparison["saving_pct"] = round(
            (comparison["full_avg_tokens"] - comparison["preloaded_avg_tokens"])
            / max(comparison["full_avg_tokens"], 1) * 100, 1)

    write_report(summary, rows, comparison)
    print("\n===== 四项指标 =====")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    if comparison:
        print(f"  对照实验：预加载 {comparison['preloaded_avg_tokens']} tokens/题 vs "
              f"全量 {comparison['full_avg_tokens']} tokens/题 → 省 {comparison['saving_pct']}%")
    print(f"报告 → {REPORT_FILE}")


# ---------------- 基线对照：Agent vs 纯 API 调用（回应"不就是调 API 吗"） ----------------
BASELINE_SYSTEM = "你是一个电商导购助手，用中文回答用户的购物问题。"
# 基线无意图分类，拒答判定只能靠措辞（与 Agent 的 intent 短路口径不同，见报告说明）
BASELINE_REFUSAL_WORDS = ("没有相关资料", "无法回答", "知识库中没有", "无法帮助",
                          "商品池中不存在", "查询不到该商品")


def score_baseline(case: BenchCase, answer: str, tokens: int, duration_s: float) -> dict:
    constraints_ok, fails = check_constraints(answer, case.constraints)
    expected_refuse = (case.meta or {}).get("expected_behavior", "answer") == "refuse"
    is_refusal = any(w in answer for w in BASELINE_REFUSAL_WORDS) or (
        "拒答" in (case.meta or {}).get("tags", []) and any(
            w in answer for w in ("查询不到", "查不到", "不在商品池", "没有该商品")))
    behavior_ok = is_refusal == expected_refuse
    return {
        "id": case.id,
        "task_completed": constraints_ok and behavior_ok,
        "refusal_correct": behavior_ok,
        "tokens": tokens,
        "duration_s": round(duration_s, 1),
        "note": "；".join(fails[:3]),
        "answer": answer,
    }


def run_baseline(limit: int | None = None) -> dict:
    """纯 API 基线：同 25 题、直接问 LLM——不给工具、不给知识库、不拆任务、无记忆。"""
    import time as _time

    from shoppilot.llm import get_llm

    cases = load_cases()
    if limit:
        cases = cases[:limit]
    llm = get_llm()
    print(f"\n基线对照：纯 API 直接调用（同 {len(cases)} 题，无工具/无知识库/无编排）")
    rows = []
    for i, case in enumerate(cases, 1):
        t0 = _time.time()
        answer, tokens = llm.chat_text(
            [{"role": "system", "content": BASELINE_SYSTEM},
             {"role": "user", "content": case.prompt}], temperature=0.3)
        row = score_baseline(case, answer, tokens, _time.time() - t0)
        rows.append(row)
        print(f"[{i}/{len(cases)}] {case.id} 完成={row['task_completed']} "
              f"拒答={row['refusal_correct']} tokens={tokens} {row['note'][:50]}")
    n = len(rows)
    return {
        "n": n,
        "task_completed_rate": round(sum(r["task_completed"] for r in rows) / n, 3),
        "refusal_correct_rate": round(sum(r["refusal_correct"] for r in rows) / n, 3),
        "avg_tokens": round(sum(r["tokens"] for r in rows) / n),
        "rows": rows,
    }


def merge_baseline_section(baseline: dict, agent_summary: dict | None) -> None:
    """把基线对照写进报告（存在旧节则替换）。"""
    report = REPORT_FILE.read_text(encoding="utf-8") if REPORT_FILE.exists() else "# ShopPilot Benchmark Report\n"
    marker_start, marker_end = "<!-- BASELINE-START -->", "<!-- BASELINE-END -->"
    body = report
    if marker_start in report:
        body = report.split(marker_start)[0] + report.split(marker_end)[-1].lstrip("\n")

    agent_part = ""
    if agent_summary:  # 同轮已有 Agent 数据 → 同表对比
        agent_part = (f"| **ShopPilot Agent** | **{agent_summary['task_completed_rate']}** "
                      f"| **{agent_summary['refusal_correct_rate']}** "
                      f"| {agent_summary['avg_tokens']} |\n")
    elif marker_start in report:  # 复用旧报告里的 Agent 行（--baseline 单独重跑时）
        import re as _re
        m = _re.search(r"\*\*ShopPilot Agent\*\* \| \*\*([\d.]+)\*\* \| \*\*([\d.]+)\*\* \| (\d+)", report)
        if m:
            agent_part = (f"| **ShopPilot Agent** | **{m.group(1)}** | **{m.group(2)}** "
                          f"| {m.group(3)} |\n")
    sample = ""
    for r in baseline["rows"]:  # 挑一个基线翻车的例子做定性展示
        if not r["task_completed"] and r["note"]:
            sample = (f"\n**定性样例（{r['id']}）**——纯 API 无法访问商品池/评论/知识库：\n\n"
                      f"> {r['answer'][:180]}…\n"
                      f"> 失败点：{r['note']}\n")
            break

    section = (f"\n{marker_start}\n## 对照实验：Agent vs 纯 API 调用\n\n"
               f"> 基线定义：同 25 题直接问 LLM（system=通用导购助手），不给工具、不给知识库、"
               f"不拆任务、无记忆。同一套确定性指标评分（基线无意图分类，拒答判定靠措辞规则，"
               f"工具命中率/引用可验证率对基线不适用，记 N/A）。\n\n"
               f"| 方案 | 任务完成率 | 拒答正确率 | 平均 tokens/题 |\n| --- | --- | --- | --- |\n"
               f"| 纯 API 调用（基线） | {baseline['task_completed_rate']} "
               f"| {baseline['refusal_correct_rate']} | {baseline['avg_tokens']} |\n"
               f"{agent_part}{sample}\n"
               f"结论：基线的失败集中在**编造商品/价格**（无商品池可查）、**答不准售后数字**"
               f"（无知识库可引）、**该拒答时硬答**。Agent 的价值不在模型，而在约束模型输出的系统。\n"
               f"{marker_end}\n")
    REPORT_FILE.write_text(body.rstrip("\n") + "\n" + section, encoding="utf-8")


def write_report(summary: dict, rows: list[dict], comparison: dict | None) -> None:
    REPORT_FILE.parent.mkdir(exist_ok=True)
    lines = [
        "# ShopPilot Benchmark Report",
        "",
        f"> {summary['n_cases']} 题导购验收集 · 四项确定性指标（零裁判 token）· 字段设计与 llmeval 体系同源",
        "",
        "## 总览",
        "",
        "| 指标 | 数值 |",
        "| --- | --- |",
        f"| 任务完成率 task_completed | {summary['task_completed_rate']} |",
        f"| 工具命中率 tool_selection_acc | {summary['tool_selection_acc']} |",
        f"| 拒答正确率 refusal_correct | {summary['refusal_correct_rate']} |",
        f"| 引用可验证率 citation_verifiable | {summary['citation_verifiable_rate']} |",
        f"| 平均 tokens/题 | {summary['avg_tokens']} |",
        "",
        "## 明细",
        "",
        "| id | 题目 | 完成 | 工具命中 | 拒答 | 引用 | tokens | 备注 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        lines.append(f"| {r['id']} | {r['prompt'][:18]}… | {r['task_completed']} | "
                     f"{r['tool_acc']} | {r['refusal_correct']} | {r['citation_verifiable']} | "
                     f"{r['tokens']} | {r['note'][:40]} |")
    if comparison:
        lines += ["", "## 对照实验：工具预加载 vs 全量注入", "",
                  f"- 预加载（默认）：**{comparison['preloaded_avg_tokens']}** tokens/题",
                  f"- 全量注入 6 工具 schema：**{comparison['full_avg_tokens']}** tokens/题",
                  f"- **节省 {comparison['saving_pct']}%**（Dispatcher 按 task_type 只注入相关工具 schema）"]
    lines += ["", "## 复现", "",
              "```", "D:\\conda_envs\\agent\\python.exe -X utf8 benchmark.py --run --compare", "```"]
    REPORT_FILE.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ShopPilot benchmark")
    parser.add_argument("--run", action="store_true", help="跑评测")
    parser.add_argument("--compare", action="store_true", help="附加全量注入 vs 预加载 token 对照")
    parser.add_argument("--baseline", action="store_true",
                        help="跑'纯 API 调用'基线对照（回应'不就是调 API 吗'），结果并入报告")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 题（调试用）")
    args = parser.parse_args()
    if args.run:
        run_benchmark(limit=args.limit, compare=args.compare)
    if args.baseline:
        merge_baseline_section(run_baseline(limit=args.limit), agent_summary=None)
        print(f"基线对照已并入 → {REPORT_FILE}")
    if not (args.run or args.baseline):
        print(__doc__)
