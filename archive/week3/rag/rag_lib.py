"""
Week3 B · RAG 公共库：切分 → Embedding → 混合检索 → 持久化
================================================================================
为什么混合检索强于纯向量（面试必问）：
  - 向量检索擅长语义（"耳机坏修" ≈ "售后维修"），但对型号、专有名词、精确数字不敏感；
  - BM25 关键词检索对专有名词/数字极准，但不懂同义改写；
  - 两者互补，用 RRF（倒数排名融合）合并两路排名，召回率显著更高。

Embedding 说明（教学取舍）：
  默认用 sklearn TF-IDF 字符 n-gram 向量（离线、秒建、中文友好）演示全链路；
  生产/更强语义请换 sentence-transformers 的 bge-small-zh-v1.5——
  只要实现 Embedder.encode() 这个接口，检索与问答代码零改动。
"""
import json
import pickle
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import jieba  # noqa: E402
import numpy as np  # noqa: E402
from rank_bm25 import BM25Okapi  # noqa: E402
from sklearn.feature_extraction.text import TfidfVectorizer  # noqa: E402

try:  # faiss 装了就用；没装退化成 numpy 暴力点积（教学规模完全够）
    import faiss
except ImportError:
    faiss = None

KNOWLEDGE_DIR = Path(__file__).resolve().parents[2] / "data" / "knowledge"
INDEX_DIR = Path(__file__).resolve().parent / "index"

CHUNK_SIZE = 300   # chunk 目标长度（字符）
OVERLAP = 60       # 相邻 chunk 重叠，避免句子被切断后两边都检索不到


# ---------- 1) 切分：按 Markdown 标题做"结构感知切分"（对应规划 4.4-④ 的切分策略） ----------
def chunk_markdown(text: str, source: str) -> list[dict]:
    chunks = []
    sections = re.split(r"^(#{1,3} .+)$", text, flags=re.M)  # 保留标题
    for i in range(1, len(sections), 2):
        heading, body = sections[i].strip("# \n"), sections[i + 1].strip()
        if not body:
            continue
        # 超长段落再按句切，带 overlap（别把一个知识点拦腰斩断）
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
    """TF-IDF 字符 n-gram：中文无需分词，向量归一化后内积=余弦相似度。"""

    def __init__(self):
        self.vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(1, 3))

    def fit(self, texts: list[str]) -> "TfidfEmbedder":
        self.vec.fit(texts)
        return self

    def encode(self, texts: list[str]) -> np.ndarray:
        m = self.vec.transform(texts).astype(np.float32).toarray()  # 稀疏→稠密
        norms = np.linalg.norm(m, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return m / norms  # L2 归一化


def _tokenize(text: str) -> list[str]:
    return [w for w in jieba.cut(text) if w.strip()]


# ---------- 3) 索引构建与持久化 ----------
def build_index() -> dict:
    """读知识库 → 切分 → 向量化 → 存 FAISS + BM25 数据。返回统计信息。"""
    chunks: list[dict] = []
    for md in sorted(KNOWLEDGE_DIR.glob("*.md")):
        chunks.extend(chunk_markdown(md.read_text(encoding="utf-8"), md.name))
    if not chunks:
        raise RuntimeError(f"{KNOWLEDGE_DIR} 下没有知识库文档")

    texts = [c["text"] for c in chunks]
    embedder = TfidfEmbedder().fit(texts)
    vectors = embedder.encode(texts)

    INDEX_DIR.mkdir(exist_ok=True)
    if faiss is not None:
        index = faiss.IndexFlatIP(vectors.shape[1])  # 内积 = 余弦（已归一化）
        index.add(vectors)
        faiss.write_index(index, str(INDEX_DIR / "kb.faiss"))
    np.save(INDEX_DIR / "vectors.npy", vectors)
    (INDEX_DIR / "chunks.json").write_text(
        json.dumps(chunks, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    with open(INDEX_DIR / "embedder.pkl", "wb") as f:
        pickle.dump(embedder, f)

    return {"chunks": len(chunks), "docs": len(list(KNOWLEDGE_DIR.glob('*.md'))), "dim": int(vectors.shape[1])}


def load_index() -> tuple[list[dict], object, object, object]:
    """加载 chunks / FAISS 索引 / 向量矩阵 / Embedder；BM25 从 chunks 现场重建（小语料毫秒级）。"""
    chunks = json.loads((INDEX_DIR / "chunks.json").read_text(encoding="utf-8"))
    embedder = pickle.loads((INDEX_DIR / "embedder.pkl").read_bytes())
    if faiss is not None and (INDEX_DIR / "kb.faiss").exists():
        index = faiss.read_index(str(INDEX_DIR / "kb.faiss"))
        vectors = None
    else:
        index, vectors = None, np.load(INDEX_DIR / "vectors.npy")
    bm25 = BM25Okapi([_tokenize(c["text"]) for c in chunks])
    return chunks, index, vectors, embedder, bm25


# ---------- 4) 混合检索：向量 Top-k + BM25 Top-k → RRF 融合 ----------
def hybrid_search(query: str, top_k: int = 4) -> list[dict]:
    chunks, index, vectors, embedder, bm25 = load_index()

    # ① 向量路
    qv = embedder.encode([query])
    if index is not None:
        _, vec_ids = index.search(qv, top_k)
        vec_rank = [i for i in vec_ids[0] if i >= 0]
    else:  # numpy 退化：全量点积取 top-k
        sims = (vectors @ qv[0])
        vec_rank = list(np.argsort(-sims)[:top_k])

    # ② BM25 路
    bm_scores = bm25.get_scores(_tokenize(query))
    bm_rank = list(np.argsort(-bm_scores)[:top_k])

    # ③ RRF 融合：score = Σ 1/(60+rank)，两路都命中的 chunk 排最前
    rrf: dict[int, float] = {}
    for rank, i in enumerate(vec_rank):
        rrf[i] = rrf.get(i, 0) + 1 / (60 + rank + 1)
    for rank, i in enumerate(bm_rank):
        rrf[i] = rrf.get(i, 0) + 1 / (60 + rank + 1)
    fused = sorted(rrf.items(), key=lambda x: -x[1])[:top_k]

    return [
        {**chunks[i], "rrf_score": round(s, 4),
         "in_vec": i in vec_rank, "in_bm25": i in bm_rank}
        for i, s in fused
    ]


if __name__ == "__main__":
    print("构建索引：", build_index())
    print("\n混合检索示例（'耳机保修多久'）：")
    for c in hybrid_search("耳机保修多久"):
        print(f"  [{c['source']}|{c['heading']}] rrf={c['rrf_score']} vec={c['in_vec']} bm25={c['in_bm25']}")
