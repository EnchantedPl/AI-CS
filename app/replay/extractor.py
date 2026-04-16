import hashlib
from typing import Any, Dict, List


def _fingerprint(params: Dict[str, Any]) -> str:
    raw = str(sorted((params or {}).items()))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def build_layered_snapshots(
    *,
    payload: Dict[str, Any],
    result: Dict[str, Any],
    observability_sections: Dict[str, Any],
    elapsed_ms: float,
    env_versions: Dict[str, Any],
) -> List[Dict[str, Any]]:
    sec1 = observability_sections.get("sec1_entry_route", {}) if isinstance(observability_sections, dict) else {}
    sec2 = observability_sections.get("sec2_cache", {}) if isinstance(observability_sections, dict) else {}
    sec3 = observability_sections.get("sec3_rag", {}) if isinstance(observability_sections, dict) else {}
    sec4 = observability_sections.get("sec4_context", {}) if isinstance(observability_sections, dict) else {}
    sec5 = observability_sections.get("sec5_workflow", {}) if isinstance(observability_sections, dict) else {}
    sec6 = observability_sections.get("sec6_tools_mcp", {}) if isinstance(observability_sections, dict) else {}
    sec8 = observability_sections.get("sec8_infra_degrade", {}) if isinstance(observability_sections, dict) else {}
    node_trace = result.get("node_trace", []) if isinstance(result.get("node_trace"), list) else []

    l0_params = {
        "action_mode": payload.get("action_mode", "auto"),
        "rewind_stage": payload.get("rewind_stage", ""),
        "memory_enabled": payload.get("memory_enabled"),
    }
    l2_params = {
        "semantic_threshold_low": env_versions.get("semantic_threshold_low", ""),
        "semantic_threshold_high": env_versions.get("semantic_threshold_high", ""),
        "l2_gray_second_threshold": env_versions.get("l2_gray_second_threshold", ""),
    }
    l3_params = {
        "rag_top_k": env_versions.get("rag_top_k", ""),
        "rag_vector_top_k": env_versions.get("rag_vector_top_k", ""),
        "rag_enable_rerank": env_versions.get("rag_enable_rerank", ""),
        "rag_rerank_top_k": env_versions.get("rag_rerank_top_k", ""),
    }

    snapshots: List[Dict[str, Any]] = [
        {
            "layer_code": "L0",
            "status": "ok",
            "input_json": {
                "query": payload.get("query", ""),
                "history_size": len(payload.get("history", []) if isinstance(payload.get("history"), list) else []),
                "tenant_id": payload.get("tenant_id", ""),
                "actor_type": payload.get("actor_type", ""),
                "channel": payload.get("channel", ""),
            },
            "output_json": {
                "run_id": result.get("run_id", ""),
                "trace_id": result.get("trace_id", ""),
            },
            "decision_json": {
                "action_mode": payload.get("action_mode", "auto"),
                "human_decision": payload.get("human_decision", {}),
            },
            "params_json": l0_params,
            "config_fingerprint": _fingerprint(l0_params),
            "metrics_json": {"latency_ms": round(elapsed_ms, 2)},
            "latency_ms": float(elapsed_ms),
        },
        {
            "layer_code": "L1",
            "status": "ok",
            "input_json": {"query": payload.get("query", "")},
            "output_json": {
                "route_target": result.get("route_target", ""),
                "aftersales_mode": result.get("aftersales_mode", ""),
                "status": result.get("status", ""),
            },
            "decision_json": {
                "handoff_required": sec1.get("handoff_required", "false"),
            },
            "params_json": {
                "intent_conf_threshold": env_versions.get("intent_conf_threshold", ""),
                "risk_high_force_human": env_versions.get("risk_high_force_human", ""),
            },
            "config_fingerprint": _fingerprint(
                {
                    "intent_conf_threshold": env_versions.get("intent_conf_threshold", ""),
                    "risk_high_force_human": env_versions.get("risk_high_force_human", ""),
                }
            ),
            "metrics_json": {"layer_latency_ms": sec1.get("latency_ms", 0.0)},
            "latency_ms": float(sec1.get("latency_ms", 0.0) or 0.0),
        },
        {
            "layer_code": "L2",
            "status": "ok",
            "input_json": {"route_target": result.get("route_target", "")},
            "output_json": {
                "cache_decision": sec2.get("decision", ""),
                "cache_level": sec2.get("level", ""),
                "cache_writeback": sec2.get("writeback", ""),
                "cache_admitted": sec2.get("admitted", ""),
            },
            "decision_json": {
                "served_by_cache": bool(result.get("served_by_cache", False)),
            },
            "params_json": l2_params,
            "config_fingerprint": _fingerprint(l2_params),
            "metrics_json": {},
            "latency_ms": 0.0,
        },
        {
            "layer_code": "L3",
            "status": "ok",
            "input_json": {
                "route_target": result.get("route_target", ""),
                "cache_decision": sec2.get("decision", ""),
            },
            "output_json": {
                "need_rag": sec3.get("need_rag", "false"),
                "rag_enabled": sec3.get("enabled", "false"),
                "rag_mode": sec3.get("mode", "none"),
                "retrieved_count": int(sec3.get("retrieved_count", 0) or 0),
                "llm_context_chars": int(sec4.get("llm_context_chars", 0) or 0),
                "memory_used_chars": int(sec4.get("memory_used_chars", 0) or 0),
            },
            "decision_json": {
                "context_has_text": sec4.get("context_has_text", "false"),
            },
            "params_json": l3_params,
            "config_fingerprint": _fingerprint(l3_params),
            "metrics_json": {},
            "latency_ms": 0.0,
        },
        {
            "layer_code": "L4",
            "status": "ok",
            "input_json": {
                "route_target": result.get("route_target", ""),
                "rag_mode": sec3.get("mode", "none"),
            },
            "output_json": {
                "status": result.get("status", ""),
                "answer": result.get("answer", ""),
                "citations": result.get("citations", []),
                "pending_action": result.get("pending_action", {}),
                "handoff_required": bool(result.get("handoff_required", False)),
            },
            "decision_json": {
                "current_stage": result.get("current_stage", ""),
                "stage_status": result.get("stage_status", ""),
            },
            "params_json": {
                "llm_mode": env_versions.get("llm_mode", ""),
                "llm_model": env_versions.get("llm_model", ""),
                "prompt_version": env_versions.get("prompt_version", ""),
                "policy_version": env_versions.get("policy_version", ""),
            },
            "config_fingerprint": _fingerprint(
                {
                    "llm_mode": env_versions.get("llm_mode", ""),
                    "llm_model": env_versions.get("llm_model", ""),
                    "prompt_version": env_versions.get("prompt_version", ""),
                    "policy_version": env_versions.get("policy_version", ""),
                }
            ),
            "metrics_json": {
                "citation_count": len(result.get("citations", []) if isinstance(result.get("citations"), list) else []),
            },
            "latency_ms": 0.0,
        },
        {
            "layer_code": "LX",
            "status": "ok",
            "input_json": {},
            "output_json": {
                "workflow": sec5,
                "tools_mcp": sec6,
                "infra_degrade": sec8,
                "node_trace": node_trace,
            },
            "decision_json": {
                "first_node": node_trace[0] if node_trace else "",
                "last_node": node_trace[-1] if node_trace else "",
            },
            "params_json": {},
            "config_fingerprint": _fingerprint({}),
            "metrics_json": {"node_trace_len": len(node_trace)},
            "latency_ms": 0.0,
        },
    ]
    return snapshots

