# -*- coding: utf-8 -*-
"""方言适配：后端差异说明 + 跨方言 SQL 转译。

【技术栈：sqlglot 方言转译】DuckDB 切 MySQL 时：
    * dialect_note()   注入提示词，告诉模型当前目标数据库的语法差异
    * transpile_sql()  把语义层里的 DuckDB 示例 SQL 转译成目标方言，
                       避免示例误导（如 DATE '...' 字面量在 MySQL 非法）
实测 Golden 值跨 DuckDB/MySQL 完全一致。
"""
from __future__ import annotations

import sqlglot

# database_url scheme -> sqlglot 方言
DIALECT_MAP = {
    "duckdb": "duckdb",
    "mysql": "mysql",
    "postgresql": "postgres",
    "postgres": "postgres",
}

DIALECT_NOTES = {
    "duckdb": "目标数据库：DuckDB（日期字面量用 DATE 'YYYY-MM-DD'，可用 strftime/GROUPING SETS）。",
    "mysql": (
        "目标数据库：MySQL 8。注意方言差异：\n"
        "- 日期直接比较：order_date >= '2026-07-01'（不要写 DATE '2026-07-01'）\n"
        "- 不支持 GROUPING SETS，多级汇总用 GROUP BY ... WITH ROLLUP 或 UNION ALL\n"
        "- 月份格式化：DATE_FORMAT(order_date, '%Y-%m')；不要使用 strftime\n"
        "- ROUND / NULLIF / COUNT(DISTINCT ...) 均可用\n"
        "- 尽量写标准 SQL，避免 EXCEPT/QUALIFY 等非 MySQL 语法"
    ),
    "postgres": (
        "目标数据库：PostgreSQL（日期字面量 DATE '...' 可用，月份用 to_char(date,'YYYY-MM')）。"
    ),
}


def current_dialect() -> str:
    """按 DATABASE_URL 推导 sqlglot 方言（不触发连接）。"""
    from config.config import get_settings

    url = get_settings().database_url
    scheme = url.split(":", 1)[0].split("+", 1)[0].lower()
    return DIALECT_MAP.get(scheme, "duckdb")


def dialect_note(dialect: str | None = None) -> str:
    d = dialect or current_dialect()
    return DIALECT_NOTES.get(d, DIALECT_NOTES["duckdb"])


def transpile_sql(sql: str, target: str | None = None, source: str = "duckdb") -> str:
    """把示例 SQL 从 source 方言转译到 target 方言；失败原样返回。"""
    d = target or current_dialect()
    if d == source:
        return sql
    try:
        return "\n".join(
            sqlglot.transpile(sql, read=source, write=d, pretty=True)
        )
    except Exception:  # noqa: BLE001  转译失败不影响主链路
        return sql
