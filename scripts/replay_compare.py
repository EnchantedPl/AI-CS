import argparse
import csv
import json
import math
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.graph.workflows.minimal_chat import run_workflow
from app.rag.hybrid_retriever import RETRIEVER, RagRuntimeConfig


DEFAULT_QUERIES = [
    {"query": "花吃了那女孩由几段不同故事组成？"},
    {"query": "退款审核通过后多久到账？"},
    {"query": "商品发票怎么开？"},
    {"query": "七天无理由退货条件是什么？"},
    {"query": "如何查看订单物流进度？"},
]


def _route_domain(query: str) -> str:
    text = query.lower()
    if any(k in text for k in ["refund", "return", "退款", "退货"]):
        return "aftersales"
    if any(k in text for k in ["price", "spec", "价格", "规格", "参数"]):
        return "product_info"
    if any(k in text for k in ["risk", "legal", "投诉", "法律", "合规"]):
        return "risk_query"
    return "faq"


def _set_runtime_rag_config(
    *,
    rerank_enabled: bool,
    rerank_candidates: int,
    rerank_topk: int,
    rerank_model: str,
    rag_vector_topk: int,
    rag_keyword_topk: int,
    rag_topk: int,
) -> None:
    os.environ["RAG_ENABLE_RERANK"] = "true" if rerank_enabled else "false"
    os.environ["RAG_RERANK_CANDIDATES"] = str(rerank_candidates)
    os.environ["RAG_RERANK_TOP_K"] = str(rerank_topk)
    os.environ["RAG_RERANK_MODEL"] = rerank_model
    os.environ["RAG_VECTOR_TOP_K"] = str(rag_vector_topk)
    os.environ["RAG_KEYWORD_TOP_K"] = str(rag_keyword_topk)
    os.environ["RAG_TOP_K"] = str(rag_topk)
    RETRIEVER.cfg = RagRuntimeConfig.from_env()
    RETRIEVER._reranker_model = None
    RETRIEVER._reranker_model_name = None


def _load_queries(path: str, limit: int) -> List[Dict[str, Any]]:
    if not path:
        return DEFAULT_QUERIES[:limit] if limit > 0 else DEFAULT_QUERIES
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"query file not found: {path}")
    queries: List[Dict[str, Any]] = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            raw = line.strip()
            if not raw:
                continue
            item = json.loads(raw)
            q = item.get("query") if isinstance(item, dict) else None
            if q:
                queries.append(
                    {
                        "query": str(q),
                        "gold_answer": item.get("gold_answer", ""),
                        "gold_doc_ids": item.get("gold_doc_ids", []),
                        "gold_chunk_ids": item.get("gold_chunk_ids", []),
                    }
                )
            if limit > 0 and len(queries) >= limit:
                break
    return queries


