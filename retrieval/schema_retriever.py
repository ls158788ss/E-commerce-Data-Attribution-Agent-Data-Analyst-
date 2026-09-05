# -*- coding: utf-8 -*-
"""Schema 检索器：把用户问题 -> 精准的 Schema + 口径上下文。

检索单元（全部来自语义层 YAML，不碰业务数据行）：
    表文档 / 列文档 / 术语 / 指标口径 / 业务规则 / 验证过的 SQL 示例

输出 render_context() 直接拼进 SQL Agent 的提示词。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from config.config import get_settings
from retrieval.hybrid_search import HybridSearch
from retrieval.join_graph import get_schema_graph
from semantic.loader import SemanticStore, load_semantic_store


@dataclass
class SchemaContext:
    tables: list[str] = field(default_factory=list)
    table_blocks: list[str] = field(default_factory=list)
    metric_blocks: list[str] = field(default_factory=list)
    glossary_hits: list[str] = field(default_factory=list)
    rule_hits: list[str] = field(default_factory=list)
    example_hits: list[str] = field(default_factory=list)
    raw_docs: list[dict] = field(default_factory=list)

    def render(self) -> str:
        parts = []
        if self.table_blocks:
            parts.append("【相关表结构】\n" + "\n".join(self.table_blocks))
        if self.metric_blocks:
            parts.append("【命中指标口径】\n" + "\n\n".join(self.metric_blocks))
        if self.glossary_hits:
            parts.append("【术语解释】\n" + "\n".join(f"- {g}" for g in self.glossary_hits))
        if self.rule_hits:
            parts.append("【必须遵守的业务规则】\n" + "\n".join(f"- {r}" for r in self.rule_hits))
        if self.example_hits:
            parts.append("【相似问题参考 SQL】\n" + "\n\n".join(self.example_hits))
        return "\n\n".join(parts)


def _render_table_block(store: SemanticStore, table_name: str) -> str:
    t = store.table(table_name)
    if t is None:
        return f"{table_name}: (语义层未收录)"
    lines = [f"{t.table}表 —— {t.description}"]
    for c in t.columns:
        ex = f"，例:{'/'.join(c.examples)}" if c.examples else ""
        lines.append(f"  {c.column}  {c.business_name}：{c.description}{ex}")
    return "\n".join(lines)


class SchemaRetriever:
    def __init__(self, store: SemanticStore | None = None) -> None:
        self.store = store or load_semantic_store()
        self._search = HybridSearch(self._build_documents())

    # ------------------------------------------------------------------
    def _build_documents(self) -> list[tuple[str, str, dict]]:
        """把语义层拍平成检索文档。"""
        docs: list[tuple[str, str, dict]] = []

        for m in self.store.metrics:
            text = f"{m.business_name} {m.name} {m.definition}"
            docs.append((f"metric:{m.name}", text, {"kind": "metric", "name": m.name}))

        for t in self.store.glossary:
            values = "、".join(t.values)
            text = f"{t.term} {t.definition} {values} {t.maps_to}".strip()
            docs.append((f"glossary:{t.term}", text, {"kind": "glossary", "term": t.term}))

        for tb in self.store.tables:
            cols = "；".join(
                f"{c.column}({c.business_name}:{c.description})"
                + (f" 例:{'/'.join(c.examples)}" if c.examples else "")
                for c in tb.columns
            )
            text = f"{tb.table}表 {tb.description} 字段: {cols}"
            docs.append((f"table:{tb.table}", text, {"kind": "table", "table": tb.table}))

            for c in tb.columns:
                ctext = (f"{tb.table}.{c.column} {c.business_name} {c.description} "
                         + " ".join(c.examples)).strip()
                docs.append((f"column:{tb.table}.{c.column}",
                             ctext, {"kind": "column", "table": tb.table, "column": c.column}))

        for r in self.store.rules:
            docs.append((f"rule:{r.id}", r.rule + " " + " ".join(r.scope),
                         {"kind": "rule", "id": r.id}))

        for i, ex in enumerate(self.store.examples):
            docs.append((f"example:{i}", ex.question, {"kind": "example", "index": i}))

        return docs

    # ------------------------------------------------------------------
    def retrieve(self, query: str, top_k: int | None = None) -> SchemaContext:
        k = top_k or get_settings().retrieval_top_k
        hits = self._search.search(query, top_k=k)

        ctx = SchemaContext(raw_docs=[{"doc_id": h.doc_id, "score": h.score,
                                       "text": h.text[:120]} for h in hits])

        hit_tables: set[str] = set()
        matched_metrics: set[str] = set()

        for h in hits:
            kind = h.meta.get("kind")

            if kind == "metric":
                name = h.meta["name"]
                if name not in matched_metrics:
                    m = self.store.metric(name)
                    if m:
                        matched_metrics.add(name)
                        ctx.metric_blocks.append(self.store.render_metric(m))

            elif kind == "table":
                hit_tables.add(h.meta["table"])

            elif kind == "column":
                hit_tables.add(h.meta["table"])

            elif kind == "glossary":
                term = next((x for x in self.store.glossary if x.term == h.meta["term"]), None)
                if term:
                    line = f"{term.term}: {term.definition}" + (f"（{', '.join(term.values)}）" if term.values else "")
                    if line not in ctx.glossary_hits:
                        ctx.glossary_hits.append(line)

            elif kind == "rule":
                rule = next((x for x in self.store.rules if x.id == h.meta["id"]), None)
                if rule and all(rule.id not in r for r in ctx.rule_hits):
                    ctx.rule_hits.append(f"[{rule.id}] {rule.rule}")

            elif kind == "example":
                ex = self.store.examples[h.meta["index"]]
                block = f"Q: {ex.question}\nSQL:\n{ex.sql.strip()}"
                if block not in ctx.example_hits:
                    ctx.example_hits.append(block)

        # ---- 命中指标时把基准表也拉进来 ----
        for name in matched_metrics:
            base = (self.store.metric(name) or type("M", (), {"base_table": ""})()).base_table or ""
            for token in base.replace("(", " ").replace(")", " ").split():
                if token.lower().rstrip(",") in self.store.table_names:
                    hit_tables.add(token.rstrip(",").lower())

        # ---- 图上扩展：仅保留"直接命中表之间的路径中间表"，避免无关邻接表混入 ----
        g = get_schema_graph()
        expanded = set(hit_tables)
        if len(hit_tables) >= 2:
            steps = None
            try:
                steps = g.connect(sorted(hit_tables), max_hops=4)
            except ValueError:
                steps = None
            if steps:
                for s_ in steps:
                    expanded.add(s_.left_table)
                    expanded.add(s_.right_table)

        known = set(self.store.table_names)
        ctx.tables = sorted(expanded & known)
        ctx.table_blocks = [_render_table_block(self.store, t) for t in ctx.tables]
        return ctx


@lru_cache
def get_schema_retriever() -> SchemaRetriever:
    return SchemaRetriever()


if __name__ == "__main__":
    r = get_schema_retriever()
    ctx = r.retrieve("红色女装最近退款率为什么上升")
    print(ctx.render())
