"""
ShopPilot rag_lib：切分 → TF-IDF 向量化 → FAISS+BM25 混合检索 → RAG 问答（吸收 week3）
================================================================================
相对 week3/rag/rag_lib.py 的升级：kb_dir 参数化——知识库换目录（不同业务）不改检索代码；
索引按 kb 目录名分片持久化，多知识库可并存。
混合检索原理（面试必问）：向量检索懂语义但对专有名词/精确数字不敏感；BM25 反之；
RRF（倒数排名融合）合并两路排名，召回率显著更高。教学用 TF-IDF 字符 n-gram 做
Embedding（离线秒建、中文友好），换 bge-small-zh 只需实现 encode() 接口。
"""
from __future__ import annotations

import json
import pickle
import re
from functools import lru_cache
from pathlib import Path

import jieba
import numpy as np
from rank_bm25 import BM25Okapi
from sklearn.feature_extraction.text import TfidfVectorizer

from shoppilot.core.settings import ECOM_DIR

try:  # faiss 装了就用；没装退化成 numpy 暴力点积（教学规模完全够）
    import faiss
except ImportError:
    faiss = None

INDEX_BASE = Path(__file__).resolve().parent / "index"
CHUNK_SIZE = 300
OVERLAP = 60


# ---------- 1) 结构感知切分（按 Markdown 标题，超长段再按句切带 overlap） ----------
def chunk_markdown(text: str, source: str) -> list[dict]:
    chunks = []
    sections = re.split(r"^(#{1,3} .+)$", text, flags=re.M)
    for i in range(1, len(sections), 2):
        heading, body = sections[i].strip("# \n"), sections[i + 1].strip()
        if not body:
            continue
        if len(body) > CHUNK_SIZE:
            start = 0
            while start < len(body):
                piece = body[start:start + CHUNK_SIZE]
                chunks.append({"text": piece, "source": source, "heading": heading})
                start += CHUNK_SIZE - OVERLAP
        else:
            chunks.append({"text": body, "source": source, "heading": heading})
    return chunks


