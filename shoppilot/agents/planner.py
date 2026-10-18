"""
ShopPilot planner.py · 规划：意图分类 + 约束抽取 + 拆子任务（保留 Reflection 重写一次）
================================================================================
一次 LLM 调用同时产出：
  - ShoppingQuery：意图 / 实体 / 预算 / 在意维度（约束）
  - 子任务列表：拆成 2~4 个可执行任务（带依赖）
输出用 json_object 强制合法 JSON，再用 Pydantic 契约校验；校验不过把错误喂回去重写一次。
记忆注入：把短期/长期记忆（含"最近提到的商品"）传给模型，让"它/这个"能正确指代。
防编造：把商品池清单注入 prompt，池外型号直接判 out_of_scope。
"""
from __future__ import annotations

import json

from shoppilot.models import Plan, ShoppingQuery, SubTask

PLANNER_SYSTEM = """你是电商导购任务规划器。分析用户输入，输出 JSON：
{"query": {"intent": "recommend|compare|review_analysis|after_sales|research|out_of_scope",
           "entities": ["提到的商品全称"], "category": "品类（未知空串）",
           "budget_max": 数字或null, "budget_min": 数字或null, "concerns": ["在意维度"]},
 "tasks": [{"id": 1, "goal": "子任务描述（含具体商品名/预算/维度）", "task_type": "data_query|review_analysis|knowledge_qa|synthesis", "depends_on": []}]}

规则：
1. 意图判定从严：只有①与导购完全无关（写代码/聊天/问路），或②询问商品池中不存在的具体型号，才判 out_of_scope；拿不准一律按导购意图处理。
2. 场景：快速推荐=recommend、A和B怎么选=compare、口碑/翻车/吐槽=review_analysis、保修/退换/售后=after_sales、
   **宽泛研究=research**（用户说"研究一下/选购攻略/帮我调研/该不该买"或问题需要多维取证，如"3000元拍照手机怎么选"）。
3. research 类拆解（DeepResearch 模式：多维取证 + 汇总）：data_query 任务定候选（写清用 recommend_products 打分）→
   review_analysis 任务查候选口碑/差评 → knowledge_qa 任务查选购指南/政策 → synthesis 汇总成研究报告。
4. 推荐类拆解：先 data_query 任务，goal 写明"用 recommend_products 按预算 X、在意维度 Y 打分推荐"；若用户在意续航/噪音等具体维度，再加一个 review_analysis 任务查对应差评；最后 synthesis 出清单与理由。
5. 对比类：data_query 查两个商品详情/评分口碑 + synthesis 出维度对比。
6. 口碑类：review_analysis 任务（差评分布 / 搜评论原文）+ synthesis。
7. 售后类：knowledge_qa 任务（RAG 查政策），answer 必须带引用。
8. **多轮继承（重要）**：若记忆块里有[最近提到的商品]，用户说"顺带/顺便买个…""除了X还得配个…""充电线/电脑这种"时，
   新需求与上一轮商品是**配套/替代关系**，不是全新需求。此时：
   - entities 里同时列出上一轮商品 + 本轮新商品（如["蓝牙耳机A","数据线E"]）
   - category 用本轮新商品的品类（如"数码配件"配数据线、"电脑整机"配笔记本）
   - 预算继承上一轮（用户没提新预算时）
   - concerns 继承上一轮（如"安全"配充电宝时继续生效）
   - goal 里写明"用户上一轮买了X，本轮要配Y，按配套场景打分"
9. depends_on 只能引用 id 更小的任务；tasks 控制在 2~5 个；goal 写到"拿到就能干活"的粒度；商品必须全称。
10. 只输出 JSON，不要其他文字。"""


def make_plan(user_text: str, memory_block: str = "", llm=None,
              on_event=None) -> tuple[ShoppingQuery | None, Plan | None, int]:
    from shoppilot.domains.ecommerce.adapter import categories, product_names
    from shoppilot.llm import get_llm

    llm = llm or get_llm()
    on_event = on_event or (lambda *_: None)
    user = (f"[商品池（池外型号一律视为不存在，禁止编造其价格参数）]\n"
            f"{'、'.join(product_names())}\n\n[品类]\n{'、'.join(categories())}\n\n")
    if memory_block:
        user += f"[记忆]\n{memory_block}\n\n"
    user += f"用户输入：{user_text}"

    messages = [{"role": "system", "content": PLANNER_SYSTEM}, {"role": "user", "content": user}]
    last_err = ""
    tokens_total = 0
    for attempt in (1, 2):
        try:
            data, tk = llm.chat_json(messages, temperature=0.2)
            tokens_total += tk
            query = ShoppingQuery(**(data.get("query") or {}), raw_text=user_text)
            if query.intent == "out_of_scope":
                on_event("plan", {"intent": query.intent, "tasks": []})
                return query, None, tokens_total
            plan = Plan(query=query, tasks=[SubTask(**t) for t in data.get("tasks") or []])
            on_event("plan", {"intent": query.intent,
                              "tasks": [t.model_dump() for t in plan.tasks]})
            return query, plan, tokens_total
        except Exception as e:
            last_err = str(e)
            on_event("plan_retry", {"attempt": attempt, "error": last_err[:160]})
            messages.append({"role": "assistant", "content": json.dumps(data, ensure_ascii=False)
                             if "data" in dir() else "{}"})
            messages.append({"role": "user",
                             "content": f"你的输出有误（{last_err}），严格按规则重写 JSON。"})
    raise RuntimeError(f"Planner 两次都无法产出合法计划：{last_err}")


GUIDE_HINT_WORDS = ("推荐", "怎么选", "对比", "口碑", "差评", "翻车", "保修", "退换", "售后",
                    "值得买", "多少钱", "预算", "选哪个", "吐槽", "评价", "购买", "入手")


def refuse_looks_wrong(user_text: str) -> bool:
    """Planner 判了 out_of_scope，但文本明显是导购请求 → 疑似误拒，值得重问一次（零 token）。"""
    return any(w in user_text for w in GUIDE_HINT_WORDS)


def unknown_entities(query: ShoppingQuery) -> list[str]:
    """确定性库外实体闸：用户点名的型号不在商品池 → 防编造池外商品参数。"""
    from shoppilot.domains.ecommerce.adapter import product_names

    pool = set(product_names())
    return [e for e in query.entities if e and e not in pool
            and not any(e in p or p in e for p in pool)]
