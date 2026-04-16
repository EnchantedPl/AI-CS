import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.rag.hybrid_retriever import RETRIEVER, RagRuntimeConfig


def _route_domain(query: str) -> str:
    text = query.lower()
    if any(k in text for k in ["refund", "return", "退款", "退货"]):
        return "aftersales"
    if any(k in text for k in ["price", "spec", "价格", "规格", "参数"]):
        return "product_info"
    if any(k in text for k in ["risk", "legal", "投诉", "法律", "合规"]):
        return "risk_query"
    return "faq"


def _citation_chunk_id(citation: str) -> str:
    if not citation or "#" not in citation:
        return ""
    return citation.split("#", 1)[1]


def _citation_source(citation: str) -> str:
    if not citation:
        return ""
    return citation.split("#", 1)[0]


def _load_samples(path: Path, limit: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            raw = line.strip()
            if not raw:
                continue
            item = json.loads(raw)
            out.append(
                {
                    "query": str(item.get("query", "")),
                    "gold_doc_ids": item.get("gold_doc_ids", []) or [],
                    "gold_chunk_ids": item.get("gold_chunk_ids", []) or [],
                }
            )
            if limit > 0 and len(out) >= limit:
                break
    return out


def _set_runtime(vector_topk: int, keyword_topk: int, final_topk: int) -> None:
    os.environ["RAG_VECTOR_TOP_K"] = str(vector_topk)
    os.environ["RAG_KEYWORD_TOP_K"] = str(keyword_topk)
    os.environ["RAG_TOP_K"] = str(final_topk)
    RETRIEVER.cfg = RagRuntimeConfig.from_env()


def _compute_metrics(
    samples: List[Dict[str, Any]],
    *,
    threshold_low: float,
    threshold_high: float,
    vector_topk: int,
    keyword_topk: int,
    final_topk: int,
    recall_k: int,
) -> Dict[str, Any]:
    _set_runtime(vector_topk=vector_topk, keyword_topk=keyword_topk, final_topk=final_topk)
    total = len(samples)
    if total == 0:
        return {}

    latencies: List[float] = []
    hits = 0
    semantic_hits = 0
    semantic_correct = 0
    false_reuse = 0
    recall_hits = 0
    gray = 0
    miss = 0

    for s in samples:
        query = s["query"]
        gold_doc_ids = [str(x) for x in (s.get("gold_doc_ids") or []) if x]
        gold_chunk_ids = [str(x) for x in (s.get("gold_chunk_ids") or []) if x]

        start = time.perf_counter()
        res = RETRIEVER.retrieve(
            query=query,
            domain=_route_domain(query),
            retrieval_mode=os.getenv("RAG_RETRIEVAL_MODE", "hybrid"),
        )
        rt_ms = (time.perf_counter() - start) * 1000
        latencies.append(rt_ms)

        vector_candidates = res.get("candidates", {}).get("vector", [])
        top1_score = float(vector_candidates[0]["score"]) if vector_candidates else 0.0
        citations = res.get("citations", [])
        top1_citation = citations[0] if citations else ""
        top1_chunk = _citation_chunk_id(top1_citation)
        top1_source = _citation_source(top1_citation)
        top_k_chunks = [_citation_chunk_id(c) for c in citations[: max(1, recall_k)]]
        top_k_sources = [_citation_source(c) for c in citations[: max(1, recall_k)]]

        if top1_score >= threshold_high:
            hits += 1
            semantic_hits += 1
            if gold_chunk_ids:
                correct = top1_chunk in gold_chunk_ids
                recall_ok = any(c in gold_chunk_ids for c in top_k_chunks)
            else:
                correct = top1_source in gold_doc_ids
                recall_ok = any(c in gold_doc_ids for c in top_k_sources)
            if correct:
                semantic_correct += 1
            else:
                false_reuse += 1
            if recall_ok:
                recall_hits += 1
        elif top1_score >= threshold_low:
            gray += 1
            if gold_chunk_ids:
                if any(c in gold_chunk_ids for c in top_k_chunks):
                    recall_hits += 1
            else:
                if any(c in gold_doc_ids for c in top_k_sources):
                    recall_hits += 1
        else:
            miss += 1
            if gold_chunk_ids:
                if any(c in gold_chunk_ids for c in top_k_chunks):
                    recall_hits += 1
            else:
                if any(c in gold_doc_ids for c in top_k_sources):
                    recall_hits += 1

    avg_rt = round(statistics.mean(latencies), 2)
    p95_rt = round(statistics.quantiles(latencies, n=20)[18], 2) if len(latencies) >= 2 else round(latencies[0], 2)
    p99_rt = round(statistics.quantiles(latencies, n=100)[98], 2) if len(latencies) >= 2 else round(latencies[0], 2)
    hit_rate = hits / total
    sem_precision = (semantic_correct / semantic_hits) if semantic_hits > 0 else 0.0
    false_reuse_rate = (false_reuse / semantic_hits) if semantic_hits > 0 else 0.0
    recall_at_k = recall_hits / total
    return {
        "threshold_low": threshold_low,
        "threshold_high": threshold_high,
        "vector_topk": vector_topk,
        "keyword_topk": keyword_topk,
        "final_topk": final_topk,
        "recall_k": recall_k,
        "avg_rt_ms": avg_rt,
        "p95_rt_ms": p95_rt,
        "p99_rt_ms": p99_rt,
        "hit_rate": round(hit_rate, 4),
        "semantic_precision_at_1": round(sem_precision, 4),
        "false_reuse_rate": round(false_reuse_rate, 4),
        "recall_at_k": round(recall_at_k, 4),
        "gray_rate": round(gray / total, 4),
        "miss_rate": round(miss / total, 4),
        "semantic_hits": semantic_hits,
        "total": total,
    }


def _score(row: Dict[str, Any]) -> float:
    # Higher score is better; prioritize quality with latency penalty.
    return (
        row["semantic_precision_at_1"] * 0.35
        + row["recall_at_k"] * 0.30
        + row["hit_rate"] * 0.20
        - row["false_reuse_rate"] * 0.35
        - (row["p95_rt_ms"] / 1000.0) * 0.10
    )


def _write_report(path: Path, stage1: List[Dict[str, Any]], stage2: List[Dict[str, Any]], best: Dict[str, Any]) -> None:
    lines: List[str] = []
    lines.append("# L2 Parameter Tuning Report")
    lines.append("")
    lines.append("## Metrics Definitions")
    lines.append("")
    lines.append("| Metric | Definition | Interpretation |")
    lines.append("|---|---|---|")
    lines.append("| P95/P99 Latency | 95%/99%请求耗时上界(ms) | 越低越好，控制尾延迟 |")
    lines.append("| Hit Rate | 强命中请求数 / 总请求数 | 越高越好，但不能牺牲质量 |")
    lines.append("| Semantic Precision@1 | 语义命中中Top1正确复用比例 | 越高越好，衡量复用质量 |")
    lines.append("| False Reuse Rate | 错误复用数 / 语义命中数 | 越低越好，关键风险指标 |")
    lines.append("| Recall@k | 前k候选包含正确答案概率 | 越高越好，衡量召回能力 |")
    lines.append("")
    lines.append("## Typical Parameter Effects")
    lines.append("")
    lines.append("- `threshold_high` 上调: 命中率下降，精度提升，误复用下降，延迟可能上升。")
    lines.append("- `threshold_low` 下调: 灰区变宽，召回更稳，但尾延迟通常上升。")
    lines.append("- `vector_topk/keyword_topk` 上调: Recall@k 提升，但检索耗时上升。")
    lines.append("- `recall_k` 上调: 评估更宽松，便于观察候选覆盖能力。")
    lines.append("")
    lines.append("## Stage-1 (Threshold Sweep, fixed k)")
    lines.append("")
    lines.append("| low | high | hit_rate | sem_precision@1 | false_reuse | recall@k | p95 | p99 |")
    lines.append("|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in stage1:
        lines.append(
            f"| {r['threshold_low']} | {r['threshold_high']} | {r['hit_rate']} | "
            f"{r['semantic_precision_at_1']} | {r['false_reuse_rate']} | {r['recall_at_k']} | "
            f"{r['p95_rt_ms']} | {r['p99_rt_ms']} |"
        )
    lines.append("")
    lines.append("## Stage-2 (K Sweep, fixed best thresholds)")
    lines.append("")
    lines.append("| vector_topk | keyword_topk | final_topk | recall_k | hit_rate | sem_precision@1 | false_reuse | recall@k | p95 | p99 |")
    lines.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in stage2:
        lines.append(
            f"| {r['vector_topk']} | {r['keyword_topk']} | {r['final_topk']} | {r['recall_k']} | "
            f"{r['hit_rate']} | {r['semantic_precision_at_1']} | {r['false_reuse_rate']} | "
            f"{r['recall_at_k']} | {r['p95_rt_ms']} | {r['p99_rt_ms']} |"
        )
    lines.append("")
    lines.append("## Recommended Parameters")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(best, ensure_ascii=False, indent=2))
    lines.append("```")
    lines.append("")
    lines.append("## Evidence-based Conclusion")
    lines.append("")
    lines.append(
        f"- 在当前数据与模型下，推荐参数实现 `hit_rate={best['hit_rate']}`、"
        f"`semantic_precision@1={best['semantic_precision_at_1']}`、"
        f"`false_reuse={best['false_reuse_rate']}`、`recall@k={best['recall_at_k']}`。"
    )
    lines.append(
        f"- 延迟表现: `p95={best['p95_rt_ms']}ms`, `p99={best['p99_rt_ms']}ms`。"
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Two-stage L2 parameter tuning.")
    parser.add_argument("--eval-set", default="data/eval/eval_set.jsonl")
    parser.add_argument("--limit", type=int, default=120)
    parser.add_argument("--output-dir", default="data/eval/reports")
    parser.add_argument("--stage1-lows", default="0.75,0.78,0.80,0.82")
    parser.add_argument("--stage1-highs", default="0.88,0.90,0.92,0.94")
    parser.add_argument("--stage1-vector-topk", type=int, default=20)
    parser.add_argument("--stage1-keyword-topk", type=int, default=20)
    parser.add_argument("--stage1-final-topk", type=int, default=5)
    parser.add_argument("--stage1-recall-k", type=int, default=5)
    parser.add_argument("--stage2-vector-topks", default="10,20,30")
    parser.add_argument("--stage2-keyword-topks", default="10,20,30")
    parser.add_argument("--stage2-final-topks", default="3,5")
    parser.add_argument("--stage2-recall-k", type=int, default=5)
    args = parser.parse_args()

    samples = _load_samples((PROJECT_ROOT / args.eval_set).resolve(), args.limit)
    if not samples:
        raise RuntimeError("No samples loaded.")

    lows = [float(x) for x in args.stage1_lows.split(",") if x.strip()]
    highs = [float(x) for x in args.stage1_highs.split(",") if x.strip()]

    stage1_rows: List[Dict[str, Any]] = []
    for low in lows:
        for high in highs:
            if high <= low:
                continue
            row = _compute_metrics(
                samples,
                threshold_low=low,
                threshold_high=high,
                vector_topk=args.stage1_vector_topk,
                keyword_topk=args.stage1_keyword_topk,
                final_topk=args.stage1_final_topk,
                recall_k=args.stage1_recall_k,
            )
            row["score"] = round(_score(row), 6)
            stage1_rows.append(row)
    stage1_rows.sort(key=lambda r: r["score"], reverse=True)
    best_stage1 = stage1_rows[0]

    stage2_rows: List[Dict[str, Any]] = []
    vks = [int(x) for x in args.stage2_vector_topks.split(",") if x.strip()]
    kks = [int(x) for x in args.stage2_keyword_topks.split(",") if x.strip()]
    fks = [int(x) for x in args.stage2_final_topks.split(",") if x.strip()]
    for vk in vks:
        for kk in kks:
            for fk in fks:
                row = _compute_metrics(
                    samples,
                    threshold_low=best_stage1["threshold_low"],
                    threshold_high=best_stage1["threshold_high"],
                    vector_topk=vk,
                    keyword_topk=kk,
                    final_topk=fk,
                    recall_k=args.stage2_recall_k,
                )
                row["score"] = round(_score(row), 6)
                stage2_rows.append(row)
    stage2_rows.sort(key=lambda r: r["score"], reverse=True)
    best = stage2_rows[0]

    output_dir = (PROJECT_ROOT / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "l2_tuning_best.json"
    csv_path = output_dir / "l2_tuning_grid.csv"
    md_path = output_dir / "l2_tuning_report.md"
    json_path.write_text(json.dumps(best, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # merge stage rows to CSV
    merged = [{"stage": "stage1", **r} for r in stage1_rows] + [{"stage": "stage2", **r} for r in stage2_rows]
    keys = sorted({k for r in merged for k in r.keys()})
    import csv

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        for r in merged:
            writer.writerow(r)

    _write_report(md_path, stage1_rows[:12], stage2_rows[:12], best)

    print(f"samples={len(samples)}")
    print(f"best={json_path}")
    print(f"grid={csv_path}")
    print(f"report={md_path}")


if __name__ == "__main__":
    main()
