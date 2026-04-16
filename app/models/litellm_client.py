import json
import os
import time
from contextlib import contextmanager
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from litellm import completion, embedding
from app.observability.langsmith_tracing import traceable
try:
    from prometheus_client import Counter, Histogram
except Exception:  # pragma: no cover
    Counter = None
    Histogram = None


if Counter is not None:
    LLM_CALL_TOTAL = Counter(
        "ai_cs_llm_call_total",
        "LLM call outcomes and fallback ratio",
        ["kind", "model", "ok", "timeout", "fallback"],
    )
    LLM_CALL_LATENCY_SECONDS = Histogram(
        "ai_cs_llm_call_latency_seconds",
        "LLM call latency by kind/model",
        ["kind", "model"],
    )
else:
    LLM_CALL_TOTAL = None
    LLM_CALL_LATENCY_SECONDS = None


def _is_timeout_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "timeout" in text or "timed out" in text


def _record_llm_metric(
    *,
    kind: str,
    model: str,
    ok: bool,
    timeout_hit: bool,
    fallback: bool,
    elapsed_seconds: float,
) -> None:
    if LLM_CALL_TOTAL is not None:
        LLM_CALL_TOTAL.labels(
            kind,
            model or "unknown",
            str(bool(ok)).lower(),
            str(bool(timeout_hit)).lower(),
            str(bool(fallback)).lower(),
        ).inc()
    if LLM_CALL_LATENCY_SECONDS is not None:
        LLM_CALL_LATENCY_SECONDS.labels(kind, model or "unknown").observe(max(0.0, float(elapsed_seconds)))


def _extract_usage(resp: Any) -> Dict[str, int]:
    usage = getattr(resp, "usage", None) or {}
    if not isinstance(usage, dict):
        usage = {
            "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
        }
    return {
        "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
        "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
        "total_tokens": int(usage.get("total_tokens", 0) or 0),
    }


def _build_prompt(
    *,
    query: str,
    route_target: str,
    rag_context: str,
    tool_result: Dict[str, Any],
) -> str:
    tool_json = json.dumps(tool_result or {}, ensure_ascii=False)
    return (
        "你是电商智能客服助手。请使用简洁、准确、可执行的中文回复用户。\n"
        "规则：\n"
        "1) 优先基于已提供的知识库上下文回答，不要编造。\n"
        "2) 如果上下文不足，请明确说明并给出下一步建议。\n"
        "3) 售后场景可以参考工具结果。\n\n"
        f"场景: {route_target}\n"
        f"用户问题: {query}\n"
        f"工具结果(JSON): {tool_json}\n"
        f"知识库上下文:\n{rag_context or '无'}\n\n"
        "请直接输出最终回复，不要输出思考过程。"
    )


def _split_models(primary: str, fallbacks: str) -> List[str]:
    models = [primary.strip()] if primary and primary.strip() else []
    if fallbacks:
        models.extend([m.strip() for m in fallbacks.split(",") if m.strip()])
    # Keep order while deduplicating.
    out: List[str] = []
    seen = set()
    for m in models:
        if m in seen:
            continue
        seen.add(m)
        out.append(m)
    return out


def _build_optional_call_args(api_base: str, api_key: str) -> Dict[str, Any]:
    args: Dict[str, Any] = {}
    if api_base:
        args["api_base"] = api_base
    if api_key:
        args["api_key"] = api_key
    return args


@contextmanager
def _proxy_bypass_if_enabled(*, api_base: str, disable_proxy: bool):
    """Temporarily bypass system proxy for model API calls."""
    if not disable_proxy:
        yield
        return

    proxy_env_keys = [
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ]
    saved: Dict[str, Optional[str]] = {k: os.environ.get(k) for k in proxy_env_keys}
    saved_no_proxy = os.environ.get("NO_PROXY")
    saved_no_proxy_lower = os.environ.get("no_proxy")
    try:
        for k in proxy_env_keys:
            os.environ.pop(k, None)
        host = (urlparse(api_base).hostname or "").strip()
        if host:
            current = (os.environ.get("NO_PROXY", "") or "").strip()
            entries = [x.strip() for x in current.split(",") if x.strip()]
            if host not in entries:
                entries.append(host)
            os.environ["NO_PROXY"] = ",".join(entries)
            os.environ["no_proxy"] = os.environ["NO_PROXY"]
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if saved_no_proxy is None:
            os.environ.pop("NO_PROXY", None)
        else:
            os.environ["NO_PROXY"] = saved_no_proxy
        if saved_no_proxy_lower is None:
            os.environ.pop("no_proxy", None)
        else:
            os.environ["no_proxy"] = saved_no_proxy_lower


