"""
ShopPilot ⑧ Streamlit 前端：类网页版大模型界面（对应设计风格：暗色主题 + 执行过程透明化）
================================================================================
交互范式对齐 ChatGPT/DeepSeek：左侧深色会话栏 + 主区对话流；用户能像看"深度思考"一样
看到 Agent 的任务拆解（任务卡片逐条打勾）、工具调用（折叠框：参数与结果摘要）与反思结果。

Streamlit 坑规避（对应规划）：
  - session_state 管理跨 rerun 状态（当前会话、执行锁）；
  - LLM client 与 RAG 索引靠模块级单例/ lru_cache 缓存（rerun 不重建、不重载索引）；
  - 执行中直接操作 st.status 容器实时更新事件（observability 事件回调驱动）；
  - 任何降级/失败用 st.warning 呈现局部提示，绝不整页报错。

跑法：D:\\conda_envs\\agent\\python.exe -m streamlit run app.py
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime

import streamlit as st

from config import SESSIONS_DIR
from shoppilot.agents.team import run_team
from shoppilot.harness.memory import MemoryManager, list_sessions
from shoppilot.models import TeamResult

st.set_page_config(page_title="ShopPilot · 电商导购智能体", page_icon="🛒", layout="wide")

# ---------------- 暗色主题（参照网页版大模型的配色层次） ----------------
st.markdown("""<style>
#MainMenu, footer, header {visibility: hidden;}
section[data-testid="stSidebar"] {background: #171717; border-right: 1px solid #2a2a2a;}
section[data-testid="stSidebar"] * {color: #d4d4d4 !important;}
[data-testid="stStatusWidget"], .stStatus {background: #1e1e1e !important; border-radius: 8px;}
.stChatMessage {background: #202020; border: 1px solid #2c2c2c; border-radius: 12px;}
.stExpander {background: #1b1b1b; border: 1px solid #2c2c2c; border-radius: 8px;}
h1, h2, h3 {color: #fafafa !important;}
</style>""", unsafe_allow_html=True)


# ---------------- 会话与产物（持久化辅助） ----------------
def artifacts_path(sid: str):
    return SESSIONS_DIR / f"{sid}.artifacts.json"


def load_artifacts(sid: str) -> list[dict]:
    p = artifacts_path(sid)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def append_artifact(sid: str, result: TeamResult) -> None:
    arts = load_artifacts(sid)
    arts.append(json.loads(result.model_dump_json()))
    artifacts_path(sid).write_text(json.dumps(arts, ensure_ascii=False, indent=1),
                                   encoding="utf-8")


def history_messages(sid: str) -> list[dict]:
    """从持久化的会话记忆重放对话（old+recent）。"""
    mem = MemoryManager(sid)
    return mem.working.old + mem.working.recent


# ---------------- 结构化产出卡渲染器 ----------------
def render_card(card: dict | None) -> None:
    if not card:
        return
    kind = card.get("kind")
    if kind == "recommendation":
        st.markdown("##### 🎁 推荐清单")
        if card.get("summary"):
            st.markdown(card["summary"])
        for it in card.get("items", []):
            cites = f" 依据[{it['citation']}]" if it.get("citation") else ""
            st.markdown(
                f"**{it.get('name')}** ｜ ¥{it.get('price')} ｜ 评分 {it.get('rating')}{cites}\n\n"
                f"- 推荐理由：{it.get('reason', '—')}\n"
                f"- 口碑摘要：{it.get('review_summary', '—')}\n"
                f"- 卖点：{'；'.join(it.get('highlights', [])) or '—'}")
    elif kind == "compare":
        st.markdown("##### ⚖️ 对比卡")
        rows = card.get("dimensions", [])
        if rows:
            header = list(rows[0].keys())
            table = "| " + " | ".join(header) + " |\n|" + "---|" * len(header) + "\n"
            for row in rows:
                table += "| " + " | ".join(str(row.get(h, "")) for h in header) + " |\n"
            st.markdown(table)
        if card.get("conclusion"):
            st.markdown(f"**结论：** {card['conclusion']}")
    elif kind == "caveats":
        st.markdown("##### ⚠️ 避坑清单")
        if card.get("summary"):
            st.markdown(card["summary"])
        for it in card.get("items", []):
            cites = f" 依据[{it['citation']}]" if it.get("citation") else ""
            st.markdown(f"- **{it.get('product', '')}｜{it.get('issue', '')}**："
                        f"{it.get('detail', '')}{cites}")


def render_citations(citations: list[dict]) -> None:
    if not citations:
        return
    st.markdown("##### 🔎 引用来源")
    for c in citations:
        with st.expander(f"[{c['no']}] {c['source']}" + (f" ｜ {c['heading']}" if c.get("heading") else "")):
            st.caption(c.get("text", ""))


def render_result_blocks(result: TeamResult, live_status=None) -> None:
    """结果区渲染：结构化卡 + 引用 + 降级警示 + 反思 + 学到的画像。"""
    if result.structured:
        render_card(result.structured)
    render_citations([c.model_dump() for c in result.citations])
    for d in result.degradations:
        st.warning(f"⚠️ 部分子任务降级：{d}")
    if result.reflect_issues:
        ok = result.reflect_issues[0].startswith("已修复")
        (st.info if ok else st.warning)(
            "🪞 反思：{'已修复' if ok else '发现'}：" + "；".join(result.reflect_issues))
    if result.learned:
        st.caption("🧠 本轮记住的偏好：" + "；".join(result.learned))
    st.caption(f"⏱ {result.duration_s:.1f}s ｜ 🔢 {result.tokens} tokens"
               + (f" ｜ trace: {result.trace_file}" if result.trace_file else ""))


# ---------------- 侧边栏 ----------------
def sidebar() -> str:
    with st.sidebar:
        st.title("🛒 ShopPilot")
        st.caption("电商导购智能体 · 8 层 Agent Harness")

        if st.button("➕ 新建会话", use_container_width=True):
            sid = datetime.now().strftime("s_%m%d_%H%M%S_") + uuid.uuid4().hex[:4]
            MemoryManager(sid).save()
            st.session_state.current_session = sid
            st.rerun()

        sessions = list_sessions()
        labels = [s["title"] for s in sessions]
        sids = [s["session_id"] for s in sessions]
        current = st.session_state.get("current_session")
        if current not in sids and sids:
            current = sids[0]
            st.session_state.current_session = current
        if not sessions:
            current = current or "default"
        if labels:
            idx = sids.index(current) if current in sids else 0
            picked = st.selectbox("会话列表", labels, index=idx, format_func=lambda x: x)
            current = sids[labels.index(picked)]
            st.session_state.current_session = current
            c1, c2 = st.columns(2)
            if c1.button("🗑 删除当前", use_container_width=True):
                MemoryManager(current).delete()
                artifacts_path(current).unlink(missing_ok=True)
                st.session_state.pop("current_session", None)
                st.rerun()
            if c2.button("🧹 清空对话", use_container_width=True):
                mem = MemoryManager(current)
                mem.working = type(mem.working)()
                mem.save()
                artifacts_path(current).unlink(missing_ok=True)
                st.rerun()

        # ---- 用户画像面板（可编辑 = 可解释记忆） ----
        st.divider()
        st.markdown("###### 🧠 用户画像（可编辑）")
        mem = MemoryManager(current or "default")
        p = mem.prefs.profile
        budget = st.text_input("预算档", p.budget_tier, placeholder="如 300-800")
        prio = st.text_input("在意维度（顿号分隔）", "、".join(p.priorities))
        cats = st.text_input("关注品类（顿号分隔）", "、".join(p.categories))
        if st.button("保存画像", use_container_width=True):
            mem.prefs.update_from_ui(budget_tier=budget.strip(),
                                     priorities=[x.strip() for x in prio.split("、") if x.strip()],
                                     categories=[x.strip() for x in cats.split("、") if x.strip()])
            st.toast("画像已保存，下一轮回答将参考")
        if p.history:
            st.caption("最近询问：" + "；".join(p.history[:3]))

        # ---- 模式说明与示例 ----
        st.divider()
        st.markdown("###### 💡 试试这些")
        for q in ["预算500以内推荐降噪耳机", "蓝牙耳机A和蓝牙耳机F怎么选",
                  "蓝牙耳机A翻车点在哪", "蓝牙耳机A保修多久",
                  "iPhone 18 值得买吗"]:
            if st.button(q, key=f"eg_{q}", use_container_width=True):
                st.session_state.pending_input = q
                st.rerun()

        st.caption(f"会话累计 tokens：{mem.token_total}")
        return current or "default"


# ---------------- 主聊天区 ----------------
def main() -> None:
    st.markdown("## ShopPilot · 你的导购 Agent")
    st.caption("结构化抽取需求 → 并行查商品池/口碑/售后知识库 → 带依据的推荐、对比与避坑")
    sid = sidebar()
    arts = load_artifacts(sid)
    history = history_messages(sid)

    # 重放历史（气泡）+ 绑定每轮的结构化产物
    a_idx = 0
    for msg in history:
        with st.chat_message(msg["role"], avatar="👤" if msg["role"] == "user" else "🛒"):
            st.markdown(msg["content"])
            if msg["role"] == "assistant" and a_idx < len(arts):
                try:
                    render_result_blocks(TeamResult(**arts[a_idx]))
                except Exception:
                    pass
                a_idx += 1

    user_input = st.chat_input("输入你的购物问题，Enter 发送…")
    pending = st.session_state.pop("pending_input", None)
    if pending:
        user_input = pending

    if not user_input:
        return

    st.chat_message("user", avatar="👤").markdown(user_input)

    # ---- 执行过程透明化：st.status 实时渲染事件流 ----
    with st.chat_message("assistant", avatar="🛒"):
        status = st.status("🤔 Agent 执行中…", expanded=True)
        plan_box = status.container()
        seen_tasks: set[int] = set()

        def on_event(etype: str, payload: dict) -> None:
            if etype == "plan":
                tasks = payload.get("tasks", [])
                if payload.get("intent") == "out_of_scope":
                    status.update(label="🚫 超出导购范围，直接拒答", state="complete")
                    return
                plan_box.markdown("##### 📋 任务拆解")
                for t in tasks:
                    deps = f" ← 依赖{t['depends_on']}" if t.get("depends_on") else ""
                    plan_box.markdown(
                        f"- [ ] **[{t['id']}]** `{t['task_type']}` {t['goal']}{deps}")
                seen_tasks |= {t["id"] for t in tasks}
            elif etype == "plan_retry":
                plan_box.warning(f"Planner 第{payload['attempt']}次校验失败，已让模型重写")
            elif etype == "task_start":
                status.update(label=f"⚙️ 执行任务{payload['id']}：{payload['goal'][:28]}…")
            elif etype == "tool_start":
                pass  # 等结果一起展示，避免折叠框闪断
            elif etype == "tool_end":
                ok = "✅" if payload.get("ok") else "❌"
                with status.expander(f"{ok} 已调用 `{payload['name']}`"):
                    st.caption("参数：" + str(payload.get("args"))[:200])
                    st.caption("结果摘要：" + str(payload.get("summary"))[:400])
            elif etype == "task_end":
                mark = "✅" if not payload.get("degraded") else "⚠️"
                plan_box.markdown(f"- [x] {mark} 任务{payload['id']}：{payload.get('summary', '')[:80]}")
            elif etype == "aggregate_end":
                status.update(label="🧩 聚合与仲裁中…")
            elif etype == "reflect_end":
                if payload.get("ok"):
                    status.update(label="🪞 反思通过", state="complete")
                else:
                    status.update(label="🪞 发现问题，修复中…")

        try:
            result = run_team(user_input, session_id=sid, user_id="default", on_event=on_event)
        except Exception as e:  # 前端任何异常都局部呈现，不整页报错
            st.error(f"执行异常（已捕获）：{type(e).__name__}: {e}")
            return

        status.update(label="✅ 执行完成", state="complete", expanded=False)

        # ---- 流式输出最终回答（分块 write_stream）----
        def stream_answer():
            text = result.answer_md
            for i in range(0, len(text), 24):
                yield text[i:i + 24]

        st.write_stream(stream_answer())
        render_result_blocks(result)
        append_artifact(sid, result)

    # 本轮对话已由 run_team 持久化进会话记忆，无需重复写
    st.rerun()


if __name__ == "__main__":
    main()