# ---------- 2) Embedding 接口 + TF-IDF 实现 ----------
class TfidfEmbedder:
    def __init__(self):
        self.vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(1, 3))

    def fit(self, texts: list[str]) -> TfidfEmbedder:
        self.vec.fit(texts)
        return self

    def encode(self, texts: list[str]) -> np.ndarray:
        m = self.vec.transform(texts).astype(np.float32).toarray()
        norms = np.linalg.norm(m, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return m / norms


def _tokenize(text: str) -> list[str]:
    return [w for w in jieba.cut(text) if w.strip()]


# ---------- 3) 索引构建与加载（按 kb_dir 分片持久化） ----------
def _index_dir(kb_dir: Path) -> Path:
    return INDEX_BASE / kb_dir.name


def build_index(kb_dir: Path | None = None) -> dict:
    kb_dir = kb_dir or ECOM_DIR / "knowledge"
    chunks: list[dict] = []
    for md in sorted(kb_dir.glob("*.md")):
        chunks.extend(chunk_markdown(md.read_text(encoding="utf-8"), md.name))
    if not chunks:
        raise RuntimeError(f"{kb_dir} 下没有知识库文档")

    texts = [c["text"] for c in chunks]
    embedder = TfidfEmbedder().fit(texts)
    vectors = embedder.encode(texts)

    idx_dir = _index_dir(kb_dir)
    idx_dir.mkdir(parents=True, exist_ok=True)
    if faiss is not None:
        index = faiss.IndexFlatIP(vectors.shape[1])
        index.add(vectors)
        faiss.write_index(index, str(idx_dir / "kb.faiss"))
    np.save(idx_dir / "vectors.npy", vectors)
    (idx_dir / "chunks.json").write_text(json.dumps(chunks, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
    with open(idx_dir / "embedder.pkl", "wb") as f:
        pickle.dump(embedder, f)
    _load_index.cache_clear()
    return {"chunks": len(chunks), "docs": len(list(kb_dir.glob("*.md"))),
            "dim": int(vectors.shape[1]), "index_dir": str(idx_dir)}


@lru_cache(maxsize=4)
def _load_index(kb_dir_str: str):
    """缓存索引（进程内单例）：Streamlit rerun 不重建，benchmark 连跑不重复加载。"""
    kb_dir = Path(kb_dir_str)
    idx_dir = _index_dir(kb_dir)
    chunks = json.loads((idx_dir / "chunks.json").read_text(encoding="utf-8"))
    embedder = pickle.loads((idx_dir / "embedder.pkl").read_bytes())
    if faiss is not None and (idx_dir / "kb.faiss").exists():
        index = faiss.read_index(str(idx_dir / "kb.faiss"))
        vectors = None
    else:
        index, vectors = None, np.load(idx_dir / "vectors.npy")
    bm25 = BM25Okapi([_tokenize(c["text"]) for c in chunks])
    return chunks, index, vectors, embedder, bm25


def ensure_index(kb_dir: Path | None = None) -> None:
    """索引缺失或知识库更新（md 文件数变化）时自动重建。"""
    kb_dir = kb_dir or ECOM_DIR / "knowledge"
    idx_dir = _index_dir(kb_dir)
    n_docs = len(list(kb_dir.glob("*.md")))
    if not (idx_dir / "chunks.json").exists():
        build_index(kb_dir)
        return
    chunks = json.loads((idx_dir / "chunks.json").read_text(encoding="utf-8"))
    if len({c["source"] for c in chunks}) != n_docs:
        build_index(kb_dir)


# ---------- 4) 混合检索：向量 Top-k + BM25 Top-k → RRF 融合 ----------
def hybrid_search(query: str, top_k: int = 4, kb_dir: Path | None = None) -> list[dict]:
    ensure_index(kb_dir)
    kb_dir = kb_dir or ECOM_DIR / "knowledge"
    chunks, index, vectors, embedder, bm25 = _load_index(str(kb_dir))

    qv = embedder.encode([query])
    if index is not None:
        _, vec_ids = index.search(qv, top_k)
        vec_rank = [i for i in vec_ids[0] if i >= 0]
    else:
        sims = vectors @ qv[0]
        vec_rank = list(np.argsort(-sims)[:top_k])

    bm_scores = bm25.get_scores(_tokenize(query))
    bm_rank = list(np.argsort(-bm_scores)[:top_k])

    rrf: dict[int, float] = {}
    for rank, i in enumerate(vec_rank):
        rrf[i] = rrf.get(i, 0) + 1 / (60 + rank + 1)
    for rank, i in enumerate(bm_rank):
        rrf[i] = rrf.get(i, 0) + 1 / (60 + rank + 1)
    fused = sorted(rrf.items(), key=lambda x: -x[1])[:top_k]

    return [{**chunks[i], "rrf_score": round(s, 4),
             "in_vec": i in vec_rank, "in_bm25": i in bm_rank}
            for i, s in fused]


# ---------- 5) RAG 问答：强制引用 + 不足时诚实拒答 ----------
ANSWER_SYSTEM = (
    "你只依据给定的参考资料回答问题，禁止使用资料外的知识编造。"
    "引用规则：每个关键结论后必须标注来源编号，如 [1]；资料不足以回答时，"
    "必须明确回答\"知识库中没有相关资料\"，禁止硬答。"
)


def rag_answer(question: str, top_k: int = 4, llm=None, kb_dir: Path | None = None) -> dict:
    """返回 {answer, citations, retrieved, tokens}，供 knowledge_qa 子 Agent 与前端复用。"""
    from shoppilot.core.llm import get_llm

    llm = llm or get_llm()
    hits = hybrid_search(question, top_k, kb_dir)
    context = "\n\n".join(
        f"[{i + 1}] 来源：{h['source']} | 小节：{h['heading']}\n{h['text']}"
        for i, h in enumerate(hits))
    answer, tokens = llm.chat_text(
        [{"role": "system", "content": ANSWER_SYSTEM},
         {"role": "user", "content": f"参考资料：\n{context}\n\n问题：{question}"}],
        temperature=0.2)
    citations = [
        {"no": i + 1, "source": h["source"], "heading": h["heading"], "text": h["text"][:80]}
        for i, h in enumerate(hits) if f"[{i + 1}]" in answer
    ]
    return {"answer": answer, "citations": citations, "retrieved": hits, "tokens": tokens}


if __name__ == "__main__":
    print("构建索引：", build_index())
    print("\n混合检索示例（'耳机保修多久'）：")
    for c in hybrid_search("耳机保修多久"):
        print(f"  [{c['source']}|{c['heading']}] rrf={c['rrf_score']}")
