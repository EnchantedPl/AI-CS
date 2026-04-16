import contextlib
import json
import logging
import os
from typing import Any, Callable, Dict

logger = logging.getLogger(__name__)


def _enabled() -> bool:
    return os.getenv("ENABLE_LANGSMITH", "false").strip().lower() in {"1", "true", "yes", "on"}


def traceable(*args, **kwargs):
    try:
        from langsmith import traceable as _traceable  # type: ignore
    except Exception:
        def _noop_decorator(func: Callable):
            return func
        return _noop_decorator
    return _traceable(*args, **kwargs)


@contextlib.contextmanager
def chat_tracing_context(*, metadata: Dict[str, Any], tags: list[str]):
    if not _enabled():
        yield
        return
    # LangChain / LangSmith clients also read LANGCHAIN_API_KEY; mirror LANGSMITH_API_KEY when unset.
    ls_key = (os.getenv("LANGSMITH_API_KEY") or os.getenv("LANGCHAIN_API_KEY") or "").strip()
    if ls_key:
        os.environ.setdefault("LANGCHAIN_API_KEY", ls_key)
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
    os.environ.setdefault("LANGCHAIN_PROJECT", os.getenv("LANGSMITH_PROJECT", "ai-cs-demo"))
    tracing_cm = None
    try:
        from langsmith import tracing_context  # type: ignore

        tracing_cm = tracing_context(
            project_name=os.getenv("LANGSMITH_PROJECT", "ai-cs-demo"),
            metadata=metadata,
            tags=tags,
            enabled=True,
        )
        tracing_cm.__enter__()
    except Exception as exc:
        # If tracing initialization fails, do not affect online flow.
        logger.debug("chat_tracing_context init failed: %s", exc)
        tracing_cm = None

    try:
        yield
    finally:
        if tracing_cm is not None:
            try:
                tracing_cm.__exit__(None, None, None)
            except Exception as exc:
                logger.debug("chat_tracing_context close failed: %s", exc)


def _to_safe_metadata_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except Exception:
        return str(value)


def set_trace_metadata(**metadata: Any) -> bool:
    if not _enabled():
        return False
    try:
        from langsmith import get_current_run_tree, set_run_metadata  # type: ignore

        run_tree = get_current_run_tree()
        if run_tree is None:
            logger.debug("set_trace_metadata skipped: no active run tree")
            return False
        safe_metadata = {k: _to_safe_metadata_value(v) for k, v in metadata.items()}
        set_run_metadata(**safe_metadata)
        return True
    except Exception as exc:
        logger.debug("set_trace_metadata failed: %s", exc)
        return False


def set_trace_tags(*tags: str) -> bool:
    if not _enabled():
        return False
    try:
        from langsmith import get_current_run_tree  # type: ignore

        run_tree = get_current_run_tree()
        if run_tree is None:
            logger.debug("set_trace_tags skipped: no active run tree")
            return False
        existing_tags = list(getattr(run_tree, "tags", []) or [])
        for tag in tags:
            if tag and tag not in existing_tags:
                existing_tags.append(tag)
        run_tree.tags = existing_tags
        return True
    except Exception as exc:
        logger.debug("set_trace_tags failed: %s", exc)
        return False
