import hashlib
import re
from typing import Dict

from app.core.config import Settings


def _normalize_query(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    return normalized


def _sha256_short(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def build_cache_keys(
    *,
    query: str,
    tenant_id: str,
    actor_type: str,
    intent_bucket: str,
    settings: Settings,
) -> Dict[str, str]:
    # A 类硬隔离字段（必须）：tenant、role_scope、region、prompt/kb version
    role_scope = actor_type
    region = settings.cache_region
    lang = settings.default_language

    normalized_query = _normalize_query(query)
    qhash = _sha256_short(normalized_query)

    l1 = (
        f"l1:{tenant_id}:{role_scope}:{region}:{settings.prompt_version}:"
        f"{intent_bucket}:{lang}:{qhash}"
    )
    l2_namespace = (
        f"l2ns:{tenant_id}:{role_scope}:{region}:{settings.prompt_version}:"
        f"{settings.kb_version}:{intent_bucket}:{lang}"
    )
    l2_payload = f"l2:{l2_namespace}:{qhash}"
    l3 = (
        f"l3:{tenant_id}:{settings.kb_version}:{settings.embedding_model}:"
        f"{intent_bucket}:{lang}:{qhash}"
    )

    return {
        "normalized_query": normalized_query,
        "qhash": qhash,
        "l1_key": l1,
        "l2_namespace": l2_namespace,
        "l2_payload_key": l2_payload,
        "l3_key": l3,
    }

