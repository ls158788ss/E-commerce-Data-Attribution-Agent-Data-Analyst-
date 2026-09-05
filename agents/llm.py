# -*- coding: utf-8 -*-
"""LLM 访问层：统一封装 + 结构化输出四级兜底。

【技术栈：LangChain + OpenRouter】所有 LLM 调用经 langchain-openai 的 ChatOpenAI
统一封装（base_url 指向 OpenRouter，支持模型降级列表）。

免费模型（OpenRouter :free 档）输出不稳定，因此所有结构化调用走四级兜底：

    1. with_structured_output（function calling）
    2. 校验失败 -> 把报错回灌重试
    3. 降级为纯 JSON 文本模式 -> 正则提取 -> Pydantic 手工解析
    4. 全部失败 -> 抛 LLMStructuredError，由图节点路由降级并记入 trace

评测模式下可用磁盘缓存避免重复消耗免费额度（TODO: S5）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import TypeVar

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ValidationError

from config.config import get_settings

T = TypeVar("T", bound=BaseModel)


# ----------------------------------------------------------------
# 评测模式磁盘缓存：免费档 50 次/天，38 题评测约需 150 次调用。
# 以 (system, user, schema) 为键缓存「解析后的结构化结果」，重跑零额度消耗。
# LLM_EVAL_CACHE=1 开启（评测器自动置位）；prompt 变更自然 miss 重新调用。
# ----------------------------------------------------------------
_CACHE_DIR = Path(__file__).resolve().parent.parent / "cache" / "llm"


def _eval_cache_enabled() -> bool:
    return os.getenv("LLM_EVAL_CACHE") == "1"


def _cache_key(system: str, user: str, schema_tag: str) -> str:
    h = hashlib.sha256()
    for part in (system, user, schema_tag):
        h.update((part or "").encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def _cache_get(key: str) -> object | None:
    p = _CACHE_DIR / f"{key}.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001  损坏的缓存直接跳过
            return None
    return None


def _cache_set(key: str, obj: object) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (_CACHE_DIR / f"{key}.json").write_text(
            json.dumps(obj, ensure_ascii=False, default=str), encoding="utf-8")
    except Exception:  # noqa: BLE001  缓存写失败不影响主流程
        pass


class LLMStructuredError(RuntimeError):
    """结构化输出最终失败。"""


class LLMRateLimitError(LLMStructuredError):
    """上游限速/临时不可用（429 等），可切换备用模型重试。"""


def _is_rate_limit(err: BaseException) -> bool:
    msg = str(err)
    return any(k in msg for k in ("429", "rate-limited", "rate limit", "temporarily", "503", "502"))


def get_chat_model(*, role: str = "main", temperature: float = 0.0, model: str | None = None) -> ChatOpenAI:
    """按角色取模型：main=主力推理/SQL，report=低成本报告模型；model 显式覆盖优先。"""
    s = get_settings()
    resolved = model or (s.report_model if role == "report" else s.llm_model)
    return ChatOpenAI(
        model=resolved,
        api_key=s.llm_api_key,
        base_url=s.llm_base_url,
        temperature=temperature,
        max_retries=2,          # SDK 层限速退避重试
        timeout=45,             # 免费档拥堵时快速失败，交给上层降级链
        default_headers={"X-Title": "Data Analyst Agent"},
    )


_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL)


def _extract_json(text: str) -> str:
    """从模型输出中提取 JSON 串：优先代码块，否则截取首个大括号平衡段。"""
    m = _JSON_BLOCK.search(text)
    if m:
        return m.group(1)
    start = text.find("{")
    if start == -1:
        raise ValueError("输出中未找到 JSON")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise ValueError("JSON 未闭合")


def call_structured(
    system: str,
    user: str,
    model_cls: type[T],
    *,
    role: str = "main",
    temperature: float = 0.0,
    max_feedback_retries: int = 2,
    model_override: str | None = None,
) -> T:
    """带兜底的结构化输出调用；限速时先退避重试、再依序切换备用模型。

    成功返回 model_cls 实例；最终失败抛 LLMStructuredError。
    评测缓存（LLM_EVAL_CACHE=1）：以 (system,user,schema) 命中则直接复用，
    不消耗免费额度；prompt 变更自然 miss。
    """
    if _eval_cache_enabled():
        schema_tag = f"{model_cls.__name__}:{json.dumps(model_cls.model_json_schema(), ensure_ascii=False, sort_keys=True)}"
        key = _cache_key(system, user, schema_tag)
        hit = _cache_get(key)
        if isinstance(hit, dict):
            try:
                return model_cls.model_validate(hit)
            except Exception:  # noqa: BLE001  schema 变更等致旧缓存失效 -> 重算
                pass

    candidates = _candidate_models(model_override)
    last_err: Exception | None = None
    # 两轮扫描：429 是秒级返回，整轮代价小；第二轮应对"全池瞬时拥堵"。
    # 轮内：逐个候选快速切换；轮间：仅当失败原因是限速时才值得再扫一轮。
    for round_no in range(2):
        for m in candidates:        # 每个候选只试一次（45s 超时兜底）
            try:
                result = _call_structured_once(system, user, model_cls, role=role,
                                               temperature=temperature,
                                               max_feedback_retries=max_feedback_retries,
                                               model=m)
                if _eval_cache_enabled():
                    _cache_set(key, result.model_dump())
                return result
            except LLMRateLimitError as e:
                last_err = e
                time.sleep(2)
            except LLMStructuredError as e:
                last_err = e        # 格式/解析失败：换下一个候选继续
        if not isinstance(last_err, LLMRateLimitError):
            break                   # 格式类错误换轮无意义
        if round_no == 0:
            time.sleep(12)          # 全池拥堵，稍等后重扫
    assert last_err is not None
    raise last_err


def _candidate_models(model_override: str | None) -> list[str | None]:
    """主模型(限速/上游故障)之后的降级候选。None 表示走角色默认配置。"""
    s = get_settings()
    models: list[str | None] = [model_override] if model_override else [None]
    for fb in s.fallback_model_list:
        if fb not in models:
            models.append(fb)
    return models


def _call_structured_once(
    system: str,
    user: str,
    model_cls: type[T],
    *,
    role: str,
    temperature: float,
    max_feedback_retries: int,
    model: str | None,
) -> T:
    llm = get_chat_model(role=role, temperature=temperature, model=model)
    prompt = ChatPromptTemplate.from_messages([("system", "{system}"), ("human", "{user}")])
    chain = prompt | llm.with_structured_output(model_cls)

    feedback = ""
    # ---- 第 1~2 级：function calling + 报错回灌 ----
    for attempt in range(max_feedback_retries + 1):
        u = user if not feedback else f"{user}\n\n上次输出未通过校验，请修正：\n{feedback}"
        try:
            result = chain.invoke({"system": system, "user": u})
            if isinstance(result, model_cls):
                return result
            raise ValueError(f"返回类型异常: {type(result)}")
        except (ValidationError, ValueError) as e:
            feedback = str(e)[:800]
        except LLMStructuredError:
            raise
        except Exception as e:  # noqa: BLE001  SDK/网络/工具不支持等
            if _is_rate_limit(e):
                raise LLMRateLimitError(str(e)[:300]) from e
            feedback = f"调用失败: {e}"[:800]
            break

    # ---- 第 3 级：纯 JSON 文本模式 ----
    try:
        json_sys = (
            f"{system}\n\n只输出一个符合以下 JSON Schema 的对象，不要任何解释、"
            f"不要 markdown 代码块以外的内容：\n{json.dumps(model_cls.model_json_schema(), ensure_ascii=False)}"
        )
        resp = llm.invoke([("system", json_sys), ("human", user + (f"\n\n注意修正：{feedback}" if feedback else ""))])
        # strict=False：容忍字符串里的裸控制字符（免费模型常见毛病）
        data = json.loads(_extract_json(resp.content), strict=False)
        return model_cls.model_validate(data)
    except LLMRateLimitError:
        raise
    except (ValidationError, ValueError, json.JSONDecodeError) as e:
        raise LLMStructuredError(f"结构化输出失败({model_cls.__name__}): {e}") from e
    except Exception as e:  # noqa: BLE001
        if _is_rate_limit(e):
            raise LLMRateLimitError(str(e)[:300]) from e
        raise LLMStructuredError(f"结构化输出失败({model_cls.__name__}): {e}") from e


def call_text(system: str, user: str, *, role: str = "main", temperature: float = 0.3, retries: int = 3) -> str:
    """普通文本调用：指数退避 + 备用模型降级。"""
    if _eval_cache_enabled():
        key = _cache_key(system, user, "text")
        hit = _cache_get(key)
        if isinstance(hit, str):
            return hit

    last_err: Exception | None = None
    for m in _candidate_models(None):
        llm = get_chat_model(role=role, temperature=temperature, model=m)
        for i in range(retries):
            try:
                resp = llm.invoke([("system", system), ("human", user)])
                content = resp.content
                text = content if isinstance(content, str) else str(content)
                if _eval_cache_enabled():
                    _cache_set(key, text)
                return text
            except Exception as e:  # noqa: BLE001
                last_err = e
                time.sleep(min(2**i * 1.5, 20))
    raise RuntimeError(f"LLM 文本调用失败: {last_err}")
