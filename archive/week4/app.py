"""
Week4 · Streamlit 演示界面：问 → 答 + 引用 + 执行步骤（现场演示用）
跑法：D:\conda_envs\agent\python.exe -m streamlit run week4/app.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st  # noqa: E402

st.set_page_config(page_title="电商多智能体系统", page_icon="🛒", layout="wide")
st.title("🛒 电商导购与评论分析多智能体系统")
st.caption("DeerFlow 范式复现：Planner 拆任务 → Dispatcher 调度 → Aggregator 聚合 | DeepSeek-V4.1-Flash")

mode = st.sidebar.radio("模式", ["单 Agent（Week2）", "多 Agent + 护栏（Week4）"])
question = st.text_input("你的问题", "蓝牙耳机A值得买吗？差评主要是什么？保修政策是什么？")

if st.button("开始分析", type="primary"):
    if mode.startswith("单"):
        from week2.agent_v01 import run_plan
        from week2.planner import plan

        with st.spinner("Planner 拆解任务中…"):
            tasks = plan(question)
        st.subheader("📋 任务分解（DAG）")
        for t in tasks:
            st.markdown(f"- **[{t['id']}]** `{t['task_type']}` {t['goal']}"
                        + (f" ← 依赖 {t['depends_on']}" if t["depends_on"] else ""))
        with st.spinner("逐任务执行工具调用…"):
            report = run_plan(question)
    else:
        from week4.multi_agent import run_multi
        with st.spinner("多 Agent 协作执行中…"):
            report = run_multi(question)

    st.subheader("📊 分析报告")
    st.markdown(report)
