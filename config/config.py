# -*- coding: utf-8 -*-
"""集中配置加载：.env -> 强类型 Settings。

所有模块统一从这里取配置，禁止散落 os.getenv。
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Windows 下强制 UTF-8，避免中文乱码
import os
import sys

os.environ.setdefault("PYTHONUTF8", "1")
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover
        pass

load_dotenv(PROJECT_ROOT / ".env")


class Settings(BaseSettings):
    """全局配置（pydantic-settings 自动从环境变量读取）。"""

    model_config = SettingsConfigDict(env_file=str(PROJECT_ROOT / ".env"), extra="ignore")

    # ---------- LLM ----------
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_api_key: str = ""
    llm_model: str = ""
    llm_fallback_models: str = ""  # 逗号分隔的降级模型列表（主力限速时依序尝试）
    llm_report_model: str = ""  # 留空则复用主力模型

    @property
    def fallback_model_list(self) -> list[str]:
        return [m.strip() for m in self.llm_fallback_models.split(",") if m.strip()]

    # ---------- 数据库 ----------
    # 【技术栈：DuckDB/MySQL 一键切换】默认 DuckDB（本地只读分析库）；
    # 改为 mysql+pymysql://... 即切换 MySQL 后端（配合 scripts/load_to_mysql.py 灌数）
    database_url: str = ""

    # ---------- MySQL（可选后端，scripts/load_to_mysql.py 使用）----------
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 
    mysql_user: str = ""
    mysql_password: str = ""
    mysql_database: str = "data_agent"

    # ---------- SQL 沙盒 ----------
    sql_max_rows: int = Field(default=1000, ge=1)
    sql_timeout_seconds: int = Field(default=15, ge=1)
    sql_max_joins: int = Field(default=6, ge=1)
    sql_repair_max_attempts: int = Field(default=3, ge=0)

    # ---------- 下钻 ----------
    drilldown_max_depth: int = Field(default=3, ge=1)

    # ---------- 检索 ----------
    retrieval_top_k: int = Field(default=8, ge=1)
    enable_vector_search: bool = False

    # ---------- 可观测性 ----------
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"
    log_level: str = "INFO"

    @property
    def report_model(self) -> str:
        return self.llm_report_model or self.llm_model


@lru_cache
def get_settings() -> Settings:
    return Settings()
