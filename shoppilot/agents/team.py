"""
ShopPilot team.py · 总装：规划 → 执行 → 成稿（自检） → 记忆回写
================================================================================
刻意保持简单：三个部件（规划 / 执行 / 成稿）+ 两个记忆（短期/长期），不做过度分层。
    run_team()
      ├─ planner.make_plan()      意图 + 约束抽取（注入长期/短期记忆，支持"它/这个"指代）
      ├─ executor.execute_plan()  DAG 分层并发调工具（工具按 task_type 预加载）
      ├─ composer.compose()       聚合子结果 → 结构化产出卡 + 强制引用 + 五项确定性自检
      ├─ 研究报告落盘            intent=research 时额外存 data/reports/*.md（可下载）
      └─ memory.add_turn()        短期进滑窗，长期追加一条可读记录
失败一律降级：任一子任务失败/超时 → 记 degraded，其余照常，必要时拒答短路。
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from shoppilot.agents.composer import compose
from shoppilot.agents.executor import execute_plan
from shoppilot.agents.planner import make_plan, refuse_looks_wrong, unknown_entities
from shoppilot.domains.ecommerce.tools import registry
from shoppilot.harness.memory import MemoryManager
from shoppilot.harness.observability import Tracer
from shoppilot.models import TeamResult

REPORT_DIR = Path(__file__).resolve().parents[1] / "reports"


def _dump_report(result: TeamResult) -> str:
    """把研究报告卡渲染成 Markdown 存档（用户可下载）。"""
    card = result.structured or {}
    lines = [f"# {card.get('topic', '选购研究报告')}", "",
             f"> 生成时间：{datetime.now():%Y-%m-%d %H:%M} ｜ 数据窗口：{card.get('data_window', '—')}", "",
             "## 结论", card.get("conclusion", ""), "", "## 候选对比", ""]
    for c in card.get("candidates", []):
        lines.append(f"- **{c.get('name')}**（¥{c.get('price')}，评分 {c.get('score')}）→ {c.get('verdict', '')}")
    lines += ["", "## 证据", ""]
    for e in card.get("evidence", []):
        cite = f" [{e.get('citation')}]" if e.get("citation") else ""
        lines.append(f"- {e.get('claim')}{cite}（来源：{e.get('source', '—')}）")
    lines += ["", "## 风险", ""]
    lines += [f"- {r}" for r in card.get("risks", [])]
    lines += ["", "## 建议", ""]
    lines += [f"- {a}" for a in card.get("advice", [])]
    lines += ["", "---", "", "## 完整回答", result.answer_md]
    return "\n".join(lines)


def _persist_report(result: TeamResult) -> str:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"report_{datetime.now():%Y%m%d_%H%M%S}.md"
    path.write_text(_dump_report(result), encoding="utf-8")
    return str(path)

OUT_OF_SCOPE_ANSWER = (
    "抱歉，这个问题超出了我的导购职责范围。\n\n"
    "我能帮你：**选购推荐**（按预算/在意维度打分推荐）、**商品对比**、**口碑解读**（差评归因/避坑）、"
    "**售后问答**（保修/退换货）。商品池覆盖手机、笔电、平板、耳机、手表、相机、显示器、"
    "路由器、智能家居等 12 个品类。换一个问题试试？"
)


def run_team(user_text: str, session_id: str = "default", user_id: str = "default",
             on_event=None, preload_tools: bool = True) -> TeamResult:
    on_event = on_event or (lambda *_: None)
    t0 = time.time()
    tracer = Tracer("shoppilot", on_event=on_event)
    memory = MemoryManager(session_id, user_id)
    memory_block = memory.memory_block(user_text)
    result = TeamResult()

    # ① 规划：意图 + 约束 + 任务拆解（带记忆）
    query, plan, tokens = make_plan(user_text, memory_block=memory_block, on_event=on_event)
    result.intent = query.intent if query else ""
    result.tokens += tokens
    tracer.log("plan", "Planner", user_text,
               plan.model_dump_json() if plan else "out_of_scope", tokens=tokens)

    # ② 防误拒：Planner 判超范围但文本明显是导购请求 → 重问一次（零 token 的确定性规则）
    if plan is None and refuse_looks_wrong(user_text):
        on_event("refuse_recheck", {})
        query, plan, tokens2 = make_plan(
            user_text + "\n（系统提示：若与导购相关请勿判 out_of_scope，商品池内品类务必正常拆任务）",
            memory_block=memory_block, on_event=on_event)
        result.intent = query.intent if query else result.intent
        result.tokens += tokens2

    # ③ 库外实体闸：点名了池外型号（如"蓝牙耳机Z"）→ 要求按拒答处理，防编造参数
    if plan is not None and query.entities and unknown_entities(query):
        on_event("pool_recheck", {"unknown": unknown_entities(query)})
        query, plan, tokens2 = make_plan(
            user_text + f"\n（系统提示：{unknown_entities(query)} 不在商品池中，"
                        "请按 out_of_scope 处理或明确说明查不到，禁止编造价格参数）",
            memory_block=memory_block, on_event=on_event)
        result.intent = query.intent if query else result.intent
        result.tokens += tokens2

    if plan is None:  # 拒答短路
        result.answer_md = OUT_OF_SCOPE_ANSWER
        result.duration_s = time.time() - t0
        tracer.log("refuse", "OutOfScope", user_text, result.answer_md)
        memory.add_turn(user_text, result.answer_md, [], "触发拒答：超出范围或池外商品")
        result.trace_file = tracer.summary()["trace_file"]
        return result

    # ④ 执行：DAG 分层并发
    results, sources = execute_plan(plan, registry, tracer, preload=preload_tools, on_event=on_event)
    result.tasks = list(results.values())
    result.tokens += sum(r.tokens for r in results.values())
    result.degradations = [f"任务{r.task_id}（{r.task_type}）降级：{r.output[:50]}"
                           for r in results.values() if r.degraded]

    # ⑤ 成稿：聚合 + 结构化卡 + 强制引用 + 五项确定性自检（必要时修复一次）
    result = compose(query, results, sources, memory_block, tracer, on_event=on_event)
    result.tokens += result.tokens  # compose 已计入自身 tokens；此处保留结构清晰
    result.duration_s = time.time() - t0
    summary = tracer.summary()
    result.trace_file = summary["trace_file"]
    result.token_breakdown = summary["by_step"]

    # ⑥ 记忆回写：短期进滑窗，长期追加一条
    items = [it.get("name", "") for it in (result.structured or {}).get("items", []) if it.get("name")]
    items += [s for s in (result.structured or {}).get("subjects", [])]
    memory.add_turn(user_text, result.answer_md, items,
                    (result.structured or {}).get("summary")
                    or (result.structured or {}).get("conclusion", ""),
                    tokens=result.tokens, llm=None)

    # ⑦ 研究报告落盘（research 意图）
    if result.intent == "research" and result.structured:
        result.report_file = _persist_report(result)
    on_event("done", {"tokens": result.tokens, "duration_s": round(result.duration_s, 1)})
    return result


if __name__ == "__main__":
    r = run_team("预算500以内推荐一副降噪耳机")
    print(r.intent, "| tokens:", r.tokens, "|", r.duration_s, "s")
    print(r.answer_md[:400])
