# -*- coding: utf-8 -*-
"""把 DuckDB 模拟数据灌入 MySQL，验证「生产数据库可切换」。

【技术栈：MySQL + SQLAlchemy + DuckDB】DuckDB 读源数据 -> pandas 中转 ->
SQLAlchemy to_sql 写入 MySQL，验证多数据库后端切换的方言适配全链路。

前置：
    1. .env 中配置 MYSQL_HOST/PORT/USER/PASSWORD/DATABASE
    2. 目标库需存在（脚本会自动 CREATE DATABASE IF NOT EXISTS）

安全建议（生产实践口径）：
    为 Agent 创建只读账号，仅授予 SELECT 权限——
        CREATE USER 'data_agent_ro'@'%' IDENTIFIED BY '<pwd>';
        GRANT SELECT ON data_agent.* TO 'data_agent_ro'@'%';
    然后在 .env 里把 DATABASE_URL 指向该只读账号。

用法：
    python scripts/load_to_mysql.py            # 加载全部 10 张表
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from urllib.parse import quote_plus

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import duckdb  # noqa: E402
import pandas as pd  # noqa: E402
import sqlalchemy as sa  # noqa: E402

from config.config import get_settings  # noqa: E402

TABLES = [
    "categories", "products", "suppliers", "customers", "marketing_channels",
    "orders", "order_items", "payments", "refunds", "inventory",
]

# 生产实践：分析查询在行存引擎上必须有 JOIN/过滤列索引支撑
# （DuckDB 列存+统计信息可以裸跑，MySQL 不行——这是后端切换的真实差异点）
INDEX_DDL = [
    "ALTER TABLE orders ADD INDEX idx_o_date (order_date)",
    "ALTER TABLE orders ADD INDEX idx_o_region (region)",
    "ALTER TABLE orders ADD INDEX idx_o_channel (channel_id)",
    "ALTER TABLE order_items ADD INDEX idx_oi_order (order_id)",
    "ALTER TABLE order_items ADD INDEX idx_oi_product (product_id)",
    "ALTER TABLE refunds ADD INDEX idx_r_order (order_id)",
    "ALTER TABLE refunds ADD INDEX idx_r_item (order_item_id)",
    "ALTER TABLE refunds ADD INDEX idx_r_product (product_id)",
    "ALTER TABLE refunds ADD INDEX idx_r_reason (refund_reason)",
    "ALTER TABLE payments ADD INDEX idx_pay_order (order_id)",
    "ALTER TABLE products ADD INDEX idx_p_cat (category_name)",
    "ALTER TABLE products ADD INDEX idx_p_color (color)",
    "ALTER TABLE customers ADD INDEX idx_c_channel (channel_id)",
]


def add_indexes(db_eng: sa.Engine) -> None:
    with db_eng.connect() as conn:
        for ddl in INDEX_DDL:
            try:
                conn.execute(sa.text(ddl))
            except sa.exc.ProgrammingError:
                pass  # 索引已存在
    print(f"✅ 索引就绪（{len(INDEX_DDL)} 条）")


def server_engine() -> sa.Engine:
    s = get_settings()
    if not (s.mysql_host and s.mysql_user):
        raise SystemExit("请先在 .env 配置 MYSQL_HOST / MYSQL_USER / MYSQL_PASSWORD / MYSQL_DATABASE")
    pwd = s.mysql_password

    url = (
        f"mysql+pymysql://{s.mysql_user}:{quote_plus(pwd)}"
        f"@{s.mysql_host}:{s.mysql_port or 3306}/?charset=utf8mb4"
    )
    return sa.create_engine(url, isolation_level="AUTOCOMMIT")


def mysql_dtypes(df: pd.DataFrame) -> dict:
    """显式指定 MySQL 列类型：字符串用 VARCHAR（TEXT 无法建索引）。"""
    dtypes = {}
    for c in df.columns:
        t = str(df[c].dtype)
        if t.startswith("datetime"):
            dtypes[c] = sa.types.DATETIME
        elif t.startswith("int"):
            dtypes[c] = sa.types.BIGINT
        elif t.startswith("float"):
            dtypes[c] = sa.types.DOUBLE
        else:
            dtypes[c] = sa.types.VARCHAR(255)
    return dtypes


def main() -> None:
    s = get_settings()
    db_name = s.mysql_database or "data_agent"
    eng = server_engine()

    with eng.connect() as conn:
        conn.execute(sa.text(
            f"CREATE DATABASE IF NOT EXISTS `{db_name}` "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        ))
    print(f"✅ 数据库就绪: {db_name}")

    db_url = (
        f"mysql+pymysql://{s.mysql_user}:{quote_plus(s.mysql_password)}"
        f"@{s.mysql_host}:{s.mysql_port or 3306}/{db_name}?charset=utf8mb4"
    )
    db_eng = sa.create_engine(db_url, isolation_level="AUTOCOMMIT")

    duck = duckdb.connect(str(PROJECT_ROOT / "data" / "ecommerce.duckdb"), read_only=True)

    total_t0 = time.perf_counter()
    for table in TABLES:
        t0 = time.perf_counter()
        df = duck.execute(f"SELECT * FROM {table}").fetchdf()
        for c in df.columns:
            if str(df[c].dtype).startswith("datetime"):
                df[c] = pd.to_datetime(df[c])
        df.to_sql(table, db_eng, if_exists="replace", index=False,
                  chunksize=5000, dtype=mysql_dtypes(df))
        print(f"  {table:<20} {len(df):>7} 行  ({time.perf_counter() - t0:.1f}s)")

    print(f"\n✅ 全部完成，耗时 {time.perf_counter() - total_t0:.1f}s")
    add_indexes(db_eng)
    print("\n下一步：在 .env 中把 DATABASE_URL 切换为")
    print(f"  mysql+pymysql://{s.mysql_user}:****@{s.mysql_host}:{s.mysql_port or 3306}/{db_name}?charset=utf8mb4")
    print("然后运行 python ask.py \"2026年7月销售额是多少？\" 验证 MySQL 后端")


if __name__ == "__main__":
    main()
