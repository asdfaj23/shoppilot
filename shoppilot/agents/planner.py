"""
ShopPilot agents · Planner：意图分类 + 需求结构化抽取 + 拆子任务 DAG
================================================================================
吸收 week2/planner.py 的 Plan-and-Solve 与 Reflection 重试，三点升级：
  1. 一次调用同时产出 ShoppingQuery（意图/实体/预算/在意维度）与任务 DAG——省一次 LLM 往返；
  2. task_type 扩为四类并加 review_analysis（口碑场景），Literal 枚举收敛模型行为；
  3. 解析即校验：json_object 输出 → Pydantic 契约校验 → 不过关把错误喂回去重写（Reflection）。
超范围（out_of_scope）不拆任务，直接由 team 走拒答通道。
"""
from __future__ import annotations

import json

from shoppilot.models import Plan, ShoppingQuery, SubTask, SubTaskResult

PLANNER_SYSTEM = """你是电商导购任务规划器。分析用户输入，输出 JSON：
{"query": {"intent": "recommend|compare|review_analysis|after_sales|out_of_scope",
           "entities": ["提到的商品名"], "category": "品类（未知则空串）",
           "budget_max": 数字或null, "budget_min": 数字或null, "concerns": ["在意维度"]},
 "tasks": [{"id": 1, "goal": "子任务描述（含具体商品名/品类）", "task_type": "data_query|review_analysis|knowledge_qa|synthesis", "depends_on": []}]}

规则：
1. intent=out_of_scope 的判据从严：只有当①请求与导购完全无关（写代码/聊天/问路），或②明确询问商品池里不存在的具体型号（如 iPhone/苹果耳机），才判 out_of_scope；
   商品池内品类（耳机/充电宝/手机壳/手表/数据线/键盘/显示器/路由器/音箱）的推荐、对比、口碑、售后一律 NOT out_of_scope，拿不准时也判为导购意图；
2. 导购意图按场景选：推荐选品=recommend、A和B怎么选=compare、口碑/翻车/吐槽=review_analysis、保修/退换/售后=after_sales;
3. tasks 拆 1~4 个：recommend 先 data_query（按预算品类筛商品）再 synthesis；compare 先 data_query（查两商品详情）再 synthesis；
   review_analysis 用 review_analysis 类型任务（查差评分布/搜评论原文）+ synthesis；after_sales 用 knowledge_qa（查售后知识库）；
   recommend 场景若用户在意具体维度（安全/质量/续航/噪音等），加一个 review_analysis 任务查该维度口碑；
4. depends_on 只能引用 id 更小的任务；最后一个任务通常是 synthesis（汇总出结论）；
5. goal 要写到"模型拿到它就能直接干活"的粒度，goal 中商品必须写全称，只输出 JSON。"""

FEWSHOT = (
    '示例1：输入"预算500内推荐降噪耳机" → {"query":{"intent":"recommend","entities":[],'
    '"category":"数码配件","budget_max":500,"budget_min":null,"concerns":["降噪"]},'
    '"tasks":[{"id":1,"goal":"筛选预算500以内、具备降噪功能的耳机商品","task_type":"data_query","depends_on":[]},'
    '{"id":2,"goal":"结合筛选结果与用户在意维度（降噪），输出推荐清单与理由","task_type":"synthesis","depends_on":[1]}]}\n'
    '示例2：输入"蓝牙耳机A保修多久" → {"query":{"intent":"after_sales","entities":["蓝牙耳机A"],'
    '"category":"","budget_max":null,"budget_min":null,"concerns":["售后"]},'
    '"tasks":[{"id":1,"goal":"从售后知识库查询蓝牙耳机A的保修政策","task_type":"knowledge_qa","depends_on":[]}]}\n'
)


def make_plan(user_text: str, profile_block: str = "", state_block: str = "",
              llm=None, on_event=None) -> tuple[ShoppingQuery | None, Plan | None, int]:
    """返回 (query, plan, tokens)。out_of_scope 时 plan=None。解析两次失败抛 RuntimeError。"""
    from shoppilot.domains.ecommerce.adapter import product_names
    from shoppilot.llm import get_llm

    llm = llm or get_llm()
    on_event = on_event or (lambda *_: None)
    pool = "、".join(product_names())
    user = (f"{FEWSHOT}\n[商品池（池外型号一律视为不存在，不得编造）]\n{pool}\n\n"
            f"[用户画像]\n{profile_block or '（暂无）'}\n")
    if state_block:  # Session State：多轮追问消解指代（"那它保修多久"的"它"）
        user += f"[任务状态]\n{state_block}\n"
    user += f"\n用户输入：{user_text}"
    messages = [{"role": "system", "content": PLANNER_SYSTEM}, {"role": "user", "content": user}]
    last_err = ""
    tokens_total = 0
    for attempt in (1, 2):
        data, tk = llm.chat_json(messages, temperature=0.2)
        tokens_total += tk
        try:
            query = ShoppingQuery(**(data.get("query") or {}), raw_text=user_text)
            if query.intent == "out_of_scope":
                on_event("plan", {"intent": query.intent, "tasks": []})
                return query, None, tokens_total
            tasks = [SubTask(**t) for t in data.get("tasks") or []]
            plan = Plan(query=query, tasks=tasks)  # Pydantic 校验 DAG（id唯一/依赖合法/无环）
            on_event("plan", {"intent": query.intent,
                              "tasks": [t.model_dump() for t in plan.tasks]})
            return query, plan, tokens_total
        except Exception as e:
            last_err = str(e)
            on_event("plan_retry", {"attempt": attempt, "error": last_err[:160]})
            messages.append({"role": "assistant", "content": json.dumps(data, ensure_ascii=False)})
            messages.append({"role": "user",
                             "content": f"你的输出有误（{last_err}），严格按规则重写 JSON。"})
    raise RuntimeError(f"Planner 两次都无法产出合法计划：{last_err}")