def _run_once(query: str, scenario: str, idx: int, retrieval_only: bool) -> Dict[str, Any]:
    start = time.perf_counter()
    if retrieval_only:
        rag = RETRIEVER.retrieve(
            query=query,
            domain=_route_domain(query),
            retrieval_mode=os.getenv("RAG_RETRIEVAL_MODE", "hybrid"),
        )
        rt_ms = (time.perf_counter() - start) * 1000
        rerank = rag.get("params", {}).get("rerank", {})
        citations = rag.get("citations", [])
        return {
            "scenario": scenario,
            "query": query,
            "rt_ms": round(rt_ms, 2),
            "retrieved_count": len(rag.get("chunks", [])),
            "top_citation": citations[0] if citations else "",
            "citations": citations,
            "answer": "",
            "rerank_applied": bool(rerank.get("applied", False)),
            "before_ids": rerank.get("before_ids", []),
            "after_ids": rerank.get("after_ids", []),
            "memory_hit": 0,
            "memory_selected_count": 0,
            "memory_write_count": 0,
            "memory_dedupe_count": 0,
        }

    state = run_workflow(
        trace_id=f"replay-{scenario}-{idx}",
        event_id="",
        user_id=f"u_{idx}",
        tenant_id=f"tenant_{scenario}_{idx}",
        actor_type="user",
        channel="script",
        query=query,
        history=[],
    )
    rt_ms = (time.perf_counter() - start) * 1000
    rag_debug = state.get("debug", {}).get("rag", {})
    rerank = rag_debug.get("rerank", {})
    citations = state.get("citations", [])
    memory_debug = state.get("debug", {}).get("memory", {})
    return {
        "scenario": scenario,
        "query": query,
        "rt_ms": round(rt_ms, 2),
        "retrieved_count": int(rag_debug.get("retrieved_count", 0)),
        "top_citation": citations[0] if citations else "",
        "citations": citations,
        "answer": state.get("answer", ""),
        "rerank_applied": bool(rerank.get("applied", False)),
        "before_ids": rerank.get("before_ids", []),
        "after_ids": rerank.get("after_ids", []),
        "memory_hit": 1 if memory_debug.get("hit", False) else 0,
        "memory_selected_count": int(memory_debug.get("context_debug", {}).get("selected_count", 0) or 0),
        "memory_write_count": int(memory_debug.get("memory_write_count", 0) or 0),
        "memory_dedupe_count": int(memory_debug.get("memory_dedupe_count", 0) or 0),
    }


def _citation_source(citation: str) -> str:
    if not citation:
        return ""
    return citation.split("#", 1)[0]


def _citation_chunk_id(citation: str) -> str:
    if not citation:
        return ""
    parts = citation.split("#", 1)
    if len(parts) < 2:
        return ""
    return parts[1]


def _char_f1(pred: str, gold: str) -> Optional[float]:
    pred = (pred or "").strip()
    gold = (gold or "").strip()
    if not pred or not gold:
        return None
    pred_chars = list(pred)
    gold_chars = list(gold)
    pred_count: Dict[str, int] = {}
    gold_count: Dict[str, int] = {}
    for c in pred_chars:
        pred_count[c] = pred_count.get(c, 0) + 1
    for c in gold_chars:
        gold_count[c] = gold_count.get(c, 0) + 1
    common = 0
    for c, n in pred_count.items():
        common += min(n, gold_count.get(c, 0))
    if common == 0:
        return 0.0
    precision = common / len(pred_chars)
    recall = common / len(gold_chars)
    return 2 * precision * recall / (precision + recall)


