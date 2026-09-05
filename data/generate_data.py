# -*- coding: utf-8 -*-
"""电商模拟数据生成器 —— 智能问数 Demo 数据源。

【技术栈：DuckDB】生成 10 张表写入本地 DuckDB 文件（data/ecommerce.duckdb），
Agent 侧以 read_only=True 打开，零配置且物理只读。

并在数据中"埋入"一个真实的业务异常故事：

    红色女装（连衣裙）中 3 个 XL/XXL 尺码的 SKU，
    在最近 30 天退款率从基线 ~8% 爬升到 ~15%，
    且退款原因 62% 集中在「尺码不合适」。

其余维度（地区/渠道/其他颜色/其他尺码）保持正常，
保证 Agent 下钻链路（类目 → SKU → 尺码/原因）每一跳都有真信号。

用法：
    python data/generate_data.py
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

# ---------------------------------------------------------------
# 可复现性：固定随机种子；日期锚定今天，保证"最近30天"永远有数据
# ---------------------------------------------------------------
SEED = 42
rng = np.random.default_rng(SEED)

TODAY = pd.Timestamp("2026-08-25")          # 数据生成锚点日
DATA_START = pd.Timestamp("2026-01-01")     # 数据起始日
N_DAYS = (TODAY - DATA_START).days + 1      # 共 238 天

ANOMALY_WINDOW_DAYS = 30                    # 异常窗口：最近 30 天
WINDOW_START = TODAY - pd.Timedelta(days=ANOMALY_WINDOW_DAYS - 1)

# 注意：以下为【明细件级】退款概率。一笔订单含多个商品、各自独立判定，
# 订单级退款率 ≈ 1-(1-p)^件数；取 p_base=0.035、均件数≈2.4 时，
# 订单级全站基线约 8%（与业务口径"去重订单退款率"一致）。
BASE_REFUND_RATE = 0.035                    # 件级基线退款概率
PEAK_REFUND_RATE = 0.24                     # 异常 SKU 窗口末峰值（件级）
ANOMALY_SKU_WEIGHT_BOOST = 6.0              # 异常 SKU 的曝光权重加成（保证窗口内样本量）

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "ecommerce.duckdb"

# ---------------------------------------------------------------
# 维度字典
# ---------------------------------------------------------------
CATEGORIES = ["女装", "男装", "童装", "鞋靴", "箱包", "数码", "家居", "美妆"]

COLORS = ["红色", "黑色", "白色", "蓝色", "灰色", "米色", "绿色", "粉色"]
SIZES = ["S", "M", "L", "XL", "XXL"]

REGIONS = {  # 大区 -> 省份
    "华东": ["上海市", "江苏省", "浙江省", "安徽省", "福建省", "江西省", "山东省"],
    "华南": ["广东省", "广西壮族自治区", "海南省"],
    "华北": ["北京市", "天津市", "河北省", "山西省", "内蒙古自治区"],
    "华中": ["河南省", "湖北省", "湖南省"],
    "西南": ["重庆市", "四川省", "贵州省", "云南省", "西藏自治区"],
    "西北": ["陕西省", "甘肃省", "青海省", "宁夏回族自治区", "新疆维吾尔自治区"],
    "东北": ["辽宁省", "吉林省", "黑龙江省"],
}
REGION_WEIGHTS = [0.34, 0.18, 0.16, 0.12, 0.10, 0.05, 0.05]

CHANNELS = ["天猫旗舰店", "淘宝直播", "抖音直播", "小红书种草", "京东自营", "微信私域", "拼多多"]

REFUND_REASONS_NORMAL = {          # 正常退款原因分布
    "七天无理由退货": 0.40,
    "质量问题": 0.20,
    "色差与描述不符": 0.15,
    "尺码不合适": 0.15,
    "发错货/其他": 0.10,
}
REASON_ANOMALY_MAIN = "尺码不合适"  # 异常 SKU 的主导原因（62%）

PAY_METHODS = {"支付宝": 0.45, "微信支付": 0.40, "银联": 0.10, "货到付款": 0.05}
ORDER_STATUS_DIST = {"completed": 0.90, "cancelled": 0.06, "unpaid": 0.04}

# 异常故事主角：3 个红色女装 XL/XXL SKU（生成时锁定）
ANOMALY_SKU_NAMES = ["法式碎花连衣裙", "气质收腰针织裙", "复古泡泡袖雪纺裙"]


def pick(d: dict[str, float]) -> str:
    """按概率字典抽一个 key（自动归一化，允许子集过滤后权重和≠1）。"""
    keys = list(d.keys())
    probs = np.array(list(d.values()), dtype=float)
    return str(rng.choice(keys, p=probs / probs.sum()))


def rand_date(start: pd.Timestamp, end: pd.Timestamp) -> pd.Timestamp:
    span = (end - start).days + 1
    return start + pd.Timedelta(days=int(rng.integers(0, span)))


STYLE_WORDS = {  # 各类目商品风格词，用于拼真实感商品名
    "女装": ["法式碎花连衣裙", "气质收腰针织裙", "复古泡泡袖雪纺裙", "宽松直筒牛仔裤", "通勤西装外套", "慵懒风毛衣", "文艺棉麻衬衫", "高腰A字半身裙"],
    "男装": ["商务休闲衬衫", "纯棉圆领T恤", "修身牛仔裤", "轻薄羽绒服", "运动卫裤", "条纹polo衫"],
    "童装": ["卡通印花卫衣", "儿童休闲短裤", "学院风针织开衫", "纯棉连体爬服"],
    "鞋靴": ["小白鞋", "马丁靴", "跑步运动鞋", "乐福鞋", "雪地靴"],
    "箱包": ["大容量双肩包", "斜挎小方包", "登机行李箱", "托特包"],
    "数码": ["无线蓝牙耳机", "智能手环", "快充充电宝", "机械键盘"],
    "家居": ["全棉四件套", "记忆棉枕头", "收纳箱组合", "香薰蜡烛套装"],
    "美妆": ["保湿精华液", "哑光口红", "氨基酸洗面奶", "防晒霜"],
}


def make_product_name(category: str, color: str, i: int) -> str:
    words = STYLE_WORDS.get(category, ["经典款"])
    return f"{words[i % len(words)]}·{color}"


def build_dimensions() -> tuple[pd.DataFrame, ...]:
    # 渠道
    channels = pd.DataFrame(
        {"channel_id": range(1, len(CHANNELS) + 1), "channel_name": CHANNELS}
    )
    # 供应商
    suppliers = pd.DataFrame(
        {
            "supplier_id": range(1, 21),
            "supplier_name": [f"供应商{i:02d}号" for i in range(1, 21)],
            "region": rng.choice(list(REGIONS.keys()), size=20),
        }
    )
    # 类目
    categories = pd.DataFrame(
        {"category_id": range(1, len(CATEGORIES) + 1), "category_name": CATEGORIES}
    )
    # 商品（每行即一个 SKU：商品名 + 类目 + 颜色 + 尺码）
    n_products = 500
    cat_names = np.array(CATEGORIES)[rng.integers(0, len(CATEGORIES), n_products)]
    # 保证女装占比足够高（Demo 主场景），前 120 个强制为女装
    cat_names[:120] = "女装"
    colors = np.array(COLORS)[rng.integers(0, len(COLORS), n_products)]
    sizes = np.array(SIZES)[rng.choice(len(SIZES), n_products, p=[0.15, 0.3, 0.3, 0.15, 0.1])]
    products = pd.DataFrame(
        {
            "product_id": range(1, n_products + 1),
            "product_name": [
                make_product_name(c, col, i)
                for i, (c, col) in enumerate(zip(cat_names, colors))
            ],
            "category_name": cat_names,
            "color": colors,
            "size": sizes,
            "price": np.round(rng.uniform(49, 899, n_products), 2),
            "supplier_id": rng.integers(1, 21, n_products),
            "launch_date": [rand_date(DATA_START, TODAY - pd.Timedelta(days=60)) for _ in range(n_products)],
        }
    )
    # ---- 确定性构造异常故事主角：前 3 个商品固定为红色女装 XL/XXL 连衣裙 ----
    # 不依赖随机抽样，保证任何 seed 下 Demo 链路都有信号
    for i, sku_name in enumerate(ANOMALY_SKU_NAMES):
        products.loc[i, "category_name"] = "女装"
        products.loc[i, "color"] = "红色"
        products.loc[i, "size"] = ["XL", "XXL", "XL"][i]
        products.loc[i, "product_name"] = sku_name
        products.loc[i, "price"] = [259.0, 299.0, 219.0][i]
    # 库存
    inventory = pd.DataFrame(
        {
            "inventory_id": range(1, n_products * 3 + 1),
            "product_id": np.repeat(products["product_id"].values, 3),
            "warehouse_region": list(rng.choice(list(REGIONS.keys()), size=n_products * 3)),
            "stock_quantity": rng.integers(0, 2000, n_products * 3),
            "update_date": TODAY,
        }
    )
    # 客户
    n_customers = 2000
    prov_pool, region_pool = [], []
    for r, provs in REGIONS.items():
        w = REGION_WEIGHTS[list(REGIONS.keys()).index(r)]
        k = max(1, int(n_customers * w))
        prov_pool += list(rng.choice(provs, k))
        region_pool += [r] * k
    idx = rng.permutation(len(prov_pool))[:n_customers]
    customers = pd.DataFrame(
        {
            "customer_id": range(1, n_customers + 1),
            "customer_name": [f"用户{i:05d}" for i in range(1, n_customers + 1)],
            "gender": rng.choice(["女", "男"], n_customers, p=[0.58, 0.42]),
            "province": np.array(prov_pool)[idx],
            "region": np.array(region_pool)[idx],
            "channel_id": rng.integers(1, len(CHANNELS) + 1, n_customers),
            "register_date": [rand_date(DATA_START, TODAY) for _ in range(n_customers)],
        }
    )
    return channels, suppliers, categories, products, inventory, customers


def build_orders_and_payments(customers: pd.DataFrame, n_orders: int = 50000):
    dates = rng.choice(
        pd.date_range(DATA_START, TODAY).values, size=n_orders, replace=True
    )
    orders = pd.DataFrame(
        {
            "order_id": range(1, n_orders + 1),
            "customer_id": rng.integers(1, len(customers) + 1, n_orders),
            "order_date": pd.to_datetime(dates),
            "region": customers["region"].values[rng.integers(0, len(customers), n_orders)],
            "channel_id": rng.integers(1, len(CHANNELS) + 1, n_orders),
        }
    )
    statuses = np.array(list(ORDER_STATUS_DIST))[
        rng.choice(len(ORDER_STATUS_DIST), n_orders, p=list(ORDER_STATUS_DIST.values()))
    ]
    orders["order_status"] = statuses
    orders["total_amount"] = 0.0  # 后续按明细汇总回填

    # 已取消/未支付订单没有支付记录；后续产生退款时再把 payment_status 改为 refunded
    paid = orders[orders["order_status"] == "completed"]
    payments = pd.DataFrame(
        {
            "payment_id": range(1, len(paid) + 1),
            "order_id": paid["order_id"].values,
            "payment_date": paid["order_date"].values,
            "payment_method": [pick(PAY_METHODS) for _ in range(len(paid))],
            "amount": 0.0,  # 回填
            "payment_status": "paid",
        }
    )
    return orders.reset_index(drop=True), payments


def pick_anomaly_skus(products: pd.DataFrame) -> pd.DataFrame:
    """返回异常故事的 3 个主角（生成时已确定性写入前 3 行）。"""
    picked = products[products["product_name"].isin(ANOMALY_SKU_NAMES)].copy()
    assert len(picked) == 3, "异常 SKU 构造失败，请检查 build_dimensions"
    return picked


def refund_window_rate(day: pd.Timestamp) -> float:
    """异常窗口内退款率线性爬升：8% -> 15%。"""
    progress = (day - WINDOW_START).days / max(ANOMALY_WINDOW_DAYS - 1, 1)
    progress = min(max(progress, 0.0), 1.0)
    return BASE_REFUND_RATE + (PEAK_REFUND_RATE - BASE_REFUND_RATE) * progress


def main() -> None:
    DB_PATH.parent.mkdir(exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()

    channels, suppliers, categories, products, inventory, customers = build_dimensions()
    # 把 3 个异常 SKU 写回商品表
    anomaly_skus = pick_anomaly_skus(products)
    products.loc[anomaly_skus.index, :] = anomaly_skus

    orders, payments = build_orders_and_payments(customers)

    # ---------------- 订单明细 ----------------
    # 每笔非取消订单 1~5 个明细行，从商品表加权抽取（女装权重略高贴近真实）
    valid = orders[orders["order_status"] != "unpaid"]
    weights = np.where(products["category_name"].values == "女装", 3.0, 1.0)
    # 异常主角 SKU 加权，保证最近30天窗口内有足够样本量让信号可检出
    anomaly_mask = products["product_name"].isin(ANOMALY_SKU_NAMES).values
    weights = np.where(anomaly_mask, ANOMALY_SKU_WEIGHT_BOOST * 3.0, weights)
    weights = weights / weights.sum()

    item_rows = []
    next_item_id = 1
    prod_ids = products["product_id"].values
    prod_price = dict(zip(products["product_id"], products["price"]))
    for oid, odate, status in zip(valid["order_id"], valid["order_date"], valid["order_status"]):
        if status == "cancelled":
            continue
        for pid in rng.choice(prod_ids, size=rng.integers(1, 6), replace=False, p=weights):
            qty = int(rng.integers(1, 3))
            price = float(prod_price[int(pid)])
            item_rows.append((next_item_id, int(oid), int(pid), qty, price, round(qty * price, 2)))
            next_item_id += 1
    order_items = pd.DataFrame(
        item_rows, columns=["order_item_id", "order_id", "product_id", "quantity", "unit_price", "subtotal"]
    )

    # 订单金额 / 支付金额按明细回填
    sums = order_items.groupby("order_id")["subtotal"].sum()
    orders["total_amount"] = orders["order_id"].map(sums).fillna(0.0).round(2)
    pay_mask = orders["order_status"] == "completed"  # 与 payments 的构建口径一致（退款稍后改状态）
    payments["amount"] = orders.loc[pay_mask, "total_amount"].values

    # ---------------- 退款 ----------------
    anomaly_ids = set(anomaly_skus["product_id"].astype(int))
    refund_rows = []
    next_rid = 1
    # 订单 -> 明细ID列表（转成原生 list，避免 pandas Series 真值歧义）
    items_by_order = {
        int(k): [int(x) for x in v]
        for k, v in order_items.groupby("order_id")["order_item_id"]
    }
    items_meta = order_items.set_index("order_item_id")

    for _, row in valid[valid["order_status"] == "completed"].iterrows():
        item_ids = items_by_order.get(int(row["order_id"]), [])
        if not item_ids:
            continue
        for iid in item_ids:
            meta = items_meta.loc[iid]
            pid = int(meta["product_id"])
            odate = pd.Timestamp(row["order_date"])

            is_anomaly_sku = pid in anomaly_ids
            in_window = odate >= WINDOW_START
            if is_anomaly_sku and in_window:
                p_refund = refund_window_rate(odate)   # 爬升曲线
            else:
                p_refund = BASE_REFUND_RATE            # 其余一律基线
                # 轻微个体差异，避免过于整齐
                p_refund *= rng.uniform(0.85, 1.15)

            if rng.random() >= p_refund:
                continue

            if is_anomaly_sku and in_window:
                reason = (
                    REASON_ANOMALY_MAIN
                    if rng.random() < 0.62
                    else pick({k: v for k, v in REFUND_REASONS_NORMAL.items() if k != REASON_ANOMALY_MAIN})
                )
            else:
                reason = pick(REFUND_REASONS_NORMAL)

            refund_rows.append(
                (
                    next_rid,
                    int(row["order_id"]),
                    int(iid),
                    pid,
                    reason,
                    round(float(meta["subtotal"]) * rng.uniform(0.9, 1.0), 2),
                    odate + pd.Timedelta(days=int(rng.integers(1, 6))),
                )
            )
            next_rid += 1

    refunds = pd.DataFrame(
        refund_rows, columns=["refund_id", "order_id", "order_item_id", "product_id", "refund_reason", "refund_amount", "refund_date"]
    )
    # 有退款的订单状态改为 refunded
    refunded_orders = refunds["order_id"].unique()
    orders.loc[orders["order_id"].isin(refunded_orders), "order_status"] = "refunded"
    payments.loc[payments["order_id"].isin(refunded_orders), "payment_status"] = "refunded"

    # ---------------- 写入 DuckDB ----------------
    con = duckdb.connect(str(DB_PATH))
    tables = {
        "categories": categories,
        "products": products,
        "suppliers": suppliers,
        "customers": customers,
        "marketing_channels": channels,
        "orders": orders,
        "order_items": order_items,
        "payments": payments,
        "refunds": refunds,
        "inventory": inventory,
    }
    for name, df in tables.items():
        con.register(f"_{name}_df", df)
        con.execute(f"CREATE TABLE {name} AS SELECT * FROM _{name}_df")
        con.unregister(f"_{name}_df")

    _sanity_check(con, anomaly_ids)
    con.close()
    print(f"\n✅ 数据已生成: {DB_PATH}")


def _sanity_check(con: duckdb.DuckDBPyConnection, anomaly_ids: set[int]) -> None:
    """打印埋点是否生效：异常 SKU 前/后窗口退款率对比。"""
    q = """
    WITH pay AS (
        SELECT oi.product_id, o.order_id, CAST(o.order_date AS DATE) AS d
        FROM orders o JOIN order_items oi ON o.order_id = oi.order_id
        WHERE o.order_status <> 'unpaid'
    ), rf AS (
        SELECT DISTINCT product_id, order_id FROM refunds
    )
    SELECT CASE WHEN p.d >= DATE '2026-07-27' THEN '最近30天' ELSE '之前' END AS period,
           COUNT(DISTINCT p.order_id) AS paid_orders,
           COUNT(DISTINCT rf.order_id) AS refunded_orders,
           ROUND(COUNT(DISTINCT rf.order_id) * 100.0 / COUNT(DISTINCT p.order_id), 1) AS refund_rate_pct
    FROM pay p LEFT JOIN rf USING (product_id, order_id)
    WHERE p.product_id IN ({ids})
    GROUP BY 1 ORDER BY 1
    """.format(ids=",".join(map(str, sorted(anomaly_ids))))
    print("\n===== 异常 SKU 退款率埋点自检 =====")
    print(con.execute(q).fetchdf().to_string(index=False))

    q2 = """
    SELECT ROUND(
             COUNT(DISTINCT CASE WHEN rf.refund_id IS NOT NULL THEN o.order_id END) * 100.0
             / NULLIF(COUNT(DISTINCT o.order_id), 0), 1) AS baseline_all_pct
    FROM orders o
    LEFT JOIN refunds rf ON o.order_id = rf.order_id
    WHERE o.order_date < DATE '2026-07-27'
      AND o.order_status IN ('completed', 'refunded')
    """
    print("\n全站基线退款率(%)：", con.execute(q2).fetchone()[0])

    # 异常窗口内红色女装的尺码 × 退款原因分布（下钻故事的证据链）
    q3 = """
    SELECT p.size AS size,
           COUNT(DISTINCT r.order_id) AS refunded_orders,
           ROUND(SUM(CASE WHEN r.refund_reason = '尺码不合适' THEN 1 ELSE 0 END) * 100.0
                 / COUNT(*), 1) AS size_reason_pct
    FROM refunds r
    JOIN order_items oi ON r.order_item_id = oi.order_item_id
    JOIN products p ON oi.product_id = p.product_id
    JOIN orders o ON r.order_id = o.order_id
    WHERE p.category_name = '女装' AND p.color = '红色'
      AND o.order_date >= DATE '2026-07-27'
    GROUP BY p.size ORDER BY refunded_orders DESC
    """
    print("\n===== 最近30天 红色女装退款：尺码分布 =====")
    print(con.execute(q3).fetchdf().to_string(index=False))


if __name__ == "__main__":
    main()
