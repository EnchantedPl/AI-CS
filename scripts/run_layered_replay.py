#!/usr/bin/env python3
import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.replay.store import REPLAY_STORE


def _load_queries(path: str, limit: int) -> List[str]:
    if not path:
        return [
            "会员积分可以提现吗？",
            "我要投诉并走法律流程怎么处理？",
            "用户反应商品破损，要求退款",
            "这款型号和上一代区别是什么？",
        ][: max(1, limit or 4)]
    out: List[str] = []
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"query file not found: {path}")
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            raw = line.strip()
            if not raw:
                continue
            item = json.loads(raw)
            if isinstance(item, dict) and item.get("query"):
                out.append(str(item["query"]))
            if limit > 0 and len(out) >= limit:
                break
    return out


def _post_chat(app_base: str, payload: Dict[str, Any], timeout_sec: float) -> Dict[str, Any]:
    r = requests.post(f"{app_base.rstrip('/')}/chat", json=payload, timeout=timeout_sec)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict):
        raise RuntimeError("chat_response_not_dict")
    return data


def _collect(
    *,
    app_base: str,
    experiment_tag: str,
    query_jsonl: str,
    limit: int,
    tenant_id: str,
    actor_type: str,
    channel: str,
    timeout_sec: float,
) -> None:
    queries = _load_queries(query_jsonl, limit)
    if not queries:
        raise RuntimeError("no queries to collect")
    print(f"[collect] experiment_tag={experiment_tag} queries={len(queries)}")
    for idx, q in enumerate(queries, start=1):
        payload = {
            "user_id": f"replay_user_{idx}",
            "tenant_id": tenant_id,
            "actor_type": actor_type,
            "query": q,
            "channel": channel,
            "history": [],
            "conversation_id": f"replay:{experiment_tag}:{idx}",
            "replay_experiment": experiment_tag,
        }
        t0 = time.perf_counter()
        try:
            data = _post_chat(app_base, payload, timeout_sec)
            elapsed = round((time.perf_counter() - t0) * 1000.0, 2)
            print(
                f"  [{idx}/{len(queries)}] ok route={data.get('route_target')} "
                f"status={data.get('status')} latency_ms={elapsed}"
            )
        except Exception as exc:
            print(f"  [{idx}/{len(queries)}] error={exc}")


def _latest_cases_by_query(*, experiment_tag: str, limit: int) -> Dict[str, Dict[str, Any]]:
    rows = REPLAY_STORE.list_cases(limit=limit)
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        input_json = row.get("input_json", {}) if isinstance(row.get("input_json"), dict) else {}
        tag = str(input_json.get("replay_experiment", "") or "")
        if tag != experiment_tag:
            continue
        query = str(input_json.get("query", "") or "")
        if not query:
            continue
        # rows are already sorted by created_at desc, keep first per query.
        if query not in out:
            out[query] = row
    return out


def _canonicalize(value: Any) -> Any:
    if isinstance(value, list):
        return [_canonicalize(v) for v in value]
    if isinstance(value, dict):
        return {k: _canonicalize(value[k]) for k in sorted(value.keys())}
    return value


def _answer_sig(answer: str) -> str:
    return hashlib.sha256((answer or "").encode("utf-8")).hexdigest()[:12]


def _layer_compare(
    baseline: Dict[str, Any],
    candidate: Dict[str, Any],
    layer_code: str,
) -> Tuple[bool, Dict[str, Any]]:
    b_out = baseline.get("output_json", {}) if isinstance(baseline.get("output_json"), dict) else {}
    c_out = candidate.get("output_json", {}) if isinstance(candidate.get("output_json"), dict) else {}
    if layer_code == "L1":
        keys = ["route_target", "aftersales_mode"]
    elif layer_code == "L2":
        keys = ["cache_decision", "cache_level", "cache_writeback", "cache_admitted"]
    elif layer_code == "L3":
        keys = ["need_rag", "rag_mode", "retrieved_count", "llm_context_chars"]
    elif layer_code == "L4":
        keys = ["status", "handoff_required"]
    else:
        keys = sorted(set(b_out.keys()) | set(c_out.keys()))

    diffs: Dict[str, Any] = {}
    for k in keys:
        bv = _canonicalize(b_out.get(k))
        cv = _canonicalize(c_out.get(k))
        if bv != cv:
            diffs[k] = {"baseline": bv, "candidate": cv}

    if layer_code == "L4":
        b_answer = str(b_out.get("answer", "") or "")
        c_answer = str(c_out.get("answer", "") or "")
        if b_answer != c_answer:
            diffs["answer_sig"] = {"baseline": _answer_sig(b_answer), "candidate": _answer_sig(c_answer)}
        b_cit = b_out.get("citations", []) if isinstance(b_out.get("citations"), list) else []
        c_cit = c_out.get("citations", []) if isinstance(c_out.get("citations"), list) else []
        if b_cit != c_cit:
            diffs["citations"] = {"baseline": b_cit, "candidate": c_cit}

    return (len(diffs) == 0), diffs