def _augment_quality_metrics(row: Dict[str, Any], sample: Dict[str, Any]) -> Dict[str, Any]:
    gold_doc_ids = sample.get("gold_doc_ids", []) or []
    gold_chunk_ids = sample.get("gold_chunk_ids", []) or []
    if isinstance(gold_doc_ids, str):
        gold_doc_ids = [gold_doc_ids]
    if isinstance(gold_chunk_ids, str):
        gold_chunk_ids = [gold_chunk_ids]
    gold_doc_ids = [str(x) for x in gold_doc_ids if x]
    gold_chunk_ids = [str(x) for x in gold_chunk_ids if x]
    gold_answer = str(sample.get("gold_answer", "") or "")
    top_source = _citation_source(row.get("top_citation", ""))
    all_sources = [_citation_source(c) for c in row.get("citations", [])]
    top_chunk = _citation_chunk_id(row.get("top_citation", ""))
    all_chunks = [_citation_chunk_id(c) for c in row.get("citations", [])]

    def _mrr(golds: List[str], ranked: List[str]) -> float:
        for idx, src in enumerate(ranked, start=1):
            if src in golds:
                return 1.0 / idx
        return 0.0

    def _ndcg_at_k(golds: List[str], ranked: List[str], k: int) -> float:
        if k <= 0:
            return 0.0
        ranked_k = ranked[:k]
        dcg = 0.0
        for i, src in enumerate(ranked_k, start=1):
            rel = 1.0 if src in golds else 0.0
            dcg += rel / math.log2(i + 1)
        ideal_hits = min(len(golds), k)
        idcg = 0.0
        for i in range(1, ideal_hits + 1):
            idcg += 1.0 / math.log2(i + 1)
        if idcg == 0:
            return 0.0
        return dcg / idcg
    use_chunk_gold = len(gold_chunk_ids) > 0
    if use_chunk_gold:
        citation_hit_at_1 = 1 if top_chunk in gold_chunk_ids else 0
        citation_hit_at_k = 1 if any(c in gold_chunk_ids for c in all_chunks) else 0
        mrr = _mrr(gold_chunk_ids, all_chunks)
        ndcg_at_k = _ndcg_at_k(gold_chunk_ids, all_chunks, len(all_chunks))
        ndcg_at_3 = _ndcg_at_k(gold_chunk_ids, all_chunks, 3)
        ndcg_at_5 = _ndcg_at_k(gold_chunk_ids, all_chunks, 5)
    else:
        citation_hit_at_1 = 1 if gold_doc_ids and top_source in gold_doc_ids else 0
        citation_hit_at_k = 1 if gold_doc_ids and any(s in gold_doc_ids for s in all_sources) else 0
        mrr = _mrr(gold_doc_ids, all_sources) if gold_doc_ids else 0.0
        ndcg_at_k = _ndcg_at_k(gold_doc_ids, all_sources, len(all_sources)) if gold_doc_ids else 0.0
        ndcg_at_3 = _ndcg_at_k(gold_doc_ids, all_sources, 3) if gold_doc_ids else 0.0
        ndcg_at_5 = _ndcg_at_k(gold_doc_ids, all_sources, 5) if gold_doc_ids else 0.0

    answer = str(row.get("answer", "") or "")
    answer_match_substring = 0
    answer_f1: Optional[float] = None
    if gold_answer:
        answer_match_substring = 1 if (gold_answer in answer or answer in gold_answer) else 0
        answer_f1 = _char_f1(answer, gold_answer)

    row["gold_answer"] = gold_answer
    row["gold_doc_ids"] = json.dumps(gold_doc_ids, ensure_ascii=False)
    row["gold_chunk_ids"] = json.dumps(gold_chunk_ids, ensure_ascii=False)
    row["gold_granularity"] = "chunk" if use_chunk_gold else "source"
    row["citation_hit_at_1"] = citation_hit_at_1
    row["citation_hit_at_k"] = citation_hit_at_k
    row["mrr"] = round(mrr, 6)
    row["ndcg_at_k"] = round(ndcg_at_k, 6)
    row["ndcg_at_3"] = round(ndcg_at_3, 6)
    row["ndcg_at_5"] = round(ndcg_at_5, 6)
    row["answer_match_substring"] = answer_match_substring
    row["answer_char_f1"] = round(answer_f1, 4) if answer_f1 is not None else ""
    return row


def _mean(values: List[float]) -> float:
    return round(statistics.mean(values), 2) if values else 0.0


def _p95(values: List[float]) -> float:
    if not values:
        return 0.0
    if len(values) < 2:
        return round(values[0], 2)
    return round(statistics.quantiles(values, n=20)[18], 2)


