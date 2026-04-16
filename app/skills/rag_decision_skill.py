from typing import Any, Dict

from app.observability.langsmith_tracing import traceable


@traceable(name="rag_decision_skill", run_type="chain")
def decide_rag_plan(
    *,
    query: str,
    route_target: str,
    aftersales_mode: str,
    tool_result: Dict[str, Any],
    policy_result: Dict[str, Any],
) -> Dict[str, Any]:
    if route_target == "risk_query":
        return {"need_rag": False, "reason": "risk_query_skip", "mode": "skip"}
    if route_target == "aftersales" and aftersales_mode == "complex":
        # Complex aftersales should rely on tool/policy/action facts by default.
        return {"need_rag": False, "reason": "aftersales_complex_use_facts", "mode": "skip"}

    q = (query or "").lower()
    should_boost = any(k in q for k in ["规则", "政策", "条款", "依据", "文档", "说明书"])
    if should_boost:
        return {
            "need_rag": True,
            "reason": "query_needs_reference",
            "mode": "hybrid",
            "topk_hint": 6,
        }
    if tool_result or policy_result:
        return {"need_rag": False, "reason": "tool_policy_sufficient", "mode": "skip"}
    return {"need_rag": True, "reason": "default_open_domain", "mode": "hybrid", "topk_hint": 4}
