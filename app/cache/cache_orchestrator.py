import hashlib
import os
import re
import uuid
import time
from typing import Any, Dict, List, Optional, Tuple

from app.cache.embedding_runtime import EmbeddingRuntime
from app.cache.l1_exact_redis import L1ExactRedisCache
from app.cache.l2_hot_redis_stack_store import L2HotRedisStackStore
from app.cache.l2_persist_pg_store import L2PersistPgStore
from app.cache.redis_client import build_redis_client
from app.core.config import Settings


def _normalize_query(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _qhash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


class CacheOrchestrator:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._redis = build_redis_client(settings)
        self._l1 = L1ExactRedisCache(self._redis, settings.l1_ttl_seconds)
        self._l2_hot = L2HotRedisStackStore(self._redis)
        self._l2_persist = L2PersistPgStore()
        self._embed = EmbeddingRuntime()
        self._ready = False
        self._reranker_model = None
        self._reranker_model_name = None
        self._events: List[Dict[str, Any]] = []
        self._breaker: Dict[str, Dict[str, float]] = {
            "redis": {"failures": 0, "open_until": 0.0},
            "pg": {"failures": 0, "open_until": 0.0},
            "rerank": {"failures": 0, "open_until": 0.0},
        }

    def _ensure_ready(self) -> None:
        if self._ready:
            return
        cloud_dim = int(os.getenv("CLOUD_EMBEDDING_DIM", "1024"))
        local_dim = int(os.getenv("LOCAL_EMBEDDING_DIM", "384"))
        self._l2_hot.ensure_index(
            cloud_dim=cloud_dim,
            local_dim=local_dim,
        )
        self._l2_persist.ensure_schema(cloud_dim=cloud_dim, local_dim=local_dim)
        self._ready = True

    def _filters(self, *, tenant_id: str, actor_type: str, domain: str) -> Dict[str, Any]:
        return {
            "tenant_id": tenant_id,
            "actor_scope": actor_type,
            "lang": self._settings.default_language,
            "region": self._settings.cache_region,
            "prompt_version": self._settings.prompt_version,
            "kb_version": self._settings.kb_version,
            "policy_version": self._settings.policy_version,
            "domain": domain,
        }

    def _parse_citation_refs(self, citations: List[str]) -> Tuple[List[str], List[str]]:
        source_doc_ids: List[str] = []
        source_chunk_ids: List[str] = []
        for c in citations:
            if not c:
                continue
            parts = c.split("#", 1)
            if parts[0]:
                source_doc_ids.append(parts[0])
            if len(parts) > 1 and parts[1]:
                source_chunk_ids.append(parts[1])
        return sorted(set(source_doc_ids)), sorted(set(source_chunk_ids))

    def _is_breaker_open(self, name: str) -> bool:
        info = self._breaker.get(name, {})
        return float(info.get("open_until", 0.0)) > time.time()

    def _record_failure(self, name: str) -> None:
        info = self._breaker[name]
        info["failures"] = float(info.get("failures", 0)) + 1
        if info["failures"] >= self._settings.cache_circuit_fail_threshold:
            info["open_until"] = time.time() + self._settings.cache_circuit_cooldown_seconds

    def _record_success(self, name: str) -> None:
        info = self._breaker[name]
        info["failures"] = 0
        info["open_until"] = 0.0

    def _get_reranker_model(self):
        model_name = self._settings.l2_gray_rerank_model
        if self._reranker_model is None or self._reranker_model_name != model_name:
            from sentence_transformers import CrossEncoder

            self._reranker_model = CrossEncoder(model_name)
            self._reranker_model_name = model_name
        return self._reranker_model

    def _rerank_candidates(
        self,
        *,
        query: str,
        candidates: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        meta: Dict[str, Any] = {
            "enabled": self._settings.l2_gray_enable_rerank,
            "provider": self._settings.l2_gray_rerank_provider,
            "model": self._settings.l2_gray_rerank_model,
            "applied": False,
        }
        if not self._settings.l2_gray_enable_rerank or not candidates:
            return candidates, meta
        if self._settings.l2_gray_rerank_provider != "local":
            meta["error"] = f"unsupported_provider={self._settings.l2_gray_rerank_provider}"
            return candidates, meta
        if self._is_breaker_open("rerank"):
            meta["breaker_open"] = True
            return candidates, meta

        rerank_n = max(1, min(self._settings.l2_gray_rerank_candidates, len(candidates)))
        head = [dict(c) for c in candidates[:rerank_n]]
        tail = [dict(c) for c in candidates[rerank_n:]]
        try:
            model = self._get_reranker_model()
            pairs = [(query, (c.get("query_norm") or c.get("answer") or "")) for c in head]
            scores = model.predict(pairs)
            for c, score in zip(head, scores):
                c["rerank_score"] = float(score)
            head.sort(key=lambda x: x.get("rerank_score", -1e9), reverse=True)
            self._record_success("rerank")
            reranked = head + tail
            meta["applied"] = True
            meta["top_scores"] = [
                {
                    "cache_id": c.get("cache_id"),
                    "rerank_score": round(float(c.get("rerank_score", 0.0)), 6),
                }
                for c in head[: min(len(head), 5)]
            ]
            return reranked, meta
        except Exception as exc:
            self._record_failure("rerank")
            meta["error"] = str(exc)
            return candidates, meta

    def lookup(self, *, query: str, tenant_id: str, actor_type: str, domain: str) -> Dict[str, Any]:
        try:
            self._ensure_ready()
        except Exception as exc:
            return {
                "served_by_cache": False,
                "decision": "MISS",
                "level": "CACHE_UNAVAILABLE",
                "answer": "",
                "citations": [],
                "debug": {"error": f"cache_init_failed: {exc}"},
            }
        normalized = _normalize_query(query)
        qhash = _qhash(normalized)
        filters = self._filters(tenant_id=tenant_id, actor_type=actor_type, domain=domain)
        l1_key = (
            f"l1:{filters['tenant_id']}:{filters['actor_scope']}:{filters['region']}:"
            f"{filters['prompt_version']}:{filters['domain']}:{filters['lang']}:{qhash}"
        )
        debug: Dict[str, Any] = {
            "normalized_query": normalized,
            "qhash": qhash,
            "l1_key": l1_key,
            "filters": filters,
            "thresholds": {
                "low": self._settings.semantic_threshold_low,
                "high": self._settings.semantic_threshold_high,
                "second": self._settings.l2_gray_second_threshold,
            },
        }

        try:
            if self._settings.enable_l1_cache and not self._is_breaker_open("redis"):
                l1 = self._l1.get(l1_key)
                if l1:
                    self._record_success("redis")
                    return {
                        "served_by_cache": True,
                        "decision": "HIT_STRONG",
                        "level": "L1",
                        "answer": l1.get("answer", ""),
                        "citations": l1.get("citations", []),
                        "debug": {**debug, "l1_hit": True},
                    }
        except Exception as exc:
            self._record_failure("redis")
            debug["l1_error"] = str(exc)

        vector, model_name = self._embed.embed_query(normalized)
        vector_column = self._embed.active_vector_column()
        vector_field = "embedding_local" if vector_column == "embedding_local" else "embedding_cloud"

        try:
            hot_hits = []
            if self._settings.enable_l2_sem_cache and not self._is_breaker_open("redis"):
                hot_hits = self._l2_hot.search_topk(
                    filters=filters,
                    vector=vector,
                    vector_field=vector_field,
                    top_k=self._settings.semantic_top_k,
                )
                self._record_success("redis")
        except Exception as exc:
            self._record_failure("redis")
            hot_hits = []
            debug["l2_hot_error"] = str(exc)

        top = hot_hits[0] if hot_hits else None
        top_score = float(top["score"]) if top else 0.0
        if top and top_score >= self._settings.semantic_threshold_high:
            return {
                "served_by_cache": True,
                "decision": "HIT_STRONG",
                "level": "L2_HOT",
                "answer": top.get("answer", ""),
                "citations": top.get("citations", []),
                "debug": {
                    **debug,
                    "l1_hit": False,
                    "vector_column": vector_column,
                    "embedding_model": model_name,
                    "l2_hot_top1_score": top_score,
                    "l2_hot_candidates": hot_hits,
                },
            }

        if top and top_score >= self._settings.semantic_threshold_low:
            gray_top_k = max(self._settings.l2_gray_pg_topk, self._settings.l2_gray_rerank_candidates)
            try:
                persist_hits = []
                if not self._is_breaker_open("pg"):
                    persist_hits = self._l2_persist.search_topk(
                        filters=filters,
                        vector=vector,
                        vector_column=vector_column,
                        top_k=gray_top_k,
                    )
                    self._record_success("pg")
            except Exception as exc:
                self._record_failure("pg")
                persist_hits = []
                debug["l2_persist_error"] = str(exc)
            reranked_hits, rerank_meta = self._rerank_candidates(query=normalized, candidates=persist_hits)
            second_top_k = max(1, self._settings.l2_gray_rerank_topk)
            reranked_hits = reranked_hits[:second_top_k]
            persist_top = reranked_hits[0] if reranked_hits else None
            persist_score = float(persist_top.get("score", 0.0)) if persist_top else 0.0
            rerank_score = float(persist_top.get("rerank_score", 0.0)) if persist_top else 0.0
            score_for_second = rerank_score if rerank_meta.get("applied") else persist_score
            if persist_top and score_for_second >= self._settings.l2_gray_second_threshold:
                return {
                    "served_by_cache": True,
                    "decision": "HIT_GRAY_CONFIRMED",
                    "level": "L2_PERSIST_GRAY",
                    "answer": persist_top.get("answer", ""),
                    "citations": persist_top.get("citations", []),
                    "debug": {
                        **debug,
                        "l1_hit": False,
                        "vector_column": vector_column,
                        "embedding_model": model_name,
                        "l2_hot_top1_score": top_score,
                        "l2_hot_candidates": hot_hits,
                        "l2_persist_top1_score": persist_score,
                        "gray_second_score": score_for_second,
                        "l2_persist_candidates": persist_hits,
                        "l2_persist_candidates_reranked": reranked_hits,
                        "rerank": rerank_meta,
                        "gray_fallback": True,
                    },
                }
            return {
                "served_by_cache": False,
                "decision": "MISS_TO_RAG",
                "level": "L2_GRAY",
                "answer": "",
                "citations": [],
                "debug": {
                    **debug,
                    "l1_hit": False,
                    "vector_column": vector_column,
                    "embedding_model": model_name,
                    "l2_hot_top1_score": top_score,
                    "l2_hot_candidates": hot_hits,
                    "l2_persist_candidates": persist_hits,
                    "l2_persist_candidates_reranked": reranked_hits,
                    "gray_second_score": score_for_second,
                    "rerank": rerank_meta,
                    "gray_fallback": True,
                },
            }

        return {
            "served_by_cache": False,
            "decision": "MISS",
            "level": "L2_MISS",
            "answer": "",
            "citations": [],
            "debug": {
                **debug,
                "l1_hit": False,
                "vector_column": vector_column,
                "embedding_model": model_name,
                "l2_hot_top1_score": top_score,
                "l2_hot_candidates": hot_hits,
            },
        }

    def _admission_check(self, *, domain: str, answer: str, citations: List[str]) -> Tuple[bool, Dict[str, Any]]:
        blocked = {
            x.strip()
            for x in self._settings.cache_admission_blocked_domains.split(",")
            if x.strip()
        }
        details = {
            "enabled": self._settings.cache_admission_enabled,
            "domain": domain,
            "answer_len": len(answer or ""),
            "citations_count": len(citations or []),
            "blocked_domains": sorted(blocked),
        }
        if not self._settings.cache_admission_enabled:
            return True, details
        if domain in blocked:
            details["reason"] = "blocked_domain"
            return False, details
        if len(answer or "") < self._settings.cache_admission_min_answer_len:
            details["reason"] = "answer_too_short"
            return False, details
        if len(citations or []) < self._settings.cache_admission_min_citations:
            details["reason"] = "citations_not_enough"
            return False, details
        details["reason"] = "pass"
        return True, details

    def writeback(
        self,
        *,
        query: str,
        tenant_id: str,
        actor_type: str,
        domain: str,
        answer: str,
        citations: List[str],
        source_trace_id: str,
        source_event_id: str,
    ) -> Dict[str, Any]:
        self._ensure_ready()
        admitted, admission_debug = self._admission_check(domain=domain, answer=answer, citations=citations)
        if not admitted:
            return {
                "admitted": False,
                "admission": admission_debug,
                "l1_written": False,
                "l2_hot_written": False,
                "l2_persist_written": False,
            }
        normalized = _normalize_query(query)
        qhash = _qhash(normalized)
        filters = self._filters(tenant_id=tenant_id, actor_type=actor_type, domain=domain)
        cache_id = f"ce_{uuid.uuid4().hex[:16]}"
        row = {
            "cache_id": cache_id,
            **filters,
            "query_text": query,
            "query_norm": normalized,
            "query_hash": qhash,
            "answer_text": answer,
            "citations": citations,
            "source_trace_id": source_trace_id,
            "source_event_id": source_event_id,
            "ttl_seconds": self._settings.l2_ttl_seconds,
        }
        source_doc_ids, source_chunk_ids = self._parse_citation_refs(citations)
        row["source_doc_ids"] = source_doc_ids
        row["source_chunk_ids"] = source_chunk_ids
        l1_key = (
            f"l1:{filters['tenant_id']}:{filters['actor_scope']}:{filters['region']}:"
            f"{filters['prompt_version']}:{filters['domain']}:{filters['lang']}:{qhash}"
        )
        if self._settings.enable_l1_cache and not self._is_breaker_open("redis"):
            try:
                self._l1.set(l1_key, {"answer": answer, "citations": citations})
                self._record_success("redis")
            except Exception:
                self._record_failure("redis")

        vector, model_name = self._embed.embed_query(normalized)
        vector_column = self._embed.active_vector_column()
        vector_field = "embedding_local" if vector_column == "embedding_local" else "embedding_cloud"
        l2_redis_key = f"l2:entry:{cache_id}"
        if self._settings.enable_l2_sem_cache and not self._is_breaker_open("redis"):
            try:
                self._l2_hot.upsert(
                    row=row,
                    vector=vector,
                    vector_field=vector_field,
                    ttl_seconds=self._settings.l2_ttl_seconds,
                )
                self._record_success("redis")
            except Exception:
                self._record_failure("redis")
        if not self._is_breaker_open("pg"):
            try:
                self._l2_persist.upsert(
                    row=row,
                    vector=vector,
                    vector_column=vector_column,
                    model_name=model_name,
                )
                self._record_success("pg")
            except Exception:
                self._record_failure("pg")

        # Reverse indexes for event-driven invalidation.
        try:
            if not self._is_breaker_open("redis"):
                rev_keys: List[str] = []
                for doc_id in source_doc_ids:
                    rev_keys.append(f"rev:doc:{doc_id}")
                for chunk_id in source_chunk_ids:
                    rev_keys.append(f"rev:chunk:{chunk_id}")
                rev_keys.append(f"rev:kb:{filters['kb_version']}")
                for rk in rev_keys:
                    self._redis.sadd(rk, l1_key, l2_redis_key)
                    self._redis.expire(rk, self._settings.l2_ttl_seconds)
        except Exception:
            self._record_failure("redis")
        return {
            "admitted": True,
            "admission": admission_debug,
            "l1_written": self._settings.enable_l1_cache,
            "l2_hot_written": self._settings.enable_l2_sem_cache,
            "l2_persist_written": not self._is_breaker_open("pg"),
            "vector_column": vector_column,
            "embedding_model": model_name,
            "cache_id": cache_id,
            "source_doc_ids": source_doc_ids,
            "source_chunk_ids": source_chunk_ids,
        }

    def publish_invalidation_event(
        self,
        *,
        event_type: str,
        source_doc_ids: Optional[List[str]] = None,
        source_chunk_ids: Optional[List[str]] = None,
        kb_version: str = "",
    ) -> Dict[str, Any]:
        event_id = f"ev_{uuid.uuid4().hex[:12]}"
        source_doc_ids = [x for x in (source_doc_ids or []) if x]
        source_chunk_ids = [x for x in (source_chunk_ids or []) if x]
        deleted_keys: List[str] = []
        pg_deactivated = 0
        errors: List[str] = []

        try:
            if event_type in {"DOC_UPDATED", "DOC_EXPIRED"} and not self._is_breaker_open("redis"):
                rev_refs = [f"rev:doc:{d}" for d in source_doc_ids] + [f"rev:chunk:{c}" for c in source_chunk_ids]
                for rk in rev_refs:
                    members = self._redis.smembers(rk)
                    for m in members:
                        key = m.decode("utf-8")
                        deleted_keys.append(key)
                    if members:
                        self._redis.delete(*members)
                    self._redis.delete(rk)
                self._record_success("redis")
            elif event_type == "KB_VERSION_BUMPED" and kb_version and not self._is_breaker_open("redis"):
                rk = f"rev:kb:{kb_version}"
                members = self._redis.smembers(rk)
                for m in members:
                    deleted_keys.append(m.decode("utf-8"))
                if members:
                    self._redis.delete(*members)
                self._redis.delete(rk)
                self._record_success("redis")
        except Exception as exc:
            self._record_failure("redis")
            errors.append(f"redis_invalidation_error={exc}")

        try:
            if not self._is_breaker_open("pg"):
                if event_type in {"DOC_UPDATED", "DOC_EXPIRED"}:
                    pg_deactivated = self._l2_persist.deactivate_by_source_refs(
                        source_doc_ids=source_doc_ids,
                        source_chunk_ids=source_chunk_ids,
                        source_event_id=event_id,
                    )
                elif event_type == "KB_VERSION_BUMPED" and kb_version:
                    pg_deactivated = self._l2_persist.deactivate_by_kb_version(
                        kb_version=kb_version,
                        source_event_id=event_id,
                    )
                self._record_success("pg")
        except Exception as exc:
            self._record_failure("pg")
            errors.append(f"pg_invalidation_error={exc}")

        event = {
            "event_id": event_id,
            "event_type": event_type,
            "source_doc_ids": source_doc_ids,
            "source_chunk_ids": source_chunk_ids,
            "kb_version": kb_version,
            "deleted_redis_keys": len(set(deleted_keys)),
            "deactivated_pg_rows": pg_deactivated,
            "errors": errors,
            "timestamp_ms": int(time.time() * 1000),
        }
        self._events.append(event)
        self._events = self._events[-100:]
        return event

    def list_recent_events(self) -> List[Dict[str, Any]]:
        return list(reversed(self._events))
