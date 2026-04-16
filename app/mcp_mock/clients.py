import random
import time
from typing import Any, Dict
from app.demo.mock_scenarios import is_demo_fixed_scenario_enabled
from app.observability.langsmith_tracing import traceable


def _base_resp(ok: bool, data: Dict[str, Any], error: str = "") -> Dict[str, Any]:
    return {
        "ok": ok,
        "status_code": 200 if ok else 500,
        "data": data if ok else {},
        "error_message": error,
        "latency_ms": 0.0,
    }


@traceable(name="refund_submit_mcp", run_type="tool")
def refund_submit_mcp(*, order_id: str, amount: float, reason: str, idempotency_key: str) -> Dict[str, Any]:
    # mock external side effect with occasional transient failures
    t0 = time.perf_counter()
    if (not is_demo_fixed_scenario_enabled()) and random.random() < 0.08:
        resp = _base_resp(False, {}, "upstream_timeout")
        resp["latency_ms"] = round((time.perf_counter() - t0) * 1000.0, 3)
        return resp
    time.sleep(random.uniform(0.005, 0.03))
    resp = _base_resp(
        True,
        {
            "refund_id": f"rf_{idempotency_key[-8:]}",
            "order_id": order_id,
            "approved_amount": amount,
            "reason": reason,
            "status": "submitted",
        },
    )
    resp["latency_ms"] = round((time.perf_counter() - t0) * 1000.0, 3)
    return resp


@traceable(name="ticket_upgrade_mcp", run_type="tool")
def ticket_upgrade_mcp(*, order_id: str, priority: str, note: str, idempotency_key: str) -> Dict[str, Any]:
    t0 = time.perf_counter()
    if (not is_demo_fixed_scenario_enabled()) and random.random() < 0.05:
        resp = _base_resp(False, {}, "ticket_system_unavailable")
        resp["latency_ms"] = round((time.perf_counter() - t0) * 1000.0, 3)
        return resp
    time.sleep(random.uniform(0.005, 0.02))
    resp = _base_resp(
        True,
        {
            "ticket_id": f"tk_{idempotency_key[-8:]}",
            "order_id": order_id,
            "priority": priority,
            "status": "upgraded",
            "note": note,
        },
    )
    resp["latency_ms"] = round((time.perf_counter() - t0) * 1000.0, 3)
    return resp


@traceable(name="approval_submit_mcp", run_type="tool")
def approval_submit_mcp(*, order_id: str, amount: float, reason: str, idempotency_key: str) -> Dict[str, Any]:
    t0 = time.perf_counter()
    if (not is_demo_fixed_scenario_enabled()) and random.random() < 0.05:
        resp = _base_resp(False, {}, "approval_gateway_error")
        resp["latency_ms"] = round((time.perf_counter() - t0) * 1000.0, 3)
        return resp
    time.sleep(random.uniform(0.005, 0.02))
    resp = _base_resp(
        True,
        {
            "approval_id": f"ap_{idempotency_key[-8:]}",
            "order_id": order_id,
            "amount": amount,
            "decision": "pending",
            "reason": reason,
        },
    )
    resp["latency_ms"] = round((time.perf_counter() - t0) * 1000.0, 3)
    return resp
