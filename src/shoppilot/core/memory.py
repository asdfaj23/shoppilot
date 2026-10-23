"""
ShopPilot 记忆：短期 + 长期（对齐 hello-agents 的两段式记忆，不做画像推断）
================================================================================
短期记忆（ShortTerm）：当前会话内
  - 最近 N 轮原文（滑窗）
  - 更早的历史由 LLM 压成滚动摘要
  - 作用：让多轮追问不断片
长期记忆（LongTerm）：跨会话，文件态 data/memory/{user}.md（人类可读、可删）
  - 每次对话追加一条：问 / 答摘要 / 涉及商品 / 结论要点
  - 注入时按关键词命中检索（jieba 分词 + 命中计数），只放最相关的几条
  - 作用：换会话后还记得上次聊过什么；天然解决"它/这个"指代（最近涉及商品就在里面）
不做「用户画像」推断：用户每次输入都可能不同，画像面板既不准确也没必要；
  需要个性化时把在意维度写进问题里即可（ShoppingQuery 已经在抽）。

可插拔：LongTermMemory 的读写是两个函数，换成向量库/Redis 只改这两处。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from shoppilot.core.settings import SESSIONS_DIR

MEM_DIR = Path(SESSIONS_DIR).parent / "memory"


class ShortTermMemory:
    """会话内短期记忆：滑窗 + 滚动摘要。"""

    def __init__(self, keep_recent: int = 6, max_old_chars: int = 800,
                 summary: str = "", old: list | None = None, recent: list | None = None):
        self.keep_recent = keep_recent
        self.max_old_chars = max_old_chars
        self.old = old or []
        self.recent = recent or []
        self.summary = summary

    def add(self, role: str, content: str) -> None:
        self.recent.append({"role": role, "content": content})
        while len(self.recent) > self.keep_recent:
            self.old.append(self.recent.pop(0))
        if sum(len(m["content"]) for m in self.old) > self.max_old_chars:
            self.compress()

    def compress(self, llm=None) -> None:
        """把旧消息压成滚动摘要；无 LLM 时直接丢弃旧消息（不阻塞主流程）。"""
        if not self.old:
            return
        if llm is None:
            self.old = []
            return
        dialogue = "\n".join(f"{m['role']}: {m['content']}" for m in self.old)
        try:
            summary, _ = llm.chat_text(
                [{"role": "system", "content":
                  "把对话历史压缩成不超过 150 字的摘要，保留：用户核心需求、涉及的关键商品、"
                  "已确认的事实与未解决的问题。只输出摘要正文。"},
                 {"role": "user",
                  "content": f"已有摘要：{self.summary or '（无）'}\n\n新历史：\n{dialogue}"}],
                temperature=0.2)
            self.summary = summary.strip()
        except Exception:
            pass
        self.old = []

    def build(self, system_prompt: str, memory_block: str = "") -> list:
        """拼装本轮 messages：system -> 记忆块 -> 早期摘要 -> 最近原文。"""
        messages = [{"role": "system", "content": system_prompt}]
        if memory_block:
            messages.append({"role": "system", "content": memory_block})
        if self.summary:
            messages.append({"role": "system", "content": f"[本会话早期摘要]\n{self.summary}"})
        messages.extend(self.recent)
        return messages

    def to_dict(self) -> dict:
        return {"summary": self.summary, "old": self.old, "recent": self.recent,
                "keep_recent": self.keep_recent, "max_old_chars": self.max_old_chars}


class LongTermMemory:
    """跨会话长期记忆：Markdown 追加式（人类可读、可编辑、可删除）。"""

    def __init__(self, user_id: str = "default"):
        self.user_id = user_id
        MEM_DIR.mkdir(parents=True, exist_ok=True)
        self.path = MEM_DIR / f"{user_id}.md"

    def append(self, question: str, answer_digest: str, items: list, findings: str) -> None:
        line = (f"\n## {datetime.now():%m-%d %H:%M}\n"
                f"- 问：{question[:80]}\n"
                f"- 答：{answer_digest[:160]}\n"
                f"- 涉及：{'、'.join([i for i in items[:5] if i]) or '—'}\n"
                f"- 结论：{findings[:120]}\n")
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line)

    def entries(self) -> list:
        if not self.path.exists():
            return []
        blocks = re.split(r"\n## ", self.path.read_text(encoding="utf-8"))
        out = []
        for b in blocks[1:]:
            rec = {"time": b.splitlines()[0].strip()}
            for line in b.splitlines()[1:]:
                m = re.match(r"-\s*(问|答|涉及|结论)：(.*)", line.strip())
                if m:
                    rec[m.group(1)] = m.group(2)
            out.append(rec)
        return out

    def recall(self, query: str, top_k: int = 4) -> list:
        """按关键词命中检索最相关的几条（jieba 分词 + 命中计数）。"""
        import jieba

        items = self.entries()
        if not items:
            return []
        qwords = {w for w in jieba.cut(query) if len(w.strip()) > 1}
        if not qwords:
            return items[-top_k:]

        def score(it: dict) -> int:
            blob = " ".join(str(v) for v in it.values())
            return sum(1 for w in qwords if w in blob)

        ranked = sorted(enumerate(items), key=lambda x: (-score(x[1]), -x[0]))
        hits = [it for _, it in ranked[:top_k] if score(it) > 0]
        return hits or items[-top_k:]

    def recent_items(self, k: int = 3) -> list:
        """最近几次对话提到的商品（供"它/这个"指代消解）。"""
        names: list = []
        for it in self.entries()[-k:]:
            for token in re.findall(r"[一-龥A-Za-z0-9]+", it.get("涉及", "")):
                if len(token) >= 2 and token not in names:
                    names.append(token)
        return names

    def render_block(self, query: str, top_k: int = 4) -> str:
        """注入给 Planner 的记忆块。"""
        items = self.recall(query, top_k)
        recent = self.recent_items()
        if not items:
            return ("[长期记忆]（无历史记录，这是新用户）"
                    + (f"\n[最近提到的商品] {'、'.join(recent)}" if recent else ""))
        lines = [f"[{it.get('time', '')}] 问：{it.get('问', '')} → 结论：{it.get('结论', '')}"
                 for it in items]
        tail = (f"\n[最近提到的商品] {'、'.join(recent)}（用户说'它/这个'时优先指这些）"
                if recent else "")
        return "[长期记忆·相关历史]\n" + "\n".join(lines) + tail

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)

    def raw(self) -> str:
        return self.path.read_text(encoding="utf-8") if self.path.exists() else ""


class MemoryManager:
    """短期 + 长期记忆的统一门面；会话本身也持久化（多会话切换用）。"""

    def __init__(self, session_id: str = "default", user_id: str = "default",
                 keep_recent: int = 6):
        self.session_id = session_id
        self.path = SESSIONS_DIR / f"{session_id}.json"
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        data = self._load()
        self.title = data.get("title", session_id)
        self.token_total = data.get("token_total", 0)
        short = data.get("short", {})
        short.setdefault("keep_recent", keep_recent)
        self.short = ShortTermMemory(**short)
        self.long = LongTermMemory(user_id)

    def _load(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def save(self) -> None:
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({
            "session_id": self.session_id, "title": self.title,
            "short": self.short.to_dict(), "token_total": self.token_total,
        }, ensure_ascii=False, indent=1), encoding="utf-8")

    def memory_block(self, query: str) -> str:
        """注入块 = 长期记忆（按当前问题检索）+ 本会话早期摘要。"""
        block = self.long.render_block(query)
        if self.short.summary:
            block += f"\n[本会话早期摘要] {self.short.summary}"
        return block

    def add_turn(self, user_text: str, answer: str, items: list, findings: str,
                 tokens: int = 0, llm=None) -> None:
        self.short.add("user", user_text)
        self.short.add("assistant", answer)
        self.token_total += tokens
        if self.title == self.session_id:
            self.title = user_text[:24]
        self.long.append(user_text, answer, items, findings)
        self.save()
        if self.short.old:
            self.short.compress(llm)
            self.save()

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)


def list_sessions() -> list:
    """侧边栏会话列表（过滤非会话文件）。"""
    out = []
    if SESSIONS_DIR.exists():
        for f in SESSIONS_DIR.glob("*.json"):
            if f.name.startswith(("profile_", "bench_", "benchfull_", "vref", "smoke", "newarch",
                                  "e2e", "tm_")):
                continue
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                out.append({"session_id": d.get("session_id", f.stem),
                            "title": d.get("title", f.stem), "mtime": f.stat().st_mtime})
            except Exception:
                continue
    return sorted(out, key=lambda x: -x["mtime"])