def _write_markdown(
    *,
    out_path: Path,
    mode: str,
    baseline_rows: List[Dict[str, Any]],
    rerank_rows: List[Dict[str, Any]],
    retrieval_only: bool,
    pass_threshold_ndcg5: float,
    pass_threshold_mrr: float,
    max_p95_rt_increase_ms: float,
) -> None:
    base_rt = [r["rt_ms"] for r in baseline_rows]
    rr_rt = [r["rt_ms"] for r in rerank_rows]
    base_summary = {
        "hit1": _mean([float(r.get("citation_hit_at_1", 0)) for r in baseline_rows]),
        "hitk": _mean([float(r.get("citation_hit_at_k", 0)) for r in baseline_rows]),
        "mrr": _mean([float(r.get("mrr", 0)) for r in baseline_rows]),
        "ndcg3": _mean([float(r.get("ndcg_at_3", 0)) for r in baseline_rows]),
        "ndcg5": _mean([float(r.get("ndcg_at_5", 0)) for r in baseline_rows]),
        "ndcgk": _mean([float(r.get("ndcg_at_k", 0)) for r in baseline_rows]),
        "answer_match": _mean([float(r.get("answer_match_substring", 0)) for r in baseline_rows]),
        "answer_f1": _mean([float(r.get("answer_char_f1") or 0) for r in baseline_rows]),
        "avg_rt": _mean(base_rt),
        "p95_rt": _p95(base_rt),
        "memory_hit": _mean([float(r.get("memory_hit", 0)) for r in baseline_rows]),
        "memory_selected": _mean([float(r.get("memory_selected_count", 0)) for r in baseline_rows]),
        "memory_write": _mean([float(r.get("memory_write_count", 0)) for r in baseline_rows]),
        "memory_dedupe": _mean([float(r.get("memory_dedupe_count", 0)) for r in baseline_rows]),
    }
    rr_summary = {
        "hit1": _mean([float(r.get("citation_hit_at_1", 0)) for r in rerank_rows]),
        "hitk": _mean([float(r.get("citation_hit_at_k", 0)) for r in rerank_rows]),
        "mrr": _mean([float(r.get("mrr", 0)) for r in rerank_rows]),
        "ndcg3": _mean([float(r.get("ndcg_at_3", 0)) for r in rerank_rows]),
        "ndcg5": _mean([float(r.get("ndcg_at_5", 0)) for r in rerank_rows]),
        "ndcgk": _mean([float(r.get("ndcg_at_k", 0)) for r in rerank_rows]),
        "answer_match": _mean([float(r.get("answer_match_substring", 0)) for r in rerank_rows]),
        "answer_f1": _mean([float(r.get("answer_char_f1") or 0) for r in rerank_rows]),
        "avg_rt": _mean(rr_rt),
        "p95_rt": _p95(rr_rt),
        "memory_hit": _mean([float(r.get("memory_hit", 0)) for r in rerank_rows]),
        "memory_selected": _mean([float(r.get("memory_selected_count", 0)) for r in rerank_rows]),
        "memory_write": _mean([float(r.get("memory_write_count", 0)) for r in rerank_rows]),
        "memory_dedupe": _mean([float(r.get("memory_dedupe_count", 0)) for r in rerank_rows]),
    }

    lines: List[str] = []
    lines.append("# Replay Compare Report")
    lines.append("")
    lines.append(f"- mode: `{mode}`")
    lines.append(f"- retrieval_only: `{retrieval_only}`")
    lines.append(
        f"- thresholds: nDCG@5 >= `{pass_threshold_ndcg5}`, "
        f"MRR >= `{pass_threshold_mrr}`, p95_rt_increase <= `{max_p95_rt_increase_ms}` ms"
    )
    lines.append("")
    lines.append("## Metric Guide")
    lines.append("")
    lines.append("| metric | meaning | direction |")
    lines.append("|---|---|---|")
    lines.append("| citation_hit@1 | 首条引用是否命中金标文档 | higher better |")
    lines.append("| citation_hit@k | 前k条引用是否至少命中一个金标文档 | higher better |")
    lines.append("| MRR | 首个命中文档的倒数排名，越靠前越高 | higher better |")
    lines.append("| nDCG@3 / nDCG@5 / nDCG@k | 排序质量，兼顾位置折损 | higher better |")
    lines.append("| answer_substring_match | 回答与标准答案是否包含匹配 | higher better |")
    lines.append("| answer_char_f1 | 基于字符级重叠的回答相似度 | higher better |")
    lines.append("| avg_rt_ms / p95_rt_ms | 平均/尾延迟，评估性能代价 | lower better |")
    lines.append("| memory_hit | memory读命中（0/1） | higher better |")
    lines.append("| memory_selected_count | 实际注入LLM上下文的memory条数 | depends |")
    lines.append("| memory_write_count | 本轮新增写入memory条数 | depends |")
    lines.append("| memory_dedupe_count | 本轮被去重合并条数 | depends |")
    lines.append("")
    lines.append("## Scenario Summary")
    lines.append("")
    lines.append(
        "| scenario | count | avg_rt_ms | p95_rt_ms | avg_retrieved_count | "
        "rerank_applied_ratio | citation_hit@1 | citation_hit@k | MRR | nDCG@3 | nDCG@5 |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    lines.append(
        f"| baseline | {len(baseline_rows)} | {_mean(base_rt)} | {_p95(base_rt)} | "
        f"{_mean([r['retrieved_count'] for r in baseline_rows])} | "
        f"{_mean([1.0 if r['rerank_applied'] else 0.0 for r in baseline_rows])} | "
        f"{base_summary['hit1']} | {base_summary['hitk']} | {base_summary['mrr']} | "
        f"{base_summary['ndcg3']} | {base_summary['ndcg5']} |"
    )
    lines.append(
        f"| rerank_on | {len(rerank_rows)} | {_mean(rr_rt)} | {_p95(rr_rt)} | "
        f"{_mean([r['retrieved_count'] for r in rerank_rows])} | "
        f"{_mean([1.0 if r['rerank_applied'] else 0.0 for r in rerank_rows])} | "
        f"{rr_summary['hit1']} | {rr_summary['hitk']} | {rr_summary['mrr']} | "
        f"{rr_summary['ndcg3']} | {rr_summary['ndcg5']} |"
    )
    lines.append("")
    lines.append("## Memory Summary")
    lines.append("")
    lines.append("| scenario | memory_hit_rate | avg_selected | avg_write | avg_dedupe_merge |")
    lines.append("|---|---:|---:|---:|---:|")
    lines.append(
        f"| baseline | {base_summary['memory_hit']} | {base_summary['memory_selected']} | "
        f"{base_summary['memory_write']} | {base_summary['memory_dedupe']} |"
    )
    lines.append(
        f"| rerank_on | {rr_summary['memory_hit']} | {rr_summary['memory_selected']} | "
        f"{rr_summary['memory_write']} | {rr_summary['memory_dedupe']} |"
    )
    lines.append("")
    lines.append("## Per Query Delta")
    lines.append("")
    lines.append("| query | base_rt_ms | rerank_rt_ms | delta_rt_ms | base_top_citation | rerank_top_citation |")
    lines.append("|---|---:|---:|---:|---|---|")
    for base, rr in zip(baseline_rows, rerank_rows):
        lines.append(
            f"| {base['query']} | {base['rt_ms']} | {rr['rt_ms']} | "
            f"{round(rr['rt_ms'] - base['rt_ms'], 2)} | "
            f"{base['top_citation']} | {rr['top_citation']} |"
        )

    has_gold = any((r.get("gold_doc_ids") or r.get("gold_answer")) for r in baseline_rows + rerank_rows)
    if has_gold:
        lines.append("")
        lines.append("## Quality Summary (Gold-labeled)")
        lines.append("")
        lines.append("| scenario | citation_hit@1 | citation_hit@k | MRR | nDCG@3 | nDCG@5 | nDCG@k | answer_substring_match | answer_char_f1 |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for name, rows in [("baseline", baseline_rows), ("rerank_on", rerank_rows)]:
            lines.append(
                f"| {name} | "
                f"{_mean([float(r.get('citation_hit_at_1', 0)) for r in rows])} | "
                f"{_mean([float(r.get('citation_hit_at_k', 0)) for r in rows])} | "
                f"{_mean([float(r.get('mrr', 0)) for r in rows])} | "
                f"{_mean([float(r.get('ndcg_at_3', 0)) for r in rows])} | "
                f"{_mean([float(r.get('ndcg_at_5', 0)) for r in rows])} | "
                f"{_mean([float(r.get('ndcg_at_k', 0)) for r in rows])} | "
                f"{_mean([float(r.get('answer_match_substring', 0)) for r in rows])} | "
                f"{_mean([float(r.get('answer_char_f1') or 0) for r in rows])} |"
            )

        lines.append("")
        lines.append("## Top nDCG Degradation Cases")
        lines.append("")
        lines.append("| query | base_nDCG@5 | rerank_nDCG@5 | delta | base_top_citation | rerank_top_citation |")
        lines.append("|---|---:|---:|---:|---|---|")
        paired = []
        for base, rr in zip(baseline_rows, rerank_rows):
            delta = float(rr.get("ndcg_at_5", 0.0)) - float(base.get("ndcg_at_5", 0.0))
            paired.append((delta, base, rr))
        paired.sort(key=lambda x: x[0])
        for delta, base, rr in paired[:10]:
            lines.append(
                f"| {base.get('query','')} | {base.get('ndcg_at_5', 0)} | "
                f"{rr.get('ndcg_at_5', 0)} | {round(delta, 6)} | "
                f"{base.get('top_citation','')} | {rr.get('top_citation','')} |"
            )

    lines.append("")
    lines.append("## Conclusion")
    lines.append("")
    delta_ndcg5 = round(rr_summary["ndcg5"] - base_summary["ndcg5"], 4)
    delta_mrr = round(rr_summary["mrr"] - base_summary["mrr"], 4)
    delta_hit1 = round(rr_summary["hit1"] - base_summary["hit1"], 4)
    delta_p95 = round(rr_summary["p95_rt"] - base_summary["p95_rt"], 2)
    passed = (
        delta_ndcg5 >= pass_threshold_ndcg5
        and delta_mrr >= pass_threshold_mrr
        and delta_p95 <= max_p95_rt_increase_ms
    )
    if passed:
        lines.append(
            f"- rerank结论：`PASS`。nDCG@5 `+{delta_ndcg5}`，MRR `+{delta_mrr}`，"
            f"citation_hit@1 `+{delta_hit1}`，但 p95 RT 变化 `{delta_p95}` ms。"
        )
    else:
        lines.append(
            f"- rerank结论：`NOT PASS`。nDCG@5 `{delta_ndcg5}`，MRR `{delta_mrr}`，"
            f"citation_hit@1 `{delta_hit1}`，p95 RT 变化 `{delta_p95}` ms。"
        )
        fail_reasons = []
        if delta_ndcg5 < pass_threshold_ndcg5:
            fail_reasons.append("nDCG@5未达阈值")
        if delta_mrr < pass_threshold_mrr:
            fail_reasons.append("MRR未达阈值")
        if delta_p95 > max_p95_rt_increase_ms:
            fail_reasons.append("p95 RT超阈值")
        if fail_reasons:
            lines.append(f"- 失败原因：{', '.join(fail_reasons)}")
    lines.append("")
    lines.append("## Improvement Suggestions")
    lines.append("")
    if delta_ndcg5 < 0:
        lines.append("- 优先调大候选池：`RAG_RERANK_CANDIDATES` 从 10 提升到 20/30 再复测。")
        lines.append("- 抽查 `Top nDCG Degradation Cases`，确认是否金标粒度与chunk粒度不一致。")
    else:
        lines.append("- 保持当前rerank配置，继续扩大样本集验证稳定性（建议>=500）。")
    if delta_p95 > 30:
        lines.append("- 若尾延迟上升明显，建议降低 rerank 候选数或仅对高风险问题启用rerank。")
    else:
        lines.append("- 延迟代价可控，可继续尝试提升 rerank 模型或候选策略。")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_csv(out_path: Path, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return
    keys = list(rows[0].keys())
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            normalized: Dict[str, Any] = {}
            for k, v in row.items():
                if isinstance(v, (list, dict)):
                    normalized[k] = json.dumps(v, ensure_ascii=False)
                else:
                    normalized[k] = v
            writer.writerow(normalized)


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay compare baseline vs rerank.")
    parser.add_argument("--query-jsonl", default="", help="JSONL file with {'query': '...'}")
    parser.add_argument("--limit", type=int, default=200, help="Max query count")
    parser.add_argument("--mode", choices=["local", "cloud", "mix"], default="mix")
    parser.add_argument("--rerank-model", default="BAAI/bge-reranker-base")
    parser.add_argument("--rerank-candidates", type=int, default=10)
    parser.add_argument("--rerank-topk", type=int, default=2)
    parser.add_argument("--rag-vector-topk", type=int, default=20)
    parser.add_argument("--rag-keyword-topk", type=int, default=20)
    parser.add_argument("--rag-topk", type=int, default=5)
    parser.add_argument(
        "--pass-threshold-ndcg5",
        type=float,
        default=0.01,
        help="Minimum nDCG@5 improvement required for PASS.",
    )
    parser.add_argument(
        "--pass-threshold-mrr",
        type=float,
        default=0.005,
        help="Minimum MRR improvement required for PASS.",
    )
    parser.add_argument(
        "--max-p95-rt-increase-ms",
        type=float,
        default=50.0,
        help="Maximum allowed p95 latency increase for PASS.",
    )
    parser.add_argument(
        "--retrieval-only",
        action="store_true",
        help="Only run retriever (faster, less noise than full /chat workflow).",
    )
    parser.add_argument("--output-dir", default="data/eval/reports", help="Output directory")
    args = parser.parse_args()

    mode_map = {
        "local": ("local", "local"),
        "cloud": ("cloud", "cloud"),
        "mix": ("local", "cloud"),
    }
    llm_mode, embedding_mode = mode_map[args.mode]
    os.environ["LLM_MODE"] = llm_mode
    os.environ["EMBEDDING_MODE"] = embedding_mode

    queries = _load_queries(args.query_jsonl, args.limit)
    if not queries:
        raise RuntimeError("No queries found for replay compare.")

    _set_runtime_rag_config(
        rerank_enabled=False,
        rerank_candidates=args.rerank_candidates,
        rerank_topk=args.rerank_topk,
        rerank_model=args.rerank_model,
        rag_vector_topk=args.rag_vector_topk,
        rag_keyword_topk=args.rag_keyword_topk,
        rag_topk=args.rag_topk,
    )
    baseline_rows = []
    for i, sample in enumerate(queries, start=1):
        row = _run_once(str(sample["query"]), "baseline", i, args.retrieval_only)
        baseline_rows.append(_augment_quality_metrics(row, sample))

    _set_runtime_rag_config(
        rerank_enabled=True,
        rerank_candidates=args.rerank_candidates,
        rerank_topk=args.rerank_topk,
        rerank_model=args.rerank_model,
        rag_vector_topk=args.rag_vector_topk,
        rag_keyword_topk=args.rag_keyword_topk,
        rag_topk=args.rag_topk,
    )
    rerank_rows = []
    for i, sample in enumerate(queries, start=1):
        row = _run_once(str(sample["query"]), "rerank_on", i, args.retrieval_only)
        rerank_rows.append(_augment_quality_metrics(row, sample))

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    all_rows = baseline_rows + rerank_rows
    csv_path = output_dir / "replay_compare_rows.csv"
    md_path = output_dir / "replay_compare_report.md"
    _write_csv(csv_path, all_rows)
    _write_markdown(
        out_path=md_path,
        mode=args.mode,
        baseline_rows=baseline_rows,
        rerank_rows=rerank_rows,
        retrieval_only=args.retrieval_only,
        pass_threshold_ndcg5=args.pass_threshold_ndcg5,
        pass_threshold_mrr=args.pass_threshold_mrr,
        max_p95_rt_increase_ms=args.max_p95_rt_increase_ms,
    )

    print(f"queries: {len(queries)}")
    print(f"rows_csv: {csv_path}")
    print(f"report_md: {md_path}")


if __name__ == "__main__":
    main()
