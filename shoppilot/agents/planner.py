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

from shoppilot.models import Plan, ShoppingQuery, SubTask

PLANNER_SYSTEM = """你是电商导购任务规划器。分析用户输入，输出 JSON：
{"query": {"intent": "recommend|compare|review_analysis|after_sales|out_of_scope",
           "entities": ["提到的商品名"], "category": "品类（未知则空串）",
           "budget_max": 数字或null, "budget_min": 数字或null, "concerns": ["在意维度"]},
 "tasks": [{"id": 1, "goal": "子任务描述（含具体商品名/品类）", "task_type": "data_query|review_analysis|knowledge_qa|synthesis", "depends_on": []}]}

规则：
1. 商品池外的商品或与导购无关的请求 → intent=out_of_scope，tasks=[];
2. 导购意图按场景选：推荐选品=recommend、A和B怎么选=compare、口碑/翻车/吐槽=review_analysis、保修/退换/售后=after_sales;
3. tasks 拆 1~4 个：recommend 先 data_query（按预算品类筛商品）再 synthesis；compare 先 data_query（查两商品详情）再 synthesis；
   review_analysis 用 review_analysis 类型任务（查差评分布/搜评论原文）+ synthesis；after_sales 用 knowledge_qa（查售后知识库）；
4. depends_on 只能引用 id 更小的任务；最后一个任务通常是 synthesis（汇总出结论）；
5. goal 要写到"模型拿到它就能直接干活"的粒度，只输出 JSON。"""

FEWSHOT = (
    '示例1：输入"预算500内推荐降噪耳机" → {"query":{"intent":"recommend","entities":[],'
    '"category":"数码配件","budget_max":500,"budget_min":null,"concerns":["降噪"]},'
    '"tasks":[{"id":1,"goal":"筛选预算500以内、具备降噪功能的耳机商品","task_type":"data_query","depends_on":[]},'
    '{"id":2,"goal":"结合筛选结果与用户在意维度（降噪），输出推荐清单与理由","task_type":"synthesis","depends_on":[1]}]}\n'
    '示例2：输入"蓝牙耳机A保修多久" → {"query":{"intent":"after_sales","entities":["蓝牙耳机A"],'
    '"category":"","budget_max":null,"budget_min":null,"concerns":["售后"]},'
    '"tasks":[{"id":1,"goal":"从售后知识库查询蓝牙耳机A的保修政策","task_type":"knowledge_qa","depends_on":[]}]}\n'
)


def make_plan(user_text: str, profile_block: str = "", llm=None,
              on_event=None) -> tuple[ShoppingQuery | None, Plan | None, int]:
    """返回 (query, plan, tokens)。out_of_scope 时 plan=None。解析两次失败抛 RuntimeError。"""
    from shoppilot.llm import get_llm

    llm = llm or get_llm()
    on_event = on_event or (lambda *_: None)
    user = f"{FEWSHOT}\n[用户画像]\n{profile_block or '（暂无）'}\n\n用户输入：{user_text}"
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
