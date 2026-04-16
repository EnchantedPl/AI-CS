from typing import Any, Dict

from app.demo.mock_scenarios import demo_product_tool

def run_mock_tool(route_target: str, query: str) -> Dict[str, Any]:
    text = query.lower()
    if route_target == "aftersales":
        return {
            "tool_name": "mock_refund_status_lookup",
            "tool_status": "success",
            "data": {
                "order_id": "MOCK-ORDER-1001",
                "refund_status": "processing",
                "eta_days": 2,
            },
        }
    if route_target == "product_info":
        demo_tool = demo_product_tool(query)
        if demo_tool:
            return demo_tool
        in_stock = "库存" in text or "stock" in text
        return {
            "tool_name": "mock_product_catalog_lookup",
            "tool_status": "success",
            "data": {
                "product_name": "Demo Product X",
                "price": "199.00",
                "stock_hint": "in_stock" if in_stock else "unknown",
            },
        }
    return {
        "tool_name": "none",
        "tool_status": "skipped",
        "data": {},
    }

