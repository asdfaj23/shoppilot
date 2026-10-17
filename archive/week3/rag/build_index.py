"""
Week3 B · 构建知识库索引（build 一次，query 多次）
用法：python week3/rag/build_index.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from week3.rag.rag_lib import build_index  # noqa: E402

if __name__ == "__main__":
    stats = build_index()
    print(f"索引构建完成：{stats['docs']} 篇文档 → {stats['chunks']} 个 chunk，维度 {stats['dim']}")
    print("产物：week3/rag/index/（faiss 索引 + 向量 + chunks + embedder）")
