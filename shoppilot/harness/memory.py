"""
ShopPilot ⑥ 记忆管理器（吸收 week2/memory.py + kamacoder《Agent 的记忆》四分类设计）
================================================================================
四类记忆各司其职（对齐 kamacoder 记忆架构：短期/Session State/长期/RAG 不要混在一层）：
  1. WorkingMemory 短期记忆：滑窗 + 滚动摘要——"摘要压缩而非全文保留"，丢掉噪音只留
     目标/约束/关键证据（week2 已验证的思路）。
  2. SessionState 结构化任务状态：不是聊天记录，是 goal/已完成/已降级/关键结论 的 JSON 状态，
     多轮追问（"那它保修多久"）时注入 Planner 消解指代——防多步任务跑散。
  3. PreferenceMemory 长期记忆：跨会话画像（预算档/在意维度/品类/历史），文件态持久化。
     写入规则对齐"长期记忆要像数据库写入"：只写从 ShoppingQuery 确定性抽取的
     **用户明确表达**（来源可靠）、更新即覆盖（冲突合并）、history/priorities 有上限
     （噪音清理）、提供 forget() 删除入口（遗忘机制）、条目带 updated_at（过期可判）。
  4. 外部知识库：shoppilot/rag（RAG 层），是"可查的资料"而非"记忆"——语义上分开。
可插拔：三类记忆都可独立替换实现（文件态 → Redis/向量库），MemoryManager 是唯一入口。
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
        from datetime import datetime

        self.profile.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        self.path.write_text(self.profile.model_dump_json(indent=1), encoding="utf-8")

    def observe(self, query: ShoppingQuery) -> list[str]:
        learned = self.profile.observe(query)
        if learned:
            self.save()
        return learned

    def forget(self, keys: list[str]) -> list[str]:
        """遗忘机制：按维度删除画像（用户要求删除/前端清理入口），返回删了什么。"""
        removed = []
        if "budget" in keys and self.profile.budget_tier:
            removed.append(f"预算档({self.profile.budget_tier})")
            self.profile.budget_tier = ""
        if "priorities" in keys and self.profile.priorities:
            removed.append(f"在意维度({'、'.join(self.profile.priorities)})")
            self.profile.priorities = []
        if "categories" in keys and self.profile.categories:
            removed.append(f"品类({'、'.join(self.profile.categories)})")
            self.profile.categories = []
        if "history" in keys and self.profile.history:
            removed.append(f"历史({len(self.profile.history)}条)")
            self.profile.history = []
        if removed:
            self.save()
        return removed

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
        if p.updated_at:
            lines.append(f"- 更新时间：{p.updated_at}")
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
        working_data = data.get("working", {})
        working_data.setdefault("keep_recent", keep_recent)
        self.working = WorkingMemory(**working_data)
        self.prefs = PreferenceMemory(user_id)
        self.token_total: int = data.get("token_total", 0)
        self.state: dict = data.get("state", {})  # SessionState：结构化任务状态（非聊天记录）

    # ---------- Session State：结构化任务状态（对齐 kamacoder 记忆四分类） ----------
    def update_state(self, goal: str, intent: str, tasks: list, key_findings: str) -> None:
        """一次 run_team 结束后写入：goal/意图/任务清单（含降级）/关键结论。"""
        self.state = {
            "goal": goal[:80],
            "intent": intent,
            "completed_tasks": [{"id": t.task_id, "type": t.task_type,
                                 "goal": t.goal[:60], "degraded": t.degraded}
                                for t in tasks],
            "key_findings": key_findings[:300],
        }
        self.save()

    def render_state(self) -> str:
        """渲染成 Planner 的 [任务状态] 块：多轮追问消解指代（"那它保修多久"的"它"）。"""
        if not self.state:
            return ""
        tasks = self.state.get("completed_tasks", [])
        done = "；".join(f"任务{t['id']}({t['type']}{'，已降级' if t['degraded'] else ''})：{t['goal']}"
                         for t in tasks) or "无"
        return (f"- 上一轮目标：{self.state.get('goal', '')}\n"
                f"- 上一轮意图：{self.state.get('intent', '')}\n"
                f"- 已完成任务：{done}\n"
                f"- 已得结论：{self.state.get('key_findings', '')[:200]}")

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
                "state": self.state,
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
    """侧边栏会话列表数据：[{session_id, title, mtime}]，按修改时间倒序。
    过滤：画像文件与 benchmark 会话（bench_*/benchfull_*）不进用户界面。"""
    out = []
    if SESSIONS_DIR.exists():
        for f in SESSIONS_DIR.glob("*.json"):
            if f.name.startswith(("profile_", "bench_", "benchfull_", "vref", "smoke")):
                continue
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                out.append({"session_id": d.get("session_id", f.stem),
                            "title": d.get("title", f.stem),
                            "mtime": f.stat().st_mtime})
            except Exception:
                continue
    return sorted(out, key=lambda x: -x["mtime"])
