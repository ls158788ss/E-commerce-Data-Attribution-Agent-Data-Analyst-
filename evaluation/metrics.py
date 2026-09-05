# -*- coding: utf-8 -*-
"""评测指标实现（v2）。

执行准确率的关键设计（v1 教训）：
* 行内单元格按「值集合(frozenset)」规范化 —— 对列顺序、列别名不敏感
* 预测结果比 Golden 多出描述性列时（如额外带出 color），按超集包含判等
* 浮点统一四舍五入到 2 位；时间戳去掉零点时刻
"""
from __future__ import annotations

import sqlglot
from sqlglot import exp


def _norm_cell(v) -> str:
    if v is None:
        return "<null>"
    if isinstance(v, bool):
        return str(v)
    s = str(v).strip()
    # 去掉时间戳的零点时刻（DATE_TRUNC 结果与字符串月份仍可能不同，属口径差异）
    if s.endswith(" 00:00:00"):
        s = s[: -len(" 00:00:00")]
    try:
        f = float(s)
        return f"{round(f, 2):g}"
    except (TypeError, ValueError):
        return s


def row_frozensets(rows: list[dict], *, date_grain: str | None = None,
                   ignore_non_numeric_cols: bool = False) -> list[frozenset[str]]:
    """每行 -> 单元格值集合（对列序/列名不敏感）。"""
    import datetime as _dt

    out = []
    for r in rows:
        cells = []
        for v in r.values():
            s = _norm_cell(v)
            if ignore_non_numeric_cols:
                try:
                    float(s.replace(",", ""))
                except ValueError:
                    continue  # 丢弃非数值列（标签命名差异不影响数值正确性）
            if date_grain == "month":
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                    try:
                        d = _dt.datetime.strptime(s, fmt)
                        s = d.strftime("%Y-%m")
                        break
                    except ValueError:
                        continue
            cells.append(s)
        out.append(frozenset(cells))
    return out


def execution_accuracy(golden_rows: list[dict], pred_rows: list[dict],
                       compare: dict | None = None) -> float:
    """1.0 = 结果等价（允许列序/别名差异、预测侧多带描述列）；否则按覆盖率给分。

    compare 选项：
        date_grain: "month" —— 日期类单元格统一归一到 %Y-%m 再比较
        ignore_non_numeric_cols: True —— 只比较数值列（标签命名差异不扣分）
    """
    compare = compare or {}
    if not golden_rows and not pred_rows:
        return 1.0
    if not golden_rows or not pred_rows:
        return 0.0

    kw = dict(date_grain=compare.get("date_grain"),
              ignore_non_numeric_cols=compare.get("ignore_non_numeric_cols", False))
    g = row_frozensets(golden_rows, **kw)
    p_sets = [frozenset(c) for c in row_frozensets(pred_rows, **kw)]
    # 空集合保护：ignore_non_numeric 可能清空单侧
    if any(not fs for fs in g) or any(not fs for fs in p_sets):
        g = row_frozensets(golden_rows)
        p_sets = [frozenset(c) for c in row_frozensets(pred_rows)]

    # 超集包含：每个 golden 行都能被某个 pred 行覆盖（pred 允许多列）
    covered = sum(1 for gr in g if any(gr <= pr for pr in p_sets))
    coverage = covered / len(g)

    # 反向惩罚：pred 行数远多于 golden（如忘了 LIMIT N）时压分
    ratio_penalty = 1.0
    if len(pred_rows) > len(golden_rows):
        excess = (len(pred_rows) - len(golden_rows)) / len(golden_rows)
        ratio_penalty = max(0.0, 1.0 - max(0.0, excess - 0.2))

    return coverage * ratio_penalty


def extract_tables(sql: str) -> set[str]:
    """从 SQL 提取涉及的全部表（含 CTE 内）。CTE 别名排除。"""
    try:
        parsed = sqlglot.parse_one(sql, read="duckdb")
    except Exception:  # noqa: BLE001
        return set()
    cte_names = {cte.alias_or_name.lower() for cte in parsed.find_all(exp.CTE)}
    tables = set()
    for t in parsed.find_all(exp.Table):
        name = t.name.lower()
        if name and name not in cte_names:
            tables.add(name)
    return tables


def schema_recall_precision(pred_tables: set[str], golden_tables: set[str]) -> tuple[float, float]:
    if not golden_tables:
        return 0.0, 0.0
    hit = pred_tables & golden_tables
    recall = len(hit) / len(golden_tables)
    precision = len(hit) / len(pred_tables) if pred_tables else 0.0
    return recall, precision


def join_accuracy(pred_sql: str, golden_tables: set[str]) -> float:
    """实际执行 SQL 的表集合与 Golden 表集合的一致率 (Jaccard)。"""
    pred = extract_tables(pred_sql)
    if not golden_tables and not pred:
        return 1.0
    union = pred | golden_tables
    return len(pred & golden_tables) / len(union) if union else 1.0
