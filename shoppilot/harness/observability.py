"""
ShopPilot ⑥ 可观测层（Poor-man's Langfuse，吸收 week4/trace.py）
================================================================================
真实可观测要能回答三个问题：每步的输入/输出/耗时/Token 是多少（回溯 badcase）、
钱花在哪个环节（成本归因 → 优化点排序）、失败发生在哪一层（拆解错/调错工具/检索偏/编造）。
实现：每步 append 一条 JSON 到 traces/*.jsonl + 事件总线回调（驱动 Streamlit st.status 实时更新）。
接 Langfuse 时只需把这层换成 SDK 上报，接口不变。
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path

from config import TRACES_DIR


class Tracer:
    """JSONL Trace + 分步 token 归因 + 事件回调。线程安全（Dispatcher 会并发调度）。"""

    def __init__(self, run_name: str = "shoppilot", on_event=None):
        TRACES_DIR.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = TRACES_DIR / f"{run_name}_{stamp}.jsonl"
        self.t0 = time.time()
        self.events: list[dict] = []
        self._lock = threading.Lock()
        self._on_event = on_event

    def emit(self, etype: str, payload: dict) -> None:
        """事件回调（供 st.status 实时渲染），与 trace 记录解耦。"""
        if self._on_event:
            try:
                self._on_event(etype, payload)
            except Exception:
                pass  # 前端渲染失败绝不影响主流程

    def log(self, step: str, name: str, input_: str, output: str,
            tokens: int = 0, duration_s: float | None = None) -> None:
        ev = {
            "ts": round(time.time() - self.t0, 2),
            "step": step,                        # plan/dispatch/agent_*/aggregate/reflect
            "name": name,
            "input": str(input_)[:500],
            "output": str(output)[:500],
            "tokens": tokens,
            "duration_s": round(duration_s, 2) if duration_s is not None else 0.0,
        }
        with self._lock:
            self.events.append(ev)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")

    def summary(self) -> dict:
        """成本归因：每步 tokens 占比（简历上"Token 降 XX%"的数据来源）。"""
        total = sum(e["tokens"] for e in self.events)
        by_step: dict[str, int] = {}
        for e in self.events:
            by_step[e["step"]] = by_step.get(e["step"], 0) + e["tokens"]
        return {
            "total_tokens": total,
            "total_seconds": round(time.time() - self.t0, 1),
            "by_step": {k: f"{v} ({v * 100 // max(total, 1)}%)" for k, v in by_step.items()},
            "trace_file": str(self.path),
        }
