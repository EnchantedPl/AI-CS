import json
import os
from typing import Any, Dict, List

import psycopg


def _vector_literal(values: List[float]) -> str:
    return "[" + ",".join(f"{v:.8f}" for v in values) + "]"


class L2PersistPgStore:
    def __init__(self, table_name: str = "semantic_cache_entries") -> None:
        self._table_name = table_name

    def _conn(self):
        return psycopg.connect(
            host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
            port=int(os.getenv("POSTGRES_PORT", "5433")),
            dbname=os.getenv("POSTGRES_DB", "ai_cs"),
            user=os.getenv("POSTGRES_USER", "postgres"),
            password=os.getenv("POSTGRES_PASSWORD", "postgres"),
            autocommit=True,
        )

    def ensure_schema(self, cloud_dim: int, local_dim: int) -> None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._table_name} (
                    cache_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    actor_scope TEXT NOT NULL,
                    lang TEXT NOT NULL,
                    region TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    kb_version TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    query_text TEXT NOT NULL,
                    query_norm TEXT NOT NULL,
                    query_hash TEXT NOT NULL,
                    answer_text TEXT NOT NULL,
                    citations_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                    source_doc_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                    source_chunk_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                    source_trace_id TEXT,
                    source_event_id TEXT,
                    cloud_embedding_model TEXT,
                    local_embedding_model TEXT,
                    embedding_cloud vector({cloud_dim}),
                    embedding_local vector({local_dim}),
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    expires_at TIMESTAMPTZ
                );
                """
            )
            cur.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_{self._table_name}_meta
                ON {self._table_name}
                (tenant_id, actor_scope, lang, region, prompt_version, kb_version, policy_version, domain, is_active);
                """
            )
            cur.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_{self._table_name}_query_hash
                ON {self._table_name} (query_hash);
                """
            )
            cur.execute(
                f"""
                ALTER TABLE {self._table_name}
                ADD COLUMN IF NOT EXISTS source_doc_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb;
                """
            )
            cur.execute(
                f"""
                ALTER TABLE {self._table_name}
                ADD COLUMN IF NOT EXISTS source_chunk_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb;
                """
            )
            # best effort vector indexes
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

    def upsert(self, row: Dict[str, Any], vector: List[float], vector_column: str, model_name: str) -> None:
        model_col = "local_embedding_model" if vector_column == "embedding_local" else "cloud_embedding_model"
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {self._table_name} (
                    cache_id, tenant_id, actor_scope, lang, region,
                    prompt_version, kb_version, policy_version,
                    domain, query_text, query_norm, query_hash,
                    answer_text, citations_json, source_trace_id, source_event_id,
                    source_doc_ids_json, source_chunk_ids_json,
                    {model_col}, {vector_column}, is_active, updated_at, expires_at
                ) VALUES (
                    %(cache_id)s, %(tenant_id)s, %(actor_scope)s, %(lang)s, %(region)s,
                    %(prompt_version)s, %(kb_version)s, %(policy_version)s,
                    %(domain)s, %(query_text)s, %(query_norm)s, %(query_hash)s,
                    %(answer_text)s, %(citations_json)s::jsonb, %(source_trace_id)s, %(source_event_id)s,
                    %(source_doc_ids_json)s::jsonb, %(source_chunk_ids_json)s::jsonb,
                    %(model_name)s, %(vector)s::vector, TRUE, now(), now() + (%(ttl_seconds)s || ' seconds')::interval
                )
                ON CONFLICT (cache_id)
                DO UPDATE SET
                    answer_text = EXCLUDED.answer_text,
                    citations_json = EXCLUDED.citations_json,
                    source_doc_ids_json = EXCLUDED.source_doc_ids_json,
                    source_chunk_ids_json = EXCLUDED.source_chunk_ids_json,
                    {model_col} = EXCLUDED.{model_col},
                    {vector_column} = EXCLUDED.{vector_column},
                    source_trace_id = EXCLUDED.source_trace_id,
                    source_event_id = EXCLUDED.source_event_id,
                    updated_at = now(),
                    expires_at = EXCLUDED.expires_at,
                    is_active = TRUE;
                """,
                {
                    **row,
                    "citations_json": json.dumps(row.get("citations", []), ensure_ascii=False),
                    "source_doc_ids_json": json.dumps(row.get("source_doc_ids", []), ensure_ascii=False),
                    "source_chunk_ids_json": json.dumps(row.get("source_chunk_ids", []), ensure_ascii=False),
                    "model_name": model_name,
                    "vector": _vector_literal(vector),
                },
            )

    def search_topk(self, filters: Dict[str, Any], vector: List[float], vector_column: str, top_k: int) -> List[Dict[str, Any]]:
        vec = _vector_literal(vector)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT cache_id, answer_text, citations_json, query_norm,
                       1 - ({vector_column} <=> %s::vector) AS score
                FROM {self._table_name}
                WHERE is_active = TRUE
                  AND expires_at > now()
                  AND tenant_id = %s
                  AND actor_scope = %s
                  AND lang = %s
                  AND region = %s
                  AND prompt_version = %s
                  AND kb_version = %s
                  AND policy_version = %s
                  AND domain = %s
                  AND {vector_column} IS NOT NULL
                ORDER BY {vector_column} <=> %s::vector
                LIMIT %s;
                """,
                (
                    vec,
                    filters["tenant_id"],
                    filters["actor_scope"],
                    filters["lang"],
                    filters["region"],
                    filters["prompt_version"],
                    filters["kb_version"],
                    filters["policy_version"],
                    filters["domain"],
                    vec,
                    top_k,
                ),
            )
            rows = cur.fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "cache_id": r[0],
                    "answer": r[1],
                    "citations": r[2] if isinstance(r[2], list) else [],
                    "query_norm": r[3],
                    "score": float(r[4]),
                    "source": "l2_persist_pg",
                }
            )
        return out

    def deactivate_by_source_refs(
        self,
        *,
        source_doc_ids: List[str],
        source_chunk_ids: List[str],
        source_event_id: str,
    ) -> int:
        if not source_doc_ids and not source_chunk_ids:
            return 0
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {self._table_name}
                   SET is_active = FALSE,
                       updated_at = now(),
                       source_event_id = %s
                 WHERE is_active = TRUE
                   AND (
                        EXISTS (
                            SELECT 1
                              FROM jsonb_array_elements_text(source_doc_ids_json) x(val)
                             WHERE x.val = ANY(%s)
                        )
                        OR EXISTS (
                            SELECT 1
                              FROM jsonb_array_elements_text(source_chunk_ids_json) y(val)
                             WHERE y.val = ANY(%s)
                        )
                   );
                """,
                (source_event_id, source_doc_ids or [""], source_chunk_ids or [""]),
            )
            return int(cur.rowcount or 0)

    def deactivate_by_kb_version(self, *, kb_version: str, source_event_id: str) -> int:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {self._table_name}
                   SET is_active = FALSE,
                       updated_at = now(),
                       source_event_id = %s
                 WHERE is_active = TRUE
                   AND kb_version = %s;
                """,
                (source_event_id, kb_version),
            )
            return int(cur.rowcount or 0)
