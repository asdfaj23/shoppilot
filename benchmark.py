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
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 题（调试用）")
    args = parser.parse_args()
    if args.run:
        run_benchmark(limit=args.limit, compare=args.compare)
    else:
        print(__doc__)
