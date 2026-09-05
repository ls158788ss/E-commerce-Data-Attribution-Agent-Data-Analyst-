# -*- coding: utf-8 -*-
"""SQL 执行器：SQLAlchemy 连接工厂 + 方言感知 + 行数上限 + 线程超时。

【技术栈：SQLAlchemy + DuckDB/MySQL】连接层用 SQLAlchemy 工厂抽象，
DATABASE_URL 换 scheme 即可切换数据库后端，上层校验/执行代码零改动：

多后端设计（DATABASE_URL 按 scheme 自动切换）：
    duckdb:///./data/ecommerce.duckdb        本地分析库（默认，零配置）
    mysql+pymysql://user:pwd@host:3306/db    MySQL（生产演示后端）
    postgresql+psycopg://user:pwd@host/db    PostgreSQL（预留）

安全设计：
* DuckDB 后端以 read_only=True 打开，物理不可写
* MySQL/PG 建议使用只读账号（见 scripts/load_to_mysql.py 说明）
* Windows 没有 signal.alarm：线程 future.result(timeout) 软超时，
  配合校验层强制 LIMIT 形成双保险；生产 PG 可用 statement_timeout
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import sqlalchemy as sa

from config.config import get_settings

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "ecommerce.duckdb"

# sqlglot 方言映射：database_url scheme -> sqlglot read dialect
DIALECT_MAP = {
    "duckdb": "duckdb",
    "mysql": "mysql",
    "postgresql": "postgres",
    "postgres": "postgres",
}


class SQLExecutionError(RuntimeError):
    """SQL 执行失败（语法/超时/表不存在等）。"""


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[dict]
    df: pd.DataFrame
    row_count: int
    elapsed_ms: int
    truncated: bool = False


_engine: sa.Engine | None = None
_dialect: str = "duckdb"
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sql-exec")


def _build_engine() -> sa.Engine:
    """按 DATABASE_URL 构建引擎。DuckDB 强制只读；外部库建议只读账号。"""
    s = get_settings()
    url = s.database_url
    scheme = url.split(":", 1)[0].split("+", 1)[0].lower()

    global _dialect
    _dialect = DIALECT_MAP.get(scheme, "duckdb")

    if scheme == "duckdb":
        # duckdb:///./data/x.duckdb —— 相对路径按项目根解析
        if url.startswith("duckdb:///") and not url.startswith("duckdb:///:"):
            raw_path = url[len("duckdb:///"):].replace("/", "\\")
            p = Path(raw_path)
            if not p.is_absolute():
                p = (PROJECT_ROOT / p).resolve()
            url = f"duckdb:///{p.as_posix()}"
            db_file = p
        else:
            db_file = DEFAULT_DB_PATH
        if not db_file.exists():
            raise FileNotFoundError(
                f"数据库不存在: {db_file}，请先运行 python data/generate_data.py"
            )
        return sa.create_engine(url, connect_args={"read_only": True})

    # 外部数据库：直接透传连接串（凭据在 .env）
    return sa.create_engine(url, pool_pre_ping=True, pool_recycle=1800)


def get_engine() -> sa.Engine:
    """进程内共享一个引擎（Streamlit session / FastAPI 复用）。"""
    global _engine
    if _engine is None:
        _engine = _build_engine()
    return _engine


def current_dialect() -> str:
    """当前后端对应的 sqlglot 方言（仅按配置推导，不触发连接）。"""
    url = get_settings().database_url
    scheme = url.split(":", 1)[0].split("+", 1)[0].lower()
    return DIALECT_MAP.get(scheme, "duckdb")


def execute_sql(sql: str) -> QueryResult:
    """在沙盒限制下执行只读 SQL。抛 SQLExecutionError 表示业务失败（可进入修复循环）。"""
    from sql.audit import audit_sql

    s = get_settings()
    engine = get_engine()

    # ---- EXPLAIN 预检：提前暴露语法/绑定错误 ----
    try:
        with engine.connect() as conn:
            conn.execute(sa.text(f"EXPLAIN {sql}"))
    except Exception as e:  # noqa: BLE001  各方言异常类型不同，统一拦截
        msg = str(e).splitlines()[0][:500]
        audit_sql(sql=sql, stage="explain", allowed=False, error=msg)
        raise SQLExecutionError(msg) from e

    def _run() -> pd.DataFrame:
        with engine.connect() as conn:
            return pd.read_sql_query(sa.text(sql), conn)

    t0 = time.perf_counter()
    fut = _pool.submit(_run)
    try:
        df = fut.result(timeout=s.sql_timeout_seconds)
    except TimeoutError as e:
        audit_sql(sql=sql, stage="execute", allowed=False, error=f"timeout>{s.sql_timeout_seconds}s")
        raise SQLExecutionError(f"查询超时(>{s.sql_timeout_seconds}s)，请缩小时间范围或减少聚合粒度") from e
    except Exception as e:  # noqa: BLE001
        msg = str(e).splitlines()[0][:500]
        audit_sql(sql=sql, stage="execute", allowed=False, error=msg)
        raise SQLExecutionError(msg) from e

    elapsed = int((time.perf_counter() - t0) * 1000)
    audit_sql(sql=sql, stage="execute", allowed=True, row_count=len(df), elapsed_ms=elapsed)

    truncated = len(df) > s.sql_max_rows
    if truncated:
        df = df.head(s.sql_max_rows)

    df.columns = [str(c) for c in df.columns]
    rows = df.to_dict(orient="records")
    clean = []
    for r in rows:
        clean.append({k: (v.item() if hasattr(v, "item") else v) for k, v in r.items()})
    return QueryResult(
        columns=list(df.columns),
        rows=clean,
        df=df,
        row_count=len(df),
        elapsed_ms=elapsed,
        truncated=truncated,
    )
