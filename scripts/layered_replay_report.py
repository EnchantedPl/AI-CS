#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple


LAYER_PARAM_HINTS = {
    "L1": [
        "INTENT_CONF_THRESHOLD",
        "RISK_HIGH_FORCE_HUMAN",
        "route prompt / policy rules",
    ],
    "L2": [
        "SEMANTIC_THRESHOLD_LOW",
        "SEMANTIC_THRESHOLD_HIGH",
        "L2_GRAY_SECOND_THRESHOLD",
        "L2_GRAY_PG_TOPK",
    ],
    "L3": [
        "RAG_TOP_K",
        "RAG_VECTOR_TOP_K",
        "RAG_KEYWORD_TOP_K",
        "RAG_ENABLE_RERANK",
        "RAG_RERANK_CANDIDATES",
        "RAG_RERANK_TOP_K",
    ],
    "L4": [
        "LLM_MODE / LOCAL_LLM_MODEL / CLOUD_LLM_MODEL",
        "PROMPT_VERSION / POLICY_VERSION",
        "LLM_MAX_CONTEXT_CHARS",
        "CONTEXT_TOTAL_BUDGET_CHARS",
    ],
}


def _read_json(path: str) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"report file not found: {path}")
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"invalid report format: {path}")
    return data


def _pct(n: int, d: int) -> float:
    if d <= 0:
        return 0.0
    return round((n / d) * 100.0, 2)


def _mean(values: List[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 4)


def _layer_summary(report: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, float]]:
    rows = report.get("rows", []) if isinstance(report.get("rows"), list) else []
    cases = int(report.get("cases", len(rows)) or 0)
    drift = report.get("drift_distribution", {}) if isinstance(report.get("drift_distribution"), dict) else {}
    route_match = _mean([float(r.get("route_match", 0.0)) for r in rows if isinstance(r, dict)])
    cache_match = _mean([float(r.get("cache_match", 0.0)) for r in rows if isinstance(r, dict)])
    rag_match = _mean([float(r.get("rag_match", 0.0)) for r in rows if isinstance(r, dict)])
    final_match = _mean([float(r.get("final_match", 0.0)) for r in rows if isinstance(r, dict)])
    metrics = {
        "route_match": route_match,
        "cache_match": cache_match,
        "rag_match": rag_match,
        "final_match": final_match,
    }
    top_drift_layer = "none"
    top_drift_count = 0
    for k, v in drift.items():
        try:
            c = int(v or 0)
        except Exception:
            c = 0
        if k != "none" and c > top_drift_count:
            top_drift_layer = str(k)
            top_drift_count = c
    summary = {
        "cases": cases,
        "drift_distribution": drift,
        "top_drift_layer": top_drift_layer,
        "top_drift_count": top_drift_count,
        "top_drift_ratio_pct": _pct(top_drift_count, cases),
    }
    return summary, metrics


def _build_suggestions(
    *,
    observe_summary: Dict[str, Any],
    observe_metrics: Dict[str, float],
    isolate_summary: Dict[str, Any],
    isolate_metrics: Dict[str, float],
) -> List[str]:
    lines: List[str] = []
    obs_layer = str(observe_summary.get("top_drift_layer", "none"))
    iso_layer = str(isolate_summary.get("top_drift_layer", "none"))
    obs_ratio = float(observe_summary.get("top_drift_ratio_pct", 0.0) or 0.0)
    iso_ratio = float(isolate_summary.get("top_drift_ratio_pct", 0.0) or 0.0)

    if obs_layer == "none":
        lines.append("observe 模式未出现明显首漂移层，当前参数整体稳定，可进入小流量灰度验证。")
    else:
        lines.append(
            f"observe 模式主漂移层为 `{obs_layer}`（{obs_ratio}%），建议优先调该层参数，避免跨层盲调。"
        )
        hints = LAYER_PARAM_HINTS.get(obs_layer, [])
        if hints:
            lines.append(f"优先参数：{', '.join(hints)}。")

    if iso_layer != "none":
        lines.append(
            f"isolate 模式主漂移层为 `{iso_layer}`（{iso_ratio}%），说明在屏蔽上游影响后该层/下游仍是主要变化来源。"
        )
        if obs_layer != iso_layer and obs_layer != "none":
            lines.append(
                "observe 与 isolate 主漂移层不一致：存在层间耦合，建议先固定上游层参数，再单独扫描目标层。"
            )
    else:
        lines.append("isolate 模式无明显漂移，说明目标层影响可控，优先排查上游层联动。")

    if observe_metrics.get("route_match", 1.0) < 0.95:
        lines.append("路由一致率偏低，先收敛 L1（路由）再做 Cache/RAG 调参。")
    if observe_metrics.get("cache_match", 1.0) < 0.95:
        lines.append("Cache 一致率偏低，建议先做阈值小步扫描（0.01 粒度）并观察误命中。")
    if observe_metrics.get("rag_match", 1.0) < 0.95:
        lines.append("RAG 一致率偏低，先锁定 `RAG_TOP_K` 与 `RAG_ENABLE_RERANK`，再调候选池。")
    if observe_metrics.get("final_match", 1.0) < 0.95:
        lines.append("最终答案一致率偏低，优先检查模型版本、上下文预算和 prompt/policy 版本一致性。")

    return lines


