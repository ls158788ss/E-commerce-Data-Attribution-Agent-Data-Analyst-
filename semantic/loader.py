# -*- coding: utf-8 -*-
"""语义层加载器：YAML -> 强类型 Pydantic 模型 -> SemanticStore 单例。

SemanticStore 是整个系统的"业务大脑"数据源：
    检索层用它构建索引，SQL Agent 用它注入口径。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field

YAML_DIR = Path(__file__).resolve().parent / "yaml"


# ---------------------------------------------------------------
# Pydantic 模型
# ---------------------------------------------------------------
class MetricDef(BaseModel):
    name: str
    business_name: str
    definition: str = ""
    sql_template: str = ""
    base_table: str = ""
    required_filters: list[str] = Field(default_factory=list)
    time_dimension: str = ""
    valid_filters: list[str] = Field(default_factory=list)
    unit: str = ""
    warning: str = ""
    same_as: str = ""  # 别名指标指向真实指标


class GlossaryTerm(BaseModel):
    term: str
    type: str = ""
    definition: str = ""
    values: list[str] = Field(default_factory=list)
    maps_to: str = ""


class ColumnDef(BaseModel):
    column: str
    business_name: str = ""
    description: str = ""
    examples: list[str] = Field(default_factory=list)


class TableDef(BaseModel):
    table: str
    description: str = ""
    columns: list[ColumnDef] = Field(default_factory=list)

    @property
    def column_names(self) -> list[str]:
        return [c.column for c in self.columns]


class JoinEdge(BaseModel):
    from_table: str = Field(alias="from")
    to_table: str = Field(alias="to")
    condition: str
    cardinality: str = ""
    note: str = ""

    model_config = {"populate_by_name": True}


class BusinessRule(BaseModel):
    id: str
    scope: list[str] = Field(default_factory=list)
    rule: str


class SqlExample(BaseModel):
    question: str
    sql: str


# ---------------------------------------------------------------
# Store
# ---------------------------------------------------------------
@dataclass
class SemanticStore:
    metrics: list[MetricDef] = field(default_factory=list)
    glossary: list[GlossaryTerm] = field(default_factory=list)
    tables: list[TableDef] = field(default_factory=list)
    joins: list[JoinEdge] = field(default_factory=list)
    rules: list[BusinessRule] = field(default_factory=list)
    examples: list[SqlExample] = field(default_factory=list)

    # ---- 便捷查询 ----
    def metric(self, name_or_alias: str) -> Optional[MetricDef]:
        low = name_or_alias.lower()
        for m in self.metrics:
            if m.name == low or m.business_name == name_or_alias:
                return self._resolve(m)
        return None

    def _resolve(self, m: MetricDef) -> MetricDef:
        if m.same_as:
            target = next((x for x in self.metrics if x.name == m.same_as), None)
            return target or m
        return m

    def table(self, name: str) -> Optional[TableDef]:
        return next((t for t in self.tables if t.table == name), None)

    @property
    def table_names(self) -> list[str]:
        return [t.table for t in self.tables]

    # ---- 渲染为提示词片段 ----
    def render_metric(self, m: MetricDef) -> str:
        parts = [f"指标 {m.business_name}({m.name}): {m.definition}"]
        if m.sql_template:
            parts.append(f"标准算法: {m.sql_template.strip()}")
        if m.base_table:
            parts.append(f"基准表: {m.base_table}")
        for f in m.required_filters:
            parts.append(f"必须过滤: {f}")
        if m.time_dimension and m.time_dimension != "无（需先按客户聚合）":
            parts.append(f"时间字段: {m.time_dimension}")
        if m.warning:
            parts.append(f"⚠️ {m.warning}")
        return "\n".join(parts)


def _load_yaml(name: str) -> dict | list:
    with open(YAML_DIR / name, encoding="utf-8") as f:
        return yaml.safe_load(f)


@lru_cache
def load_semantic_store() -> SemanticStore:
    metrics_raw = _load_yaml("metrics.yaml")
    metrics = []
    for item in metrics_raw:
        base = {k: v for k, v in item.items()}
        alias = base.pop("same_as", "")
        m = MetricDef(**base, same_as=alias or "")
        metrics.append(m)

    glossary = [GlossaryTerm(**t) for t in _load_yaml("glossary.yaml")]

    tables_raw = _load_yaml("tables.yaml")["tables"]
    tables = [TableDef(**t) for t in tables_raw]

    joins_raw = _load_yaml("joins.yaml")["edges"]
    joins = [JoinEdge(**j) for j in joins_raw]

    rules_raw = _load_yaml("business_rules.yaml")
    rules = [BusinessRule(**r) for r in rules_raw["rules"]]
    examples = [SqlExample(**e) for e in rules_raw["verified_sql_examples"]]

    return SemanticStore(
        metrics=metrics,
        glossary=glossary,
        tables=tables,
        joins=joins,
        rules=rules,
        examples=examples,
    )


if __name__ == "__main__":
    store = load_semantic_store()
    print(f"指标 {len(store.metrics)} 个 | 术语 {len(store.glossary)} 条 | "
          f"表 {len(store.tables)} 张 | Join 边 {len(store.joins)} 条 | "
          f"规则 {len(store.rules)} 条 | 示例 SQL {len(store.examples)} 条")
    rr = store.metric("退款率")
    assert rr is not None
    print("\n--- 退款率口径 ---")
    print(store.render_metric(rr))
