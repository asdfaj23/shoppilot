"""
ShopPilot ⑤ 工具注册中心 ToolRegistry（吸收 week2/tools.py 手写 schema 的痛点）
================================================================================
设计原因：
  - week2 里 OpenAI schema 与函数签名各写一遍，改参数要改两处——用 Pydantic
    args_schema 生成 schema 后单一事实源：函数 + args 模型即全部。
  - task_type 预加载：Dispatcher 只把当前子任务需要的工具 schema 注入上下文，
    无关工具不进 prompt（benchmark --compare 的对照实验就量化这件事）。
  - 结果缓存：相同参数的重复调用直接命中（DAG 多个任务常查同一商品）。
  - 危险工具确认闸：dangerous=True 的工具执行前必须过 confirm_fn，
    无头模式（benchmark/CLI）默认拒绝，Streamlit 前端可以换成 st.confirm。
"""
from __future__ import annotations

import json
import threading
from typing import Callable

from pydantic import BaseModel


def _schema_to_openai(name: str, description: str, schema: dict) -> dict:
    """Pydantic model_json_schema → OpenAI function schema（去掉多余键）。"""
    props = schema.get("properties", {})
    for p in props.values():  # pydantic 的 anyOf(类型|null) 收敛成单类型
        if "anyOf" in p:
            types = [t.get("type") for t in p["anyOf"] if t.get("type") and t["type"] != "null"]
            if types:
                p["type"] = types[0]
            p.pop("anyOf", None)
        for k in ("title", "default"):
            p.pop(k, None)
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": props,
                           "required": schema.get("required", [])},
        },
    }


class Tool:
    def __init__(self, name: str, func: Callable[..., str], description: str,
                 args_model: type[BaseModel], task_types: list[str],
                 dangerous: bool = False):
        self.name = name
        self.func = func
        self.description = description
        self.args_model = args_model
        self.task_types = task_types
        self.dangerous = dangerous

    def to_openai_schema(self) -> dict:
        return _schema_to_openai(self.name, self.description, self.args_model.model_json_schema())


class ToolRegistry:
    def __init__(self, cache_size: int = 256):
        self._tools: dict[str, Tool] = {}
        self._cache: dict[str, str] = {}
        self._cache_order: list[str] = []
        self._cache_size = cache_size
        self.confirm_fn: Callable[[str, dict], bool] = lambda name, args: False
        self._lock = threading.Lock()

    # ---------- 注册 ----------
    def register(self, name: str, func: Callable[..., str], description: str,
                 args_model: type[BaseModel], task_types: list[str],
                 dangerous: bool = False) -> Callable[..., str]:
        """直接注册一个实现函数：name + func + Pydantic args 模型即完整契约。"""
        self._tools[name] = Tool(name, func, description, args_model, task_types, dangerous)
        return func

    # ---------- 查询 ----------
    def get(self, name: str) -> Tool:
        return self._tools[name]

    def names(self) -> list[str]:
        return list(self._tools)

    def schemas(self, names: list[str] | None = None) -> list[dict]:
        """全部或指定工具的 OpenAI schema。"""
        chosen = names if names is not None else list(self._tools)
        return [self._tools[n].to_openai_schema() for n in chosen if n in self._tools]

    def preload(self, task_type: str) -> list[dict]:
        """按任务类型预加载工具 schema——无关工具绝不进上下文（省 token）。"""
        return [t.to_openai_schema() for t in self._tools.values() if task_type in t.task_types]

    def preload_names(self, task_type: str) -> list[str]:
        return [t.name for t in self._tools.values() if task_type in t.task_types]

    # ---------- 执行 ----------
    def call(self, name: str, arguments: str | dict) -> str:
        """校验参数（Pydantic 契约）→ 缓存 → 确认闸 → 执行。始终返回字符串。"""
        tool = self._tools.get(name)
        if tool is None:
            return json.dumps({"error": f"未知工具：{name}"}, ensure_ascii=False)
        try:
            raw = json.loads(arguments) if isinstance(arguments, str) else (arguments or {})
            args = tool.args_model(**raw).model_dump()
        except Exception as e:  # 参数不合法：把错误喂回模型让它自己纠正
            return json.dumps({"error": f"参数校验失败：{e}",
                               "expected": tool.args_model.model_json_schema()["properties"]},
                              ensure_ascii=False)

        key = name + "|" + json.dumps(args, ensure_ascii=False, sort_keys=True)
        with self._lock:
            if key in self._cache:
                return self._cache[key]

        if tool.dangerous and not self.confirm_fn(name, args):
            return json.dumps({"error": "该工具为危险操作，用户未确认执行", "confirmed": False},
                              ensure_ascii=False)
        try:
            result = tool.func(**args)
        except Exception as e:
            result = json.dumps({"error": f"工具执行异常：{type(e).__name__}: {e}"}, ensure_ascii=False)
        result = str(result)

        with self._lock:  # 简单 FIFO 淘汰，防止缓存无限膨胀
            self._cache[key] = result
            self._cache_order.append(key)
            while len(self._cache_order) > self._cache_size:
                self._cache.pop(self._cache_order.pop(0), None)
        return result

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()
            self._cache_order.clear()
