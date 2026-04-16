from typing import Any, Dict

from app.demo.mock_scenarios import demo_aftersales_tool_snapshot
from app.observability.langsmith_tracing import traceable


@traceable(name="order_query_tool", run_type="tool")
def order_query_tool(query: str) -> Dict[str, Any]:
    snap = demo_aftersales_tool_snapshot(query)
    if snap and isinstance(snap.get("order_query_tool"), dict):
        return dict(snap["order_query_tool"])
    text = (query or "").lower()
    order_amount = 299 if any(k in text for k in ["手机", "phone"]) else 89
    return {
        "status": "success",
        "order_id": "MOCK-AF-10086",
        "paid": True,
        "delivered": True,
        "days_since_delivery": 5,
        "amount": order_amount,
        "item_category": "electronics" if order_amount >= 200 else "general",
    }


@traceable(name="ticket_query_tool", run_type="tool")
def ticket_query_tool(query: str) -> Dict[str, Any]:
    snap = demo_aftersales_tool_snapshot(query)
    if snap and isinstance(snap.get("ticket_query_tool"), dict):
        return dict(snap["ticket_query_tool"])
    text = (query or "").lower()
    risk_level = "high" if any(k in text for k in ["投诉", "法律", "仲裁", "升级"]) else "medium"
    return {
        "status": "success",
        "open_ticket": True,
        "previous_refund_attempts": 1,
        "manual_approval_required": risk_level == "high",
    }


@traceable(name="logistics_query_tool", run_type="tool")
def logistics_query_tool(query: str) -> Dict[str, Any]:
    snap = demo_aftersales_tool_snapshot(query)
    if snap and isinstance(snap.get("logistics_query_tool"), dict):
        return dict(snap["logistics_query_tool"])
    text = (query or "").lower()
    return {
        "status": "success",
        "signed": True,
        "damage_reported": any(k in text for k in ["损坏", "坏了", "质量"]),
    }


def run_aftersales_complex_tools(query: str) -> Dict[str, Any]:
    return {
        "order_query_tool": order_query_tool(query),
        "ticket_query_tool": ticket_query_tool(query),
        "logistics_query_tool": logistics_query_tool(query),
    }
