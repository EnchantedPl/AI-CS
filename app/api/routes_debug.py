from typing import Any, Dict, List

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response
from langchain_core.runnables.graph_mermaid import draw_mermaid_png
from pydantic import BaseModel, Field

from app.graph.workflows.minimal_chat import (
    get_cache_orchestrator,
    get_checkpoint_store,
    get_workflow_mermaid,
)
from app.memory.store import MemoryStore
from app.replay.store import REPLAY_STORE

router = APIRouter(prefix="/debug/events", tags=["debug"])
memory_router = APIRouter(prefix="/debug/memory", tags=["debug"])
workflow_router = APIRouter(prefix="/debug/workflow", tags=["debug"])
replay_router = APIRouter(prefix="/debug/replay", tags=["debug"])
MEMORY_DEBUG_STORE = MemoryStore()
WORKFLOW_CKPT_STORE = get_checkpoint_store()


class PublishEventRequest(BaseModel):
    event_type: str = Field(..., description="DOC_UPDATED | DOC_EXPIRED | KB_VERSION_BUMPED")
    source_doc_ids: List[str] = Field(default_factory=list)
    source_chunk_ids: List[str] = Field(default_factory=list)
    kb_version: str = Field(default="")


@router.post("/publish")
async def publish_event(payload: PublishEventRequest) -> Dict[str, Any]:
    orchestrator = get_cache_orchestrator()
    return orchestrator.publish_invalidation_event(
        event_type=payload.event_type,
        source_doc_ids=payload.source_doc_ids,
        source_chunk_ids=payload.source_chunk_ids,
        kb_version=payload.kb_version,
    )


@router.get("/state")
async def event_state() -> Dict[str, Any]:
    orchestrator = get_cache_orchestrator()
    events = orchestrator.list_recent_events()
    return {"count": len(events), "events": events}


class CleanupMemoryRequest(BaseModel):
    trace_id: str = Field(default="debug_memory_cleanup")


class DeleteMemoryRequest(BaseModel):
    memory_id: str = Field(..., min_length=1)
    trace_id: str = Field(default="debug_memory_delete")


class DeleteSessionMemoryRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    tenant_id: str = Field(..., min_length=1)
    user_id: str = Field(..., min_length=1)
    trace_id: str = Field(default="debug_memory_delete_session")


@memory_router.post("/cleanup")
async def cleanup_memory(payload: CleanupMemoryRequest) -> Dict[str, Any]:
    return MEMORY_DEBUG_STORE.cleanup_expired(trace_id=payload.trace_id)


@memory_router.post("/delete")
async def delete_memory(payload: DeleteMemoryRequest) -> Dict[str, Any]:
    return MEMORY_DEBUG_STORE.soft_delete_memory(
        memory_id=payload.memory_id,
        trace_id=payload.trace_id,
    )


@memory_router.post("/delete-session")
async def delete_session_memory(payload: DeleteSessionMemoryRequest) -> Dict[str, Any]:
    return MEMORY_DEBUG_STORE.soft_delete_by_session(
        session_id=payload.session_id,
        tenant_id=payload.tenant_id,
        user_id=payload.user_id,
        trace_id=payload.trace_id,
    )


@memory_router.get("/stats")
async def memory_stats() -> Dict[str, Any]:
    return MEMORY_DEBUG_STORE.memory_stats()


class WorkflowCheckpointListRequest(BaseModel):
    thread_id: str = Field(..., min_length=1)
    limit: int = Field(default=20, ge=1, le=200)


class WorkflowCheckpointGetRequest(BaseModel):
    checkpoint_id: str = Field(..., min_length=1)


@workflow_router.post("/checkpoints")
async def list_checkpoints(payload: WorkflowCheckpointListRequest) -> Dict[str, Any]:
    rows = WORKFLOW_CKPT_STORE.list_checkpoints(thread_id=payload.thread_id, limit=payload.limit)
    return {"count": len(rows), "items": rows}


