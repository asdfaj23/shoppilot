"""
Week2 · Planner：把一个模糊目标拆成"子任务 DAG"（结构化 JSON 输出）
================================================================================
两种做法（面试必问）：
  A. 一次拆完（Plan-and-Solve，本文件做法）：一次 LLM 调用输出完整计划。
     优点：省 token、计划可先人工审阅再执行、无依赖的子任务将来可并行。
     缺点：执行中发现信息不足时不会自我修正。
  B. 边做边拆（ReAct / 动态规划）：每做完一步再决定下一步。
     优点：灵活；缺点：token 贵、行为不可预测、难评测。
  生产实践：先用 A 出骨架，执行阶段每个子任务内部仍可以用 B（这正是 Week4 多 Agent 的结构）。

结构化输出原理：response_format=json_object 强制模型输出合法 JSON
（采样时 logits 被约束到合法 JSON 字符的词表内），再由我们做字段校验兜底。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import MODEL, get_client  # noqa: E402

client = get_client()

PLANNER_SYSTEM = """你是电商运营任务规划器。把用户目标拆成 2~5 个子任务，输出 JSON：
{"tasks": [{"id": 1, "goal": "子任务描述（含具体对象）", "task_type": "data_query|knowledge_qa|synthesis", "depends_on": []}]}

规则：
1. depends_on 只能引用 id 更小的任务（保证按 id 顺序执行即为合法拓扑序）；
2. 先查数据/查资料，后综合分析；最后一个任务通常是 synthesis（汇总出结论）；
3. 子任务之间不重复；goal 要写到"模型拿到它就能直接干活"的粒度；
4. 只输出 JSON，不要输出其他文字。"""

FEWSHOT = (
    '示例：目标"分析蓝牙耳机A的差评痛点并给改进建议" →\n'
    '{"tasks": ['
    '{"id": 1, "goal": "查询蓝牙耳机A的评论统计与差评维度分布", "task_type": "data_query", "depends_on": []}, '
    '{"id": 2, "goal": "查询数码配件品类销量排行，确认蓝牙耳机A的位置", "task_type": "data_query", "depends_on": []}, '
    '{"id": 3, "goal": "结合差评分布与销量数据，归纳差评痛点并给出改进建议", '
    '"task_type": "synthesis", "depends_on": [1, 2]}]}'
)


def plan(goal: str) -> list[dict]:
    """返回子任务列表（按 id 升序即拓扑序）。解析/校验失败 → Reflection 让模型重写一次。"""
    messages = [
        {"role": "system", "content": PLANNER_SYSTEM},
        {"role": "user", "content": f"{FEWSHOT}\n\n目标：{goal}"},
    ]
    last_err = ""
    for attempt in (1, 2):  # 最多重试 1 次（Reflection：把错误喂回去让它自己改）
        resp = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            response_format={"type": "json_object"},  # 结构化输出：强制合法 JSON
            temperature=0.2,
        )
        text = resp.choices[0].message.content or ""
        try:
            tasks = json.loads(text)["tasks"]
            _validate(tasks)
            return tasks
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            last_err = str(e)
            print(f"[Planner] 第{attempt}次解析/校验失败：{e}，让模型重写…")
            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user", "content": f"你的输出有误（{last_err}），严格按规则重写 JSON。"})
    raise RuntimeError(f"Planner 两次都无法产出合法计划：{last_err}")


def _validate(tasks: list) -> None:
    """校验：字段齐全、id 唯一、依赖只指向更小的 id（无环）。"""
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("tasks 必须是非空数组")
    ids = [t.get("id") for t in tasks]
    if len(set(ids)) != len(ids):
        raise ValueError("任务 id 重复")
    for t in tasks:
        if not t.get("goal") or t.get("task_type") not in ("data_query", "knowledge_qa", "synthesis"):
            raise ValueError(f"任务字段非法：{t}")
        for dep in t.get("depends_on", []):
            if dep >= t["id"]:
                raise ValueError(f"依赖必须指向更小的 id：任务{t['id']} 依赖 {dep}")


def show_plan(tasks: list[dict]) -> None:
    """把计划打印成可读形态（= DeerFlow 里"人工确认计划"那一步的极简版）。"""
    print("\n===== Planner 产出（DAG） =====")
    for t in tasks:
        deps = f" ← 依赖任务{t['depends_on']}" if t["depends_on"] else ""
        print(f"  [{t['id']}] ({t['task_type']}) {t['goal']}{deps}")
    print("=" * 30)


if __name__ == "__main__":
    goal = sys.argv[1] if len(sys.argv) > 1 else "分析蓝牙耳机A的差评痛点并给出改进建议"
    show_plan(plan(goal))
