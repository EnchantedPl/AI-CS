from typing import Any, Dict, List
from app.observability.langsmith_tracing import traceable


@traceable(name="refund_policy_skill", run_type="chain")
def evaluate_refund_policy(query: str, tool_result: Dict[str, Any]) -> Dict[str, Any]:
    order = tool_result.get("order_query_tool", {})
    ticket = tool_result.get("ticket_query_tool", {})
    logistics = tool_result.get("logistics_query_tool", {})
    q = str(query or "").lower()
    delivered_days = int(order.get("days_since_delivery", 0) or 0)
    amount = float(order.get("amount", 0) or 0)
    category = str(order.get("category", "general") or "general")
    paid_channel = str(order.get("paid_channel", "online") or "online")
    member_level = str(order.get("member_level", "normal") or "normal")
    is_cross_border = bool(order.get("cross_border", False))
    used_flag = bool(order.get("used", False))
    has_opening_video = bool(logistics.get("opening_video", False))
    has_damage_photo = bool(logistics.get("damage_photo", False))
    damage_reported = bool(logistics.get("damage_reported", False))
    has_open_ticket = bool(ticket.get("open_ticket", False))
    fraud_marked = bool(ticket.get("fraud_marked", False))
    prior_refund_count = int(ticket.get("prior_refund_count_30d", 0) or 0)

    # Policy windows: normal 7 days, quality issue 15 days, cross-border stricter (5 days).
    base_window_days = 5 if is_cross_border else 7
    quality_window_days = 15
    within_base_window = delivered_days <= base_window_days
    within_quality_window = delivered_days <= quality_window_days
    quality_issue = damage_reported or ("破损" in q) or ("质量" in q)

    non_returnable_category = category in {"fresh_food", "virtual_service", "customized"}
    evidence_missing = quality_issue and not (has_opening_video or has_damage_photo)
    high_amount = amount >= 1000
    medium_amount = amount >= 500
    frequent_refund = prior_refund_count >= 3
    manual_required = (
        bool(ticket.get("manual_approval_required", False))
        or medium_amount
        or fraud_marked
        or frequent_refund
        or evidence_missing
        or non_returnable_category
    )

    eligible = False
    eligibility_reason = "out_of_policy_window"
    if non_returnable_category:
        eligible = False
        eligibility_reason = "non_returnable_category"
    elif quality_issue and within_quality_window:
        eligible = True
        eligibility_reason = "quality_issue_within_15_days"
    elif within_base_window and not used_flag:
        eligible = True
        eligibility_reason = "within_7_day_no_reason_window"

    risk_level = "high" if (high_amount or fraud_marked or frequent_refund) else ("medium" if manual_required or has_open_ticket else "low")
    reasons: List[str] = []
    reason_labels: Dict[str, str] = {}
    if within_base_window:
        reasons.append("within_base_window")
        reason_labels["within_base_window"] = f"SOP-R01 无理由窗口：签收后{base_window_days}天内可发起退货退款（商品需保持完好）"
    if quality_issue and within_quality_window:
        reasons.append("quality_issue_within_15_days")
        reason_labels["quality_issue_within_15_days"] = "SOP-R02 质量保障：质量/破损问题在签收后15天内，优先按质量责任通道受理"
    if damage_reported:
        reasons.append("damage_reported")
        reason_labels["damage_reported"] = "SOP-R03 物流破损：物流链路已上报破损，进入快速核损并可优先审核"
    if non_returnable_category:
        reasons.append("non_returnable_category")
        reason_labels["non_returnable_category"] = "SOP-R04 特殊类目：生鲜/虚拟服务/定制类商品原则上不支持直接退货退款，需人工特批"
    if evidence_missing:
        reasons.append("evidence_missing")
        reason_labels["evidence_missing"] = "SOP-R05 凭证缺失：质量类申请需提供开箱视频或破损照片，否则转人工补件"
    if used_flag:
        reasons.append("used_or_activated")
        reason_labels["used_or_activated"] = "SOP-R06 使用状态：商品已使用/激活，需根据折损率人工核定退款比例"
    if has_open_ticket:
        reasons.append("existing_open_ticket")
        reason_labels["existing_open_ticket"] = "SOP-R07 重单控制：存在进行中售后工单，需并单处理避免重复受理"
    if frequent_refund:
        reasons.append("frequent_refund")
        reason_labels["frequent_refund"] = "SOP-R08 频次风控：近30天退款次数偏高，需二线风控复核"
    if fraud_marked:
        reasons.append("fraud_marked")
        reason_labels["fraud_marked"] = "SOP-R09 风险命中：命中历史风控标签，需人工审核并留痕"
    if not reasons:
        reasons.append("out_of_policy")
        reason_labels["out_of_policy"] = "SOP-R10 超窗处理：超出政策受理时效且未提供有效质量凭证，默认不支持自动退款"
    clause_id_map: Dict[str, str] = {
        "within_base_window": "SOP-R01",
        "quality_issue_within_15_days": "SOP-R02",
        "damage_reported": "SOP-R03",
        "non_returnable_category": "SOP-R04",
        "evidence_missing": "SOP-R05",
        "used_or_activated": "SOP-R06",
        "existing_open_ticket": "SOP-R07",
        "frequent_refund": "SOP-R08",
        "fraud_marked": "SOP-R09",
        "out_of_policy": "SOP-R10",
    }
    matched_clause_ids: List[str] = []
    for r in reasons:
        cid = clause_id_map.get(str(r))
        if cid and cid not in matched_clause_ids:
            matched_clause_ids.append(cid)

    suggested_channel = "refund_to_original_path"
    if paid_channel in {"cod", "bank_transfer"}:
        suggested_channel = "manual_bank_refund"

    review_sla = "系统自动审核，通常 30 分钟内完成"
    settlement_sla = "原路退款 T+1 到 T+3 个工作日到账"
    if manual_required:
        review_sla = "人工复核 24 小时内（高峰期可延长至 48 小时）"
        settlement_sla = "审核通过后原路退款 T+3 到 T+7 个工作日到账"
    if is_cross_border:
        review_sla = "跨境单人工复核 48 小时内"
        settlement_sla = "审核通过后原路退款 T+5 到 T+10 个工作日到账"
    combined_sla = f"{review_sla}；{settlement_sla}"

    if quality_issue:
        evidence_required = ["order_proof", "damage_photo", "opening_video"]
    elif used_flag:
        evidence_required = ["order_proof", "product_status_photo"]
    else:
        evidence_required = ["order_proof"]

    policy_clauses = [
        f"签收后{base_window_days}天内支持无理由退货退款，商品需保持完好不影响二次销售。",
        f"质量/破损问题在签收后{quality_window_days}天内可申请质量责任退款。",
        "特殊类目（生鲜/虚拟服务/定制）默认不支持自动退款，需人工特批。",
        "质量类申请需提交开箱视频或破损照片；证据不足时转补件流程。",
        "高风险订单（高金额/高频退款/风控命中）进入人工复核并保留审计记录。",
    ]
    explain_short = (
        f"本单命中{', '.join(matched_clause_ids) or 'SOP-R10'}，"
        f"{'满足' if eligible else '暂不满足'}自动退款条件，"
        f"{'需人工复核' if manual_required else '可走自动审核'}。"
    )

    return {
        "eligible": eligible,
        "eligibility_reason": eligibility_reason,
        "risk_level": risk_level,
        "manual_required": manual_required,
        "reasons": reasons,
        "reason_labels": reason_labels,
        "policy_version": "refund_policy_v2026.04",
        "policy_scope": {
            "tenant": "demo",
            "scene": "aftersales_refund",
            "channels": ["app", "web", "mini_program"],
            "cross_border_supported": True,
        },
        "matched_clause_ids": matched_clause_ids,
        "policy_clauses": policy_clauses,
        "explain_short": explain_short,
        "return_freight_rule": {
            "quality_issue": "质量/破损由商家承担退回运费，可按上限 25 元补贴。",
            "non_quality_issue": "无理由退货由用户先行垫付；商家购买运费险时可自动抵扣。",
            "cross_border": "跨境退货运费按线路实报实销，需保留国际物流面单。",
        },
        "service_fee_rule": {
            "default": "已发货且非质量问题，原订单服务费不退。",
            "quality_issue": "确认质量责任后，服务费与订单金额一并退回。",
            "risk_freeze": "命中风控冻结时，服务费退款需待风控结案后处理。",
        },
        "depreciation_rule": {
            "unused": "商品未拆封未使用，不计折损。",
            "used": "商品已使用/激活按 10%-30%折损区间评估，具体以质检结论为准。",
            "missing_accessories": "缺少配件按配件成本扣减后退款。",
        },
        "policy_window_days": {
            "base": base_window_days,
            "quality_issue": quality_window_days,
        },
        "evidence_required": evidence_required,
        "evidence_guidance": {
            "required": evidence_required,
            "supplement_deadline": "补件通知后 48 小时内上传，逾期工单自动挂起。",
            "quality_issue_examples": [
                "外包装六面照片（含物流面单）",
                "破损细节近景 2 张以上",
                "30 秒以上连续开箱视频",
            ],
            "supplement_if_missing": "缺少关键凭证时进入补件队列，最多补件 2 轮。",
        },
        "suggested_refund_channel": suggested_channel,
        "review_sla": review_sla,
        "settlement_sla": settlement_sla,
        "refund_sla": combined_sla,
        "special_constraints": {
            "non_returnable_category": non_returnable_category,
            "cross_border": is_cross_border,
            "used_or_activated": used_flag,
            "member_level": member_level,
        },
        "confidence": 0.91 if eligible and not manual_required else (0.84 if eligible else 0.68),
    }


