"""LLM provider abstraction.

* :class:`LLMProvider` is the structural protocol every agent/orchestrator depends on.
* :class:`OpenAICompatLLM` is the thin OpenAI-compatible implementation (DeepSeek,
  OpenAI, a local vLLM, ... — swap provider by passing a different instance).
* :class:`ScriptedLLM` is a deterministic, offline test double (inspired by
  PydanticAI's ``TestModel``): it replays scripted responses and records calls,
  so the whole agent + guardrail loop can be unit-tested without any network/token
  spend.

There is no hard-coded module singleton inside the orchestrator: agents receive an
``llm`` explicitly. :func:`get_llm` is only a convenience for CLIs / entry points,
and its default can be replaced with :func:`set_default_llm` (used by tests).
"""
from __future__ import annotations

import json
import threading
from collections.abc import Callable, Generator
from typing import Any, Protocol, runtime_checkable

from shoppilot.core.settings import Settings
from shoppilot.core.settings import settings as default_settings


@runtime_checkable
class LLMProvider(Protocol):
    """The three call modes every provider must implement."""

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             temperature: float | None = None) -> dict: ...

    def chat_json(self, messages: list[dict],
                  temperature: float | None = None) -> tuple[dict, int]: ...

    def chat_text(self, messages: list[dict],
                  temperature: float | None = None) -> tuple[str, int]: ...


class OpenAICompatLLM:
    """Thin wrapper around an OpenAI-compatible chat.completions endpoint."""

    def __init__(self, model: str | None = None, temperature: float | None = None,
                 cfg: Settings | None = None):
        self.cfg = cfg or default_settings
        if not self.cfg.deepseek_api_key:
            raise RuntimeError(
                "DEEPSEEK_API_KEY not found: create a .env at the repo root (see .env.example)."
            )
        from openai import OpenAI

        self.client = OpenAI(api_key=self.cfg.deepseek_api_key,
                             base_url=self.cfg.deepseek_base_url)
        self.model = model or self.cfg.deepseek_model
        self.temperature = self.cfg.llm_temperature if temperature is None else temperature
        self.total_tokens = 0

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
        return "".join(parts), (usage.total_tokens if usage else 0)

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             temperature: float | None = None) -> dict:
        resp = self._create(messages, tools, temperature=temperature)
        msg = resp.choices[0].message
        self.total_tokens += resp.usage.total_tokens
        return {"message": msg.model_dump(exclude_none=True), "tokens": resp.usage.total_tokens}

    def chat_json(self, messages: list[dict], temperature: float | None = None) -> tuple[dict, int]:
        resp = self._create(messages, json_mode=True, temperature=temperature)
        text = resp.choices[0].message.content or ""
        self.total_tokens += resp.usage.total_tokens
        return json.loads(text), resp.usage.total_tokens

    def chat_text(self, messages: list[dict], temperature: float | None = None) -> tuple[str, int]:
        resp = self._create(messages, temperature=temperature)
        self.total_tokens += resp.usage.total_tokens
        return resp.choices[0].message.content or "", resp.usage.total_tokens

    def pseudo_stream(self, messages: list[dict],
                      temperature: float | None = None) -> tuple[Generator[str, None, None], Callable]:
        """Return a chunked generator over the *already-fetched* completion.

        Note: this is not token-level streaming — the endpoint is called once for the
        full text, then re-chunked for the UI. Named ``pseudo_stream`` to be honest.
        """
        holder: dict[str, Any] = {}

        def gen() -> Generator[str, None, None]:
            text, tokens = self._create(messages, temperature=temperature, stream=True)  # type: ignore[misc]
            holder["text"], holder["tokens"] = text, tokens
            yield from (text[i:i + 24] for i in range(0, len(text), 24))

        return gen(), lambda: (holder.get("text", ""), holder.get("tokens", 0))


class ScriptedLLM:
    """Deterministic offline test double. No network; records every call and tokens.

    Provide either:
      * ``responses`` — a queue of payloads (dict for ``chat_json``, str for ``chat_text``);
      * ``router`` — a callable ``(messages) -> payload`` that branches on the prompt
        (e.g. detect planner vs composer vs synthesizer).
    """

    def __init__(self, responses: list | None = None, router: Callable[[list[dict]], Any] | None = None,
                 token_per_call: int = 120):
        self._queue = list(responses or [])
        self._router = router
        self.token_per_call = token_per_call
        self.calls: list[list[dict]] = []
        self.total_tokens = 0
        self.roles: list[str] = []

    def _next(self, messages: list[dict]) -> Any:
        self.calls.append(messages)
        system = messages[0].get("content", "") if messages else ""
        self.roles.append(system[:24])
        if self._router is not None:
            return self._router(messages)
        if self._queue:
            idx = min(len(self.calls) - 1, len(self._queue) - 1)
            return self._queue[idx]
        return {}

    def chat_json(self, messages: list[dict], temperature: float | None = None) -> tuple[dict, int]:
        payload = self._next(messages)
        self.total_tokens += self.token_per_call
        if isinstance(payload, dict):
            return payload, self.token_per_call
        return json.loads(payload), self.token_per_call

    def chat_text(self, messages: list[dict], temperature: float | None = None) -> tuple[str, int]:
        payload = self._next(messages)
        self.total_tokens += self.token_per_call
        return (payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)), self.token_per_call

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             temperature: float | None = None) -> dict:
        payload = self._next(messages)
        self.total_tokens += self.token_per_call
        if isinstance(payload, dict):
            # An explicit assistant message (may carry tool_calls), else a JSON body.
            if "tool_calls" in payload or "content" in payload:
                message = {"role": "assistant", **payload}
            else:
                message = {"role": "assistant",
                           "content": json.dumps(payload, ensure_ascii=False)}
        else:
            message = {"role": "assistant", "content": str(payload)}
        return {"message": message, "tokens": self.token_per_call}


# ---------------------------------------------------------------------------
# Process-wide default for CLIs / entry points only (orchestrator takes llm explicitly).
# ---------------------------------------------------------------------------
_LOCK = threading.Lock()
_DEFAULT: LLMProvider | None = None


def set_default_llm(llm: LLMProvider | None) -> None:
    """Replace the default provider (tests use this to inject ``ScriptedLLM``)."""
    global _DEFAULT
    with _LOCK:
        _DEFAULT = llm


def build_default_llm(cfg: Settings | None = None) -> LLMProvider:
    return OpenAICompatLLM(cfg=cfg)


def get_llm() -> LLMProvider:
    """Lazily build the default OpenAI-compatible provider (singleton for CLI/UI)."""
    global _DEFAULT
    with _LOCK:
        if _DEFAULT is None:
            _DEFAULT = OpenAICompatLLM()
        return _DEFAULT
