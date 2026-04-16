import os
import re
from typing import Any, Dict, List, Tuple


_PHONE_RE = re.compile(r"\b1[3-9]\d{9}\b")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_IDCN_RE = re.compile(r"\b\d{17}[\dXx]\b")
_BANK_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")


def _load_banned_terms() -> List[str]:
    raw = os.getenv("GUARDRAIL_BANNED_TERMS", "")
    terms = [x.strip() for x in raw.split(",") if x.strip()]
    return terms


def estimate_tokens(query: str, history: List[Dict[str, Any]]) -> int:
    history_text = ""
    for item in history or []:
        if not isinstance(item, dict):
            continue
        history_text += str(item.get("content", "") or "")
    total_chars = len(query or "") + len(history_text)
    # Simple approximation for Chinese-heavy traffic: ~1 token per 1.5 chars.
    return max(1, int(total_chars / 1.5) + 1)


def detect_sensitive_items(text: str) -> List[str]:
    t = text or ""
    hits: List[str] = []
    if _PHONE_RE.search(t):
        hits.append("phone")
    if _EMAIL_RE.search(t):
        hits.append("email")
    if _IDCN_RE.search(t):
        hits.append("id_cn")
    if _BANK_RE.search(t):
        hits.append("bank_card")
    return hits


def mask_sensitive(text: str) -> str:
    t = text or ""
    t = _PHONE_RE.sub(lambda m: m.group(0)[:3] + "****" + m.group(0)[-4:], t)
    t = _EMAIL_RE.sub("[email_masked]", t)
    t = _IDCN_RE.sub(lambda m: m.group(0)[:4] + "**********" + m.group(0)[-4:], t)
    t = _BANK_RE.sub("[bank_card_masked]", t)
    return t


def apply_output_guardrail(
    *,
    answer: str,
    citations: List[str],
    route_target: str,
) -> Tuple[str, Dict[str, Any]]:
    max_chars = int(os.getenv("GUARDRAIL_MAX_OUTPUT_CHARS", "1200"))
    require_citation_routes = {
        x.strip() for x in os.getenv("GUARDRAIL_REQUIRE_CITATION_ROUTES", "faq,product_info").split(",") if x.strip()
    }
    action = "pass"
    reasons: List[str] = []
    out = answer or ""

    if len(out) > max_chars:
        out = out[:max_chars]
        action = "rewrite"
        reasons.append("truncate_long_output")

    banned_terms = _load_banned_terms()
    lowered = out.lower()
    banned_hit = [term for term in banned_terms if term.lower() in lowered]
    if banned_hit:
        action = "block"
        reasons.append("banned_terms")
        out = "抱歉，该请求涉及受限内容，我已转人工处理。"

    sensitive_hits = detect_sensitive_items(out)
    if sensitive_hits:
        out = mask_sensitive(out)
        if action != "block":
            action = "rewrite"
        reasons.append("mask_sensitive")

    if route_target in require_citation_routes and len(citations or []) == 0 and action != "block":
        action = "rewrite"
        reasons.append("missing_citation")
        out = (out + "\n\n提示：当前回答缺少可追溯引用，建议复核后再回复用户。").strip()

    return out, {
        "action": action,
        "reasons": reasons,
        "sensitive_hits": sensitive_hits,
        "banned_hits": banned_hit,
    }