@workflow_router.post("/checkpoint/get")
async def get_checkpoint(payload: WorkflowCheckpointGetRequest) -> Dict[str, Any]:
    item = WORKFLOW_CKPT_STORE.get_checkpoint(payload.checkpoint_id)
    return {"found": bool(item), "item": item or {}}


class WorkflowTraceRequest(BaseModel):
    thread_id: str = Field(..., min_length=1)
    limit: int = Field(default=80, ge=1, le=500)


class ReplayCasesRequest(BaseModel):
    limit: int = Field(default=100, ge=1, le=500)
    run_id: str = Field(default="")
    tenant_id: str = Field(default="")


class ReplayCaseSnapshotsRequest(BaseModel):
    case_id: str = Field(..., min_length=1)


def _build_trace_mermaid(path: List[str]) -> str:
    if not path:
        return "flowchart LR\n  empty[No checkpoints found]"
    lines = ["flowchart LR"]
    for idx, node in enumerate(path):
        safe = node.replace('"', "'")
        lines.append(f'  n{idx}["{idx+1}. {safe}"]')
        if idx > 0:
            lines.append(f"  n{idx-1} --> n{idx}")
    return "\n".join(lines)


@workflow_router.get("/graph/mermaid")
async def workflow_graph_mermaid() -> Dict[str, Any]:
    return {"mermaid": get_workflow_mermaid()}


@workflow_router.post("/trace")
async def workflow_trace(payload: WorkflowTraceRequest) -> Dict[str, Any]:
    rows_desc = WORKFLOW_CKPT_STORE.list_checkpoints(thread_id=payload.thread_id, limit=payload.limit)
    rows = list(reversed(rows_desc))
    path = [str(x.get("node_name", "")) for x in rows if x.get("node_name")]
    return {
        "thread_id": payload.thread_id,
        "count": len(rows),
        "node_path": path,
        "items": rows,
    }


@workflow_router.post("/trace-render")
async def workflow_trace_render(payload: WorkflowTraceRequest) -> Dict[str, Any]:
    rows_desc = WORKFLOW_CKPT_STORE.list_checkpoints(thread_id=payload.thread_id, limit=payload.limit)
    rows = list(reversed(rows_desc))
    path = [str(x.get("node_name", "")) for x in rows if x.get("node_name")]
    trace_mermaid = _build_trace_mermaid(path)
    return {
        "thread_id": payload.thread_id,
        "graph_mermaid": get_workflow_mermaid(),
        "trace_mermaid": trace_mermaid,
        "node_path": path,
        "count": len(path),
    }


@workflow_router.post("/trace-render.png")
async def workflow_trace_render_png(payload: WorkflowTraceRequest):
    rows_desc = WORKFLOW_CKPT_STORE.list_checkpoints(thread_id=payload.thread_id, limit=payload.limit)
    rows = list(reversed(rows_desc))
    path = [str(x.get("node_name", "")) for x in rows if x.get("node_name")]
    trace_mermaid = _build_trace_mermaid(path)
    try:
        png_bytes = draw_mermaid_png(mermaid_syntax=trace_mermaid)
        return Response(content=png_bytes, media_type="image/png")
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={
                "error": "trace_render_png_failed",
                "message": str(exc),
                "trace_mermaid": trace_mermaid,
            },
        )


@replay_router.post("/cases")
async def replay_cases(payload: ReplayCasesRequest) -> Dict[str, Any]:
    rows = REPLAY_STORE.list_cases(limit=payload.limit, run_id=payload.run_id, tenant_id=payload.tenant_id)
    return {"count": len(rows), "items": rows}


@replay_router.post("/case/snapshots")
async def replay_case_snapshots(payload: ReplayCaseSnapshotsRequest) -> Dict[str, Any]:
    rows = REPLAY_STORE.get_case_snapshots(payload.case_id)
    return {"count": len(rows), "items": rows}
