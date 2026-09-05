# -*- coding: utf-8 -*-
"""混合检索：BM25(jieba) 必选 + 本地向量(可选) -> RRF 融合。

语料规模极小（几十条文档），不引入独立向量库：
* 词法通道：rank-bm25 + jieba 分词，零网络依赖，保底可用
* 向量通道：sentence-transformers/bge-small-zh-v1.5（可选安装，
  HF_ENDPOINT 镜像加速），numpy 余弦即可
融合用 Reciprocal Rank Fusion，对两路分数分布差异鲁棒。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

import jieba  # 【技术栈：jieba】中文分词（搜索引擎模式 cut_for_search，切得更碎利于召回）

logger = logging.getLogger(__name__)

try:
    from rank_bm25 import BM25Okapi  # 【技术栈：rank-bm25】BM25 词法评分（TF-IDF 家族）
except ImportError as e:  # pragma: no cover
    raise RuntimeError("缺少 rank-bm25，请 pip install -r requirements.txt") from e


def tokenize(text: str) -> list[str]:
    return [t.strip() for t in jieba.cut_for_search(text) if t.strip()]


@dataclass
class RetrievedDoc:
    doc_id: str
    text: str
    score: float = 0.0
    meta: dict = field(default_factory=dict)


class HybridSearch:
    """对一组 (doc_id, text, meta) 文档做混合检索。"""

    def __init__(self, docs: list[tuple[str, str, dict]]) -> None:
        self.doc_ids = [d[0] for d in docs]
        self.texts = [d[1] for d in docs]
        self.metas = [d[2] for d in docs]
        corpus_tokens = [tokenize(t) for t in self.texts]
        self._bm25 = BM25Okapi(corpus_tokens)
        self._vectors = self._try_embed(self.texts)

    # ------------------------------------------------------------------
    def _try_embed(self, texts: list[str]):
        """可选向量通道：未安装 sentence-transformers 或开关关闭时返回 None。"""
        if os.getenv("ENABLE_VECTOR_SEARCH", "").lower() not in ("1", "true", "yes"):
            return None
        try:
            from sentence_transformers import SentenceTransformer  # 延迟导入
        except ImportError:
            logger.warning("ENABLE_VECTOR_SEARCH=true 但未安装 sentence-transformers，向量通道停用")
            return None
        model_name = "BAAI/bge-small-zh-v1.5"
        try:
            model = SentenceTransformer(model_name)
            vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        except Exception as e:  # noqa: BLE001  下载失败/网络问题不影响主链路
            logger.warning("向量化失败(%s)，仅用词法检索", e)
            return None
        return vecs

    # ------------------------------------------------------------------
    def _bm25_rank(self, query: str, top_k: int) -> list[int]:
        scores = self._bm25.get_scores(tokenize(query))
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        return [i for i in order[:top_k] if scores[i] > 0]

    def _vector_rank(self, query: str, top_k: int) -> list[int]:
        import numpy as np

        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer("BAAI/bge-small-zh-v1.5")
        q = model.encode([query], normalize_embeddings=True)[0]
        sims = self._vectors @ q
        order = sorted(range(len(sims)), key=lambda i: sims[i], reverse=True)
        return [i for i in order[:top_k] if sims[i] > 0.3]

    @staticmethod
    def _rrf(rankings: list[list[int]], k: int = 60) -> dict[int, float]:
        fused: dict[int, float] = {}
        for ranking in rankings:
            for pos, idx in enumerate(ranking):
                fused[idx] = fused.get(idx, 0.0) + 1.0 / (k + pos + 1)
        return fused

    def search(self, query: str, top_k: int = 8) -> list[RetrievedDoc]:
        rankings = [self._bm25_rank(query, top_k * 2)]
        if self._vectors is not None:
            rankings.append(self._vector_rank(query, top_k * 2))
        fused = self._rrf(rankings)
        order = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
        results = []
        for idx, score in order:
            results.append(RetrievedDoc(
                doc_id=self.doc_ids[idx],
                text=self.texts[idx],
                score=round(score, 4),
                meta=self.metas[idx],
            ))
        return results


if __name__ == "__main__":
    demo = HybridSearch([
        ("m1", "退款率：退款订单数除以支付订单数，需要去重", {"kind": "metric"}),
        ("g1", "华东大区包含上海 江苏 浙江 安徽 福建 江西 山东", {"kind": "glossary"}),
        ("t1", "orders 订单主表 含下单时间 地区 渠道 状态 金额", {"kind": "table"}),
    ])
    for d in demo.search("华东退款率怎么算"):
        print(d.doc_id, d.score, d.text[:30])
