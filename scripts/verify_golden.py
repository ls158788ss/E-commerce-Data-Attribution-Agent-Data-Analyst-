# -*- coding: utf-8 -*-
"""核对关键业务数字的独立 Golden 值（与 Agent 链路无关的第二口径）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sql.executor import execute_sql  # noqa: E402

CHECKS = {
    "2026年7月销售额": """
        SELECT ROUND(SUM(total_amount), 2) AS v FROM orders
        WHERE order_status IN ('completed','refunded')
          AND order_date >= DATE '2026-07-01' AND order_date < DATE '2026-08-01'
    """,
    "全站最近30天退款率(%)": """
        SELECT ROUND(COUNT(DISTINCT CASE WHEN r.refund_id IS NOT NULL THEN o.order_id END) * 100.0
                     / NULLIF(COUNT(DISTINCT o.order_id), 0), 2) AS v
        FROM orders o LEFT JOIN refunds r ON o.order_id = r.order_id
        WHERE o.order_status IN ('completed','refunded')
          AND o.order_date >= DATE '2026-07-27' AND o.order_date <= DATE '2026-08-25'
    """,
    "红色女装最近30天退款率(%)": """
        SELECT ROUND(COUNT(DISTINCT CASE WHEN r.refund_id IS NOT NULL THEN o.order_id END) * 100.0
                     / NULLIF(COUNT(DISTINCT o.order_id), 0), 2) AS v
        FROM orders o
        JOIN order_items oi ON o.order_id = oi.order_id
        JOIN products p ON oi.product_id = p.product_id
        LEFT JOIN refunds r ON r.order_item_id = oi.order_item_id
        WHERE p.category_name='女装' AND p.color='红色'
          AND o.order_status IN ('completed','refunded')
          AND o.order_date >= DATE '2026-07-27' AND o.order_date <= DATE '2026-08-25'
    """,
    "华东女装销售额(全部时间)": """
        SELECT ROUND(SUM(o.total_amount), 2) AS v
        FROM orders o
        WHERE o.order_status IN ('completed','refunded')
          AND o.region = '华东'
          AND o.order_id IN (
              SELECT oi.order_id FROM order_items oi
              JOIN products p ON oi.product_id = p.product_id
              WHERE p.category_name = '女装')
    """,
}

if __name__ == "__main__":
    for name, sql in CHECKS.items():
        r = execute_sql(sql)
        print(f"{name}: {r.rows[0]['v']}")
