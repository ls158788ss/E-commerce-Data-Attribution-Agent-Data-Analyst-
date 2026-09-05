# -*- coding: utf-8 -*-
"""SQL 校验器（沙盒第一道闸）：sqlglot AST 静态检查。

【技术栈：sqlglot】把 SQL 解析成语法树（AST）做白名单/黑名单静态校验，
比正则可靠；同时承担方言感知解析（duckdb/mysql，见 dialects 参数）。

检查链：
    解析失败 -> 拒绝
    语句类型 -> 仅允许 SELECT / UNION / EXCEPT / INTERSECT
    危险节点 -> INSERT/UPDATE/DELETE/DROP/CREATE/ALTER/TRUNCATE/COPY/ATTACH/PRAGMA 等一律拒绝
    JOIN 数量 -> 上限限制（防止笛卡尔积爆炸）
    LIMIT    -> 缺失则强制补上；超出上限则钳制

S3 将追加：函数黑名单、扫描量估算、审计日志。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from config.config import get_settings

# 根节点白名单：合法的只读查询形态
_ALLOWED_ROOTS = (exp.Select, exp.Union, exp.Except, exp.Intersect)

# 全树黑名单：出现即拒绝（含子查询内夹带写操作的防御）
# 注：sqlglot>=25 中 AlterTable 已更名为 Alter；Command 兜住 PRAGMA/VACUUM 等裸命令
_FORBIDDEN_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge,
    exp.Create, exp.Drop, exp.Alter, exp.TruncateTable,
    exp.Copy, exp.Attach, exp.Detach, exp.Command, exp.Set, exp.Use,
)


@dataclass
class ValidationResult:
    ok: bool = False             # 默认失败：任何检查不通过都要显式置 True
    errors: list[str] = field(default_factory=list)
    final_sql: str = ""          # 校验通过后的规范化 SQL（已强制 LIMIT）
    original_sql: str = ""

    @property
    def error_message(self) -> str:
        return "；".join(self.errors)


def validate_sql(sql: str, dialect: str | None = None) -> ValidationResult:
    """静态校验并规范化 SQL（方言感知）。不通过时 ok=False 并给出可回灌给 LLM 的错误说明。"""
    from sql.executor import current_dialect

    s = get_settings()
    read_dialect = dialect or current_dialect()
    res = ValidationResult(original_sql=sql)
    if not sql or not sql.strip():
        res.errors.append("SQL 为空")
        return res

    try:
        # RAISE：语法错误直接抛 ParseError（默认 WARN 会宽容恢复，放过烂 SQL）
        parsed = sqlglot.parse_one(sql, read=read_dialect, error_level=sqlglot.ErrorLevel.RAISE)
    except ParseError as e:
        res.errors.append(f"SQL 语法解析失败: {e}")
        return res

    root = parsed
    while isinstance(root, exp.Subquery):  # (SELECT ...) 包一层的情况
        root = root.this

    if not isinstance(root, _ALLOWED_ROOTS):
        res.errors.append("仅允许只读查询（SELECT/WITH ... SELECT）")
        return res

    for node in root.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            res.errors.append(f"包含被禁止的操作: {node.key.upper()}")
            return res

    # ---- 文件访问防御：DuckDB 允许 FROM 'x.csv' 直读本地文件，一律拒绝 ----
    for t in root.find_all(exp.Table):
        this = t.this
        if this is None or getattr(this, "is_string", False) or isinstance(this, exp.Literal):
            res.errors.append("禁止访问文件路径（只允许查询库内表）")
            return res
        if isinstance(this, exp.Identifier) and any(
            ext in (this.name or "").lower() for ext in (".csv", ".parquet", ".json", ".xlsx", ".db")
        ):
            res.errors.append("禁止访问文件路径（只允许查询库内表）")
            return res
    for lit in root.find_all(exp.Literal):
        if getattr(lit, "is_string", False):
            val = (lit.name or "").lower()
            if val.endswith((".csv", ".parquet", ".json", ".xlsx", ".db")):
                res.errors.append("SQL 中出现文件路径字符串，已拦截")
                return res

    join_count = sum(1 for n in root.find_all(exp.Join))
    if join_count > s.sql_max_joins:
        res.errors.append(f"JOIN 数量({join_count})超过上限({s.sql_max_joins})")
        return res

    # ---- 强制 LIMIT：缺失则补上；超出上限则钳制 ----
    need_limit = True
    limit_node = root.args.get("limit")
    if limit_node is not None:
        try:
            existing = int(limit_node.expression.name)
            need_limit = existing > s.sql_max_rows
        except (ValueError, AttributeError):
            need_limit = True
    if need_limit:
        root.set(
            "limit",
            exp.Limit(expression=exp.Literal.number(s.sql_max_rows)),
        )

    res.ok = True
    res.final_sql = root.sql(dialect=read_dialect, pretty=True)
    return res
