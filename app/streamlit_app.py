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

from shoppilot.core.settings import SESSIONS_DIR
from shoppilot.core.memory import MemoryManager, list_sessions
from shoppilot.core.types import TeamResult
from shoppilot.domains.ecommerce.factory import build_ecommerce_agent

@st.cache_resource
def get_agent():
    """Singleton agent: connection reuse; per-session isolation is handled
    inside run() by session_id."""
    return build_ecommerce_agent(guardrail_mode="deterministic", enable_memory=True)


st.set_page_config(page_title="ShopPilot · 电商导购智能体", page_icon="🛒", layout="wide")

# ---------------- 暗色主题（配色与字体栈对齐主流大模型 Web 端） ----------------
st.markdown("""<style>
#MainMenu, footer, header {visibility: hidden;}
html, body, [class*="css"], .stApp {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC",
               "Hiragino Sans GB", "Microsoft YaHei", "HarmonyOS Sans SC", "MiSans",
               "Source Han Sans SC", sans-serif;
  -webkit-font-smoothing: antialiased;
}
section[data-testid="stSidebar"] {background: #171717; border-right: 1px solid #2a2a2a;}
section[data-testid="stSidebar"] * {color: #d4d4d4 !important;}
[data-testid="stStatusWidget"], .stStatus {background: #1e1e1e !important; border-radius: 8px;}
.stChatMessage {background: #202020; border: 1px solid #2c2c2c; border-radius: 12px;}
.stExpander {background: #1b1b1b; border: 1px solid #2c2c2c; border-radius: 8px;}
h1, h2, h3 {color: #fafafa !important; font-weight: 600 !important;}
.stMarkdown p {line-height: 1.75;}
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
    return mem.short.old + mem.short.recent


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
    elif kind == "research":
        st.markdown("##### 🧭 研究报告")
        st.markdown(f"**结论：** {card.get('conclusion', '')}")
        if card.get("candidates"):
            rows = "| 商品 | 价格 | 得分 | 结论 |\n| --- | --- | --- | --- |\n"
            for c in card["candidates"]:
                rows += f"| {c.get('name', '')} | ¥{c.get('price', '')} | {c.get('score', '')} | {c.get('verdict', '')} |\n"
            st.markdown(rows)
        if card.get("evidence"):
            st.markdown("**证据链（可溯源）：**")
            for e in card["evidence"]:
                cite = f" [{e['citation']}]" if e.get("citation") else ""
                st.markdown(f"- {e.get('claim', '')}{cite}（{e.get('source', '')}）")
        cols = st.columns(2)
        with cols[0]:
            st.markdown("**⚠️ 风险：**")
            st.markdown("\n".join(f"- {r}" for r in card.get("risks", [])) or "—")
        with cols[1]:
            st.markdown("**✅ 建议：**")
            st.markdown("\n".join(f"- {a}" for a in card.get("advice", [])) or "—")
        if card.get("data_window"):
            st.caption(f"数据窗口：{card['data_window']}（教学仿真数据，生产环境请替换为实时数据）")


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
    bd = " ｜ ".join(f"{k} {v}" for k, v in (result.token_breakdown or {}).items())
    st.caption(f"⏱ {result.duration_s:.1f}s ｜ 🔢 本轮 {result.tokens} tokens"
               + (f"（{bd}）" if bd else "")
               + (f" ｜ trace: {result.trace_file}" if result.trace_file else ""))
    if result.report_file and Path(result.report_file).exists():
        st.download_button("📄 下载研究报告 (Markdown)",
                           data=Path(result.report_file).read_text(encoding="utf-8"),
                           file_name=Path(result.report_file).name, key=f"dl_{sid}")


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
        if current not in sids:
            # 每次打开应用默认进入一个全新空白会话（历史会话仍可从列表切回）
            current = datetime.now().strftime("s_%m%d_%H%M%S_") + uuid.uuid4().hex[:4]
            MemoryManager(current).save()
            st.session_state.current_session = current
            sessions = list_sessions()
            labels = [s["title"] for s in sessions]
            sids = [s["session_id"] for s in sessions]
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

        # ---- 长期记忆（跨会话，人类可读可删） ----
        st.divider()
        st.markdown("###### 🗂 长期记忆")
        mem = MemoryManager(sid or "default")
        raw = mem.long.raw()
        if raw:
            with st.expander(f"查看记忆（{len(mem.long.entries())} 条）"):
                st.caption(raw[-800:] + ("…" if len(raw) > 800 else ""))
            if st.button("🗑 清除长期记忆", use_container_width=True):
                mem.long.clear()
                st.toast("长期记忆已清除")
                st.rerun()
        else:
            st.caption("（暂无跨会话记忆）")
        st.caption(f"本会话 tokens：{mem.token_total}")

        # ---- 模式说明与示例 ----
        st.divider()
        st.markdown("###### 💡 试试这些")
        for q in ["预算500以内推荐降噪耳机", "蓝牙耳机A和蓝牙耳机F怎么选",
                  "蓝牙耳机A翻车点在哪", "蓝牙耳机A保修多久",
                  "iPhone 18 值得买吗"]:
            if st.button(q, key=f"eg_{q}", use_container_width=True):
                st.session_state.pending_input = q
                st.rerun()

        return current or "default"


# ---------------- 主聊天区 ----------------
def main() -> None:
    st.markdown("## ShopPilot · 你的导购 Agent")
    st.caption("结构化抽取需求 → 并行查商品池/口碑/售后知识库 → 带依据的推荐、对比与避坑")
    sid = sidebar()
    arts = load_artifacts(sid)
    history = history_messages(sid)

    # 空状态欢迎页（对齐大模型首屏：问候语 + 示例问题）
    if not history:
        st.markdown(
            "<div style='text-align:center; padding:36px 0 10px;'>"
            "<div style='font-size:40px;'>🛒</div>"
            "<h2 style='margin-top:10px;'>你好，我是 ShopPilot</h2>"
            "<p style='color:#9aa0a6;'>会比价、看口碑、读售后，每一步都给你依据，还越用越懂你</p>"
            "</div>", unsafe_allow_html=True)
        cols = st.columns(2)
        examples = [("🎁 选购推荐", "预算500以内推荐一副降噪耳机"),
                    ("⚖️ 商品对比", "蓝牙耳机A和蓝牙耳机F怎么选"),
                    ("⚠️ 口碑解读", "蓝牙耳机A翻车点在哪"),
                    ("🛡️ 售后问答", "蓝牙耳机A保修多久")]
        for i, (tag, q) in enumerate(examples):
            with cols[i % 2]:
                if st.button(f"{tag}\n\n{q}", key=f"welcome_{i}", use_container_width=True):
                    st.session_state.pending_input = q
                    st.rerun()

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

    # 控制台（ShopPilot.html）场景卡片经 ?q= 直达提问
    if "q" in st.query_params and not st.session_state.get("_qp_done"):
        st.session_state.pending_input = st.query_params["q"]
        st.session_state._qp_done = True
        del st.query_params["q"]
        st.rerun()

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
                seen_tasks.update(t["id"] for t in tasks)
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
            elif etype == "replan_start":
                status.update(label="🔁 子任务失败，重新规划补救…")
                plan_box.warning("部分子任务降级，Replanner 带失败证据重拆补救任务")
            elif etype == "replan":
                for t in payload.get("tasks", []):
                    plan_box.markdown(f"- [ ] **[补救{t['id']}]** `{t['task_type']}` {t['goal']}")
            elif etype == "refuse_recheck":
                status.update(label="🛡 疑似误拒，复核中…")
            elif etype == "pool_recheck":
                status.update(label(f"🛡 商品池外实体 {payload.get('unknown')}，复核中…"))
            elif etype == "aggregate_end":
                status.update(label="🧩 聚合与仲裁中…")
            elif etype == "reflect_end":
                if payload.get("ok"):
                    status.update(label="🪞 反思通过", state="complete")
                else:
                    status.update(label="🪞 发现问题，修复中…")

        def _safe_event(fn):
            def _wrap(*a):  # 前端渲染异常绝不影响 Agent 主流程
                try:
                    fn(*a)
                except Exception:
                    pass
            return _wrap

        agent = get_agent()
        agent.session_id = sid
        agent.on_event = _safe_event(on_event)
        try:
            result = agent.run(user_input)
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
