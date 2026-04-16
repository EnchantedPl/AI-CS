import json
import logging
import os
import time
import uuid
from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from dotenv import load_dotenv

load_dotenv()

from app.api.routes_chat import router as chat_router
from app.api.routes_demo_ui import router as demo_ui_router
from app.api.routes_debug import memory_router as debug_memory_router
from app.api.routes_debug import replay_router as debug_replay_router
from app.api.routes_debug import router as debug_events_router
from app.api.routes_debug import workflow_router as debug_workflow_router
from app.cache.embedding_runtime import EmbeddingRuntime
from app.core.config import Settings
from app.models.litellm_client import llm_healthcheck

try:
    from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
except Exception:  # pragma: no cover - fallback when dependency is absent
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4"
    Counter = None
    Histogram = None
    generate_latest = None


settings = Settings.from_env()


def setup_logging() -> logging.Logger:
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    return logging.getLogger(settings.app_name)


logger = setup_logging()


if settings.enable_prometheus and Counter is not None:
    REQUEST_COUNT = Counter(
        "ai_cs_requests_total",
        "Total HTTP requests",
        ["method", "path", "status_code"],
    )
    REQUEST_LATENCY = Histogram(
        "ai_cs_request_latency_seconds",
        "HTTP request latency in seconds",
        ["method", "path"],
    )
else:
    REQUEST_COUNT = None
    REQUEST_LATENCY = None


app = FastAPI(title=settings.app_name, debug=settings.enable_debug)
app.include_router(chat_router)
app.include_router(demo_ui_router)
app.include_router(debug_events_router)
app.include_router(debug_memory_router)
app.include_router(debug_workflow_router)
app.include_router(debug_replay_router)


@app.on_event("startup")
async def preload_local_embedding_model() -> None:
    should_preload = os.getenv("PRELOAD_LOCAL_EMBEDDING_ON_STARTUP", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not should_preload:
        return
    if settings.embedding_mode != "local":
        return
    try:
        EmbeddingRuntime().embed_query("warmup")
        logger.info(
            json.dumps(
                {
                    "type": "startup_warmup",
                    "status": "ok",
                    "component": "local_embedding",
                    "model": settings.embedding_model,
                },
                ensure_ascii=True,
            )
        )
    except Exception as exc:
        logger.warning(
            json.dumps(
                {
                    "type": "startup_warmup",
                    "status": "failed",
                    "component": "local_embedding",
                    "model": settings.embedding_model,
                    "error": str(exc),
                },
                ensure_ascii=True,
            )
        )


@app.middleware("http")
async def trace_and_metrics_middleware(request: Request, call_next):
    trace_id = request.headers.get("x-trace-id", str(uuid.uuid4()))
    request.state.trace_id = trace_id
    start = time.perf_counter()
    response = await call_next(request)
    latency = time.perf_counter() - start

    response.headers["x-trace-id"] = trace_id
    if REQUEST_COUNT is not None:
        REQUEST_COUNT.labels(request.method, request.url.path, str(response.status_code)).inc()
    if REQUEST_LATENCY is not None:
        REQUEST_LATENCY.labels(request.method, request.url.path).observe(latency)

    logger.info(
        json.dumps(
            {
                "type": "http_access",
                "trace_id": trace_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "latency_ms": round(latency * 1000, 2),
            },
            ensure_ascii=True,
        )
    )
    return response


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": settings.app_name,
        "env": settings.app_env,
    }


@app.get(settings.metrics_path)
async def metrics():
    if not settings.enable_prometheus or generate_latest is None:
        return PlainTextResponse("prometheus_disabled\n", status_code=200)
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/debug/llm-health")
async def llm_health() -> Dict[str, Any]:
    result = llm_healthcheck()
    return result


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    trace_id = getattr(request.state, "trace_id", "unknown")
    logger.exception(
        json.dumps(
            {
                "type": "unhandled_exception",
                "trace_id": trace_id,
                "path": request.url.path,
                "error": str(exc),
            },
            ensure_ascii=True,
        )
    )
    return JSONResponse(
        status_code=500,
        content={
            "trace_id": trace_id,
            "error": "internal_server_error",
            "message": "Unexpected error happened. Please retry or hand off to human agent.",
        },
    )

