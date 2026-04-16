import hashlib
import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional

import psycopg

from app.cache.embedding_runtime import EmbeddingRuntime
from app.models.litellm_client import summarize_text_with_litellm


def _vector_literal(values: List[float]) -> str:
    return "[" + ",".join(f"{v:.8f}" for v in values) + "]"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class MemoryStore:
    def __init__(
        self,
        table_name: str = "memory_items",
        events_table: str = "memory_events",
        session_table: str = "session_memories",
    ) -> None:
        self._table_name = table_name
        self._events_table = events_table
        self._session_table = session_table
        self._embed = EmbeddingRuntime()
        self._ready = False

    def _conn(self):
        return psycopg.connect(
            host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
            port=int(os.getenv("POSTGRES_PORT", "5433")),
            dbname=os.getenv("POSTGRES_DB", "ai_cs"),
            user=os.getenv("POSTGRES_USER", "postgres"),
            password=os.getenv("POSTGRES_PASSWORD", "postgres"),
            autocommit=True,
        )

    def ensure_schema(self) -> None:
        if self._ready:
            return
        cloud_dim = int(os.getenv("CLOUD_EMBEDDING_DIM", "1024"))
        local_dim = int(os.getenv("LOCAL_EMBEDDING_DIM", "384"))
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._table_name} (
                    memory_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    memory_type TEXT NOT NULL, -- short|long|l3
                    source_node TEXT NOT NULL,
                    source_event_id TEXT,
                    idempotency_key TEXT NOT NULL,
                    content TEXT NOT NULL,
                    summary TEXT,
                    content_hash TEXT NOT NULL,
                    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                    importance_score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                    admission_passed BOOLEAN NOT NULL DEFAULT TRUE,
                    admission_reason TEXT,
                    citations_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                    metadata_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    embedding_cloud vector({cloud_dim}),
                    embedding_local vector({local_dim}),
                    embedding_model TEXT,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    expires_at TIMESTAMPTZ,
                    deleted_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                """
            )
            cur.execute(
                f"""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_{self._table_name}_idempotency
                ON {self._table_name} (idempotency_key);
                """
            )
            cur.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_{self._table_name}_scope
                ON {self._table_name}
                (tenant_id, user_id, thread_id, memory_type, is_active, created_at DESC);
                """
            )
            cur.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_{self._table_name}_content_hash
                ON {self._table_name} (content_hash);
                """
            )
            cur.execute(
                f"""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_indexes
                        WHERE indexname = 'idx_{self._table_name}_emb_cloud_hnsw'
                    ) THEN
                        EXECUTE 'CREATE INDEX idx_{self._table_name}_emb_cloud_hnsw '
                             || 'ON {self._table_name} USING hnsw (embedding_cloud vector_cosine_ops)';
                    END IF;
                EXCEPTION WHEN others THEN
                    NULL;
                END $$;
                """
            )
            cur.execute(
                f"""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_indexes
                        WHERE indexname = 'idx_{self._table_name}_emb_local_hnsw'
                    ) THEN
                        EXECUTE 'CREATE INDEX idx_{self._table_name}_emb_local_hnsw '
                             || 'ON {self._table_name} USING hnsw (embedding_local vector_cosine_ops)';
                    END IF;
                EXCEPTION WHEN others THEN
                    NULL;
                END $$;
                """
            )
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._events_table} (
                    event_id TEXT PRIMARY KEY,
                    memory_id TEXT,
                    trace_id TEXT,
                    node_name TEXT,
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    error TEXT,
                    latency_ms DOUBLE PRECISION,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                """
            )
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._session_table} (
                    session_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    recent_turns_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                    rolling_summary TEXT NOT NULL DEFAULT '',
                    turn_count INTEGER NOT NULL DEFAULT 0,
                    compressed_count INTEGER NOT NULL DEFAULT 0,
                    chars_estimate INTEGER NOT NULL DEFAULT 0,
                    metadata_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    deleted_at TIMESTAMPTZ,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                """
            )
            cur.execute(
                f"""
                ALTER TABLE {self._session_table}
                ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
                """
            )
            cur.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_{self._session_table}_scope
                ON {self._session_table} (tenant_id, user_id, thread_id, updated_at DESC);
                """
            )
        self._ready = True

    def _dedup_threshold(self, memory_type: str) -> float:
        mt = (memory_type or "").strip().lower()
        if mt == "l3":
            return float(os.getenv("MEMORY_DEDUP_SIM_THRESHOLD_L3", "0.95"))
        return float(os.getenv("MEMORY_DEDUP_SIM_THRESHOLD_LONG", "0.92"))

    def _find_duplicate_memory(
        self,
        *,
        tenant_id: str,
        user_id: str,
        thread_id: str,
        memory_type: str,
        content_hash: str,
        vector_column: str,
        vec_literal: str,
    ) -> Optional[Dict[str, Any]]:
        if os.getenv("MEMORY_DEDUP_ENABLED", "true").strip().lower() not in {"1", "true", "yes", "on"}:
            return None
        threshold = self._dedup_threshold(memory_type)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT memory_id, content_hash,
                       1 - ({vector_column} <=> %s::vector) AS sim
                  FROM {self._table_name}
                 WHERE is_active = TRUE
                   AND deleted_at IS NULL
                   AND expires_at > now()
                   AND tenant_id = %s
                   AND user_id = %s
                   AND thread_id = %s
                   AND memory_type = %s
                   AND {vector_column} IS NOT NULL
                 ORDER BY {vector_column} <=> %s::vector
                 LIMIT 5;
                """,
                (vec_literal, tenant_id, user_id, thread_id, memory_type, vec_literal),
            )
            rows = cur.fetchall()
        for row in rows:
            mid = str(row[0])
            h = str(row[1] or "")
            sim = float(row[2] or 0.0)
            if h == content_hash or sim >= threshold:
                return {"memory_id": mid, "sim": sim, "hit_type": "hash" if h == content_hash else "semantic"}
        return None

    def _merge_duplicate_memory(
        self,
        *,
        memory_id: str,
        citations: List[str],
        metadata: Dict[str, Any],
        ttl_seconds: int,
    ) -> None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {self._table_name}
                   SET citations_json = (
                        SELECT jsonb_agg(DISTINCT x) FROM (
                            SELECT jsonb_array_elements_text(citations_json) AS x
                            UNION ALL
                            SELECT jsonb_array_elements_text(%s::jsonb) AS x
                        ) t
                   ),
                       metadata_json = metadata_json || %s::jsonb,
                       expires_at = GREATEST(expires_at, now() + (%s || ' seconds')::interval),
                       updated_at = now()
                 WHERE memory_id = %s;
                """,
                (
                    json.dumps(citations or [], ensure_ascii=False),
                    json.dumps(metadata or {}, ensure_ascii=False),
                    int(ttl_seconds),
                    memory_id,
                ),
            )

    def _compress_turns(
        self,
        *,
        recent_turns: List[Dict[str, Any]],
        rolling_summary: str,
        max_recent_turns: int,
        max_summary_chars: int = 800,
        prefer_llm_summary: bool = False,
    ) -> Dict[str, Any]:
        if max_recent_turns < 1:
            max_recent_turns = 1
        compressed_count = 0
        compressed_snippets: List[str] = []
        while len(recent_turns) > max_recent_turns:
            oldest = recent_turns.pop(0)
            q = (oldest.get("q") or "").strip()
            a = (oldest.get("a") or "").strip()
            snippet = f"[Q]{q}\n[A]{a}\n"
            compressed_snippets.append(snippet)
            rolling_summary = f"{rolling_summary}\n{snippet}".strip()
            compressed_count += 1
            if len(rolling_summary) > max_summary_chars:
                rolling_summary = rolling_summary[-max_summary_chars:]
        llm_summary_used = False
        llm_summary_error = ""
        if prefer_llm_summary and compressed_count > 0:
            try:
                merged = f"已有摘要:\n{rolling_summary}\n\n新增历史片段:\n{''.join(compressed_snippets)}"
                resp = summarize_text_with_litellm(
                    text=merged,
                    max_chars=max_summary_chars,
                    timeout_seconds=float(os.getenv("MEMORY_SUMMARY_LLM_TIMEOUT", "8")),
                    max_retries=int(os.getenv("MEMORY_SUMMARY_LLM_MAX_RETRIES", "1")),
                )
                rolling_summary = (resp.get("summary") or rolling_summary).strip()[:max_summary_chars]
                llm_summary_used = True
            except Exception as exc:
                llm_summary_error = str(exc)
        return {
            "recent_turns": recent_turns,
            "rolling_summary": rolling_summary,
            "compressed_count": compressed_count,
            "llm_summary_used": llm_summary_used,
            "llm_summary_error": llm_summary_error,
        }

    def upsert_session_turn(
        self,
        *,
        session_id: str,
        tenant_id: str,
        user_id: str,
        thread_id: str,
        query: str,
        answer: str,
        trace_id: str,
        node_name: str,
        max_recent_turns: int,
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        self.ensure_schema()
        start = time.perf_counter()
        turn = {"q": query.strip(), "a": answer.strip(), "ts_ms": int(time.time() * 1000)}
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT recent_turns_json, rolling_summary, turn_count
                  FROM {self._session_table}
                 WHERE session_id = %s
                   AND deleted_at IS NULL;
                """,
                (session_id,),
            )
            row = cur.fetchone()
            if row:
                recent_turns = row[0] if isinstance(row[0], list) else []
                rolling_summary = str(row[1] or "")
                turn_count = int(row[2] or 0)
            else:
                recent_turns = []
                rolling_summary = ""
                turn_count = 0
            recent_turns.append(turn)
            compressed = self._compress_turns(
                recent_turns=recent_turns,
                rolling_summary=rolling_summary,
                max_recent_turns=max_recent_turns,
                max_summary_chars=int(os.getenv("MEMORY_SUMMARY_MAX_CHARS", "800")),
                prefer_llm_summary=os.getenv("MEMORY_SUMMARIZER_ENABLED", "false").strip().lower()
                in {"1", "true", "yes", "on"},
            )
            recent_turns = compressed["recent_turns"]
            rolling_summary = compressed["rolling_summary"]
            compressed_count = int(compressed["compressed_count"])
            llm_summary_used = bool(compressed.get("llm_summary_used", False))
            llm_summary_error = str(compressed.get("llm_summary_error", "") or "")
            turn_count += 1
            chars_estimate = len(rolling_summary) + sum(
                len((t.get("q") or "")) + len((t.get("a") or "")) for t in recent_turns
            )
            cur.execute(
                f"""
                INSERT INTO {self._session_table}
                (session_id, tenant_id, user_id, thread_id, recent_turns_json, rolling_summary,
                 turn_count, compressed_count, chars_estimate, metadata_json, updated_at)
                VALUES
                (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s::jsonb, now())
                ON CONFLICT (session_id) DO UPDATE SET
                    recent_turns_json = EXCLUDED.recent_turns_json,
                    rolling_summary = EXCLUDED.rolling_summary,
                    turn_count = EXCLUDED.turn_count,
                    compressed_count = {self._session_table}.compressed_count + EXCLUDED.compressed_count,
                    chars_estimate = EXCLUDED.chars_estimate,
                    metadata_json = EXCLUDED.metadata_json,
                    deleted_at = NULL,
                    updated_at = now();
                """,
                (
                    session_id,
                    tenant_id,
                    user_id,
                    thread_id,
                    json.dumps(recent_turns, ensure_ascii=False),
                    rolling_summary,
                    turn_count,
                    compressed_count,
                    chars_estimate,
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
        elapsed_ms = (time.perf_counter() - start) * 1000
        self._event(
            event_type="session_memory_upsert",
            status="ok",
            payload={
                "session_id": session_id,
                "turn_count": turn_count,
                "compressed_count": compressed_count,
                "chars_estimate": chars_estimate,
                "llm_summary_used": llm_summary_used,
                "llm_summary_error": llm_summary_error,
            },
            trace_id=trace_id,
            node_name=node_name,
            latency_ms=elapsed_ms,
        )
        return {
            "session_id": session_id,
            "turn_count": turn_count,
            "compressed_count": compressed_count,
            "chars_estimate": chars_estimate,
            "recent_turns_count": len(recent_turns),
            "llm_summary_used": llm_summary_used,
            "llm_summary_error": llm_summary_error,
        }

    def read_session_memory(
        self,
        *,
        session_id: str,
        trace_id: str,
        node_name: str,
    ) -> Dict[str, Any]:
        self.ensure_schema()
        start = time.perf_counter()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT recent_turns_json, rolling_summary, turn_count, compressed_count, chars_estimate
                  FROM {self._session_table}
                 WHERE session_id = %s
                   AND deleted_at IS NULL;
                """,
                (session_id,),
            )
            row = cur.fetchone()
        elapsed_ms = (time.perf_counter() - start) * 1000
        if not row:
            self._event(
                event_type="session_memory_read",
                status="miss",
                payload={"session_id": session_id},
                trace_id=trace_id,
                node_name=node_name,
                latency_ms=elapsed_ms,
            )
            return {"found": False, "session_id": session_id}
        recent_turns = row[0] if isinstance(row[0], list) else []
        rolling_summary = str(row[1] or "")
        out = {
            "found": True,
            "session_id": session_id,
            "recent_turns": recent_turns,
            "rolling_summary": rolling_summary,
            "turn_count": int(row[2] or 0),
            "compressed_count": int(row[3] or 0),
            "chars_estimate": int(row[4] or 0),
        }
        self._event(
            event_type="session_memory_read",
            status="ok",
            payload={
                "session_id": session_id,
                "turn_count": out["turn_count"],
                "recent_turns_count": len(recent_turns),
            },
            trace_id=trace_id,
            node_name=node_name,
            latency_ms=elapsed_ms,
        )
        return out

    def _event(self, *, event_type: str, status: str, payload: Dict[str, Any], trace_id: str, node_name: str, memory_id: str = "", error: str = "", latency_ms: float = 0.0) -> None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {self._events_table}
                (event_id, memory_id, trace_id, node_name, event_type, status, payload_json, error, latency_ms)
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s);
                """,
                (
                    f"mev_{uuid.uuid4().hex[:16]}",
                    memory_id,
                    trace_id,
                    node_name,
                    event_type,
                    status,
                    json.dumps(payload, ensure_ascii=False),
                    error,
                    latency_ms,
                ),
            )

    def write_memory(
        self,
        *,
        tenant_id: str,
        user_id: str,
        thread_id: str,
        memory_type: str,
        source_node: str,
        source_event_id: str,
        trace_id: str,
        content: str,
        summary: str,
        citations: List[str],
        metadata: Dict[str, Any],
        confidence: float,
        importance_score: float,
        admission_passed: bool,
        admission_reason: str,
        idempotency_key: str,
        ttl_seconds: int,
    ) -> Dict[str, Any]:
        self.ensure_schema()
        start = time.perf_counter()
        content = (content or "").strip()
        if not content:
            return {"written": False, "reason": "empty_content"}
        vector, model_name = self._embed.embed_query(content)
        vector_column = self._embed.active_vector_column()
        content_hash = _sha256(content)
        emb_cloud = _vector_literal(vector) if vector_column == "embedding_cloud" else None
        emb_local = _vector_literal(vector) if vector_column == "embedding_local" else None
        vec_literal = emb_cloud if vector_column == "embedding_cloud" else emb_local
        memory_id = f"mem_{uuid.uuid4().hex[:16]}"
        try:
            duplicate = self._find_duplicate_memory(
                tenant_id=tenant_id,
                user_id=user_id,
                thread_id=thread_id,
                memory_type=memory_type,
                content_hash=content_hash,
                vector_column=vector_column,
                vec_literal=str(vec_literal or "[]"),
            )
            if duplicate:
                self._merge_duplicate_memory(
                    memory_id=duplicate["memory_id"],
                    citations=citations,
                    metadata=metadata,
                    ttl_seconds=ttl_seconds,
                )
                elapsed_ms = (time.perf_counter() - start) * 1000
                self._event(
                    event_type="memory_dedupe_merge",
                    status="ok",
                    payload={
                        "memory_type": memory_type,
                        "target_memory_id": duplicate["memory_id"],
                        "sim": round(float(duplicate["sim"]), 6),
                        "hit_type": duplicate["hit_type"],
                    },
                    trace_id=trace_id,
                    node_name=source_node,
                    memory_id=duplicate["memory_id"],
                    latency_ms=elapsed_ms,
                )
                return {
                    "written": False,
                    "dedupe_hit": True,
                    "merged_into": duplicate["memory_id"],
                    "dedupe_sim": round(float(duplicate["sim"]), 6),
                    "dedupe_hit_type": duplicate["hit_type"],
                    "memory_type": memory_type,
                    "vector_column": vector_column,
                    "embedding_model": model_name,
                }

            with self._conn() as conn, conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {self._table_name} (
                        memory_id, tenant_id, user_id, thread_id, memory_type,
                        source_node, source_event_id, idempotency_key,
                        content, summary, content_hash, confidence, importance_score,
                        admission_passed, admission_reason, citations_json, metadata_json,
                        embedding_cloud, embedding_local, embedding_model,
                        is_active, expires_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s::jsonb, %s::jsonb,
                        %s::vector, %s::vector, %s,
                        TRUE, now() + (%s || ' seconds')::interval, now()
                    )
                    ON CONFLICT (idempotency_key) DO NOTHING;
                    """,
                    (
                        memory_id,
                        tenant_id,
                        user_id,
                        thread_id,
                        memory_type,
                        source_node,
                        source_event_id,
                        idempotency_key,
                        content,
                        summary,
                        content_hash,
                        float(confidence),
                        float(importance_score),
                        bool(admission_passed),
                        admission_reason,
                        json.dumps(citations or [], ensure_ascii=False),
                        json.dumps(metadata or {}, ensure_ascii=False),
                        emb_cloud,
                        emb_local,
                        model_name,
                        int(ttl_seconds),
                    ),
                )
                written = int(cur.rowcount or 0) > 0
            elapsed_ms = (time.perf_counter() - start) * 1000
            self._event(
                event_type="memory_write",
                status="ok" if written else "duplicate",
                payload={"memory_type": memory_type, "idempotency_key": idempotency_key},
                trace_id=trace_id,
                node_name=source_node,
                memory_id=memory_id if written else "",
                latency_ms=elapsed_ms,
            )
            return {
                "written": written,
                "dedupe_hit": False,
                "memory_id": memory_id if written else "",
                "memory_type": memory_type,
                "vector_column": vector_column,
                "embedding_model": model_name,
            }
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            self._event(
                event_type="memory_write",
                status="error",
                payload={"memory_type": memory_type, "idempotency_key": idempotency_key},
                trace_id=trace_id,
                node_name=source_node,
                error=str(exc),
                latency_ms=elapsed_ms,
            )
            raise

    def query_memory(
        self,
        *,
        tenant_id: str,
        user_id: str,
        thread_id: str,
        query: str,
        memory_types: List[str],
        top_k: int,
        trace_id: str,
        node_name: str,
    ) -> List[Dict[str, Any]]:
        self.ensure_schema()
        start = time.perf_counter()
        vector, _ = self._embed.embed_query(query)
        vector_column = self._embed.active_vector_column()
        vec = _vector_literal(vector)
        rows: List[Any] = []
        try:
            with self._conn() as conn, conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT memory_id, memory_type, content, summary, citations_json, metadata_json,
                           confidence, importance_score,
                           EXTRACT(EPOCH FROM (now() - created_at)) AS age_seconds,
                           1 - ({vector_column} <=> %s::vector) AS score
                      FROM {self._table_name}
                     WHERE is_active = TRUE
                       AND deleted_at IS NULL
                       AND expires_at > now()
                       AND tenant_id = %s
                       AND user_id = %s
                       AND thread_id = %s
                       AND memory_type = ANY(%s)
                       AND {vector_column} IS NOT NULL
                     ORDER BY {vector_column} <=> %s::vector
                     LIMIT %s;
                    """,
                    (vec, tenant_id, user_id, thread_id, memory_types, vec, top_k),
                )
                rows = cur.fetchall()
            out: List[Dict[str, Any]] = []
            for r in rows:
                out.append(
                    {
                        "memory_id": r[0],
                        "memory_type": r[1],
                        "content": r[2],
                        "summary": r[3] or "",
                        "citations": r[4] if isinstance(r[4], list) else [],
                        "metadata": r[5] if isinstance(r[5], dict) else {},
                        "confidence": float(r[6] or 0.0),
                        "importance_score": float(r[7] or 0.0),
                        "age_seconds": float(r[8] or 0.0),
                        "score": float(r[9] or 0.0),
                    }
                )
            elapsed_ms = (time.perf_counter() - start) * 1000
            self._event(
                event_type="memory_read",
                status="ok",
                payload={"hit_count": len(out), "top_k": top_k, "memory_types": memory_types},
                trace_id=trace_id,
                node_name=node_name,
                latency_ms=elapsed_ms,
            )
            return out
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            self._event(
                event_type="memory_read",
                status="error",
                payload={"top_k": top_k, "memory_types": memory_types},
                trace_id=trace_id,
                node_name=node_name,
                error=str(exc),
                latency_ms=elapsed_ms,
            )
            raise

    def cleanup_expired(self, *, trace_id: str, node_name: str = "memory_cleanup") -> Dict[str, int]:
        self.ensure_schema()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {self._table_name}
                   SET is_active = FALSE,
                       updated_at = now()
                 WHERE is_active = TRUE
                   AND expires_at <= now();
                """
            )
            memory_deactivated = int(cur.rowcount or 0)
            cur.execute(
                f"""
                UPDATE {self._session_table}
                   SET deleted_at = now(),
                       updated_at = now()
                 WHERE deleted_at IS NULL
                   AND updated_at < now() - interval '30 days';
                """
            )
            session_deleted = int(cur.rowcount or 0)
        self._event(
            event_type="memory_cleanup",
            status="ok",
            payload={"memory_deactivated": memory_deactivated, "session_deleted": session_deleted},
            trace_id=trace_id,
            node_name=node_name,
        )
        return {"memory_deactivated": memory_deactivated, "session_deleted": session_deleted}

    def soft_delete_memory(
        self,
        *,
        memory_id: str,
        trace_id: str,
        node_name: str = "memory_delete",
    ) -> Dict[str, int]:
        self.ensure_schema()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {self._table_name}
                   SET is_active = FALSE,
                       deleted_at = now(),
                       updated_at = now()
                 WHERE memory_id = %s;
                """,
                (memory_id,),
            )
            affected = int(cur.rowcount or 0)
        self._event(
            event_type="memory_soft_delete",
            status="ok",
            payload={"memory_id": memory_id, "affected": affected},
            trace_id=trace_id,
            node_name=node_name,
            memory_id=memory_id,
        )
        return {"affected": affected}

    def soft_delete_by_session(
        self,
        *,
        session_id: str,
        tenant_id: str,
        user_id: str,
        trace_id: str,
        node_name: str = "memory_delete_session",
    ) -> Dict[str, int]:
        self.ensure_schema()
        l3_scope = f"l3:{tenant_id}:{user_id}:{session_id}"
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {self._session_table}
                   SET deleted_at = now(),
                       updated_at = now()
                 WHERE session_id = %s
                   AND deleted_at IS NULL;
                """,
                (session_id,),
            )
            session_affected = int(cur.rowcount or 0)
            cur.execute(
                f"""
                UPDATE {self._table_name}
                   SET is_active = FALSE,
                       deleted_at = now(),
                       updated_at = now()
                 WHERE tenant_id = %s
                   AND user_id = %s
                   AND thread_id = %s
                   AND is_active = TRUE;
                """,
                (tenant_id, user_id, l3_scope),
            )
            memory_affected = int(cur.rowcount or 0)
        self._event(
            event_type="memory_soft_delete_session",
            status="ok",
            payload={
                "session_id": session_id,
                "session_affected": session_affected,
                "memory_affected": memory_affected,
            },
            trace_id=trace_id,
            node_name=node_name,
        )
        return {"session_affected": session_affected, "memory_affected": memory_affected}

    def memory_stats(self) -> Dict[str, Any]:
        self.ensure_schema()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT memory_type, COUNT(*)
                  FROM {self._table_name}
                 WHERE is_active = TRUE
                   AND deleted_at IS NULL
                   AND expires_at > now()
                 GROUP BY memory_type;
                """
            )
            rows = cur.fetchall()
            cur.execute(
                f"""
                SELECT COUNT(*)
                  FROM {self._session_table}
                 WHERE deleted_at IS NULL;
                """
            )
            session_count = int(cur.fetchone()[0] or 0)
        by_type = {str(r[0]): int(r[1]) for r in rows}
        total = int(sum(by_type.values()))
        return {
            "memory_total_active": total,
            "memory_by_type": by_type,
            "session_total_active": session_count,
        }
