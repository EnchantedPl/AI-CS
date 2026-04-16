#!/usr/bin/env python3
import argparse
import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List
from urllib import request


def _post_json(base_url: str, payload: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    req = request.Request(
        base_url.rstrip("/") + "/chat",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _bool_score(ok: bool) -> float:
    return 1.0 if ok else 0.0


def _evaluate_case(base_url: str, query: str, timeout: float) -> Dict[str, Any]:
    conversation_id = f"eval_aftersales_{uuid.uuid4().hex[:12]}"
    base_payload = {
        "conversation_id": conversation_id,
        "user_id": "u_demo",
        "tenant_id": "demo",
        "actor_type": "agent",
        "channel": "web",
        "history": [],
    }

    step1 = _post_json(base_url, {**base_payload, "query": query}, timeout=timeout)
    run_id = str(step1.get("run_id", "") or "")
    step2 = _post_json(
        base_url,
        {
            **base_payload,
            "query": "客服操作: approve",
            "run_id": run_id,
            "human_decision": {"decision": "approve", "reason": "评测：同意第1步动作"},
        },
        timeout=timeout,
    )
    step3 = _post_json(
        base_url,
        {
            **base_payload,
            "query": "客服操作: approve",
            "run_id": run_id,
            "human_decision": {"decision": "approve", "reason": "评测：同意第2步动作"},
        },
        timeout=timeout,
    )

    action1 = str(((step1.get("pending_action") or {}).get("action_name") or ""))
    action2 = str(((step2.get("pending_action") or {}).get("action_name") or ""))
    final_pending = str(((step3.get("pending_action") or {}).get("action_name") or ""))

    nodes1 = (((step1.get("debug") or {}).get("run_step_summary") or {}).get("current_path") or {}).get("nodes", [])
    nodes1 = nodes1 if isinstance(nodes1, list) else []
    trace3 = (((step3.get("debug") or {}).get("aftersales_agent") or {}).get("trace") or [])
    trace3 = trace3 if isinstance(trace3, list) else []

    action_args1 = (step1.get("pending_action") or {}).get("action_args") or {}
    action_args2 = (step2.get("pending_action") or {}).get("action_args") or {}
    readable_action1 = bool(str(action_args1.get("business_title", "")).strip() and str(action_args1.get("business_desc", "")).strip())
    readable_action2 = bool(str(action_args2.get("business_title", "")).strip() and str(action_args2.get("business_desc", "")).strip())

    has_stage_done = any(isinstance(t, dict) and t.get("status") == "stage_done" for t in trace3)
    final_tool_result = (((step3.get("debug") or {}).get("aftersales_agent") or {}).get("tool_result") or {})
    final_tool_keys = set(final_tool_result.keys()) if isinstance(final_tool_result, dict) else set()

    checks: Dict[str, bool] = {
        # Some API responses may not echo `aftersales_mode`, so use route+action pattern.
        "route_and_mode_ok": str(step1.get("route_target", "")) == "aftersales" and action1 == "approval_submit_mcp",
        "loop_sequence_ok": str(step1.get("status", "")) == "NEED_HUMAN" and str(step2.get("status", "")) == "NEED_HUMAN" and str(step3.get("status", "")) != "NEED_HUMAN",
        "action_order_ok": action1 == "approval_submit_mcp" and action2 == "refund_submit_mcp",
        "stage_coverage_ok": all(x in nodes1 for x in ["aftersales_facts", "aftersales_policy", "aftersales_action"]),
        "action_readability_ok": readable_action1 and readable_action2,
        "completion_ok": (not final_pending) and has_stage_done and {"approval_submit_mcp", "refund_submit_mcp"}.issubset(final_tool_keys),
    }
    rationality_score = round(sum(_bool_score(v) for v in checks.values()) / max(1, len(checks)) * 100.0, 2)
    return {
        "query": query,
        "run_id": run_id,
        "actions": {"step1": action1, "step2": action2, "final_pending": final_pending},
        "checks": checks,
        "rationality_score": rationality_score,
    }


def _write_markdown(path: Path, rows: List[Dict[str, Any]], overall: float) -> None:
    lines: List[str] = []
    lines.append("# Complex Aftersales Rationality Evaluation")
    lines.append("")
    lines.append(f"- `aftersales_complex_rationality_score`: **{overall}**")
    lines.append("")
    lines.append("| query | score | action1 | action2 | loop_sequence_ok | completion_ok |")
    lines.append("|---|---:|---|---|---|---|")
    for row in rows:
        checks = row.get("checks", {})
        actions = row.get("actions", {})
        lines.append(
            f"| {row.get('query', '')} | {row.get('rationality_score', 0)} | "
            f"{actions.get('step1', '')} | {actions.get('step2', '')} | "
            f"{checks.get('loop_sequence_ok', False)} | {checks.get('completion_ok', False)} |"
        )
    lines.append("")
    lines.append("## Rule")
    lines.append("")
    lines.append("- 目标链路：`action1 -> 同意 -> action2 -> 同意 -> 结束`。")
    lines.append("- 该评测依赖 `DEMO_FIXED_SCENARIO=true` 的固定复杂售后 mock 场景。")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate complex aftersales loop rationality.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="API base url")
    parser.add_argument(
        "--queries",
        nargs="*",
        default=["商品破损，要求退款", "收货后发现商品损坏，申请退款"],
        help="Complex aftersales queries for evaluation",
    )
    parser.add_argument("--timeout", type=float, default=90.0, help="Per request timeout seconds")
    parser.add_argument(
        "--output-json",
        default="data/eval/reports/aftersales_complex_eval.json",
        help="Output JSON path",
    )
    parser.add_argument(
        "--output-md",
        default="data/eval/reports/aftersales_complex_eval.md",
        help="Output markdown path",
    )
    args = parser.parse_args()

    started = time.time()
    rows: List[Dict[str, Any]] = []
    for q in args.queries:
        rows.append(_evaluate_case(args.base_url, q, timeout=args.timeout))

    overall = round(sum(float(x.get("rationality_score", 0.0)) for x in rows) / max(1, len(rows)), 2)
    out = {
        "metric_name": "aftersales_complex_rationality_score",
        "aftersales_complex_rationality_score": overall,
        "cases": len(rows),
        "elapsed_seconds": round(time.time() - started, 3),
        "rows": rows,
    }

    out_json = Path(args.output_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    out_md = Path(args.output_md)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    _write_markdown(out_md, rows, overall)

    print(f"aftersales_complex_rationality_score={overall}")
    print(f"report_json={out_json}")
    print(f"report_md={out_md}")


if __name__ == "__main__":
    main()

