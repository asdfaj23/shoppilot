"""
Week2 · 会话记忆：短期原文 + 超长自动摘要压缩（上下文工程最小实现）
================================================================================
为什么长对话会"变傻"（面试必问）：
  1. 上下文窗口有限，塞满了要么报错要么被截断；
  2. 即使塞得下，无关历史会稀释注意力，模型更容易跑偏（上下文污染）；
  3. Token 按输入计费，历史越长每轮越贵。

压缩策略（Sliding window + Running summary，DeerFlow/hello-agents 第八章同款思路）：
  - 最近 keep_recent 条消息保留原文（近期信息最重要）；
  - 更早的消息超出 max_old_chars 时，用 LLM 压成一段摘要，滚动合并进 running summary；
  - 每轮请求时拼装：[system] + [历史摘要] + [最近原文]。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import MODEL, get_client  # noqa: E402

COMPRESS_PROMPT = (
    "你是对话记录压缩器。把历史对话压缩成一段不超过200字的摘要，"
    "必须保留：用户的核心需求、已确认的关键事实/数字、尚未解决的问题。"
    "只输出摘要正文。已有摘要与新的历史都要融合。\n"
)


class SessionMemory:
    def __init__(self, keep_recent: int = 6, max_old_chars: int = 800):
        self.keep_recent = keep_recent      # 最近多少条保留原文
        self.max_old_chars = max_old_chars  # 旧消息累计超过这个字符数就压缩
        self.old: list[dict] = []           # 被挤出窗口的旧消息
        self.recent: list[dict] = []        # 窗口内的近期消息
        self.summary = ""                   # 滚动摘要（running summary）

    def add(self, role: str, content: str) -> None:
        """记一轮消息；窗口满了就把最老的挤出去。"""
        self.recent.append({"role": role, "content": content})
        while len(self.recent) > self.keep_recent:
            self.old.append(self.recent.pop(0))
        # 旧消息积压超过预算 → 立刻压缩（这就是"上下文预算"的执行点）
        if sum(len(m["content"]) for m in self.old) > self.max_old_chars:
            self.compress()

    def compress(self) -> None:
        """把 old 里的消息用 LLM 压成摘要，并入 running summary。"""
        if not self.old:
            return
        client = get_client()
        dialogue = "\n".join(f"{m['role']}: {m['content']}" for m in self.old)
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": COMPRESS_PROMPT},
                {"role": "user", "content": f"已有摘要：{self.summary or '（无）'}\n\n新历史：\n{dialogue}"},
            ],
            temperature=0.2,
        )
        self.summary = (resp.choices[0].message.content or "").strip()
        used = resp.usage.total_tokens
        self.old = []
        print(f"[Memory] 已压缩历史 → 摘要 {len(self.summary)} 字（压缩消耗 {used} tokens）")

    def build(self, system_prompt: str) -> list[dict]:
        """拼装本轮发给模型的 messages：system + 摘要 + 近期原文。"""
        messages = [{"role": "system", "content": system_prompt}]
        if self.summary:
            messages.append({"role": "system", "content": f"[历史对话摘要]\n{self.summary}"})
        messages.extend(self.recent)
        return messages


if __name__ == "__main__":
    # 自测：塞 20 条消息，观察自动压缩触发
    mem = SessionMemory(keep_recent=4, max_old_chars=300)
    for i in range(1, 21):
        mem.add("user" if i % 2 else "assistant", f"第{i}轮消息：我们讨论蓝牙耳机A的差评问题{i}")
    print(f"\n窗口内保留 {len(mem.recent)} 条原文，摘要：{mem.summary}")