# ---------------- 计划更新（Replan）：计划是"可更新的任务清单"，不是机械执行的死计划 ----------------
REPLAN_SYSTEM = """你是导购任务的重新规划器。原计划执行时有子任务失败/降级，请基于失败证据输出 JSON：
{"tasks": [{"id": 数字, "goal": "补救子任务（含具体商品名全称）", "task_type": "data_query|review_analysis|knowledge_qa|synthesis", "depends_on": []}]}

规则：
1. 只为没拿到的信息拆任务（成功过的任务不要重复拆）；
2. 补救思路要换路径：换更宽的筛选条件、换关键词、或改用 synthesis 直接基于已有结论收尾；
3. id 从传入的 start_id 开始连续编号；depends_on 只能引用已有任务 id（原任务或本次新任务中 id 更小者）；
4. 最多 2 个任务；只输出 JSON。"""


def replan(query: ShoppingQuery, failed_results: list[SubTaskResult],
           done_results: dict[int, SubTaskResult], start_id: int,
           llm=None, on_event=None) -> tuple[Plan | None, int]:
    """基于失败证据重拆补救任务。失败/无产出时返回 (None, tokens)——降级不阻塞主链路。"""
    import json as _json

    from shoppilot.llm import get_llm
    from shoppilot.models import Plan, SubTask

    if not failed_results:
        return None, 0
    llm = llm or get_llm()
    on_event = on_event or (lambda *_: None)
    done_ids = sorted(done_results)
    fail_block = "\n".join(f"- 任务{r.task_id}（{r.task_type}）{r.goal}\n  失败信息：{r.output[:150]}"
                           for r in failed_results)
    done_block = "\n".join(f"- 任务{tid}（成功）：{done_results[tid].goal} → {done_results[tid].output[:80]}"
                           for tid in done_ids) or "（无）"
    messages = [
        {"role": "system", "content": REPLAN_SYSTEM},
        {"role": "user", "content": f"用户需求：{query.raw_text}\n意图：{query.intent}\n"
                                    f"已成功任务：\n{done_block}\n\n失败任务：\n{fail_block}\n"
                                    f"start_id={start_id}"},
    ]
    for attempt in (1, 2):
        data: dict = {}
        try:
            data, tokens = llm.chat_json(messages, temperature=0.2)
            tasks = [SubTask(**t) for t in data.get("tasks") or []]
            for t in tasks:  # 依赖合法性：允许指向原任务 id（保证拓扑序）
                for d in t.depends_on:
                    if d >= t.id:
                        raise ValueError(f"依赖必须指向更小的 id：任务{t.id}→{d}")
            if not tasks:
                return None, tokens
            plan = Plan(query=query, tasks=tasks)
            on_event("replan", {"tasks": [t.model_dump() for t in plan.tasks]})
            return plan, tokens
        except Exception as e:
            on_event("replan_retry", {"attempt": attempt, "error": str(e)[:120]})
            messages.append({"role": "assistant", "content": _json.dumps(data or {}, ensure_ascii=False)})
            messages.append({"role": "user", "content": f"输出有误（{e}），按规则重写 JSON。"})
    return None, 0


# ---------------- 防误拒复核（确定性规则，不花 token） ----------------
GUIDE_HINT_WORDS = ("推荐", "怎么选", "对比", "口碑", "差评", "翻车", "保修", "退换", "售后",
                    "值得买", "多少钱", "预算", "选哪个", "吐槽", "评价")


def refuse_looks_wrong(user_text: str) -> bool:
    """Planner 判了 out_of_scope，但文本明显是导购请求 → 疑似误拒，值得重问一次。"""
    return any(w in user_text for w in GUIDE_HINT_WORDS)


def unknown_entities(query: ShoppingQuery) -> list[str]:
    """确定性库外实体闸：用户点名的型号不在商品池 → 疑似该走拒答/说明通道（零 token）。"""
    from shoppilot.domains.ecommerce.adapter import product_names

    pool = set(product_names())
    return [e for e in query.entities if e and e not in pool
            and not any(e in p or p in e for p in pool)]
