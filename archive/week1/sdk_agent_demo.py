"""
Week1 Day5 · OpenAI Agents SDK 5 行跑通一个 Agent（模型换成 DeepSeek）
====================================================================
对比体会（这是本周最重要的认知）：
  - 手写 react_loop.py  ≈ 60 行，你能看清每一次模型调用、工具分发、历史累积；
  - Agents SDK          ≈ 5 行，上面这些它全包了（循环 / 工具分发 / 重试 / tracing）。
框架不神秘，它就是把 Loop 包起来 + 加工程件。先手写再上框架，才不会被框架绑架。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents import Agent, FunctionTool, OpenAIChatCompletionsModel, Runner, function_tool  # noqa: E402
from openai import AsyncOpenAI  # noqa: E402

from config import API_KEY, BASE_URL, MODEL  # noqa: E402
from week1.react_tools import query_product_reviews  # noqa: E402

# SDK 默认连 OpenAI；DeepSeek 兼容 OpenAI 格式，把客户端指向自家 base_url 即可
client = AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL)
model = OpenAIChatCompletionsModel(model=MODEL, openai_client=client)


# 给 SDK 挂工具只需：函数签名 + docstring（自动转 schema）+ 塞进 tools 列表
@function_tool
def get_product_reviews(product: str) -> str:
    """查询某商品评论统计：差评数、差评率、差评维度分布、典型评论原文。分析口碑时必须先调用。"""
    return query_product_reviews(product)


agent = Agent(
    name="电商导购助手",
    instructions="你是电商导购与评论分析助手，回答前先用工具查真实数据，结论优先。",
    model=model,
    tools=[get_product_reviews],
)

if __name__ == "__main__":
    result = Runner.run_sync(agent, "蓝牙耳机A 的差评主要集中在哪些方面？")
    print(result.final_output)
