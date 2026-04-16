import os
from typing import Any, Dict, List, Optional, Tuple


def _norm(text: str) -> str:
    return (text or "").strip().lower()


def is_demo_fixed_scenario_enabled() -> bool:
    # Default on for demo experience; set DEMO_FIXED_SCENARIO=false to restore dynamic behavior.
    return os.getenv("DEMO_FIXED_SCENARIO", "true").strip().lower() in {"1", "true", "yes", "on"}


_SCENARIOS: List[Dict[str, Any]] = [
    {
        "name": "faq_shipping_time",
        "keywords": ["什么时候发货", "多久发货", "发货时间"],
        "route_target": "faq",
        "aftersales_mode": "",
        "answer": "亲，常规商品会在付款后 24 小时内出库，预售商品以商品页承诺时间为准。你可在【我的订单-物流详情】查看实时进度。",
        "rag_chunks": [
            {
                "chunk_id": "demo-faq-ship-1",
                "doc_id": "demo_orders_products_faq",
                "source_name": "demo_orders_products_faq.md",
                "domain": "faq",
                "lang": "zh",
                "text": "发货时效：现货订单支付后24小时内出库，节假日顺延；预售订单以商品页预计发货时间为准。",
                "score": 0.95,
            }
        ],
    },
    {
        "name": "faq_update_address",
        "keywords": ["修改收货地址", "地址改", "改地址"],
        "route_target": "faq",
        "aftersales_mode": "",
        "answer": "若订单未出库，可在【我的订单】提交地址修改；若已出库，需要联系客服拦截或改派，最终以物流公司处理结果为准。",
        "rag_chunks": [
            {
                "chunk_id": "demo-faq-address-1",
                "doc_id": "demo_orders_products_faq",
                "source_name": "demo_orders_products_faq.md",
                "domain": "faq",
                "lang": "zh",
                "text": "地址修改规则：未出库可自助修改，已出库需人工拦截；拦截失败将按原地址配送。",
                "score": 0.93,
            }
        ],
    },
    {
        "name": "faq_order_where",
        "keywords": ["在哪里查看我购买的东西", "查看订单", "我的订单在哪"],
        "route_target": "faq",
        "aftersales_mode": "",
        "answer": "你可以在小程序【我的】-【订单记录】查看全部订单；点击订单可查看支付、发货和售后进度。",
        "rag_chunks": [
            {
                "chunk_id": "demo-faq-order-1",
                "doc_id": "demo_orders_products_faq",
                "source_name": "demo_orders_products_faq.md",
                "domain": "faq",
                "lang": "zh",
                "text": "订单查看入口：小程序“我的-订单记录”；支持查看支付状态、物流轨迹与售后处理进度。",
                "score": 0.94,
            }
        ],
    },
    {
        "name": "aftersales_simple_refund_process",
        "keywords": ["退款流程怎么走", "退货流程", "申请退款流程"],
        "route_target": "aftersales",
        "aftersales_mode": "simple",
        "answer": "退款流程：提交申请 -> 平台审核 -> 商家确认 -> 原路退款。一般 1-3 个工作日到账，特殊支付渠道最长 5 个工作日。",
        "rag_chunks": [
            {
                "chunk_id": "demo-aftersales-simple-1",
                "doc_id": "demo_orders_products_faq",
                "source_name": "demo_orders_products_faq.md",
                "domain": "aftersales",
                "lang": "zh",
                "text": "退款标准流程：提交退款申请、平台审核、商家确认、原路退款。审核通过后通常1-3个工作日到账。",
                "score": 0.92,
            }
        ],
    },
    {
        "name": "aftersales_complex_damage_refund",
        "keywords": ["商品破损", "商品损坏", "质量问题 退款", "收货后损坏"],
        "route_target": "aftersales",
        "aftersales_mode": "complex",
        "answer": "已进入复杂售后审核流程：系统将先核验订单、工单和物流证据，再由客服决定是否执行退款或回退复核。",
        "action_plan": [
            {
                "name": "approval_submit_mcp",
                "args": {
                    "reason": "damage_claim_need_risk_review",
                    "business_title": "动作1：提交风控审批单",
                    "business_desc": "先提交风控审批，确认破损证据、责任归属和退款权限，再进入财务退款。",
                    "risk_tip": "该订单涉及破损争议与历史售后记录，需要人工复核后继续。",
                },
            },
            {
                "name": "refund_submit_mcp",
                "args": {
                    "reason": "post_risk_approval_refund",
                    "amount": 399.0,
                    "business_title": "动作2：执行退款",
                    "business_desc": "风控通过后，按订单实付金额提交退款，通知用户预计到账时效。",
                    "risk_tip": "退款为资金类副作用动作，执行前需二次人工确认。",
                },
            },
        ],
        "rag_chunks": [],
    },
    {
        "name": "risk_query_legal",
        "keywords": ["投诉", "法律", "维权", "仲裁", "违规", "合规风险"],
        "route_target": "risk_query",
        "aftersales_mode": "",
        "answer": "该问题涉及风险与合规判断，系统将直接转人工客服处理。",
        "rag_chunks": [],
    },
    {
        "name": "product_info_specs",
        "keywords": ["商品参数", "这款商品怎么样", "规格", "价格", "库存"],
        "route_target": "product_info",
        "aftersales_mode": "",
        "answer": "这款商品当前活动价 199 元，库存充足，支持 7 天无理由退货。核心参数：2L 容量、食品级材质、全国联保。",
        "rag_chunks": [
            {
                "chunk_id": "demo-product-1",
                "doc_id": "demo_orders_products_faq",
                "source_name": "demo_orders_products_faq.md",
                "domain": "product_info",
                "lang": "zh",
                "text": "商品X：活动价199元，库存状态in_stock，支持7天无理由；规格为2L容量，食品级材质。",
                "score": 0.96,
            }
        ],
    },
]