def _pick_first_drift(
    *,
    layer_results: List[Tuple[str, bool]],
    mode: str,
    target_layer: str,
) -> str:
    if mode == "isolate":
        start_idx = 0
        for i, (layer, _) in enumerate(layer_results):
            if layer == target_layer:
                start_idx = i
                break
        for layer, ok in layer_results[start_idx:]:
            if not ok:
                return layer
        return "none"
    for layer, ok in layer_results:
        if not ok:
            return layer
    return "none"


def _compare(
    *,
    baseline_tag: str,
    candidate_tag: str,
    mode: str,
    target_layer: str,
    case_limit: int,
    output_path: str,
) -> Dict[str, Any]:
    baseline_cases = _latest_cases_by_query(experiment_tag=baseline_tag, limit=case_limit)
    candidate_cases = _latest_cases_by_query(experiment_tag=candidate_tag, limit=case_limit)
    common_queries = sorted(set(baseline_cases.keys()) & set(candidate_cases.keys()))
    if not common_queries:
        raise RuntimeError("no common queries between baseline and candidate experiments")

    exp_id = REPLAY_STORE.create_experiment(
        name=f"layered_replay_{baseline_tag}_vs_{candidate_tag}",
        mode=mode,
        baseline_ref=baseline_tag,
        candidate_ref=candidate_tag,
        global_params_json={"mode": mode, "target_layer": target_layer},
        meta_json={"common_query_count": len(common_queries)},
    )

    layer_order = ["L1", "L2", "L3", "L4"]
    report_rows: List[Dict[str, Any]] = []
    drift_counter: Dict[str, int] = {}

    for q in common_queries:
        b_case = baseline_cases[q]
        c_case = candidate_cases[q]
        b_layers = {x["layer_code"]: x for x in REPLAY_STORE.get_case_snapshots(b_case["case_id"])}
        c_layers = {x["layer_code"]: x for x in REPLAY_STORE.get_case_snapshots(c_case["case_id"])}

        layer_results: List[Tuple[str, bool]] = []
        layer_diffs: Dict[str, Any] = {}
        for layer in layer_order:
            b = b_layers.get(layer, {"output_json": {}})
            c = c_layers.get(layer, {"output_json": {}})
            ok, diffs = _layer_compare(b, c, layer)
            layer_results.append((layer, ok))
            if not ok:
                layer_diffs[layer] = diffs

        first_drift = _pick_first_drift(layer_results=layer_results, mode=mode, target_layer=target_layer)
        drift_counter[first_drift] = drift_counter.get(first_drift, 0) + 1
        score_delta = {
            "route_match": 1.0 if dict(layer_results).get("L1", False) else 0.0,
            "cache_match": 1.0 if dict(layer_results).get("L2", False) else 0.0,
            "rag_match": 1.0 if dict(layer_results).get("L3", False) else 0.0,
            "final_match": 1.0 if dict(layer_results).get("L4", False) else 0.0,
        }
        summary = {
            "query": q,
            "mode": mode,
            "target_layer": target_layer if mode == "isolate" else "",
            "baseline_case_id": b_case["case_id"],
            "candidate_case_id": c_case["case_id"],
        }
        REPLAY_STORE.save_diff(
            experiment_id=exp_id,
            case_id=str(b_case["case_id"]),
            first_drift_layer=first_drift,
            layer_diffs_json=layer_diffs,
            score_delta_json=score_delta,
            summary_json=summary,
            severity="high" if first_drift in {"L1", "L2"} else "medium",
        )
        report_rows.append(
            {
                "query": q,
                "first_drift_layer": first_drift,
                "route_match": score_delta["route_match"],
                "cache_match": score_delta["cache_match"],
                "rag_match": score_delta["rag_match"],
                "final_match": score_delta["final_match"],
            }
        )

    out = {
        "experiment_id": exp_id,
        "mode": mode,
        "target_layer": target_layer if mode == "isolate" else "",
        "baseline_tag": baseline_tag,
        "candidate_tag": candidate_tag,
        "cases": len(report_rows),
        "drift_distribution": drift_counter,
        "rows": report_rows,
    }
    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"experiment_id={exp_id}")
    print(f"report={out_file}")
    print(f"drift_distribution={drift_counter}")
    return out


