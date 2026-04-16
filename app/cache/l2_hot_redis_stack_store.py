import json
import time
from array import array
from typing import Any, Dict, List

from redis import Redis


def _vec_bytes(values: List[float]) -> bytes:
    return array("f", values).tobytes()


def _escape_tag_value(value: str) -> str:
    # RediSearch TAG special chars escaping.
    escaped = value.replace("\\", "\\\\")
    for ch in [",", ".", "<", ">", "{", "}", "[", "]", "\"", "'", ":", ";", "!", "@", "#", "$", "%", "^", "&", "*", "(", ")", "-", "+", "=", "~", "/"]:
        escaped = escaped.replace(ch, f"\\{ch}")
    return escaped


class L2HotRedisStackStore:
    def __init__(self, redis_client: Redis, index_name: str = "idx:l2_semantic_cache") -> None:
        self._r = redis_client
        self._index_name = index_name
        self._prefix = "l2:entry:"

    def ensure_index(self, cloud_dim: int, local_dim: int) -> None:
        try:
            self._r.execute_command("FT.INFO", self._index_name)
            return
        except Exception:
            pass

        self._r.execute_command(
            "FT.CREATE",
            self._index_name,
            "ON",
            "HASH",
            "PREFIX",
            "1",
            self._prefix,
            "SCHEMA",
            "tenant_id",
            "TAG",
            "actor_scope",
            "TAG",
            "lang",
            "TAG",
            "region",
            "TAG",
            "prompt_version",
            "TAG",
            "kb_version",
            "TAG",
            "policy_version",
            "TAG",
            "domain",
            "TAG",
            "query_norm",
            "TEXT",
            "answer_text",
            "TEXT",
            "embedding_cloud",
            "VECTOR",
            "HNSW",
            "6",
            "TYPE",
            "FLOAT32",
            "DIM",
            str(cloud_dim),
            "DISTANCE_METRIC",
            "COSINE",
            "embedding_local",
            "VECTOR",
            "HNSW",
            "6",
            "TYPE",
            "FLOAT32",
            "DIM",
            str(local_dim),
            "DISTANCE_METRIC",
            "COSINE",
        )

    def upsert(self, row: Dict[str, Any], vector: List[float], vector_field: str, ttl_seconds: int) -> None:
        key = f"{self._prefix}{row['cache_id']}"
        payload = {
            "cache_id": row["cache_id"],
            "tenant_id": row["tenant_id"],
            "actor_scope": row["actor_scope"],
            "lang": row["lang"],
            "region": row["region"],
            "prompt_version": row["prompt_version"],
            "kb_version": row["kb_version"],
            "policy_version": row["policy_version"],
            "domain": row["domain"],
            "query_norm": row["query_norm"],
            "answer_text": row["answer_text"],
            "citations_json": json.dumps(row.get("citations", []), ensure_ascii=False),
            "updated_at": str(int(time.time())),
            vector_field: _vec_bytes(vector),
        }
        self._r.hset(key, mapping=payload)
        self._r.expire(key, ttl_seconds)

    def search_topk(self, filters: Dict[str, Any], vector: List[float], vector_field: str, top_k: int) -> List[Dict[str, Any]]:
        filter_expr = (
            f"@tenant_id:{{{_escape_tag_value(str(filters['tenant_id']))}}} "
            f"@actor_scope:{{{_escape_tag_value(str(filters['actor_scope']))}}} "
            f"@lang:{{{_escape_tag_value(str(filters['lang']))}}} "
            f"@region:{{{_escape_tag_value(str(filters['region']))}}} "
            f"@prompt_version:{{{_escape_tag_value(str(filters['prompt_version']))}}} "
            f"@kb_version:{{{_escape_tag_value(str(filters['kb_version']))}}} "
            f"@policy_version:{{{_escape_tag_value(str(filters['policy_version']))}}} "
            f"@domain:{{{_escape_tag_value(str(filters['domain']))}}}"
        )
        query = f"({filter_expr})=>[KNN {top_k} @{vector_field} $vec AS distance]"
        raw = self._r.execute_command(
            "FT.SEARCH",
            self._index_name,
            query,
            "PARAMS",
            "2",
            "vec",
            _vec_bytes(vector),
            "SORTBY",
            "distance",
            "DIALECT",
            "2",
            "RETURN",
            "5",
            "cache_id",
            "answer_text",
            "citations_json",
            "query_norm",
            "distance",
        )
        total = int(raw[0]) if raw else 0
        if total <= 0:
            return []

        out: List[Dict[str, Any]] = []
        i = 1
        while i < len(raw):
            fields = raw[i + 1]
            i += 2
            data: Dict[str, bytes] = {}
            for j in range(0, len(fields), 2):
                key = fields[j].decode("utf-8")
                data[key] = fields[j + 1]
            dist = float(data.get("distance", b"1.0").decode("utf-8"))
            score = 1.0 - dist
            citations = []
            try:
                citations = json.loads((data.get("citations_json", b"[]")).decode("utf-8"))
            except Exception:
                citations = []
            out.append(
                {
                    "cache_id": data.get("cache_id", b"").decode("utf-8"),
                    "answer": data.get("answer_text", b"").decode("utf-8"),
                    "citations": citations,
                    "query_norm": data.get("query_norm", b"").decode("utf-8"),
                    "score": score,
                    "source": "l2_hot_redis",
                }
            )
        return out
