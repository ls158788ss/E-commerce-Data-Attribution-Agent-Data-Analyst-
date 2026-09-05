# -*- coding: utf-8 -*-
"""Schema / Join Graph：把外键关系建成无向图，用 BFS 搜索 Join 路径。

Text2SQL 最容易翻车的多表 Join 不交给 LLM 猜，
而是由图搜索给出确定性的路径，再渲染成 JOIN 子句骨架。

    find_join_path(source_tables=["refunds"], target_tables=["products"])
    -> refunds -> order_items -> products （含 ON 条件）
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from functools import lru_cache

from semantic.loader import JoinEdge, SemanticStore, load_semantic_store


@dataclass(frozen=True)
class JoinStep:
    left_table: str
    right_table: str
    condition: str
    cardinality: str = ""

    def render(self) -> str:
        return f"{self.right_table} ON {self.condition}"


class SchemaGraph:
    """tables 为节点、外键为边的无向图。"""

    def __init__(self, store: SemanticStore | None = None) -> None:
        self.store = store or load_semantic_store()
        self._edges: dict[str, list[tuple[str, JoinEdge]]] = {}
        for e in self.store.joins:
            self._edges.setdefault(e.from_table, []).append((e.to_table, e))
            self._edges.setdefault(e.to_table, []).append((e.from_table, _reverse(e)))

    def neighbors(self, table: str) -> list[tuple[str, JoinEdge]]:
        return self._edges.get(table, [])

    def all_tables(self) -> list[str]:
        return list(self._edges.keys())

    # ------------------------------------------------------------------
    def shortest_path(self, source: str, target: str, *, max_hops: int = 4) -> list[JoinStep] | None:
        """单源最短路（BFS），返回边序列；不可达返回 None。"""
        if source == target:
            return []
        queue: deque[tuple[str, list[JoinStep]]] = deque([(source, [])])
        visited = {source}
        while queue:
            node, path = queue.popleft()
            if len(path) >= max_hops:
                continue
            for nxt, edge in self.neighbors(node):
                if nxt in visited:
                    continue
                step = JoinStep(left_table=node, right_table=nxt,
                                condition=edge.condition, cardinality=edge.cardinality)
                new_path = path + [step]
                if nxt == target:
                    return new_path
                visited.add(nxt)
                queue.append((nxt, new_path))
        return None

    def connect(self, tables: list[str], *, max_hops: int = 4) -> list[JoinStep] | None:
        """把若干张表连通成一棵 JOIN 树（Steiner 树的贪心近似：逐表接入已连通集合）。"""
        if not tables:
            return None
        connected = {tables[0]}
        steps: list[JoinStep] = []
        rest = [t for t in tables[1:] if t not in connected]
        while rest:
            best: tuple[int, str, list[JoinStep]] | None = None
            for cand in rest:
                for anchor in connected:
                    p = self.shortest_path(anchor, cand, max_hops=max_hops)
                    if p is not None and (best is None or len(p) < len(best[2])):
                        best = (len(p), cand, p)
            if best is None:
                return None  # 存在不可达表
            _, picked, path = best
            steps.extend(path)
            connected.add(picked)
            rest.remove(picked)
        # 去重（同一对表可能被两条路径经过）
        seen: set[tuple[str, str]] = set()
        unique_steps = []
        for s in steps:
            key = frozenset((s.left_table, s.right_table))
            if key in seen:
                continue
            seen.add(key)
            unique_steps.append(s)
        return unique_steps


def _reverse(e: JoinEdge) -> JoinEdge:
    """反向边：交换左右表并把条件里的表顺序对调。"""
    lt, rt = e.from_table, e.to_table
    cond = e.condition
    cond_rev = cond.replace(lt, "\x00").replace(rt, lt).replace("\x00", rt)
    return JoinEdge(from_table=e.to_table, to_table=e.from_table,
                    condition=cond_rev, cardinality=e.cardinality, note=e.note)


@lru_cache
def get_schema_graph() -> SchemaGraph:
    return SchemaGraph()


def find_join_path(
    source_tables: list[str],
    target_tables: list[str],
    *,
    max_hops: int = 4,
) -> list[JoinStep] | None:
    """Agent 工具入口：给定查询涉及的表，返回把它们连起来的 JOIN 步骤。"""
    g = get_schema_graph()
    unknown = [t for t in source_tables + target_tables
               if t not in g.all_tables()]
    if unknown:
        raise ValueError(f"未知表名: {unknown}；可用表: {g.all_tables()}")
    return g.connect(list(dict.fromkeys(source_tables + target_tables)), max_hops=max_hops)


if __name__ == "__main__":
    path = find_join_path(["refunds"], ["products", "categories"])
    assert path is not None
    print("refunds → products/categories 的 Join 路径:")
    for i, step in enumerate(path, 1):
        print(f"  {i}. {step.render()}   [{step.left_table} → {step.right_table}]")
