"""
ShopPilot ⑥ 记忆管理器（吸收 week2/memory.py 单一会话记忆的升级版）
================================================================================
三层记忆设计（可插拔，MemoryManager 是唯一入口）：
  1. WorkingMemory 会话滑窗 + 滚动摘要：最近 keep_recent 条保原文，更早的用 LLM 压成
     running summary——长对话不变傻、上下文不污染、token 不白烧（week2 已验证的思路）。
  2. PreferenceMemory 用户画像文件态记忆：跨会话持久到 data/sessions/profile_{user}.json，
     由 ShoppingQuery 确定性写入（预算档/在意维度/品类/历史），前端可查看可编辑——
     "可解释记忆"是本项目与 DeerFlow/hello-agents 复现品的差异点。
  3. 会话持久化：多会话各存一个 JSON（消息 + 摘要），支撑网页版前端的多会话切换。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from config import SESSIONS_DIR
from shoppilot.models import PreferenceProfile, ShoppingQuery

COMPRESS_PROMPT = (
    "你是对话记录压缩器。把历史对话压缩成一段不超过200字的摘要，"
    "必须保留：用户的核心需求、已确认的关键事实/数字、尚未解决的问题。"
    "只输出摘要正文。已有摘要与新的历史都要融合。"
)


class WorkingMemory:
    """滑窗 + 滚动摘要。compress 需要注入 llm（有 key 才压缩，无 key 降级为丢弃旧消息）。"""

    def __init__(self, keep_recent: int = 6, max_old_chars: int = 800,
                 summary: str = "", old: list[dict] | None = None,
                 recent: list[dict] | None = None):
        self.keep_recent = keep_recent
        self.max_old_chars = max_old_chars
        self.old: list[dict] = old or []
        self.recent: list[dict] = recent or []
        self.summary = summary

    def add(self, role: str, content: str) -> None:
        self.recent.append({"role": role, "content": content})
        while len(self.recent) > self.keep_recent:
            self.old.append(self.recent.pop(0))
        if sum(len(m["content"]) for m in self.old) > self.max_old_chars:
            self.compress()

    def compress(self, llm=None) -> None:
        if not self.old:
            return
        if llm is None:  # 无 LLM（如纯工具自测）就只丢旧消息，不阻塞主流程
            self.old = []
            return
        dialogue = "\n".join(f"{m['role']}: {m['content']}" for m in self.old)
        try:
            summary, _ = llm.chat_text(
                [{"role": "system", "content": COMPRESS_PROMPT},
                 {"role": "user", "content": f"已有摘要：{self.summary or '（无）'}\n\n新历史：\n{dialogue}"}],
                temperature=0.2)
            self.summary = summary.strip()
        except Exception:
            pass  # 摘要失败不影响对话，只是旧消息未被压缩
        self.old = []

    def build(self, system_prompt: str, profile_block: str = "") -> list[dict]:
        """拼装本轮 messages：system + 画像块 + 历史摘要 + 最近原文。"""
        messages = [{"role": "system", "content": system_prompt}]
        if profile_block:
            messages.append({"role": "system", "content": f"[用户画像]\n{profile_block}"})
        if self.summary:
            messages.append({"role": "system", "content": f"[历史对话摘要]\n{self.summary}"})
        messages.extend(self.recent)
        return messages

    def to_dict(self) -> dict:
        return {"summary": self.summary, "old": self.old, "recent": self.recent,
                "keep_recent": self.keep_recent, "max_old_chars": self.max_old_chars}


class PreferenceMemory:
    """用户画像：JSON 文件态，可插拔（换成 Redis/DB 只改 load/save 两行）。"""

    def __init__(self, user_id: str = "default"):
        self.user_id = user_id
        self.path = SESSIONS_DIR / f"profile_{user_id}.json"
        self.profile = self._load()

    def _load(self) -> PreferenceProfile:
        if self.path.exists():
            try:
                return PreferenceProfile(**json.loads(self.path.read_text(encoding="utf-8")))
            except Exception:
                pass
        return PreferenceProfile(user_id=self.user_id)

    def save(self) -> None:
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        self.path.write_text(self.profile.model_dump_json(indent=1), encoding="utf-8")

    def observe(self, query: ShoppingQuery) -> list[str]:
        learned = self.profile.observe(query)
        if learned:
            self.save()
        return learned

    def render(self) -> str:
        """渲染成注入 prompt 的画像块（前端也用同一份数据展示，保证一致）。"""
        p = self.profile
        if not (p.budget_tier or p.priorities or p.categories):
            return "（暂无画像：新用户）"
        lines = [f"- 预算档：{p.budget_tier or '未知'}",
                 f"- 在意维度：{'、'.join(p.priorities) or '未知'}",
                 f"- 关注品类：{'、'.join(p.categories) or '未知'}"]
        if p.history:
            lines.append("- 最近询问：" + "；".join(p.history[:5]))
        return "\n".join(lines)

    def update_from_ui(self, **kwargs) -> None:
        """前端可编辑入口：覆盖非空字段后落盘。"""
        for k, v in kwargs.items():
            if hasattr(self.profile, k) and v is not None:
                setattr(self.profile, k, v)
        self.save()


class MemoryManager:
    """会话记忆 + 用户画像的统一门面。每个会话持久化为 data/sessions/{session_id}.json。"""

    _lock = threading.Lock()

    def __init__(self, session_id: str = "default", user_id: str = "default",
                 keep_recent: int = 6):
        self.session_id = session_id
        self.path = SESSIONS_DIR / f"{session_id}.json"
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        data = self._load()
        self.title: str = data.get("title", session_id)
        self.created_at: str = data.get("created_at", "")
        self.working = WorkingMemory(keep_recent=keep_recent, **data.get("working", {}))
        self.prefs = PreferenceMemory(user_id)
        self.token_total: int = data.get("token_total", 0)

    def _load(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    # ---------- 会话持久化 ----------
    def save(self) -> None:
        with MemoryManager._lock:
            from datetime import datetime

            payload = {
                "session_id": self.session_id,
                "title": self.title,
                "created_at": self.created_at or datetime.now().strftime("%Y-%m-%d %H:%M"),
                "working": self.working.to_dict(),
                "token_total": self.token_total,
            }
            self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                 encoding="utf-8")

    def add_message(self, role: str, content: str, tokens: int = 0, llm=None) -> None:
        self.working.add(role, content)
        self.token_total += tokens
        if role == "user" and (self.title == self.session_id or not self.title):
            self.title = content[:24]  # 首条消息当会话标题
        self.save()
        # 旧消息积压即压缩（上下文预算的执行点）
        if llm is not None and self.working.old:
            self.working.compress(llm)
            self.save()

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)


def list_sessions() -> list[dict]:
    """侧边栏会话列表数据：[{session_id, title, mtime}]，按修改时间倒序。"""
    out = []
    if SESSIONS_DIR.exists():
        for f in SESSIONS_DIR.glob("*.json"):
            if f.name.startswith("profile_"):
                continue
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                out.append({"session_id": d.get("session_id", f.stem),
                            "title": d.get("title", f.stem),
                            "mtime": f.stat().st_mtime})
            except Exception:
                continue
    return sorted(out, key=lambda x: -x["mtime"])
