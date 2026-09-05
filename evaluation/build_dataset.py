# -*- coding: utf-8 -*-
"""构建评测数据集 evaluation/dataset.jsonl。

每条记录：
    id            题号
    level         类别（simple/multi_table/join/aggregation/nested/
                  time_compare/ranking/ambiguous/business_metric/multi_hop）
    question      自然语言业务问题
    golden_sql    标准答案 SQL（评测时在库上实时执行得到 Golden 结果）
    golden_tables 该题必须涉及的表（schema recall/precision/join accuracy 用）
    metric        关联语义层指标名（metric accuracy 用，可空）
    scoring       execution=结果集精确比对 | lenient=宽松评分(开放题)

用法：
    python -m evaluation.build_dataset
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

PAID = "o.order_status IN ('completed','refunded')"

Q = []


def add(level, question, sql, tables, metric=None, scoring="execution", **extra):
    Q.append({
        "level": level, "question": question,
        "golden_sql": " ".join(sql.split()),
        "golden_tables": tables, "metric": metric, "scoring": scoring,
        "id": f"Q{len(Q) + 1:03d}",
        **extra,
    })


# ==================== Level 1：简单问数 ====================
add("simple", "2026年7月销售额是多少？", f"""
    SELECT ROUND(SUM(total_amount),2) AS sales_amount FROM orders o
    WHERE {PAID} AND o.order_date>=DATE '2026-07-01' AND o.order_date<DATE '2026-08-01'""",
    ["orders"], "sales_amount")

add("simple", "最近30天一共有多少笔支付订单？", f"""
    SELECT COUNT(DISTINCT order_id) AS order_count FROM orders o
    WHERE {PAID} AND o.order_date>=DATE '2026-07-27' AND o.order_date<=DATE '2026-08-25'""",
    ["orders"], "order_count")

add("simple", "2026年6月的退款总金额是多少？", """
    SELECT ROUND(SUM(refund_amount),2) AS refund_amount FROM refunds r
    WHERE r.refund_date>=DATE '2026-06-01' AND r.refund_date<DATE '2026-07-01'""",
    ["refunds"], "refund_amount")

add("simple", "现在一共有多少个SKU？", "SELECT COUNT(*) AS sku_count FROM products",
    ["products"])

add("simple", "注册客户总数是多少？", "SELECT COUNT(*) AS customer_count FROM customers",
    ["customers"])

# ==================== 多表基础 ====================
add("multi_table", "每个渠道的支付订单量是多少？", f"""
    SELECT c.channel_name, COUNT(DISTINCT o.order_id) AS order_count
    FROM orders o JOIN marketing_channels c ON o.channel_id=c.channel_id
    WHERE {PAID} GROUP BY c.channel_name ORDER BY order_count DESC""",
    ["orders", "marketing_channels"], "order_count",
    compare={"ignore_non_numeric_cols": True})

add("multi_table", "女装商品的销量是多少？", f"""
    SELECT SUM(oi.quantity) AS sell_quantity FROM order_items oi
    JOIN products p ON oi.product_id=p.product_id
    JOIN orders o ON oi.order_id=o.order_id
    WHERE {PAID} AND p.category_name='女装'""",
    ["order_items", "products", "orders"], "sell_quantity")

add("multi_table", "供应商02号供应了多少个SKU？", """
    SELECT COUNT(*) AS sku_count FROM products p
    JOIN suppliers s ON p.supplier_id=s.supplier_id
    WHERE s.supplier_name='供应商02号'""",
    ["products", "suppliers"])

add("multi_table", "支付宝收到的总金额是多少？", """
    SELECT ROUND(SUM(amount),2) AS total_amount FROM payments
    WHERE payment_method='支付宝'""",
    ["payments"])

# ==================== 多表 Join ====================
add("join", "华东地区女装的销售额是多少？", f"""
    SELECT ROUND(SUM(oi.subtotal),2) AS sales_amount FROM orders o
    JOIN order_items oi ON o.order_id=oi.order_id
    JOIN products p ON oi.product_id=p.product_id
    WHERE {PAID} AND o.region='华东' AND p.category_name='女装'""",
    ["orders", "order_items", "products"], "sales_amount")

add("join", "红色商品的退款总金额是多少？", """
    SELECT ROUND(SUM(r.refund_amount),2) AS refund_amount FROM refunds r
    JOIN products p ON r.product_id=p.product_id
    WHERE p.color='红色'""",
    ["refunds", "products"], "refund_amount")

add("join", "XL尺码商品的整体退款率是多少？", f"""
    SELECT ROUND(COUNT(DISTINCT CASE WHEN r.refund_id IS NOT NULL THEN o.order_id END)*100.0
                 /NULLIF(COUNT(DISTINCT o.order_id),0),2) AS refund_rate_pct
    FROM orders o
    JOIN order_items oi ON o.order_id=oi.order_id
    JOIN products p ON oi.product_id=p.product_id
    LEFT JOIN refunds r ON r.order_item_id=oi.order_item_id
    WHERE {PAID} AND p.size='XL'""",
    ["orders", "order_items", "products", "refunds"], "refund_rate")

add("join", "抖音直播渠道的销售额是多少？", f"""
    SELECT ROUND(SUM(o.total_amount),2) AS sales_amount FROM orders o
    JOIN marketing_channels c ON o.channel_id=c.channel_id
    WHERE {PAID} AND c.channel_name='抖音直播'""",
    ["orders", "marketing_channels"], "sales_amount")

add("join", "华东仓的在途库存总量是多少？", """
    SELECT SUM(stock_quantity) AS stock_total FROM inventory
    WHERE warehouse_region='华东'""",
    ["inventory"])

# ==================== 聚合分析 ====================
# 口径：按类目等商品属性拆解销售额时用 order_items.subtotal（订单总额会跨类目重复计入）
add("aggregation", "每个类目的销售额是多少？按销售额从高到低排。", f"""
    SELECT p.category_name, ROUND(SUM(oi.subtotal),2) AS sales_amount
    FROM order_items oi
    JOIN products p ON oi.product_id=p.product_id
    JOIN orders o ON oi.order_id=o.order_id
    WHERE {PAID}
    GROUP BY p.category_name ORDER BY sales_amount DESC""",
    ["orders", "order_items", "products"], "sales_amount")

add("aggregation", "2026年6月到8月每个月的销售额趋势。", f"""
    SELECT strftime(order_date,'%Y-%m') AS month, ROUND(SUM(total_amount),2) AS sales_amount
    FROM orders o
    WHERE {PAID} AND o.order_date>=DATE '2026-06-01' AND o.order_date<DATE '2026-09-01'
    GROUP BY month ORDER BY month""",
    ["orders"], "sales_amount", compare={"date_grain": "month"})

add("aggregation", "各大区的客单价分别是多少？", f"""
    SELECT region, ROUND(SUM(total_amount)*1.0/NULLIF(COUNT(DISTINCT order_id),0),2) AS aov
    FROM orders o WHERE {PAID} GROUP BY region ORDER BY aov DESC""",
    ["orders"], "aov")

add("aggregation", "各种退款原因的单数分布。", """
    SELECT refund_reason, COUNT(DISTINCT order_id) AS refund_orders
    FROM refunds GROUP BY refund_reason ORDER BY refund_orders DESC""",
    ["refunds"])

add("aggregation", "每个供应商的商品平均定价是多少？", """
    SELECT s.supplier_name, ROUND(AVG(p.price),2) AS avg_price
    FROM products p JOIN suppliers s ON p.supplier_id=s.supplier_id
    GROUP BY s.supplier_name ORDER BY avg_price DESC""",
    ["products", "suppliers"])

# ==================== 嵌套查询 ====================
add("nested_query", "买过红色商品的客户有多少个？", f"""
    SELECT COUNT(DISTINCT o.customer_id) AS customer_count FROM orders o
    JOIN order_items oi ON o.order_id=oi.order_id
    JOIN products p ON oi.product_id=p.product_id
    WHERE {PAID} AND p.color='红色'""",
    ["orders", "order_items", "products"])

add("nested_query", "销售额最高的类目叫什么？", f"""
    SELECT category_name FROM (
        SELECT p.category_name, SUM(oi.subtotal) AS amt
        FROM order_items oi
        JOIN products p ON oi.product_id=p.product_id
        JOIN orders o ON oi.order_id=o.order_id
        WHERE {PAID} GROUP BY p.category_name
    ) t ORDER BY amt DESC LIMIT 1""",
    ["orders", "order_items", "products"], "sales_amount")

add("nested_query", "哪些大区的销售额超过了整体平均水平？", f"""
    SELECT region, SUM(total_amount) AS amt FROM orders o
    WHERE {PAID} GROUP BY region
    HAVING SUM(total_amount) > (SELECT AVG(amt) FROM (
        SELECT SUM(total_amount) AS amt FROM orders WHERE order_status IN ('completed','refunded')
        GROUP BY region))""",
    ["orders"], "sales_amount")

# ==================== 时间对比 ====================
add("time_compare", "2026年7月和8月的销售额各是多少？", f"""
    SELECT strftime(order_date,'%Y-%m') AS month, ROUND(SUM(total_amount),2) AS sales_amount
    FROM orders o WHERE {PAID}
      AND ((o.order_date>=DATE '2026-07-01' AND o.order_date<DATE '2026-08-01')
        OR (o.order_date>=DATE '2026-08-01' AND o.order_date<DATE '2026-09-01'))
    GROUP BY month ORDER BY month""",
    ["orders"], "sales_amount")

add("time_compare", "最近30天的退款率和再往前30天比是多少？", f"""
    SELECT CASE WHEN o.order_date>=DATE '2026-07-27' THEN 'recent_30d' ELSE 'prior_30d' END AS period,
           ROUND(COUNT(DISTINCT CASE WHEN r.refund_id IS NOT NULL THEN o.order_id END)*100.0
                 /NULLIF(COUNT(DISTINCT o.order_id),0),2) AS refund_rate_pct
    FROM orders o LEFT JOIN refunds r ON o.order_id=r.order_id
    WHERE {PAID}
      AND o.order_date>=DATE '2026-06-27' AND o.order_date<=DATE '2026-08-25'
    GROUP BY period ORDER BY period""",
    ["orders", "refunds"], "refund_rate",
    compare={"date_grain": None, "ignore_non_numeric_cols": True})

add("time_compare", "2026年第二季度（4-6月）的销售额是多少？", f"""
    SELECT ROUND(SUM(total_amount),2) AS sales_amount FROM orders o
    WHERE {PAID} AND o.order_date>=DATE '2026-04-01' AND o.order_date<DATE '2026-07-01'""",
    ["orders"], "sales_amount")

# ==================== 排名 ====================
add("ranking", "销量最高的10个商品是什么？", f"""
    SELECT p.product_name, SUM(oi.quantity) AS total_qty FROM order_items oi
    JOIN products p ON oi.product_id=p.product_id
    JOIN orders o ON oi.order_id=o.order_id
    WHERE {PAID}
    GROUP BY p.product_name ORDER BY total_qty DESC LIMIT 10""",
    ["order_items", "products", "orders"], "sell_quantity")

add("ranking", "退款金额最高的10个商品是什么？", """
    SELECT p.product_name, ROUND(SUM(r.refund_amount),2) AS refund_amount
    FROM refunds r JOIN products p ON r.product_id=p.product_id
    GROUP BY p.product_name ORDER BY refund_amount DESC LIMIT 10""",
    ["refunds", "products"], "refund_amount")

add("ranking", "客单价最高的5个大区是哪些？", f"""
    SELECT region, ROUND(SUM(total_amount)*1.0/COUNT(DISTINCT order_id),2) AS aov
    FROM orders o WHERE {PAID}
    GROUP BY region ORDER BY aov DESC LIMIT 5""",
    ["orders"], "aov")

add("ranking", "按注册客户数排名前3的获客渠道是哪些？", """
    SELECT c.channel_name, COUNT(*) AS customer_count
    FROM customers cu JOIN marketing_channels c ON cu.channel_id=c.channel_id
    GROUP BY c.channel_name ORDER BY customer_count DESC LIMIT 3""",
    ["customers", "marketing_channels"])

# ==================== 口径敏感（业务指标）====================
add("business_metric", "上个月（2026年7月）的退款率是多少？", f"""
    SELECT ROUND(COUNT(DISTINCT CASE WHEN r.refund_id IS NOT NULL THEN o.order_id END)*100.0
                 /NULLIF(COUNT(DISTINCT o.order_id),0),2) AS refund_rate_pct
    FROM orders o LEFT JOIN refunds r ON o.order_id=r.order_id
    WHERE {PAID} AND o.order_date>=DATE '2026-07-01' AND o.order_date<DATE '2026-08-01'""",
    ["orders", "refunds"], "refund_rate")

add("business_metric", "2026年7月的GMV是多少？", f"""
    SELECT ROUND(SUM(total_amount),2) AS gmv FROM orders o
    WHERE {PAID} AND o.order_date>=DATE '2026-07-01' AND o.order_date<DATE '2026-08-01'""",
    ["orders"], "gmv")

add("business_metric", "2026年7月的客单价是多少？", f"""
    SELECT ROUND(SUM(total_amount)*1.0/NULLIF(COUNT(DISTINCT order_id),0),2) AS aov
    FROM orders o WHERE {PAID} AND o.order_date>=DATE '2026-07-01' AND o.order_date<DATE '2026-08-01'""",
    ["orders"], "aov")

add("business_metric", "最近30天的复购率是多少？", f"""
    SELECT ROUND(SUM(CASE WHEN cnt>=2 THEN 1 ELSE 0 END)*100.0/NULLIF(COUNT(*),0),2) AS repurchase_rate_pct
    FROM (SELECT customer_id, COUNT(DISTINCT order_id) AS cnt FROM orders
          WHERE order_status IN ('completed','refunded')
            AND order_date>=DATE '2026-07-27' AND order_date<=DATE '2026-08-25'
          GROUP BY customer_id) t""",
    ["orders"], "repurchase_rate")

add("business_metric", "红色女装最近30天的退款率是多少？", f"""
    SELECT ROUND(COUNT(DISTINCT CASE WHEN r.refund_id IS NOT NULL THEN o.order_id END)*100.0
                 /NULLIF(COUNT(DISTINCT o.order_id),0),2) AS refund_rate_pct
    FROM orders o
    JOIN order_items oi ON o.order_id=oi.order_id
    JOIN products p ON oi.product_id=p.product_id
    LEFT JOIN refunds r ON r.order_item_id=oi.order_item_id
    WHERE {PAID} AND p.category_name='女装' AND p.color='红色'
      AND o.order_date>=DATE '2026-07-27' AND o.order_date<=DATE '2026-08-25'""",
    ["orders", "order_items", "products", "refunds"], "refund_rate")

# ==================== 开放题（宽松评分）====================
add("ambiguous", "女装卖得怎么样？",
    "SELECT 1", ["orders", "order_items", "products"], scoring="lenient")
add("ambiguous", "最近哪个颜色的商品卖得比较好？",
    "SELECT 1", ["orders", "order_items", "products"], scoring="lenient")
add("multi_hop", "红色女装最近退款率为什么上升？",
    "SELECT 1", ["orders", "order_items", "products", "refunds"],
    metric="refund_rate", scoring="lenient")
add("multi_hop", "过去30天整体的退款异常主要是什么原因导致的？",
    "SELECT 1", ["orders", "refunds"], metric="refund_rate", scoring="lenient")


def main() -> None:
    out = HERE / "dataset.jsonl"
    with open(out, "w", encoding="utf-8") as f:
        for q in Q:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")
    # 按 level 统计
    from collections import Counter

    stat = Counter(q["level"] for q in Q)
    print(f"✅ 已生成 {out.name}: {len(Q)} 题")
    for k, v in stat.items():
        print(f"   {k}: {v}")


if __name__ == "__main__":
    main()
