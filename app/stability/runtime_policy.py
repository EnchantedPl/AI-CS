import os
from typing import Any, Dict, Tuple


def estimate_route_bucket(query: str) -> str:
    text = (query or "").strip().lower()
    if any(k in text for k in ["法律", "诉讼", "合规", "投诉", "律师函", "隐私", "数据泄露", "risk", "legal"]):
        return "risk_query"
    if any(k in text for k in ["退款", "退货", "售后", "工单", "换货", "仲裁", "aftersales"]):
        return "aftersales"
    if any(k in text for k in ["价格", "库存", "参数", "规格", "物流", "发票"]):
        return "faq"
    return "faq"


def infer_priority_tier(payload: Dict[str, Any]) -> Tuple[str, str]:
    query = str(payload.get("query", "") or "")
    route_est = estimate_route_bucket(query)
    action_mode = str(payload.get("action_mode", "auto") or "auto").lower()
    resume_checkpoint_id = str(payload.get("resume_checkpoint_id", "") or "")
    run_id = str(payload.get("run_id", "") or "")

    if route_est == "risk_query":
        return "high", "risk_query"
    if action_mode in {"continue", "rewind"} or bool(resume_checkpoint_id) or bool(run_id):
        return "high", "workflow_resume_or_rewind"
    return "low", "default"


def resolve_degrade_level(priority_tier: str, quota_state: Dict[str, Any]) -> str:
    tier = "high" if str(priority_tier).lower() == "high" else "low"
    req_left = float(quota_state.get("req_tokens_left", 0.0) or 0.0)
    tok_left = float(quota_state.get("llm_tokens_left", 0.0) or 0.0)
    req_cap = max(1.0, float(quota_state.get("req_tokens_capacity", 1.0) or 1.0))
    tok_cap = max(1.0, float(quota_state.get("llm_tokens_capacity", 1.0) or 1.0))
    left_ratio = min(req_left / req_cap, tok_left / tok_cap)

    if tier == "high":
        soft = float(os.getenv("DEGRADE_SOFT_RATIO_HIGH", "0.18"))
        hard = float(os.getenv("DEGRADE_HARD_RATIO_HIGH", "0.08"))
    else:
        soft = float(os.getenv("DEGRADE_SOFT_RATIO_LOW", "0.28"))
        hard = float(os.getenv("DEGRADE_HARD_RATIO_LOW", "0.14"))

    if left_ratio <= hard:
        return "L2"
    if left_ratio <= soft:
        return "L1"
    return "L0"


def build_runtime_policy(priority_tier: str, degrade_level: str, quota_state: Dict[str, Any]) -> Dict[str, Any]:
    level = str(degrade_level or "L0").upper()
    if level not in {"L0", "L1", "L2"}:
        level = "L0"

    rag_enabled = level != "L2"
    rag_max_chunks = 3 if level == "L0" else (2 if level == "L1" else 0)
    context_budget_scale = 1.0 if level == "L0" else (0.82 if level == "L1" else 0.62)
    memory_budget_scale = 1.0 if level == "L0" else (0.75 if level == "L1" else 0.45)
    tool_enabled = level != "L2"
    tool_timeout_scale = 1.0 if level == "L0" else (0.85 if level == "L1" else 0.65)
    tool_retry_cap = 1 if level in {"L0", "L1"} else 0

    return {
        "priority_tier": "high" if str(priority_tier).lower() == "high" else "low",
        "degrade_level": level,
        "quota_state": quota_state,
        "chain_controls": {
            "rag": {
                "enabled": rag_enabled,
                "max_chunks": rag_max_chunks,
            },
            "context": {
                "budget_scale": context_budget_scale,
                "memory_budget_scale": memory_budget_scale,
                "drop_tool_facts": level == "L2",
            },
            "tool": {
                "enabled": tool_enabled,
                "timeout_scale": tool_timeout_scale,
                "retry_cap": tool_retry_cap,
            },
        },
    }