def _run_full(
    *,
    baseline_tag: str,
    candidate_tag: str,
    target_layer: str,
    case_limit: int,
    observe_output: str,
    isolate_output: str,
    attribution_output: str,
) -> None:
    _compare(
        baseline_tag=baseline_tag,
        candidate_tag=candidate_tag,
        mode="observe",
        target_layer=target_layer,
        case_limit=case_limit,
        output_path=observe_output,
    )
    _compare(
        baseline_tag=baseline_tag,
        candidate_tag=candidate_tag,
        mode="isolate",
        target_layer=target_layer,
        case_limit=case_limit,
        output_path=isolate_output,
    )
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "layered_replay_report.py"),
        "--observe-json",
        observe_output,
        "--isolate-json",
        isolate_output,
        "--output-md",
        attribution_output,
    ]
    subprocess.run(cmd, check=True)
    print("[full] done")
    print(f"[full] observe={observe_output}")
    print(f"[full] isolate={isolate_output}")
    print(f"[full] attribution={attribution_output}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Layered replay collector/comparator.")
    sub = parser.add_subparsers(dest="command", required=True)

    collect = sub.add_parser("collect", help="Replay queries through /chat and capture layered snapshots.")
    collect.add_argument("--app-base", default="http://127.0.0.1:8000")
    collect.add_argument("--experiment-tag", required=True)
    collect.add_argument("--query-jsonl", default="")
    collect.add_argument("--limit", type=int, default=100)
    collect.add_argument("--tenant-id", default="demo")
    collect.add_argument("--actor-type", default="agent")
    collect.add_argument("--channel", default="web")
    collect.add_argument("--timeout-sec", type=float, default=60.0)

    compare = sub.add_parser("compare", help="Compare baseline vs candidate layered snapshots.")
    compare.add_argument("--baseline-tag", required=True)
    compare.add_argument("--candidate-tag", required=True)
    compare.add_argument("--mode", choices=["observe", "isolate"], default="observe")
    compare.add_argument("--target-layer", choices=["L1", "L2", "L3", "L4"], default="L3")
    compare.add_argument("--case-limit", type=int, default=2000)
    compare.add_argument("--output", default="data/eval/reports/layered_replay_report.json")

    full = sub.add_parser(
        "full",
        help="Run observe+isolate compare and generate attribution report in one command.",
    )
    full.add_argument("--baseline-tag", required=True)
    full.add_argument("--candidate-tag", required=True)
    full.add_argument("--target-layer", choices=["L1", "L2", "L3", "L4"], default="L3")
    full.add_argument("--case-limit", type=int, default=2000)
    full.add_argument(
        "--observe-output",
        default="data/eval/reports/layered_replay_report.observe.json",
    )
    full.add_argument(
        "--isolate-output",
        default="data/eval/reports/layered_replay_report.isolate.json",
    )
    full.add_argument(
        "--attribution-output",
        default="data/eval/reports/layered_replay_attribution_report.md",
    )

    args = parser.parse_args()
    if args.command == "collect":
        _collect(
            app_base=args.app_base,
            experiment_tag=args.experiment_tag,
            query_jsonl=args.query_jsonl,
            limit=args.limit,
            tenant_id=args.tenant_id,
            actor_type=args.actor_type,
            channel=args.channel,
            timeout_sec=args.timeout_sec,
        )
        return

    if args.command == "compare":
        _compare(
            baseline_tag=args.baseline_tag,
            candidate_tag=args.candidate_tag,
            mode=args.mode,
            target_layer=args.target_layer,
            case_limit=args.case_limit,
            output_path=args.output,
        )
        return

    _run_full(
        baseline_tag=args.baseline_tag,
        candidate_tag=args.candidate_tag,
        target_layer=args.target_layer,
        case_limit=args.case_limit,
        observe_output=args.observe_output,
        isolate_output=args.isolate_output,
        attribution_output=args.attribution_output,
    )


if __name__ == "__main__":
    main()

