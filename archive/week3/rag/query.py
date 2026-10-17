"""
Week3 B · RAG 问答：混合检索 → 强制引用生成 → 打印溯源
用法：python week3/rag/query.py "蓝牙耳机保修多久"
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from config import MODEL, get_client  # noqa: E402
from week3.rag.rag_lib import hybrid_search  # noqa: E402

client = get_client()

ANSWER_SYSTEM = (
    "你只依据给定的参考资料回答问题，禁止使用资料外的知识编造。\n"
    "引用规则：每个关键结论后必须标注来源编号，如 [1]；资料不足以回答时，"
    "必须明确回答\"知识库中没有相关资料\"，禁止硬答。"
)


def answer(question: str, top_k: int = 4) -> dict:
    """返回 {answer, citations}，供 week4 检索 Agent 复用。"""
    hits = hybrid_search(question, top_k)

    # 把检索结果编号拼进 prompt —— 引用溯源的关键就在这里：编号 ↔ chunk 一一对应
    context = "\n\n".join(
        f"[{i + 1}] 来源：{h['source']} | 小节：{h['heading']}（相关度 {h['rrf_score']}）\n{h['text']}"
        for i, h in enumerate(hits)
    )
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": ANSWER_SYSTEM},
            {"role": "user", "content": f"参考资料：\n{context}\n\n问题：{question}"},
        ],
        temperature=0.2,
    )
    ans = resp.choices[0].message.content or ""
    citations = [
        {"no": i + 1, "source": h["source"], "heading": h["heading"], "text": h["text"][:80]}
        for i, h in enumerate(hits)
        if f"[{i + 1}]" in ans  # 只保留答案里真正引用到的
    ]
    return {"answer": ans, "citations": citations, "retrieved": hits,
            "tokens": resp.usage.total_tokens}


if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "蓝牙耳机的保修期是多久？充不进电怎么办？"
    result = answer(q)
    print(f"\n问题：{q}\n\n{result['answer']}\n\n--- 引用来源 ---")
    for c in result["citations"]:
        print(f"  [{c['no']}] {c['source']} | {c['heading']} | {c['text']}…")