def match_demo_scenario(query: str) -> Optional[Dict[str, Any]]:
    if not is_demo_fixed_scenario_enabled():
        return None
    q = _norm(query)
    if not q:
        return None
    for item in _SCENARIOS:
        if any(k in q for k in item.get("keywords", [])):
            return item
    return None


def infer_demo_intent(query: str) -> Optional[Tuple[str, str]]:
    matched = match_demo_scenario(query)
    if not matched:
        return None
    return str(matched.get("route_target", "")), str(matched.get("aftersales_mode", ""))


def demo_rag_result(query: str, domain: str, mode: str) -> Optional[Dict[str, Any]]:
    matched = match_demo_scenario(query)
    if not matched:
        return None
    if str(matched.get("route_target", "")) != str(domain or ""):
        return None
    chunks = list(matched.get("rag_chunks", []) or [])
    citations = [f"{c.get('source_name', 'demo')}#{c.get('chunk_id', '')}" for c in chunks]
    context = "\n".join([str(c.get("text", "") or "") for c in chunks]).strip()
    return {
        "enabled": True,
        "mode": mode,
        "timings_ms": {"vector": 4.0, "keyword": 2.0, "fusion": 1.0, "rerank": 0.0, "total": 8.0},
        "params": {
            "vector_topk": len(chunks),
            "keyword_topk": len(chunks),
            "final_topk": len(chunks),
            "rrf_k": 60,
            "rrf_weights": {"vector": 0.7, "keyword": 0.3},
            "rerank": {"enabled": False, "applied": False, "before_ids": [], "after_ids": [], "top_scores": [], "low_score_ratio": 0.0, "error": None},
        },
        "filters": {"domain": domain, "lang": "zh", "kb_version": "demo_v1", "is_active": True, "embedding_mode": "mock", "embedding_model": "mock"},
        "candidates": {
            "vector": [{"chunk_id": c.get("chunk_id", ""), "score": round(float(c.get("score", 0.9)), 6)} for c in chunks],
            "keyword": [{"chunk_id": c.get("chunk_id", ""), "score": round(float(c.get("score", 0.9)), 6)} for c in chunks],
            "fused": [{"chunk_id": c.get("chunk_id", ""), "score": round(float(c.get("score", 0.9)), 6)} for c in chunks],
        },
        "chunks": chunks,
        "citations": citations,
        "context": context,
    }


