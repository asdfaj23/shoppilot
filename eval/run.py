"""Run the output-checking ablation: one case set, four strategies, one ruler.

Real run (needs an API key; costs a little on the paid LLM):
    shoppilot eval --all --limit 5
    python -m eval.run --all --limit 5

Offline smoke test (ScriptedLLM, zero tokens, proves the pipeline end-to-end):
    python -m eval.run --dry-run

The dry-run numbers are *scripted* placeholders, not research results. Use them
only to verify the harness wires up; the headline table comes from a real run.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from eval.metrics import score_case
from eval.strategies import STRATEGIES, STRATEGY_ORDER, VIOLATION_KINDS
from shoppilot.core.llm import ScriptedLLM
from shoppilot.domains.ecommerce.adapter import get_adapter
from shoppilot.domains.ecommerce.factory import build_ecommerce_agent

CASES_FILE = Path(__file__).resolve().parent / "cases.json"
ADVERSARIAL_FILE = Path(__file__).resolve().parent / "adversarial_cases.json"
REPORT_DIR = Path(__file__).resolve().parent / "reports"


def load_cases(include_adversarial: bool = True) -> list[dict]:
    data = json.loads(CASES_FILE.read_text(encoding="utf-8"))["cases"]
    if include_adversarial and ADVERSARIAL_FILE.exists():
        data += json.loads(ADVERSARIAL_FILE.read_text(encoding="utf-8"))["cases"]
    return data


def _agent(strategy: str, llm):
    return build_ecommerce_agent(
        llm=llm, guardrail_mode=STRATEGIES[strategy]["guardrail_mode"],
        enable_memory=False, report_dir=REPORT_DIR)


def run_strategy(strategy: str, cases: list[dict], llm) -> list[dict]:
    agent = _agent(strategy, llm)
    rows = []
    adapter = get_adapter()
    for case in cases:
        result = agent.run(case["prompt"])
        row = score_case(adapter, case, result, agent.last_query)
        row["strategy"] = strategy
        rows.append(row)
    return rows


def summarize(rows_by_strategy: dict[str, list[dict]]) -> dict:
    out = {}
    for s, rows in rows_by_strategy.items():
        n = max(len(rows), 1)
        out[s] = {
            "cases": len(rows),
            "pass_rate": round(sum(r["task_passed"] for r in rows) / n, 3),
            "violation_rate": round(sum(r["violation_total"] for r in rows) / n, 3),
            **{k: round(sum(r[k] for r in rows) / n, 3) for k in VIOLATION_KINDS},
            "extra_tokens_mean": round(sum(r["extra_tokens"] for r in rows) / n, 1),
            "tokens_mean": round(sum(r["tokens"] for r in rows) / n, 1),
            "repaired": sum(r["repaired"] for r in rows),
        }
    return out


def render_markdown(summary: dict, dry: bool) -> str:
    banner = ("# 输出护栏对照实验\n\n> ⚠️ **DRY-RUN 演示**：以下数字由 ScriptedLLM 脚本化生成，"
             "仅证明管线可用，**不是真实模型结论**。\n"
             "> 真实结论请用 `shoppilot eval --all`（需 API key）跑出。\n\n"
             if dry else "# 输出护栏对照实验（真实运行）\n\n")
    header = ("| 策略 | 通过率 | 违规率 | 超预算 | 编造 | 越界引用 | 误拒答 | 额外tokens/题 | 修复数 |\n"
              "|---|---|---|---|---|---|---|---|---|")
    lines = [banner, header]
    for s in STRATEGY_ORDER:
        if s not in summary:
            continue
        m = summary[s]
        label = STRATEGIES[s]["label"]
        lines.append(
            f"| {label} | {m['pass_rate']:.0%} | {m['violation_rate']:.0%} "
            f"| {m['over_budget']:.0%} | {m['fabrication']:.0%} | {m['citation']:.0%} "
            f"| {m['refusal']:.0%} | {m['extra_tokens_mean']:.0f} | {m['repaired']} |")
    lines.append("\n**读法**：同一 ReAct 内核、同一题库、同一模型 temperature=0，只换"
                 "“最终输出怎么被检查”。确定性护栏的目标是在违规率接近 0 的同时，"
                 "额外 token 也接近 0。\n")
    return "\n".join(lines)


# --------------------------- dry-run ScriptedLLM ---------------------------
def _dry_run_router(messages):
    """Scripted router: a first draft that over-recommends, an LLM critic that
    passes it through, and a deterministic repair that later fixes it. We only
    run this to smoke-test the harness."""
    system = messages[0]["content"]
    if "规划器" in system:
        return {"query": {"intent": "recommend", "entities": ["蓝牙耳机A"],
                          "category": "数码配件", "budget_max": 500,
                          "budget_min": None, "concerns": ["降噪"]},
                "tasks": [{"id": 1, "goal": "推荐500元内降噪耳机",
                           "task_type": "data_query", "depends_on": []},
                          {"id": 2, "goal": "汇总理由", "task_type": "synthesis",
                           "depends_on": [1]}]}
    if "任务执行者" in system:
        if any(m.get("role") == "tool" for m in messages):
            return {"content": "蓝牙耳机A 349元 降噪好；蓝牙耳机F 899元 音质强。"}
        return {"tool_calls": [{"id": "c1", "type": "function", "function": {
            "name": "recommend_products",
            "arguments": '{"category":"数码配件","budget_max":500}'}}]}
    if "分析执行者" in system:
        return "蓝牙耳机A 是500元内降噪首选。"
    if "审查" in system or "Critic" in system:  # LLM arms pass the bad draft through
        return {"pass": True, "issues": []}
    if "成稿者" in system:  # over-budget draft (recommends the 899 model)
        return {"answer_md": "为你推荐蓝牙耳机A与蓝牙耳机F（¥899），都很出色。[1]",
                "card": {"kind": "recommendation", "summary": "两款耳机",
                         "items": [{"name": "蓝牙耳机A", "price": 349, "citation": 1},
                                   {"name": "蓝牙耳机F", "price": 899, "citation": 1}]},
                "citations": [{"no": 1, "source": "任务1", "heading": "推荐",
                               "text": "蓝牙耳机A 349 降噪"}]}
    if "修复器" in system:  # deterministic arm's repaired draft (in budget)
        return {"answer_md": "为你推荐蓝牙耳机A（¥349），降噪好且在500元预算内。[1]",
                "card": {"kind": "recommendation", "summary": "500元内首选",
                         "items": [{"name": "蓝牙耳机A", "price": 349, "citation": 1}]},
                "citations": [{"no": 1, "source": "任务1", "heading": "推荐",
                               "text": "蓝牙耳机A 349 降噪"}]}
    return {"answer_md": "（脚本化占位）", "card": None}


def main(argv=None):
    p = argparse.ArgumentParser(description="ShopPilot output-checking ablation")
    p.add_argument("--strategy", choices=STRATEGY_ORDER, help="只跑单臂")
    p.add_argument("--all", action="store_true", help="跑全部臂")
    p.add_argument("--limit", type=int, default=None, help="只跑前 N 题")
    p.add_argument("--dry-run", action="store_true",
                   help="用 ScriptedLLM 离线冒烟（零 token，不打真实 LLM）")
    args = p.parse_args(argv)

    cases = load_cases()
    if args.limit:
        cases = cases[:args.limit]
    strategies = ([args.strategy] if args.strategy else
                  STRATEGY_ORDER if args.all else ["deterministic"])

    llm = ScriptedLLM(router=_dry_run_router) if args.dry_run else None  # None => real
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    rows_by_strategy = {s: run_strategy(s, cases, llm) for s in strategies}
    summary = summarize(rows_by_strategy)
    md = render_markdown(summary, dry=args.dry_run)

    stamp = time.strftime("%Y%m%d_%H%M%S")
    tag = "dryrun" if args.dry_run else "real"
    (REPORT_DIR / f"results_{tag}_{stamp}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORT_DIR / f"main_table_{tag}_{stamp}.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"\n耗时 {time.time() - t0:.1f}s，报告已写入 {REPORT_DIR}/")


if __name__ == "__main__":
    main()