def _write_markdown(
    *,
    out_path: Path,
    observe: Dict[str, Any],
    isolate: Dict[str, Any],
    observe_summary: Dict[str, Any],
    observe_metrics: Dict[str, float],
    isolate_summary: Dict[str, Any],
    isolate_metrics: Dict[str, float],
    suggestions: List[str],
) -> None:
    lines: List[str] = []
    lines.append("# Layered Replay Attribution Report")
    lines.append("")
    lines.append("## Experiment Metadata")
    lines.append("")
    lines.append(f"- observe_experiment_id: `{observe.get('experiment_id', '')}`")
    lines.append(f"- isolate_experiment_id: `{isolate.get('experiment_id', '')}`")
    lines.append(f"- baseline_tag: `{observe.get('baseline_tag', '')}`")
    lines.append(f"- candidate_tag: `{observe.get('candidate_tag', '')}`")
    lines.append(f"- isolate_target_layer: `{isolate.get('target_layer', '')}`")
    lines.append("")
    lines.append("## Drift Distribution")
    lines.append("")
    lines.append("| mode | cases | top_drift_layer | top_drift_count | top_drift_ratio | distribution |")
    lines.append("|---|---:|---|---:|---:|---|")
    lines.append(
        f"| observe | {observe_summary['cases']} | {observe_summary['top_drift_layer']} | "
        f"{observe_summary['top_drift_count']} | {observe_summary['top_drift_ratio_pct']}% | "
        f"{json.dumps(observe_summary['drift_distribution'], ensure_ascii=False)} |"
    )
    lines.append(
        f"| isolate | {isolate_summary['cases']} | {isolate_summary['top_drift_layer']} | "
        f"{isolate_summary['top_drift_count']} | {isolate_summary['top_drift_ratio_pct']}% | "
        f"{json.dumps(isolate_summary['drift_distribution'], ensure_ascii=False)} |"
    )
    lines.append("")
    lines.append("## Layer Match Score")
    lines.append("")
    lines.append("| mode | route_match | cache_match | rag_match | final_match |")
    lines.append("|---|---:|---:|---:|---:|")
    lines.append(
        f"| observe | {observe_metrics['route_match']} | {observe_metrics['cache_match']} | "
        f"{observe_metrics['rag_match']} | {observe_metrics['final_match']} |"
    )
    lines.append(
        f"| isolate | {isolate_metrics['route_match']} | {isolate_metrics['cache_match']} | "
        f"{isolate_metrics['rag_match']} | {isolate_metrics['final_match']} |"
    )
    lines.append("")
    lines.append("## Parameter Suggestions")
    lines.append("")
    for s in suggestions:
        lines.append(f"- {s}")
    lines.append("")
    lines.append("## Output Field Glossary (observe / isolate)")
    lines.append("")
    lines.append("- `experiment_id`: 本次 compare 的唯一标识，可关联数据库中的 `replay_experiment/replay_diff`。")
    lines.append("- `mode`: `observe` 或 `isolate`。")
    lines.append("- `target_layer`: 仅 `isolate` 有值，表示从该层开始看漂移（上游视为冻结影响）。")
    lines.append("- `baseline_tag` / `candidate_tag`: 两次采集批次标签，用于对比。")
    lines.append("- `cases`: 本次成功匹配并比较的 query 数量。")
    lines.append("- `drift_distribution`: 首漂移层分布统计，`none` 表示该样本四层都一致。")
    lines.append("- `rows[].query`: 样本 query 文本。")
    lines.append("- `rows[].first_drift_layer`: 该样本第一处漂移层（或 `none`）。")
    lines.append("- `rows[].route_match/cache_match/rag_match/final_match`: 各层是否一致（1=一致，0=不一致）。")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate attribution report from layered replay observe/isolate outputs."
    )
    parser.add_argument(
        "--observe-json",
        default="data/eval/reports/layered_replay_report.observe.json",
        help="observe mode output json path",
    )
    parser.add_argument(
        "--isolate-json",
        default="data/eval/reports/layered_replay_report.isolate.json",
        help="isolate mode output json path",
    )
    parser.add_argument(
        "--output-md",
        default="data/eval/reports/layered_replay_attribution_report.md",
        help="attribution markdown report output path",
    )
    args = parser.parse_args()

    observe = _read_json(args.observe_json)
    isolate = _read_json(args.isolate_json)
    observe_summary, observe_metrics = _layer_summary(observe)
    isolate_summary, isolate_metrics = _layer_summary(isolate)
    suggestions = _build_suggestions(
        observe_summary=observe_summary,
        observe_metrics=observe_metrics,
        isolate_summary=isolate_summary,
        isolate_metrics=isolate_metrics,
    )
    out_path = Path(args.output_md)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _write_markdown(
        out_path=out_path,
        observe=observe,
        isolate=isolate,
        observe_summary=observe_summary,
        observe_metrics=observe_metrics,
        isolate_summary=isolate_summary,
        isolate_metrics=isolate_metrics,
        suggestions=suggestions,
    )
    print(f"report_md={out_path}")


if __name__ == "__main__":
    main()

