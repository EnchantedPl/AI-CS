import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.graph.workflows.minimal_chat import run_workflow
from scripts.replay_compare import _augment_quality_metrics, _load_queries


def _mean(values: List[float]) -> float:
    return round(statistics.mean(values), 4) if values else 0.0


def _p95(values: List[float]) -> float:
    if not values:
        return 0.0
    if len(values) < 2:
        return round(float(values[0]), 2)
    return round(statistics.quantiles(values, n=20)[18], 2)


def _run_once(query: str, idx: int, memory_enabled: bool) -> Dict[str, Any]:
    start = time.perf_counter()
    state = run_workflow(
        trace_id=f"replay-memory-{'on' if memory_enabled else 'off'}-{idx}",
        event_id="",
        conversation_id=f"replay_mem_{'on' if memory_enabled else 'off'}_{idx}",
        user_id=f"u_mem_{idx}",
        tenant_id=f"tenant_mem_{idx}",
        actor_type="user",
        channel="script",
        query=query,
        history=[],
        memory_enabled=memory_enabled,
    )
    rt_ms = (time.perf_counter() - start) * 1000
    memory_debug = state.get("debug", {}).get("memory", {})
    context_debug = memory_debug.get("context_debug", {})
    citations = state.get("citations", [])
    selected_count = int(context_debug.get("selected_count", 0) or 0)
    memory_error = bool(memory_debug.get("error")) or bool(
        state.get("debug", {}).get("memory_write", {}).get("error")
    )
    hallucination_proxy = (
        state.get("route_target") != "risk_query"
        and (not state.get("handoff_required", False))
        and len(citations) == 0
    )
    recovery_success = (not memory_error) or (memory_error and not state.get("handoff_required", False))
    return {
        "scenario": "memory_on" if memory_enabled else "memory_off",
        "query": query,
        "rt_ms": round(rt_ms, 2),
        "status": state.get("status", ""),
        "handoff_required": 1 if state.get("handoff_required", False) else 0,
        "top_citation": citations[0] if citations else "",
        "citations": citations,
        "answer": state.get("answer", ""),
        "memory_hit": 1 if memory_debug.get("hit", False) else 0,
        "memory_selected_count": selected_count,
        "memory_dropped_count": int(context_debug.get("dropped_count", 0) or 0),
        "memory_write_count": int(memory_debug.get("memory_write_count", 0) or 0),
        "memory_dedupe_count": int(memory_debug.get("memory_dedupe_count", 0) or 0),
        "effective_injection": 1 if selected_count > 0 else 0,
        "hallucination_proxy": 1 if hallucination_proxy else 0,
        "recovery_success": 1 if recovery_success else 0,
        "llm_context_chars": int(state.get("debug", {}).get("context", {}).get("llm_context_chars", 0) or 0),
        "memory_used_chars": int(context_debug.get("memory_used_chars", 0) or 0),
    }


def _summary(rows: List[Dict[str, Any]]) -> Dict[str, float]:
    return {
        "count": float(len(rows)),
        "p95_latency_ms": _p95([float(r["rt_ms"]) for r in rows]),
        "avg_latency_ms": _mean([float(r["rt_ms"]) for r in rows]),
        "memory_hit_rate": _mean([float(r.get("memory_hit", 0)) for r in rows]),
        "effective_injection_rate": _mean([float(r.get("effective_injection", 0)) for r in rows]),
        "hallucination_proxy_rate": _mean([float(r.get("hallucination_proxy", 0)) for r in rows]),
        "recovery_success_rate": _mean([float(r.get("recovery_success", 0)) for r in rows]),
        "avg_selected_count": _mean([float(r.get("memory_selected_count", 0)) for r in rows]),
        "avg_memory_used_chars": _mean([float(r.get("memory_used_chars", 0)) for r in rows]),
        "avg_llm_context_chars": _mean([float(r.get("llm_context_chars", 0)) for r in rows]),
        "avg_memory_write_count": _mean([float(r.get("memory_write_count", 0)) for r in rows]),
        "avg_memory_dedupe_count": _mean([float(r.get("memory_dedupe_count", 0)) for r in rows]),
        "handoff_rate": _mean([float(r.get("handoff_required", 0)) for r in rows]),
        "citation_hit_at_1": _mean([float(r.get("citation_hit_at_1", 0)) for r in rows]),
        "citation_hit_at_k": _mean([float(r.get("citation_hit_at_k", 0)) for r in rows]),
        "mrr": _mean([float(r.get("mrr", 0)) for r in rows]),
        "ndcg_at_5": _mean([float(r.get("ndcg_at_5", 0)) for r in rows]),
        "answer_char_f1": _mean([float(r.get("answer_char_f1") or 0) for r in rows]),
    }


