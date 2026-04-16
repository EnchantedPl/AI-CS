#!/usr/bin/env python3
import argparse
import json
import random
import statistics
import time
import uuid
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _p95(values: List[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = int(round(0.95 * (len(s) - 1)))
    return float(s[max(0, min(idx, len(s) - 1))])


def _safe_div(a: float, b: float) -> float:
    return float(a) / float(b) if b else 0.0


def _post_chat(base_url: str, payload: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post_with_retry(
    base_url: str,
    payload: Dict[str, Any],
    *,
    timeout: float,
    retries: int,
    backoff: float,
) -> Tuple[Optional[Dict[str, Any]], Optional[str], float]:
    started = time.perf_counter()
    last_error = None
    for attempt in range(1, max(1, retries) + 1):
        try:
            return _post_chat(base_url, payload, timeout), None, time.perf_counter() - started
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", errors="ignore")
            except Exception:
                body = ""
            last_error = f"http_{exc.code}:{body}"
            if 500 <= int(exc.code) <= 599 and attempt < retries:
                time.sleep(backoff * attempt)
                continue
            break
        except Exception as exc:
            last_error = str(exc)
            if attempt < retries:
                time.sleep(backoff * attempt)
                continue
            break
    return None, last_error, time.perf_counter() - started


def _extract_tokens(resp: Dict[str, Any]) -> float:
    debug = resp.get("debug", {}) if isinstance(resp, dict) else {}
    sec = debug.get("observability_sections", {}) if isinstance(debug.get("observability_sections"), dict) else {}
    sec4 = sec.get("sec4_context", {}) if isinstance(sec.get("sec4_context"), dict) else {}
    total_tokens = sec4.get("total_tokens", 0)
    try:
        return float(total_tokens or 0.0)
    except Exception:
        return 0.0


def _extract_mcp_calls(resp: Dict[str, Any]) -> int:
    debug = resp.get("debug", {}) if isinstance(resp, dict) else {}
    agent = debug.get("aftersales_agent", {}) if isinstance(debug.get("aftersales_agent"), dict) else {}
    tool_result = agent.get("tool_result", {}) if isinstance(agent.get("tool_result"), dict) else {}
    return sum(1 for k in tool_result.keys() if str(k).endswith("_mcp"))


def _extract_trace(resp: Dict[str, Any]) -> List[Dict[str, Any]]:
    debug = resp.get("debug", {}) if isinstance(resp, dict) else {}
    agent = debug.get("aftersales_agent", {}) if isinstance(debug.get("aftersales_agent"), dict) else {}
    trace = agent.get("trace", [])
    return trace if isinstance(trace, list) else []


def _is_need_human(resp: Dict[str, Any]) -> bool:
    return str(resp.get("status", "")) == "NEED_HUMAN"


def _is_handoff(resp: Dict[str, Any]) -> bool:
    if bool(resp.get("handoff_required", False)):
        return True
    pending = resp.get("pending_action", {}) if isinstance(resp.get("pending_action"), dict) else {}
    return str(pending.get("action_name", "")) == "handoff_human"


def _feedback_payload(
    *,
    conv: str,
    run_id: str,
    source_route: str,
    source_query: str,
    feedback: str,
) -> Dict[str, Any]:
    return {
        "conversation_id": conv,
        "user_id": "u_kpi_demo",
        "tenant_id": "demo",
        "actor_type": "agent",
        "channel": "web",
        "query": source_query,
        "history": [],
        "run_id": run_id,
        "user_feedback": feedback,
        "human_decision": {
            "decision": feedback,
            "reason": "用户反馈",
            "source_query": source_query,
            "source_route": source_route,
            "feedback_ts": _now_iso(),
            "feedback_event_id": f"fb_{uuid.uuid4().hex[:10]}",
        },
    }


def _scenario_queries(demo_fixed: bool) -> Dict[str, List[str]]:
    if demo_fixed:
        return {
            "faq": ["什么时候发货", "修改收货地址", "在哪里查看我购买的东西"],
            "product": ["商品参数和价格"],
            "risk": ["我要投诉并走法律流程怎么处理？"],
            "aftersales_complex": ["商品破损，要求退款", "收货后发现商品损坏，申请退款"],
        }
    return {
        "faq": ["什么时候发货", "订单多久能到", "如何查看物流"],
        "product": ["这款商品参数是什么", "这个型号支持退货吗"],
        "risk": ["涉及维权该怎么处理", "我要走仲裁流程"],
        "aftersales_complex": ["商品损坏要退款", "签收后出现质量问题，申请退款"],
    }


def run(args: argparse.Namespace) -> Dict[str, Any]:
    random.seed(args.seed)
    base_url = args.base_url.rstrip("/")

    warm_payload = {
        "conversation_id": f"kpi_warmup_{uuid.uuid4().hex[:8]}",
        "user_id": "u_kpi_demo",
        "tenant_id": "demo",
        "actor_type": "agent",
        "channel": "web",
        "query": "什么时候发货",
        "history": [],
    }
    warm_resp, _, _ = _post_with_retry(
        base_url,
        warm_payload,
        timeout=args.timeout,
        retries=args.retries,
        backoff=args.retry_backoff,
    )
    demo_fixed = bool((warm_resp or {}).get("debug", {}).get("demo_fixed_scenario_enabled", False))

    q = _scenario_queries(demo_fixed)

    rows: List[Dict[str, Any]] = []
    http_total = 0
    http_5xx = 0
    feedback_total = 0
    feedback_unsatisfied = 0
    resolved_cases = 0
    reopened_cases = 0
    approval_eval_total = 0
    approval_eval_ok = 0

    def send(payload: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str], float]:
        nonlocal http_total, http_5xx
        http_total += 1
        resp, err, elapsed = _post_with_retry(
            base_url,
            payload,
            timeout=args.timeout,
            retries=args.retries,
            backoff=args.retry_backoff,
        )
        if err and "http_5" in err:
            http_5xx += 1
        return resp, err, elapsed

    def run_case(case_type: str, idx: int) -> None:
        nonlocal feedback_total, feedback_unsatisfied, resolved_cases, reopened_cases, approval_eval_total, approval_eval_ok
        conv = f"kpi_demo_{case_type}_{idx}_{uuid.uuid4().hex[:8]}"
        base = {
            "conversation_id": conv,
            "user_id": "u_kpi_demo",
            "tenant_id": "demo",
            "actor_type": "agent",
            "channel": "web",
            "history": [],
        }
        case_started = time.perf_counter()
        trace_pack: List[List[Dict[str, Any]]] = []
        request_count = 0
        token_sum = 0.0
        mcp_calls = 0
        need_human_triggered = False
        final_resp: Dict[str, Any] = {}
        final_error = ""
        source_query = ""

        def call(query: str, extra: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
            nonlocal request_count, token_sum, mcp_calls, need_human_triggered, final_resp, final_error, source_query
            payload = dict(base)
            payload["query"] = query
            if extra:
                payload.update(extra)
            source_query = source_query or query
            resp, err, _ = send(payload)
            request_count += 1
            if resp is not None:
                token_sum += _extract_tokens(resp)
                mcp_calls = max(mcp_calls, _extract_mcp_calls(resp))
                trace_pack.append(_extract_trace(resp))
                need_human_triggered = need_human_triggered or _is_need_human(resp)
                final_resp = resp
            if err:
                final_error = err
            return resp

        if case_type == "faq_resolved":
            r1 = call(random.choice(q["faq"]))
            if r1:
                feedback_total += 1
                call(
                    source_query,
                    _feedback_payload(
                        conv=conv,
                        run_id=str(r1.get("run_id", "")),
                        source_route=str(r1.get("route_target", "faq")),
                        source_query=source_query,
                        feedback="resolved",
                    ),
                )
                resolved_cases += 1
        elif case_type == "faq_unresolved":
            r1 = call(random.choice(q["faq"]))
            if r1:
                feedback_total += 1
                feedback_unsatisfied += 1
                call(
                    source_query,
                    _feedback_payload(
                        conv=conv,
                        run_id=str(r1.get("run_id", "")),
                        source_route=str(r1.get("route_target", "faq")),
                        source_query=source_query,
                        feedback="unresolved",
                    ),
                )
        elif case_type == "product_resolved":
            r1 = call(random.choice(q["product"]))
            if r1:
                feedback_total += 1
                call(
                    source_query,
                    _feedback_payload(
                        conv=conv,
                        run_id=str(r1.get("run_id", "")),
                        source_route=str(r1.get("route_target", "product_info")),
                        source_query=source_query,
                        feedback="resolved",
                    ),
                )
                resolved_cases += 1
        elif case_type == "risk_handoff":
            call(random.choice(q["risk"]))
        elif case_type == "aftersales_closed":
            r1 = call(random.choice(q["aftersales_complex"]))
            if r1 and _is_need_human(r1):
                run_id = str(r1.get("run_id", ""))
                approval_eval_total += 1
                approval_eval_ok += 1 if str((r1.get("pending_action") or {}).get("action_name", "")) != "handoff_human" else 0
                r2 = call(
                    "客服操作: approve",
                    {
                        "run_id": run_id,
                        "action_mode": "continue",
                        "human_decision": {"decision": "approve", "reason": "通过动作1"},
                    },
                )
                if r2 and _is_need_human(r2):
                    call(
                        "客服操作: approve",
                        {
                            "run_id": run_id,
                            "action_mode": "continue",
                            "human_decision": {"decision": "approve", "reason": "通过动作2"},
                        },
                    )
                feedback_total += 1
                feedback_unsatisfied += 0
                call(
                    source_query,
                    _feedback_payload(
                        conv=conv,
                        run_id=run_id,
                        source_route="aftersales",
                        source_query=source_query,
                        feedback="resolved",
                    ),
                )
                resolved_cases += 1
        elif case_type == "aftersales_reopen":
            r1 = call(random.choice(q["aftersales_complex"]))
            if r1 and _is_need_human(r1):
                run_id = str(r1.get("run_id", ""))
                approval_eval_total += 1
                approval_eval_ok += 1 if str((r1.get("pending_action") or {}).get("action_name", "")) != "handoff_human" else 0
                call(
                    "客服操作: reject",
                    {
                        "run_id": run_id,
                        "action_mode": "continue",
                        "human_decision": {"decision": "reject", "reason": "拒绝处理"},
                    },
                )
                feedback_total += 1
                feedback_unsatisfied += 1
                call(
                    source_query,
                    _feedback_payload(
                        conv=conv,
                        run_id=run_id,
                        source_route="aftersales",
                        source_query=source_query,
                        feedback="unresolved",
                    ),
                )
            # reopen in same conversation after a close path
            r_reopen = call(random.choice(q["faq"]))
            if r_reopen:
                reopened_cases += 1

        # high-risk compliance check: for every mcp_ok should have prior wait_human on same action
        flattened: List[Dict[str, Any]] = []
        seen = set()
        for tr in trace_pack:
            for item in tr:
                if not isinstance(item, dict):
                    continue
                key = (
                    str(item.get("stage", "")),
                    str(item.get("status", "")),
                    str(item.get("action", "")),
                    str(item.get("loop_idx", "")),
                    str(item.get("source", "")),
                )
                if key in seen:
                    continue
                seen.add(key)
                flattened.append(item)
        wait_seen: Dict[str, bool] = {}
        compliance_ok = True
        for item in flattened:
            st = str(item.get("status", ""))
            action = str(item.get("action", ""))
            if st == "wait_human" and action:
                wait_seen[action] = True
            if st == "mcp_ok" and action and not wait_seen.get(action, False):
                compliance_ok = False

        # lightweight cost model for demo board
        token_cost = token_sum * args.token_unit_cost
        tool_cost = float(mcp_calls) * args.tool_unit_cost
        total_cost = token_cost + tool_cost
        elapsed_s = time.perf_counter() - case_started
        final_status = str(final_resp.get("status", "ERROR")) if final_resp else "ERROR"

        rows.append(
            {
                "case_type": case_type,
                "conversation_id": conv,
                "request_count": request_count,
                "latency_s": round(elapsed_s, 4),
                "token_sum": round(token_sum, 3),
                "mcp_calls": mcp_calls,
                "cost_usd": round(total_cost, 6),
                "need_human_triggered": need_human_triggered,
                "final_status": final_status,
                "final_handoff": _is_handoff(final_resp) if final_resp else False,
                "high_risk_compliance_ok": compliance_ok,
                "error": final_error,
            }
        )

    # Build a large and diverse mock dataset
    case_plan: List[str] = []
    for _ in range(args.scale):
        case_plan.extend(
            [
                "faq_resolved",
                "faq_resolved",
                "faq_unresolved",
                "product_resolved",
                "risk_handoff",
                "aftersales_closed",
                "aftersales_reopen",
            ]
        )
    random.shuffle(case_plan)

    for idx, case_type in enumerate(case_plan, start=1):
        run_case(case_type, idx)
        if args.interval > 0:
            time.sleep(args.interval)

    latencies = [float(r["latency_s"]) for r in rows]
    costs = [float(r["cost_usd"]) for r in rows]
    need_human_rows = [r for r in rows if bool(r["need_human_triggered"])]
    need_human_closed = [r for r in need_human_rows if str(r["final_status"]) != "NEED_HUMAN"]
    high_risk_rows = [r for r in rows if str(r["case_type"]).startswith("aftersales")]
    high_risk_ok_rows = [r for r in high_risk_rows if bool(r["high_risk_compliance_ok"])]
    feedback_cases = max(1, feedback_total)
    resolved_feedback = max(0, resolved_cases)

    core_ops = {
        "e2e_success_rate": round(_safe_div(sum(1 for r in rows if str(r["final_status"]) == "AUTO_DRAFT"), len(rows)), 4),
        "e2e_p95_latency_s": round(_p95(latencies), 4),
        "need_human_closed_loop_rate": round(_safe_div(len(need_human_closed), len(need_human_rows)), 4),
        "high_risk_approval_before_execute_compliance_rate": round(_safe_div(len(high_risk_ok_rows), len(high_risk_rows)), 4),
        "http_5xx_error_rate": round(_safe_div(http_5xx, http_total), 4),
        "avg_cost_per_session_usd": round(_safe_div(sum(costs), len(costs)), 6),
        "handoff_rate": round(_safe_div(sum(1 for r in rows if bool(r["final_handoff"])), len(rows)), 4),
        "negative_feedback_rate": round(_safe_div(feedback_unsatisfied, feedback_cases), 4),
        "reopen_rate": round(_safe_div(reopened_cases, max(1, resolved_cases)), 4),
        "fcr_rate": round(_safe_div(resolved_feedback - reopened_cases, len(rows)), 4),
    }

    business = {
        "fcr_rate": core_ops["fcr_rate"],
        "handoff_rate": core_ops["handoff_rate"],
        "high_risk_approval_accuracy": round(_safe_div(approval_eval_ok, max(1, approval_eval_total)), 4),
        "aht_seconds": round(_safe_div(sum(latencies), len(latencies)), 4),
        "csat": round(_safe_div(resolved_feedback, feedback_cases), 4),
        "reopen_rate": core_ops["reopen_rate"],
    }

    return {
        "generated_at": _now_iso(),
        "base_url": base_url,
        "demo_fixed_scenario_enabled": demo_fixed,
        "scale": args.scale,
        "cases": len(rows),
        "http_requests": http_total,
        "http_5xx": http_5xx,
        "cost_model": {
            "token_unit_cost": args.token_unit_cost,
            "tool_unit_cost": args.tool_unit_cost,
        },
        "core_ops_dashboard": core_ops,
        "business_dashboard": business,
        "rows": rows,
    }


def _write_markdown(path: Path, report: Dict[str, Any]) -> None:
    core = report.get("core_ops_dashboard", {})
    biz = report.get("business_dashboard", {})
    lines: List[str] = []
    lines.append("# KPI Demo Dataset Report")
    lines.append("")
    lines.append(f"- generated_at: `{report.get('generated_at', '')}`")
    lines.append(f"- demo_fixed_scenario_enabled: `{report.get('demo_fixed_scenario_enabled', False)}`")
    lines.append(f"- cases: `{report.get('cases', 0)}`")
    lines.append(f"- http_requests: `{report.get('http_requests', 0)}`")
    lines.append("")
    lines.append("## 核心运营看板（北极星+红线）")
    lines.append("")
    for k in [
        "e2e_success_rate",
        "e2e_p95_latency_s",
        "need_human_closed_loop_rate",
        "high_risk_approval_before_execute_compliance_rate",
        "http_5xx_error_rate",
        "avg_cost_per_session_usd",
        "handoff_rate",
        "negative_feedback_rate",
        "reopen_rate",
        "fcr_rate",
    ]:
        lines.append(f"- {k}: `{core.get(k, 0)}`")
    lines.append("")
    lines.append("## 智能客服业务指标")
    lines.append("")
    for k in [
        "fcr_rate",
        "handoff_rate",
        "high_risk_approval_accuracy",
        "aht_seconds",
        "csat",
        "reopen_rate",
    ]:
        lines.append(f"- {k}: `{biz.get(k, 0)}`")
    lines.append("")
    lines.append("> 说明：当 `DEMO_FIXED_SCENARIO=true` 时，复杂售后链路使用固定 mock 场景，指标更稳定，适合演示。")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate large mock dataset and KPI values for dashboard demo.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="API base url")
    parser.add_argument("--scale", type=int, default=30, help="Scenario bundle multiplier (larger => bigger dataset)")
    parser.add_argument("--interval", type=float, default=0.03, help="Sleep between cases")
    parser.add_argument("--timeout", type=float, default=60.0, help="Request timeout seconds")
    parser.add_argument("--retries", type=int, default=3, help="Retry times per request")
    parser.add_argument("--retry-backoff", type=float, default=0.4, help="Retry backoff seconds")
    parser.add_argument("--seed", type=int, default=20260414, help="Random seed")
    parser.add_argument("--token-unit-cost", type=float, default=0.000002, help="USD per estimated token")
    parser.add_argument("--tool-unit-cost", type=float, default=0.01, help="USD per MCP call")
    parser.add_argument(
        "--output-json",
        default="data/eval/reports/kpi_demo_dataset_report.json",
        help="Output json path",
    )
    parser.add_argument(
        "--output-md",
        default="data/eval/reports/kpi_demo_dataset_report.md",
        help="Output markdown path",
    )
    args = parser.parse_args()

    report = run(args)
    out_json = Path(args.output_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out_md = Path(args.output_md)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    _write_markdown(out_md, report)

    print(f"demo_fixed_scenario_enabled={report.get('demo_fixed_scenario_enabled', False)}")
    print(f"cases={report.get('cases', 0)}")
    print(f"core_ops={json.dumps(report.get('core_ops_dashboard', {}), ensure_ascii=False)}")
    print(f"business={json.dumps(report.get('business_dashboard', {}), ensure_ascii=False)}")
    print(f"report_json={out_json}")
    print(f"report_md={out_md}")


if __name__ == "__main__":
    main()