@traceable(name="aftersales_plan_skill", run_type="chain")
def generate_aftersales_plan(policy_eval: Dict[str, Any], tool_result: Dict[str, Any]) -> Dict[str, Any]:
    order = tool_result.get("order_query_tool", {})
    order_id = str(order.get("order_id", "unknown"))
    refund_sla = str(policy_eval.get("refund_sla", "T+1 到 T+3 个工作日"))
    manual_required = bool(policy_eval.get("manual_required", False))
    evidence_required = policy_eval.get("evidence_required", [])
    reason_labels = policy_eval.get("reason_labels", {}) if isinstance(policy_eval.get("reason_labels"), dict) else {}
    reason_text = "；".join([str(v) for v in reason_labels.values()][:3])
    matched_clause_ids = [str(x) for x in (policy_eval.get("matched_clause_ids", []) or []) if str(x).strip()]
    explain_short = str(policy_eval.get("explain_short", "") or "").strip()
    if not policy_eval.get("eligible", False):
        steps = [
            "请补充凭证：商品照片、问题描述、订单支付凭证。",
            "系统将自动生成人工复核工单，客服会在24小时内联系你补充材料。",
            "复核通过后进入退款流程，若不通过会同步驳回原因并给出申诉入口。",
        ]
        customer_message = f"订单{order_id} 当前不满足自动退款条件，已为你转人工复核。"
    else:
        if manual_required:
            steps = [
                "系统已通过规则初审，但命中风控条件，需人工复核后放款。",
                f"请补齐材料：{', '.join([str(x) for x in evidence_required]) or '订单凭证'}。",
                f"复核通过后原路退款，预计{refund_sla}。",
            ]
            customer_message = f"订单{order_id} 满足退款条件，但需人工复核后退款。"
        else:
            steps = [
                "系统已通过初审，可直接提交退款申请。",
                f"退款将按原支付路径返还，预计{refund_sla}。",
                "到账后你会收到站内消息和短信提醒。",
            ]
            customer_message = f"订单{order_id} 满足退款条件，建议立即发起退款流程。"
    return {
        "steps": steps,
        "eta": refund_sla,
        "policy_summary": "；".join(
            [x for x in [("命中条款: " + ",".join(matched_clause_ids)) if matched_clause_ids else "", explain_short, reason_text] if x]
        ),
        "customer_message": customer_message,
    }