def demo_answer(query: str) -> Optional[str]:
    matched = match_demo_scenario(query)
    if not matched:
        return None
    return str(matched.get("answer", "") or "")


def demo_product_tool(query: str) -> Optional[Dict[str, Any]]:
    matched = match_demo_scenario(query)
    if not matched or str(matched.get("route_target", "")) != "product_info":
        return None
    return {
        "tool_name": "mock_product_catalog_lookup",
        "tool_status": "success",
        "data": {
            "product_name": "柚汇净饮壶2L",
            "sku": "YH-POT-2L",
            "price": "199.00",
            "stock_hint": "in_stock",
            "delivery_sla": "24小时内出库",
            "return_policy": "7天无理由",
        },
    }


def demo_aftersales_tool_snapshot(query: str) -> Optional[Dict[str, Dict[str, Any]]]:
    matched = match_demo_scenario(query)
    if not matched or str(matched.get("route_target", "")) != "aftersales":
        return None
    is_complex = str(matched.get("aftersales_mode", "")) == "complex"
    if is_complex:
        return {
            "order_query_tool": {
                "status": "success",
                "order_id": "MOCK-AF-230901",
                "paid": True,
                "delivered": True,
                "days_since_delivery": 2,
                "amount": 399,
                "item_category": "small_appliance",
                "item_name": "柚汇净饮壶2L",
            },
            "ticket_query_tool": {
                "status": "success",
                "open_ticket": True,
                "previous_refund_attempts": 1,
                "manual_approval_required": True,
                "ticket_id": "TK-88321",
            },
            "logistics_query_tool": {
                "status": "success",
                "signed": True,
                "damage_reported": True,
                "carrier": "顺丰",
                "sign_time": "2026-04-14 10:22:00",
            },
        }
    return {
        "order_query_tool": {
            "status": "success",
            "order_id": "MOCK-AF-230777",
            "paid": True,
            "delivered": False,
            "days_since_delivery": 0,
            "amount": 199,
            "item_category": "daily_goods",
            "item_name": "柚汇清洁套装",
        },
        "ticket_query_tool": {
            "status": "success",
            "open_ticket": False,
            "previous_refund_attempts": 0,
            "manual_approval_required": False,
            "ticket_id": "",
        },
        "logistics_query_tool": {
            "status": "success",
            "signed": False,
            "damage_reported": False,
            "carrier": "中通",
            "sign_time": "",
        },
    }


def demo_aftersales_action_plan(query: str) -> Optional[List[Dict[str, Any]]]:
    matched = match_demo_scenario(query)
    if not matched and is_demo_fixed_scenario_enabled():
        # Resume turns like "客服操作: approve" won't match keywords; for demo mode we
        # still need the same deterministic complex-after-sales plan.
        matched = next(
            (
                x
                for x in _SCENARIOS
                if str(x.get("route_target", "")) == "aftersales" and str(x.get("aftersales_mode", "")) == "complex"
            ),
            None,
        )
    if not matched:
        return None
    if str(matched.get("route_target", "")) != "aftersales" or str(matched.get("aftersales_mode", "")) != "complex":
        return None
    plan = matched.get("action_plan", [])
    if not isinstance(plan, list) or not plan:
        return None
    normalized: List[Dict[str, Any]] = []
    for item in plan:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "") or "").strip()
        if not name:
            continue
        args = item.get("args", {}) if isinstance(item.get("args"), dict) else {}
        normalized.append({"name": name, "args": args})
    return normalized or None

