"""
Week4 · 可观测层（Poor-man's Langfuse）：结构化 Trace + 成本统计
================================================================================
真实指标不是"页面上有日志"，而是三个能回答的问题：
  1. 这次任务每一步的输入/输出/耗时/Token 是多少？（回溯 badcase）
  2. 钱（Token）花在哪个环节？（成本归因 → 优化点排序）
  3. 失败发生在哪一层：拆解错 / 调错工具 / 检索偏 / 编造？（失败归因）

实现：每步 append 一条 JSON 到 traces/*.jsonl；结束后打印汇总。
接 Langfuse 时只需把这层换成 langfuse SDK 的 span 上报，接口不变。
"""
import json
import time
from datetime import datetime
from pathlib import Path

TRACE_DIR = Path(__file__).resolve().parent / "traces"


class Tracer:
    def __init__(self, run_name: str):
        TRACE_DIR.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%H%M%S")
        self.path = TRACE_DIR / f"{run_name}_{stamp}.jsonl"
        self.t0 = time.time()
        self.events: list[dict] = []

    def log(self, step: str, name: str, input_: str, output: str,
            tokens: int = 0, duration_s: float | None = None) -> None:
        ev = {
            "ts": round(time.time() - self.t0, 2),        # 距任务开始的秒数
            "step": step,                                  # plan/dispatch/agent_*/aggregate
            "name": name,                                  # 子任务或工具名
            "input": str(input_)[:500],                    # 截断，防止 trace 本身爆炸
            "output": str(output)[:500],
            "tokens": tokens,
            "duration_s": duration_s if duration_s is not None else 0.0,
        }
        self.events.append(ev)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")

    def summary(self) -> dict:
        """成本归因：每步 tokens 占比。这是你简历上"Token 降 XX%"的数据来源。"""
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
