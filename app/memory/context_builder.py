from typing import Any, Dict, List

from app.models.litellm_client import summarize_text_with_litellm


def _truncate(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    t = (text or "").strip()
    return t[:max_chars]


def build_context_with_budget(
    *,
    rag_context: str,
    memory_items: List[Dict[str, Any]],
    total_budget_chars: int,
    memory_budget_ratio: float,
    short_ratio: float = 0.5,
    long_ratio: float = 0.3,
    l3_ratio: float = 0.2,
    summarizer_enabled: bool = False,
    summary_max_chars: int = 180,
    system_policy: str = "",
    scenario_rules: str = "",
    tool_facts: str = "",
    quoted_context: str = "",
    user_query: str = "",
) -> Dict[str, Any]:
    total_budget_chars = max(200, int(total_budget_chars))
    memory_budget_ratio = min(0.7, max(0.1, float(memory_budget_ratio)))
    memory_budget = int(total_budget_chars * memory_budget_ratio)
    dynamic_budget = max(100, total_budget_chars - memory_budget)
    fixed_prefix = ""
    fixed_parts: List[str] = []
    if (system_policy or "").strip():
        fixed_parts.append(f"[System Policy]\n{system_policy.strip()}")
    if (scenario_rules or "").strip():
        fixed_parts.append(f"[Scenario Rules]\n{scenario_rules.strip()}")
    if fixed_parts:
        fixed_budget = max(120, int(total_budget_chars * 0.35))
        fixed_prefix = _truncate("\n\n".join(fixed_parts), fixed_budget)
    fixed_used = len(fixed_prefix)
    rest_dynamic = max(80, dynamic_budget - fixed_used)
    rag_budget = max(60, int(rest_dynamic * 0.65))
    tool_budget = max(40, rest_dynamic - rag_budget)

    rag_part = _truncate(rag_context or "", rag_budget)
    tool_part = _truncate(tool_facts or "", tool_budget)
    used_memory = 0
    dropped: List[Dict[str, Any]] = []
    selected: List[Dict[str, Any]] = []
    chunks: List[str] = []
    ratio_sum = max(0.01, short_ratio + long_ratio + l3_ratio)
    bucket_budget = {
        "short": int(memory_budget * (short_ratio / ratio_sum)),
        "long": int(memory_budget * (long_ratio / ratio_sum)),
        "l3": int(memory_budget * (l3_ratio / ratio_sum)),
    }
    bucket_used = {"short": 0, "long": 0, "l3": 0}

    grouped: Dict[str, List[Dict[str, Any]]] = {"short": [], "long": [], "l3": []}
    for item in memory_items:
        mt = str(item.get("memory_type", "long")).lower()
        if mt not in grouped:
            mt = "long"
        grouped[mt].append(item)
    for mt in grouped:
        grouped[mt] = sorted(grouped[mt], key=lambda x: float(x.get("score", 0.0)), reverse=True)

    summary_used_count = 0
    summary_error_count = 0

    for mt in ["short", "long", "l3"]:
        for item in grouped[mt]:
            text = (item.get("summary") or item.get("content") or "").strip()
            if not text:
                dropped.append(
                    {
                        "memory_id": item.get("memory_id", ""),
                        "memory_type": mt,
                        "reason": "empty_text",
                    }
                )
                continue
            source_text = text
            if summarizer_enabled and len(text) > max(60, int(summary_max_chars)):
                try:
                    s = summarize_text_with_litellm(
                        text=text,
                        max_chars=max(60, int(summary_max_chars)),
                        instruction="请将以下记忆片段压缩成简洁摘要，仅保留可直接用于回答用户问题的关键信息。",
                    )
                    text = (s.get("summary") or text).strip()
                    summary_used_count += 1
                except Exception:
                    summary_error_count += 1
                    text = source_text
            remain_bucket = bucket_budget[mt] - bucket_used[mt]
            remain_global = memory_budget - used_memory
            remain = min(remain_bucket, remain_global)
            if remain <= 30:
                dropped.append(
                    {
                        "memory_id": item.get("memory_id", ""),
                        "memory_type": mt,
                        "reason": "budget_exhausted",
                    }
                )
                continue
            cut = _truncate(text, remain)
            if not cut:
                continue
            chunks.append(f"[{mt}] {cut}")
            cut_len = len(cut)
            used_memory += cut_len
            bucket_used[mt] += cut_len
            selected.append(
                {
                    "memory_id": item.get("memory_id", ""),
                    "memory_type": mt,
                    "score": float(item.get("score", 0.0)),
                    "chars": cut_len,
                    "age_seconds": float(item.get("age_seconds", 0.0) or 0.0),
                }
            )

    memory_part = "\n".join(chunks)
    merged = ""
    dynamic_sections: List[str] = []
    if tool_part:
        dynamic_sections.append(f"[Tool Facts]\n{tool_part}")
    if rag_part:
        dynamic_sections.append(f"[RAG Context]\n{rag_part}")
    if memory_part:
        dynamic_sections.append(f"[Memory Context]\n{memory_part}")
    if (quoted_context or "").strip():
        dynamic_sections.append(f"[Quoted Resolved Turn]\n{_truncate(quoted_context.strip(), 900)}")
    if (user_query or "").strip():
        dynamic_sections.append(f"[User Query]\n{_truncate(user_query.strip(), 500)}")
    dynamic_merged = "\n\n".join(dynamic_sections).strip()
    if fixed_prefix and dynamic_merged:
        merged = f"{fixed_prefix}\n\n{dynamic_merged}"
    elif fixed_prefix:
        merged = fixed_prefix
    else:
        merged = dynamic_merged
    return {
        "context": merged,
        "debug": {
            "total_budget_chars": total_budget_chars,
            "fixed_prefix_used_chars": fixed_used,
            "rag_budget_chars": rag_budget,
            "tool_budget_chars": tool_budget,
            "memory_budget_chars": memory_budget,
            "rag_used_chars": len(rag_part),
            "tool_used_chars": len(tool_part),
            "memory_used_chars": used_memory,
            "memory_budget_by_type": bucket_budget,
            "memory_used_by_type": bucket_used,
            "selected_memories": selected,
            "selected_count": len(selected),
            "dropped_memories": dropped,
            "dropped_count": len(dropped),
            "memory_summary_enabled": bool(summarizer_enabled),
            "memory_summary_max_chars": int(summary_max_chars),
            "memory_summary_used_count": summary_used_count,
            "memory_summary_error_count": summary_error_count,
            "quoted_context_used_chars": len(_truncate((quoted_context or "").strip(), 900)),
        },
    }
