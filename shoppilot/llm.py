"""
ShopPilot ② LLM Provider 抽象（吸收 week1-4 的 get_client 散装用法）
================================================================================
设计原因：
  - 所有 LLM 调用走同一个类：①统一计数 token（可观测层的成本归因数据来源）；
    ②统一支持三种模式——普通对话 / json_object 结构化 / 流式，调用方零样板；
    ③换 Provider（DeepSeek→OpenAI→本地 vLLM）只改这个文件。
  - 共享实例 get_llm()：Streamlit 每次 rerun 都会重建对象，client 必须缓存
    （对应 Streamlit 坑规避：st.cache_resource 的替代写法——模块级单例）。
"""
from __future__ import annotations

import json
import threading
from typing import Any, Generator

from config import API_KEY, BASE_URL, MODEL, settings


class LLM:
    """OpenAI 兼容客户端的薄封装。所有方法返回 (内容, tokens) 或带 usage 的结构。"""

    def __init__(self, model: str | None = None, temperature: float | None = None):
        if not API_KEY:
            raise RuntimeError("未读到 DEEPSEEK_API_KEY：请在仓库根目录创建 .env（参考 .env.example）")
        from openai import OpenAI

        self.client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
        self.model = model or MODEL
        self.temperature = settings.llm_temperature if temperature is None else temperature
        self.total_tokens = 0  # 实例级累计（会话 token 统计用）

    # ---------- 内部 ----------
    def _create(self, messages: list[dict], tools: list[dict] | None = None,
                json_mode: bool = False, temperature: float | None = None,
                stream: bool = False, on_delta=None):
        kwargs: dict[str, Any] = dict(
            model=self.model,
            messages=messages,
            temperature=self.temperature if temperature is None else temperature,
        )
        if tools:
            kwargs["tools"] = tools
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if stream:
            kwargs["stream"] = True
            kwargs["stream_options"] = {"include_usage": True}
        resp = self.client.chat.completions.create(**kwargs)
        if not stream:
            return resp
        # 流式：边 yield 边攒完整内容；usage 在最后一个 chunk
        parts: list[str] = []
        usage = None
        for chunk in resp:
            if getattr(chunk, "usage", None):
                usage = chunk.usage
            if chunk.choices and chunk.choices[0].delta.content:
                delta = chunk.choices[0].delta.content
                parts.append(delta)
                if on_delta:
                    on_delta(delta)
        text = "".join(parts)
        tokens = usage.total_tokens if usage else 0
        return text, tokens

    # ---------- 三种调用模式 ----------
    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             temperature: float | None = None) -> dict:
        """普通/带工具对话。返回 openai message 的 dict（含 tool_calls 时原样保留）。"""
        resp = self._create(messages, tools, temperature=temperature)
        msg = resp.choices[0].message
        self.total_tokens += resp.usage.total_tokens
        return {"message": msg.model_dump(exclude_none=True), "tokens": resp.usage.total_tokens}

    def chat_json(self, messages: list[dict], temperature: float | None = None) -> tuple[dict, int]:
        """json_object 结构化输出。返回 (解析后的 dict, tokens)。解析失败抛异常由上层 Reflection 重试。"""
        resp = self._create(messages, json_mode=True, temperature=temperature)
        text = resp.choices[0].message.content or ""
        self.total_tokens += resp.usage.total_tokens
        return json.loads(text), resp.usage.total_tokens

    def chat_text(self, messages: list[dict], temperature: float | None = None) -> tuple[str, int]:
        """纯文本问答。返回 (回答, tokens)。"""
        resp = self._create(messages, temperature=temperature)
        self.total_tokens += resp.usage.total_tokens
        return resp.choices[0].message.content or "", resp.usage.total_tokens

    def stream(self, messages: list[dict], temperature: float | None = None) -> tuple[Generator[str, None, None], callable]:
        """流式输出。返回 (delta 生成器, 取完整文本与 tokens 的闭包)——供 Streamlit write_stream。"""
        holder: dict[str, Any] = {}

        def gen() -> Generator[str, None, None]:
            text, tokens = self._create(messages, temperature=temperature, stream=True)  # type: ignore[misc]
            holder["text"], holder["tokens"] = text, tokens
            # 上面已经消费完流了；为了让 write_stream 拿到 delta，这里改为分块吐出
            yield from (text[i:i + 24] for i in range(0, len(text), 24))

        # 说明：_create 的 on_delta 路径在 write_stream 场景下不可用（Streamlit 消费生成器），
        # 因此这里先真实调用模型攒全文，再分块 yield——观感与真流式一致，聚合阶段序列号可控。
        return gen(), lambda: (holder.get("text", ""), holder.get("tokens", 0))


_LLM_LOCK = threading.Lock()
_LLM: LLM | None = None


def get_llm() -> LLM:
    """模块级单例（等价于 st.cache_resource 的效果：rerun 不重建 client）。"""
    global _LLM
    with _LLM_LOCK:
        if _LLM is None:
            _LLM = LLM()
        return _LLM