def _write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return
    keys = list(rows[0].keys())
    lines = [",".join(keys)]
    for row in rows:
        vals: List[str] = []
        for k in keys:
            v = row.get(k, "")
            if isinstance(v, (list, dict)):
                vals.append(json.dumps(v, ensure_ascii=False).replace(",", ";"))
            else:
                vals.append(str(v).replace(",", ";"))
        lines.append(",".join(vals))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_report(path: Path, on_rows: List[Dict[str, Any]], off_rows: List[Dict[str, Any]]) -> None:
    on = _summary(on_rows)
    off = _summary(off_rows)
    lines: List[str] = []
    lines.append("# Memory ON vs OFF Replay Report")
    lines.append("")
    lines.append("## Metric Definitions")
    lines.append("")
    lines.append("| metric | definition | direction |")
    lines.append("|---|---|---|")
    lines.append("| memory_hit_rate | memory读命中请求 / 总请求 | higher better |")
    lines.append("| effective_injection_rate | selected_count>0 的请求占比 | higher better |")
    lines.append("| hallucination_proxy_rate | 非risk自动回复且无citation占比（代理） | lower better |")
    lines.append("| recovery_success_rate | memory异常后仍成功自动回复占比（代理） | higher better |")
    lines.append("| avg/p95_latency_ms | 端到端延迟均值/尾延迟 | lower better |")
    lines.append("| avg_llm_context_chars | 传入LLM上下文长度（成本代理） | lower better |")
    lines.append("| citation_hit@1/@k, MRR, nDCG@5 | 金标质量指标（若有gold） | higher better |")
    lines.append("")
    lines.append("## Scenario Summary")
    lines.append("")
    lines.append("| scenario | count | avg_latency_ms | p95_latency_ms | memory_hit_rate | effective_injection_rate | hallucination_proxy_rate | recovery_success_rate | avg_selected | avg_context_chars | avg_memory_used_chars | hit@1 | hit@k | MRR | nDCG@5 |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    lines.append(
        f"| memory_on | {int(on['count'])} | {on['avg_latency_ms']} | {on['p95_latency_ms']} | "
        f"{on['memory_hit_rate']} | {on['effective_injection_rate']} | {on['hallucination_proxy_rate']} | "
        f"{on['recovery_success_rate']} | {on['avg_selected_count']} | {on['avg_llm_context_chars']} | "
        f"{on['avg_memory_used_chars']} | {on['citation_hit_at_1']} | {on['citation_hit_at_k']} | {on['mrr']} | {on['ndcg_at_5']} |"
    )
    lines.append(
        f"| memory_off | {int(off['count'])} | {off['avg_latency_ms']} | {off['p95_latency_ms']} | "
        f"{off['memory_hit_rate']} | {off['effective_injection_rate']} | {off['hallucination_proxy_rate']} | "
        f"{off['recovery_success_rate']} | {off['avg_selected_count']} | {off['avg_llm_context_chars']} | "
        f"{off['avg_memory_used_chars']} | {off['citation_hit_at_1']} | {off['citation_hit_at_k']} | {off['mrr']} | {off['ndcg_at_5']} |"
    )
    lines.append("")
    lines.append("## Delta (ON - OFF)")
    lines.append("")
    lines.append(
        f"- p95_latency_ms: `{round(on['p95_latency_ms'] - off['p95_latency_ms'], 2)}`"
    )
    lines.append(
        f"- memory_hit_rate: `{round(on['memory_hit_rate'] - off['memory_hit_rate'], 4)}`"
    )
    lines.append(
        f"- effective_injection_rate: `{round(on['effective_injection_rate'] - off['effective_injection_rate'], 4)}`"
    )
    lines.append(
        f"- hallucination_proxy_rate: `{round(on['hallucination_proxy_rate'] - off['hallucination_proxy_rate'], 4)}`"
    )
    lines.append(
        f"- recovery_success_rate: `{round(on['recovery_success_rate'] - off['recovery_success_rate'], 4)}`"
    )
    lines.append(
        f"- avg_llm_context_chars: `{round(on['avg_llm_context_chars'] - off['avg_llm_context_chars'], 2)}`"
    )
    lines.append(
        f"- nDCG@5: `{round(on['ndcg_at_5'] - off['ndcg_at_5'], 4)}`"
    )
    lines.append("")
    lines.append("## Suggested PASS Criteria")
    lines.append("")
    lines.append("- hallucination_proxy_rate 不升高，或下降 >= 0.5%")
    lines.append("- recovery_success_rate 不下降")
    lines.append("- nDCG@5 不下降（有gold时）")
    lines.append("- p95_latency_ms 增量 <= 50ms（可按业务调整）")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare memory ON vs OFF via replay.")
    parser.add_argument("--query-jsonl", default="", help="JSONL file with {'query': '...'}")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--output-dir", default="data/eval/reports")
    args = parser.parse_args()

    queries = _load_queries(args.query_jsonl, args.limit)
    if not queries:
        raise RuntimeError("No queries found.")

    on_rows: List[Dict[str, Any]] = []
    off_rows: List[Dict[str, Any]] = []
    for idx, sample in enumerate(queries, start=1):
        row_on = _run_once(str(sample["query"]), idx, True)
        row_off = _run_once(str(sample["query"]), idx, False)
        on_rows.append(_augment_quality_metrics(row_on, sample))
        off_rows.append(_augment_quality_metrics(row_off, sample))

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    all_rows = on_rows + off_rows
    csv_path = output_dir / "replay_memory_compare_rows.csv"
    md_path = output_dir / "replay_memory_compare_report.md"
    _write_csv(csv_path, all_rows)
    _write_report(md_path, on_rows, off_rows)
    print(f"queries: {len(queries)}")
    print(f"rows_csv: {csv_path}")
    print(f"report_md: {md_path}")


if __name__ == "__main__":
    main()