def _resolve_llm_runtime_config() -> Dict[str, str]:
    mode = os.getenv("LLM_MODE", "cloud").strip().lower()
    if mode == "local":
        model = os.getenv("LOCAL_LLM_MODEL", "ollama/qwen2.5:0.5b")
        fallback_models = os.getenv("LOCAL_LLM_FALLBACK_MODELS", "")
        api_base = os.getenv("LOCAL_LLM_API_BASE", "http://127.0.0.1:11434")
        api_key = os.getenv("LOCAL_LLM_API_KEY", "")
    else:
        model = os.getenv("CLOUD_LLM_MODEL", os.getenv("LLM_MODEL", "openai/qwen-turbo"))
        fallback_models = os.getenv(
            "CLOUD_LLM_FALLBACK_MODELS",
            os.getenv("LLM_FALLBACK_MODELS", "ollama/qwen2.5:0.5b"),
        )
        api_base = os.getenv(
            "CLOUD_LLM_API_BASE",
            os.getenv("LLM_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        )
        api_key = os.getenv(
            "CLOUD_LLM_API_KEY",
            os.getenv("LLM_API_KEY", os.getenv("DASHSCOPE_API_KEY", "")),
        )
    return {
        "mode": mode,
        "model": model,
        "fallback_models": fallback_models,
        "api_base": api_base,
        "api_key": api_key,
    }


def generate_answer_with_litellm(
    *,
    query: str,
    route_target: str,
    rag_context: str,
    tool_result: Dict[str, Any],
) -> Dict[str, Any]:
    llm_runtime = _resolve_llm_runtime_config()
    llm_model = llm_runtime["model"]
    llm_fallback_models = llm_runtime["fallback_models"]
    llm_timeout = float(os.getenv("LLM_TIMEOUT", "60"))
    llm_max_retries = int(os.getenv("LLM_MAX_RETRIES", "2"))
    llm_retry_backoff_sec = float(os.getenv("LLM_RETRY_BACKOFF_SEC", "1.5"))
    llm_temperature = float(os.getenv("LLM_TEMPERATURE", "0.2"))
    llm_max_tokens = int(os.getenv("LLM_MAX_TOKENS", "256"))
    llm_api_base = llm_runtime["api_base"]
    llm_api_key = llm_runtime["api_key"]
    llm_disable_proxy_for_api = os.getenv("LLM_DISABLE_PROXY_FOR_API", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    max_context_chars = int(
        os.getenv("LLM_MAX_CONTEXT_CHARS", os.getenv("OLLAMA_MAX_CONTEXT_CHARS", "1200"))
    )

    if rag_context and max_context_chars > 0:
        rag_context = rag_context[:max_context_chars]

    prompt = _build_prompt(
        query=query,
        route_target=route_target,
        rag_context=rag_context,
        tool_result=tool_result,
    )
    models = _split_models(llm_model, llm_fallback_models)
    if not models:
        raise RuntimeError("No LLM model configured. Set LLM_MODEL at least.")

    last_error: Optional[Exception] = None
    call_args = _build_optional_call_args(llm_api_base, llm_api_key)
    for model in models:
        for attempt in range(1, llm_max_retries + 1):
            call_start = time.perf_counter()
            try:
                with _proxy_bypass_if_enabled(
                    api_base=llm_api_base, disable_proxy=llm_disable_proxy_for_api
                ):
                    resp = completion(
                        model=model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=llm_temperature,
                        max_tokens=llm_max_tokens,
                        timeout=llm_timeout,
                        **call_args,
                    )
                content = (resp.choices[0].message.content or "").strip()
                if not content:
                    raise RuntimeError("LiteLLM completion returned empty content.")
                _record_llm_metric(
                    kind="answer",
                    model=model,
                    ok=True,
                    timeout_hit=False,
                    fallback=(model != models[0]),
                    elapsed_seconds=time.perf_counter() - call_start,
                )
                return {
                    "answer": content,
                    "provider": "litellm",
                    "model": model,
                    "mode": llm_runtime["mode"],
                    "fallback": model != models[0],
                    "usage": _extract_usage(resp),
                }
            except Exception as exc:
                _record_llm_metric(
                    kind="answer",
                    model=model,
                    ok=False,
                    timeout_hit=_is_timeout_error(exc),
                    fallback=(model != models[0]),
                    elapsed_seconds=time.perf_counter() - call_start,
                )
                last_error = exc
                if attempt < llm_max_retries:
                    time.sleep(llm_retry_backoff_sec * attempt)
                    continue
                break
    raise RuntimeError(f"LiteLLM chat failed for models={models}: {last_error}") from last_error


def summarize_text_with_litellm(
    *,
    text: str,
    max_chars: int = 600,
    instruction: str = "请将以下多轮对话压缩成简洁摘要，保留用户偏好、关键事实、已确认决策与未完成事项。",
    timeout_seconds: Optional[float] = None,
    max_retries: Optional[int] = None,
) -> Dict[str, Any]:
    llm_runtime = _resolve_llm_runtime_config()
    llm_model = llm_runtime["model"]
    llm_fallback_models = llm_runtime["fallback_models"]
    llm_timeout = (
        float(timeout_seconds) if timeout_seconds is not None else float(os.getenv("LLM_TIMEOUT", "60"))
    )
    llm_max_retries = (
        int(max_retries) if max_retries is not None else int(os.getenv("LLM_MAX_RETRIES", "2"))
    )
    llm_retry_backoff_sec = float(os.getenv("LLM_RETRY_BACKOFF_SEC", "1.5"))
    llm_api_base = llm_runtime["api_base"]
    llm_api_key = llm_runtime["api_key"]
    llm_disable_proxy_for_api = os.getenv("LLM_DISABLE_PROXY_FOR_API", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    models = _split_models(llm_model, llm_fallback_models)
    if not models:
        raise RuntimeError("No LLM model configured for summarization.")

    prompt = (
        f"{instruction}\n"
        f"要求：\n1) 输出中文；2) 不超过{max_chars}字；3) 只输出摘要正文。\n\n"
        f"待压缩内容：\n{text[:4000]}"
    )
    last_error: Optional[Exception] = None
    call_args = _build_optional_call_args(llm_api_base, llm_api_key)
    for model in models:
        for attempt in range(1, llm_max_retries + 1):
            call_start = time.perf_counter()
            try:
                with _proxy_bypass_if_enabled(
                    api_base=llm_api_base, disable_proxy=llm_disable_proxy_for_api
                ):
                    resp = completion(
                        model=model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.0,
                        max_tokens=256,
                        timeout=llm_timeout,
                        **call_args,
                    )
                content = (resp.choices[0].message.content or "").strip()
                if not content:
                    raise RuntimeError("LiteLLM summarization returned empty content.")
                _record_llm_metric(
                    kind="summary",
                    model=model,
                    ok=True,
                    timeout_hit=False,
                    fallback=(model != models[0]),
                    elapsed_seconds=time.perf_counter() - call_start,
                )
                return {
                    "summary": content[:max_chars],
                    "provider": "litellm",
                    "model": model,
                    "mode": llm_runtime["mode"],
                    "fallback": model != models[0],
                }
            except Exception as exc:
                _record_llm_metric(
                    kind="summary",
                    model=model,
                    ok=False,
                    timeout_hit=_is_timeout_error(exc),
                    fallback=(model != models[0]),
                    elapsed_seconds=time.perf_counter() - call_start,
                )
                last_error = exc
                if attempt < llm_max_retries:
                    time.sleep(llm_retry_backoff_sec * attempt)
                    continue
                break
    raise RuntimeError(f"LiteLLM summarization failed for models={models}: {last_error}") from last_error


def embed_texts_with_litellm(texts: List[str]) -> Dict[str, Any]:
    if not texts:
        return {"embeddings": [], "model": None, "fallback": False}

    embedding_mode = os.getenv("EMBEDDING_MODE", "cloud").strip().lower()
    if embedding_mode == "local":
        embedding_model = os.getenv(
            "LOCAL_EMBEDDING_MODEL",
            os.getenv("EMBEDDING_MODEL", "openai/text-embedding-v3"),
        )
    else:
        embedding_model = os.getenv(
            "CLOUD_EMBEDDING_MODEL",
            os.getenv("EMBEDDING_MODEL", "openai/text-embedding-v3"),
        )
    embedding_fallback_models = os.getenv("EMBEDDING_FALLBACK_MODELS", "")
    embedding_timeout = float(os.getenv("EMBEDDING_TIMEOUT", "60"))
    embedding_api_base = os.getenv("EMBEDDING_API_BASE", os.getenv("LLM_API_BASE", ""))
    embedding_api_key = os.getenv("EMBEDDING_API_KEY", os.getenv("DASHSCOPE_API_KEY", ""))
    embedding_disable_proxy_for_api = os.getenv("EMBEDDING_DISABLE_PROXY_FOR_API", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    models = _split_models(embedding_model, embedding_fallback_models)
    if not models:
        raise RuntimeError("No embedding model configured. Set EMBEDDING_MODEL at least.")

    call_args = _build_optional_call_args(embedding_api_base, embedding_api_key)
    last_error: Optional[Exception] = None
    for model in models:
        call_start = time.perf_counter()
        try:
            with _proxy_bypass_if_enabled(
                api_base=embedding_api_base, disable_proxy=embedding_disable_proxy_for_api
            ):
                resp = embedding(
                    model=model,
                    input=texts,
                    encoding_format="float",
                    timeout=embedding_timeout,
                    **call_args,
                )
            vectors = [item["embedding"] for item in resp.data]
            if not vectors:
                raise RuntimeError("LiteLLM embedding returned empty vectors.")
            _record_llm_metric(
                kind="embedding",
                model=model,
                ok=True,
                timeout_hit=False,
                fallback=(model != models[0]),
                elapsed_seconds=time.perf_counter() - call_start,
            )
            return {
                "embeddings": vectors,
                "model": model,
                "fallback": model != models[0],
                "dim": len(vectors[0]),
            }
        except Exception as exc:
            _record_llm_metric(
                kind="embedding",
                model=model,
                ok=False,
                timeout_hit=_is_timeout_error(exc),
                fallback=(model != models[0]),
                elapsed_seconds=time.perf_counter() - call_start,
            )
            last_error = exc
            continue
    raise RuntimeError(f"LiteLLM embedding failed for models={models}: {last_error}") from last_error


def llm_healthcheck() -> Dict[str, Any]:
    try:
        resp = generate_answer_with_litellm(
            query="请回复:ok",
            route_target="faq",
            rag_context="",
            tool_result={},
        )
        return {
            "ok": True,
            "provider": resp.get("provider"),
            "mode": resp.get("mode"),
            "model": resp.get("model"),
            "fallback": resp.get("fallback"),
            "runtime": _resolve_llm_runtime_config(),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "runtime": _resolve_llm_runtime_config(),
        }


@traceable(name="aftersales_planner", run_type="llm")
def decide_aftersales_next_step(
    *,
    query: str,
    context: Dict[str, Any],
    step_idx: int,
    available_tools: List[Dict[str, Any]],
    available_skills: List[Dict[str, Any]],
    available_mcp: List[Dict[str, Any]],
    allow_side_effect: bool,
) -> Dict[str, Any]:
    llm_runtime = _resolve_llm_runtime_config()
    llm_model = llm_runtime["model"]
    llm_fallback_models = llm_runtime["fallback_models"]
    llm_timeout = float(os.getenv("LLM_TIMEOUT", "60"))
    llm_api_base = llm_runtime["api_base"]
    llm_api_key = llm_runtime["api_key"]
    llm_disable_proxy_for_api = os.getenv("LLM_DISABLE_PROXY_FOR_API", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    call_args = _build_optional_call_args(llm_api_base, llm_api_key)
    models = _split_models(llm_model, llm_fallback_models)
    if not models:
        raise RuntimeError("No LLM model configured for aftersales planner.")

    tool_defs: List[Dict[str, Any]] = []
    for item in (available_tools + available_skills + available_mcp):
        if (item.get("type") == "mcp") and (not allow_side_effect):
            continue
        tool_defs.append(
            {
                "type": "function",
                "function": {
                    "name": item.get("name", ""),
                    "description": item.get("description", ""),
                    "parameters": item.get("parameters", {"type": "object", "properties": {}}),
                },
            }
        )
    tool_defs.append(
        {
            "type": "function",
            "function": {
                "name": "finalize_answer",
                "description": "All required facts are ready. Produce final answer.",
                "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}},
            },
        }
    )
    prompt = (
        "你是售后复杂流程规划器。每一步只能选择一个函数调用。\n"
        "优先查询事实 -> 策略评估 -> 方案生成。涉及退款提交/工单升级等副作用操作前，"
        "应优先触发人工审批。\n"
        f"当前步骤: {step_idx}\n"
        f"allow_side_effect: {allow_side_effect}\n"
        f"用户问题: {query}\n"
        f"当前上下文摘要: {json.dumps(context, ensure_ascii=False)[:1800]}\n"
    )
    last_error: Optional[Exception] = None
    for model in models:
        call_start = time.perf_counter()
        try:
            with _proxy_bypass_if_enabled(
                api_base=llm_api_base, disable_proxy=llm_disable_proxy_for_api
            ):
                resp = completion(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    tools=tool_defs,
                    tool_choice="auto",
                    temperature=0.0,
                    max_tokens=180,
                    timeout=llm_timeout,
                    **call_args,
                )
            msg = resp.choices[0].message
            calls = getattr(msg, "tool_calls", None) or []
            if calls:
                fc = calls[0].function
                args_raw = getattr(fc, "arguments", "{}") or "{}"
                try:
                    args = json.loads(args_raw)
                except Exception:
                    args = {}
                _record_llm_metric(
                    kind="planner",
                    model=model,
                    ok=True,
                    timeout_hit=False,
                    fallback=(model != models[0]),
                    elapsed_seconds=time.perf_counter() - call_start,
                )
                return {
                    "action_type": "function_call",
                    "name": getattr(fc, "name", ""),
                    "arguments": args,
                    "source": "llm_tool_call",
                    "model": model,
                }
            content = (msg.content or "").strip()
            if content.startswith("{") and content.endswith("}"):
                parsed = json.loads(content)
                if isinstance(parsed, dict) and parsed.get("name"):
                    _record_llm_metric(
                        kind="planner",
                        model=model,
                        ok=True,
                        timeout_hit=False,
                        fallback=(model != models[0]),
                        elapsed_seconds=time.perf_counter() - call_start,
                    )
                    return {
                        "action_type": "json_fallback",
                        "name": str(parsed.get("name")),
                        "arguments": parsed.get("arguments", {}),
                        "source": "llm_json",
                        "model": model,
                    }
        except Exception as exc:
            _record_llm_metric(
                kind="planner",
                model=model,
                ok=False,
                timeout_hit=_is_timeout_error(exc),
                fallback=(model != models[0]),
                elapsed_seconds=time.perf_counter() - call_start,
            )
            last_error = exc
            continue

    # deterministic fallback planner
    tool_state = context.get("tool_result", {})
    policy = context.get("policy_eval", {})
    plan = context.get("plan", {})
    if not tool_state.get("order_query_tool"):
        return {
            "action_type": "heuristic",
            "name": "order_query_tool",
            "arguments": {"query": query},
            "source": f"heuristic:{last_error}",
        }
    if not tool_state.get("ticket_query_tool"):
        return {
            "action_type": "heuristic",
            "name": "ticket_query_tool",
            "arguments": {"query": query},
            "source": "heuristic",
        }
    if not policy:
        return {
            "action_type": "heuristic",
            "name": "refund_policy_skill",
            "arguments": {"query": query},
            "source": "heuristic",
        }
    if policy.get("manual_required", False):
        return {
            "action_type": "heuristic",
            "name": "human_gate",
            "arguments": {"reason": "manual_required"},
            "source": "heuristic",
        }
    if not plan:
        return {
            "action_type": "heuristic",
            "name": "aftersales_plan_skill",
            "arguments": {},
            "source": "heuristic",
        }
    if allow_side_effect and policy.get("eligible", False):
        return {
            "action_type": "heuristic",
            "name": "refund_submit_mcp",
            "arguments": {"reason": "eligible_refund"},
            "source": "heuristic",
        }
    return {
        "action_type": "heuristic",
        "name": "finalize_answer",
        "arguments": {"reason": "ready"},
        "source": "heuristic",
    }
