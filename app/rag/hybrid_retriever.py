import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

load_dotenv()

from app.cache.embedding_runtime import get_shared_local_embedding_model
from app.models.litellm_client import embed_texts_with_litellm


def _as_bool(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class RagRuntimeConfig:
    pg_host: str
    pg_port: int
    pg_db: str
    pg_user: str
    pg_password: str
    table_name: str
    embedding_mode: str
    embedding_provider: str
    embedding_model: str
    embedding_dim: int
    embedding_batch_size: int
    cloud_embedding_provider: str
    cloud_embedding_model: str
    cloud_embedding_dim: int
    local_embedding_provider: str
    local_embedding_model: str
    local_embedding_dim: int
    auto_reset_on_embedding_change: bool
    chunk_strategy: str
    chunk_size: int
    chunk_overlap: int
    vector_topk: int
    keyword_topk: int
    final_topk: int
    enable_rerank: bool
    rerank_provider: str
    rerank_model: str
    rerank_candidates: int
    rerank_topk: int
    rrf_k: int
    rrf_vector_weight: float
    rrf_keyword_weight: float
    kb_version: str
    policy_version: str
    default_language: str

    @classmethod
    def from_env(cls) -> "RagRuntimeConfig":
        embedding_mode = os.getenv("EMBEDDING_MODE", "cloud").strip().lower()
        if embedding_mode == "local":
            active_provider = os.getenv(
                "LOCAL_EMBEDDING_PROVIDER",
                os.getenv("EMBEDDING_PROVIDER", "huggingface"),
            )
            active_model = os.getenv(
                "LOCAL_EMBEDDING_MODEL",
                os.getenv(
                    "EMBEDDING_MODEL",
                    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
                ),
            )
            active_dim = int(os.getenv("LOCAL_EMBEDDING_DIM", os.getenv("EMBEDDING_DIM", "384")))
            active_batch = int(
                os.getenv("LOCAL_EMBEDDING_BATCH_SIZE", os.getenv("EMBEDDING_BATCH_SIZE", "32"))
            )
        else:
            active_provider = os.getenv("CLOUD_EMBEDDING_PROVIDER", "litellm")
            active_model = os.getenv(
                "CLOUD_EMBEDDING_MODEL",
                os.getenv("EMBEDDING_MODEL", "openai/text-embedding-v3"),
            )
            active_dim = int(os.getenv("CLOUD_EMBEDDING_DIM", os.getenv("EMBEDDING_DIM", "1024")))
            active_batch = int(
                os.getenv("CLOUD_EMBEDDING_BATCH_SIZE", os.getenv("EMBEDDING_BATCH_SIZE", "10"))
            )

        return cls(
            pg_host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
            pg_port=int(os.getenv("POSTGRES_PORT", "5433")),
            pg_db=os.getenv("POSTGRES_DB", "ai_cs"),
            pg_user=os.getenv("POSTGRES_USER", "postgres"),
            pg_password=os.getenv("POSTGRES_PASSWORD", "postgres"),
            table_name=os.getenv("RAG_TABLE_NAME", "kb_chunks"),
            embedding_mode=embedding_mode,
            embedding_provider=active_provider,
            embedding_model=active_model,
            embedding_dim=active_dim,
            embedding_batch_size=active_batch,
            cloud_embedding_provider=os.getenv("CLOUD_EMBEDDING_PROVIDER", "litellm"),
            cloud_embedding_model=os.getenv("CLOUD_EMBEDDING_MODEL", "openai/text-embedding-v3"),
            cloud_embedding_dim=int(os.getenv("CLOUD_EMBEDDING_DIM", "1024")),
            local_embedding_provider=os.getenv("LOCAL_EMBEDDING_PROVIDER", "huggingface"),
            local_embedding_model=os.getenv(
                "LOCAL_EMBEDDING_MODEL",
                "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            ),
            local_embedding_dim=int(os.getenv("LOCAL_EMBEDDING_DIM", "384")),
            auto_reset_on_embedding_change=_as_bool(
                os.getenv("RAG_AUTO_RESET_ON_EMBEDDING_CHANGE"), True
            ),
            chunk_strategy=os.getenv("RAG_CHUNK_STRATEGY", "fixed"),
            chunk_size=int(os.getenv("RAG_CHUNK_SIZE", "512")),
            chunk_overlap=int(os.getenv("RAG_CHUNK_OVERLAP", "64")),
            vector_topk=int(os.getenv("RAG_VECTOR_TOP_K", "20")),
            keyword_topk=int(os.getenv("RAG_KEYWORD_TOP_K", "20")),
            final_topk=int(os.getenv("RAG_TOP_K", "3")),
            enable_rerank=_as_bool(os.getenv("RAG_ENABLE_RERANK"), False),
            rerank_provider=os.getenv("RAG_RERANK_PROVIDER", "local"),
            rerank_model=os.getenv("RAG_RERANK_MODEL", "BAAI/bge-reranker-base"),
            rerank_candidates=int(os.getenv("RAG_RERANK_CANDIDATES", "20")),
            rerank_topk=int(os.getenv("RAG_RERANK_TOP_K", os.getenv("RAG_TOP_K", "3"))),
            rrf_k=int(os.getenv("RAG_RRF_K", "60")),
            rrf_vector_weight=float(os.getenv("RAG_RRF_VECTOR_WEIGHT", "0.7")),
            rrf_keyword_weight=float(os.getenv("RAG_RRF_KEYWORD_WEIGHT", "0.3")),
            kb_version=os.getenv("KB_VERSION", "v1"),
            policy_version=os.getenv("POLICY_VERSION", "v1"),
            default_language=os.getenv("DEFAULT_LANGUAGE", "zh"),
        )


def _vector_literal(values: List[float]) -> str:
    return "[" + ",".join(f"{v:.8f}" for v in values) + "]"


def _hash_chunk_id(doc_id: str, index: int, strategy: str, kb_version: str) -> str:
    raw = f"{doc_id}:{index}:{strategy}:{kb_version}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _domain_from_text(text: str) -> str:
    lowered = text.lower()
    if any(k in lowered for k in ["退款", "退货", "refund", "return"]):
        return "aftersales"
    if any(k in lowered for k in ["法律", "投诉", "合规", "risk", "legal"]):
        return "risk_query"
    if any(k in lowered for k in ["价格", "规格", "参数", "price", "spec"]):
        return "product_info"
    return "faq"


class HybridPgRetriever:
    """Minimal hybrid retriever:
    - uses LlamaIndex splitter + embedding for ingestion
    - stores chunks in Postgres/pgvector
    - supports vector/keyword/hybrid (RRF) retrieval
    """

    def __init__(self, cfg: Optional[RagRuntimeConfig] = None) -> None:
        self.cfg = cfg or RagRuntimeConfig.from_env()
        self._local_embed_model = None
        self._local_embed_model_name = None
        self._reranker_model = None
        self._reranker_model_name = None

    def _active_vector_column(self) -> str:
        return "embedding_local" if self.cfg.embedding_mode == "local" else "embedding"

    def _active_embedding_meta_prefix(self) -> str:
        return "local" if self.cfg.embedding_mode == "local" else "cloud"

    # ---------- dependency / connection helpers ----------
    def _get_conn(self):
        import psycopg

        return psycopg.connect(
            host=self.cfg.pg_host,
            port=self.cfg.pg_port,
            dbname=self.cfg.pg_db,
            user=self.cfg.pg_user,
            password=self.cfg.pg_password,
            autocommit=True,
        )

    def _get_local_embed_model(self):
        target_model = self.cfg.embedding_model
        if self._local_embed_model is None or self._local_embed_model_name != target_model:
            self._local_embed_model = get_shared_local_embedding_model(target_model)
            self._local_embed_model_name = target_model
        return self._local_embed_model

    def _get_reranker_model(self):
        target_model = self.cfg.rerank_model
        if self._reranker_model is None or self._reranker_model_name != target_model:
            from sentence_transformers import CrossEncoder

            self._reranker_model = CrossEncoder(target_model)
            self._reranker_model_name = target_model
        return self._reranker_model

    def _embed_texts(self, texts: List[str]) -> Tuple[List[List[float]], str]:
        if self.cfg.embedding_provider == "huggingface":
            model = self._get_local_embed_model()
            vectors = [model.get_text_embedding(t) for t in texts]
            return vectors, self.cfg.embedding_model
        result = embed_texts_with_litellm(texts)
        vectors = result["embeddings"]
        model_used = result.get("model", self.cfg.embedding_model)
        return vectors, model_used

    def _embed_query(self, query: str) -> List[float]:
        vectors, _ = self._embed_texts([query])
        return vectors[0]

    def _embed_texts_resilient(self, texts: List[str]) -> Tuple[List[List[float]], str]:
        """Embed with adaptive micro-batching for provider batch limits."""
        if not texts:
            return [], self.cfg.embedding_model

        def _should_reduce_batch(error_msg: str) -> bool:
            lowered = (error_msg or "").lower()
            return (
                "batch size is invalid" in lowered
                or "should not be larger than" in lowered
                or "input.contents" in lowered
            )

        pending = list(texts)
        vectors: List[List[float]] = []
        model_used = self.cfg.embedding_model
        # Start from configured batch size, then degrade on provider constraints.
        current_batch_size = max(1, self.cfg.embedding_batch_size)
        if self.cfg.embedding_provider == "litellm":
            current_batch_size = min(current_batch_size, 10)

        while pending:
            chunk = pending[:current_batch_size]
            try:
                emb_chunk, model_used = self._embed_texts(chunk)
                vectors.extend(emb_chunk)
                pending = pending[current_batch_size:]
            except Exception as exc:
                if current_batch_size > 1 and _should_reduce_batch(str(exc)):
                    current_batch_size = max(1, current_batch_size // 2)
                    continue
                raise
        return vectors, model_used

    def _get_existing_vector_dim(self, column_name: str) -> Optional[int]:
        sql = """
            SELECT format_type(a.atttypid, a.atttypmod) AS type_name
            FROM pg_attribute a
            JOIN pg_class c ON a.attrelid = c.oid
            JOIN pg_namespace n ON c.relnamespace = n.oid
            WHERE c.relname = %s
              AND a.attname = %s
              AND a.attnum > 0
              AND NOT a.attisdropped
            LIMIT 1;
        """
        with self._get_conn() as conn, conn.cursor() as cur:
            cur.execute(sql, (self.cfg.table_name, column_name))
            row = cur.fetchone()
        if not row or not row[0]:
            return None
        type_name = str(row[0])
        if "(" not in type_name or ")" not in type_name:
            return None
        try:
            return int(type_name.split("(", 1)[1].split(")", 1)[0])
        except Exception:
            return None

    def _drop_table_if_exists(self) -> None:
        with self._get_conn() as conn, conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {self.cfg.table_name};")

    def _refresh_vector_column_if_dim_changed(
        self,
        *,
        column_name: str,
        target_dim: int,
        reset_meta_columns: List[str],
    ) -> None:
        existing_dim = self._get_existing_vector_dim(column_name)
        if existing_dim is None:
            with self._get_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    f"ALTER TABLE {self.cfg.table_name} "
                    f"ADD COLUMN IF NOT EXISTS {column_name} vector({target_dim});"
                )
            return
        if existing_dim == target_dim:
            return
        if not self.cfg.auto_reset_on_embedding_change:
            return

        with self._get_conn() as conn, conn.cursor() as cur:
            cur.execute(f"ALTER TABLE {self.cfg.table_name} DROP COLUMN IF EXISTS {column_name};")
            cur.execute(
                f"ALTER TABLE {self.cfg.table_name} "
                f"ADD COLUMN {column_name} vector({target_dim});"
            )
            for col in reset_meta_columns:
                cur.execute(f"UPDATE {self.cfg.table_name} SET {col}=NULL;")

    def _has_embedding_metadata_mismatch(self) -> bool:
        prefix = self._active_embedding_meta_prefix()
        provider_col = f"{prefix}_embedding_provider"
        model_col = f"{prefix}_embedding_model"
        sql = f"""
            SELECT COUNT(*)
            FROM {self.cfg.table_name}
            WHERE ({provider_col} IS NOT NULL AND {provider_col} <> %s)
               OR ({model_col} IS NOT NULL AND {model_col} <> %s);
        """
        try:
            with self._get_conn() as conn, conn.cursor() as cur:
                cur.execute(sql, (self.cfg.embedding_provider, self.cfg.embedding_model))
                row = cur.fetchone()
            return bool(row and int(row[0]) > 0)
        except Exception:
            return False

    # ---------- schema ----------
    def ensure_schema(self) -> None:
        with self._get_conn() as conn, conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.cfg.table_name} (
                    chunk_id TEXT PRIMARY KEY,
                    doc_id TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    source_url TEXT,
                    domain TEXT NOT NULL,
                    lang TEXT NOT NULL,
                    tenant_scope TEXT NOT NULL DEFAULT 'public',
                    risk_level TEXT NOT NULL DEFAULT 'low',
                    kb_version TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    chunk_strategy TEXT NOT NULL,
                    chunk_size INTEGER NOT NULL,
                    chunk_overlap INTEGER NOT NULL,
                    cloud_embedding_provider TEXT,
                    cloud_embedding_model TEXT,
                    local_embedding_provider TEXT,
                    local_embedding_model TEXT,
                    content TEXT NOT NULL,
                    content_tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', coalesce(content, ''))) STORED,
                    embedding vector({self.cfg.cloud_embedding_dim}),
                    embedding_local vector({self.cfg.local_embedding_dim}),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                """
            )
            cur.execute(
                f"""
                ALTER TABLE {self.cfg.table_name}
                ADD COLUMN IF NOT EXISTS cloud_embedding_provider TEXT;
                """
            )
            cur.execute(
                f"""
                ALTER TABLE {self.cfg.table_name}
                ADD COLUMN IF NOT EXISTS embedding_provider TEXT;
                """
            )
            cur.execute(
                f"""
                ALTER TABLE {self.cfg.table_name}
                ADD COLUMN IF NOT EXISTS embedding_local vector({self.cfg.local_embedding_dim});
                """
            )
            cur.execute(
                f"""
                ALTER TABLE {self.cfg.table_name}
                ADD COLUMN IF NOT EXISTS cloud_embedding_model TEXT;
                """
            )
            cur.execute(
                f"""
                ALTER TABLE {self.cfg.table_name}
                ADD COLUMN IF NOT EXISTS embedding_model TEXT;
                """
            )
            cur.execute(
                f"""
                ALTER TABLE {self.cfg.table_name}
                ADD COLUMN IF NOT EXISTS local_embedding_provider TEXT;
                """
            )
            cur.execute(
                f"""
                ALTER TABLE {self.cfg.table_name}
                ADD COLUMN IF NOT EXISTS local_embedding_model TEXT;
                """
            )
            cur.execute(
                f"""
                ALTER TABLE {self.cfg.table_name}
                ALTER COLUMN embedding DROP NOT NULL;
                """
            )
            cur.execute(
                f"""
                ALTER TABLE {self.cfg.table_name}
                ALTER COLUMN embedding_provider DROP NOT NULL;
                """
            )
            cur.execute(
                f"""
                ALTER TABLE {self.cfg.table_name}
                ALTER COLUMN embedding_model DROP NOT NULL;
                """
            )

        self._refresh_vector_column_if_dim_changed(
            column_name="embedding",
            target_dim=self.cfg.cloud_embedding_dim,
            reset_meta_columns=["cloud_embedding_provider", "cloud_embedding_model"],
        )
        self._refresh_vector_column_if_dim_changed(
            column_name="embedding_local",
            target_dim=self.cfg.local_embedding_dim,
            reset_meta_columns=["local_embedding_provider", "local_embedding_model"],
        )

        with self._get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{self.cfg.table_name}_content_tsv "
                f"ON {self.cfg.table_name} USING GIN (content_tsv);"
            )
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{self.cfg.table_name}_meta "
                f"ON {self.cfg.table_name} (domain, lang, kb_version, is_active);"
            )
            cur.execute(
                f"""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_indexes
                        WHERE indexname = 'idx_{self.cfg.table_name}_embedding_hnsw'
                    ) THEN
                        EXECUTE 'CREATE INDEX idx_{self.cfg.table_name}_embedding_hnsw '
                             || 'ON {self.cfg.table_name} USING hnsw (embedding vector_cosine_ops)';
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
                        WHERE indexname = 'idx_{self.cfg.table_name}_embedding_local_hnsw'
                    ) THEN
                        EXECUTE 'CREATE INDEX idx_{self.cfg.table_name}_embedding_local_hnsw '
                             || 'ON {self.cfg.table_name} USING hnsw (embedding_local vector_cosine_ops)';
                    END IF;
                EXCEPTION WHEN others THEN
                    NULL;
                END $$;
                """
            )

    # ---------- ingestion ----------
    def _load_docs_from_kb(self, kb_dir: str = "data/kb") -> List[Dict[str, str]]:
        docs: List[Dict[str, str]] = []
        base = Path(kb_dir)
        if not base.exists():
            return docs
        for path in sorted(list(base.glob("*.md")) + list(base.glob("*.txt"))):
            content = path.read_text(encoding="utf-8", errors="ignore").strip()
            if not content:
                continue
            docs.append(
                {
                    "doc_id": path.stem,
                    "source_name": path.name,
                    "source_url": "",
                    "text": content,
                }
            )
        return docs

    def _split_documents(self, docs: List[Dict[str, str]], strategy: str) -> List[Tuple[Dict[str, str], str]]:
        from llama_index.core import Document
        from llama_index.core.node_parser import SentenceSplitter

        li_docs = [Document(text=d["text"], metadata={"doc_id": d["doc_id"], "source_name": d["source_name"]}) for d in docs]
        if strategy == "semantic":
            try:
                from llama_index.core.node_parser import SemanticSplitterNodeParser

                if self.cfg.embedding_provider != "huggingface":
                    raise RuntimeError("semantic split requires local huggingface embedding model")
                parser = SemanticSplitterNodeParser(
                    embed_model=self._get_local_embed_model(),
                    breakpoint_percentile_threshold=95,
                    buffer_size=1,
                )
            except Exception:
                parser = SentenceSplitter(
                    chunk_size=self.cfg.chunk_size,
                    chunk_overlap=self.cfg.chunk_overlap,
                )
        else:
            parser = SentenceSplitter(
                chunk_size=self.cfg.chunk_size,
                chunk_overlap=self.cfg.chunk_overlap,
            )
        nodes = parser.get_nodes_from_documents(li_docs)

        out: List[Tuple[Dict[str, str], str]] = []
        for node in nodes:
            metadata = node.metadata or {}
            doc_id = metadata.get("doc_id", "unknown")
            source_name = metadata.get("source_name", "unknown")
            out.append(
                (
                    {"doc_id": doc_id, "source_name": source_name, "source_url": ""},
                    node.get_content(),
                )
            )
        return out

    def ingest_kb(self, *, kb_dir: str = "data/kb", target_chunks: int = 2000) -> Dict[str, Any]:
        self.ensure_schema()
        docs = self._load_docs_from_kb(kb_dir=kb_dir)
        if not docs:
            return {"ok": False, "reason": "no_docs_found", "kb_dir": kb_dir}

        strategy = self.cfg.chunk_strategy
        chunks = self._split_documents(docs, strategy=strategy)
        if target_chunks > 0:
            chunks = chunks[:target_chunks]

        inserted = 0
        with self._get_conn() as conn, conn.cursor() as cur:
            effective_idx = 0
            batch_size = max(1, self.cfg.embedding_batch_size)
            if self.cfg.embedding_provider == "litellm":
                # DashScope embedding enforces input batch size <= 10.
                batch_size = min(batch_size, 10)
            for start in range(0, len(chunks), batch_size):
                chunk_batch = chunks[start : start + batch_size]
                valid_rows = [(meta, text) for meta, text in chunk_batch if text.strip()]
                if not valid_rows:
                    continue
                text_batch = [text for _, text in valid_rows]
                emb_batch, model_used = self._embed_texts_resilient(text_batch)

                for (doc_meta, chunk_text), emb in zip(valid_rows, emb_batch):
                    chunk_id = _hash_chunk_id(
                        doc_meta["doc_id"], effective_idx, strategy, self.cfg.kb_version
                    )
                    effective_idx += 1
                    domain = _domain_from_text(chunk_text)
                    if self.cfg.embedding_mode == "local":
                        query = f"""
                        INSERT INTO {self.cfg.table_name} (
                            chunk_id, doc_id, source_name, source_url,
                            domain, lang, tenant_scope, risk_level,
                            kb_version, policy_version, is_active,
                            chunk_strategy, chunk_size, chunk_overlap,
                            local_embedding_provider, local_embedding_model,
                            content, embedding_local, updated_at
                        ) VALUES (
                            %s, %s, %s, %s,
                            %s, %s, 'public', 'low',
                            %s, %s, TRUE,
                            %s, %s, %s,
                            %s, %s,
                            %s, %s::vector, now()
                        )
                        ON CONFLICT (chunk_id)
                        DO UPDATE SET
                            content = EXCLUDED.content,
                            embedding_local = EXCLUDED.embedding_local,
                            local_embedding_provider = EXCLUDED.local_embedding_provider,
                            local_embedding_model = EXCLUDED.local_embedding_model,
                            updated_at = now(),
                            is_active = TRUE;
                        """
                    else:
                        query = f"""
                        INSERT INTO {self.cfg.table_name} (
                            chunk_id, doc_id, source_name, source_url,
                            domain, lang, tenant_scope, risk_level,
                            kb_version, policy_version, is_active,
                            chunk_strategy, chunk_size, chunk_overlap,
                            cloud_embedding_provider, cloud_embedding_model,
                            content, embedding, updated_at
                        ) VALUES (
                            %s, %s, %s, %s,
                            %s, %s, 'public', 'low',
                            %s, %s, TRUE,
                            %s, %s, %s,
                            %s, %s,
                            %s, %s::vector, now()
                        )
                        ON CONFLICT (chunk_id)
                        DO UPDATE SET
                            content = EXCLUDED.content,
                            embedding = EXCLUDED.embedding,
                            cloud_embedding_provider = EXCLUDED.cloud_embedding_provider,
                            cloud_embedding_model = EXCLUDED.cloud_embedding_model,
                            updated_at = now(),
                            is_active = TRUE;
                        """
                    cur.execute(
                        query,
                        (
                            chunk_id,
                            doc_meta["doc_id"],
                            doc_meta["source_name"],
                            doc_meta.get("source_url", ""),
                            domain,
                            self.cfg.default_language,
                            self.cfg.kb_version,
                            self.cfg.policy_version,
                            strategy,
                            self.cfg.chunk_size,
                            self.cfg.chunk_overlap,
                            self.cfg.embedding_provider,
                            model_used,
                            chunk_text,
                            _vector_literal(emb),
                        ),
                    )
                    inserted += 1
        return {
            "ok": True,
            "docs": len(docs),
            "chunks_inserted": inserted,
            "chunk_strategy": strategy,
            "kb_version": self.cfg.kb_version,
            "table_name": self.cfg.table_name,
            "embedding_mode": self.cfg.embedding_mode,
            "embedding_provider": self.cfg.embedding_provider,
            "embedding_model": self.cfg.embedding_model,
            "embedding_dim": self.cfg.embedding_dim,
        }

    # ---------- retrieval ----------
    def _vector_search(
        self, *, query: str, domain: str, lang: str, top_k: int
    ) -> List[Dict[str, Any]]:
        emb = self._embed_query(query)
        emb_lit = _vector_literal(emb)
        vector_column = self._active_vector_column()
        sql = f"""
            SELECT chunk_id, doc_id, source_name, domain, lang, content,
                   1 - ({vector_column} <=> %s::vector) AS score
            FROM {self.cfg.table_name}
            WHERE is_active = TRUE
              AND kb_version = %s
              AND lang = %s
              AND domain = %s
              AND {vector_column} IS NOT NULL
            ORDER BY {vector_column} <=> %s::vector
            LIMIT %s;
        """
        with self._get_conn() as conn, conn.cursor() as cur:
            cur.execute(sql, (emb_lit, self.cfg.kb_version, lang, domain, emb_lit, top_k))
            rows = cur.fetchall()
        return [
            {
                "chunk_id": r[0],
                "doc_id": r[1],
                "source_name": r[2],
                "domain": r[3],
                "lang": r[4],
                "content": r[5],
                "score": float(r[6]),
            }
            for r in rows
        ]

    def _keyword_search(
        self, *, query: str, domain: str, lang: str, top_k: int
    ) -> List[Dict[str, Any]]:
        sql = f"""
            SELECT chunk_id, doc_id, source_name, domain, lang, content,
                   ts_rank_cd(content_tsv, plainto_tsquery('simple', %s)) AS score
            FROM {self.cfg.table_name}
            WHERE is_active = TRUE
              AND kb_version = %s
              AND lang = %s
              AND domain = %s
              AND content_tsv @@ plainto_tsquery('simple', %s)
            ORDER BY score DESC
            LIMIT %s;
        """
        with self._get_conn() as conn, conn.cursor() as cur:
            cur.execute(sql, (query, self.cfg.kb_version, lang, domain, query, top_k))
            rows = cur.fetchall()
        return [
            {
                "chunk_id": r[0],
                "doc_id": r[1],
                "source_name": r[2],
                "domain": r[3],
                "lang": r[4],
                "content": r[5],
                "score": float(r[6]),
            }
            for r in rows
        ]

    def _rrf_fuse(
        self,
        vector_candidates: List[Dict[str, Any]],
        keyword_candidates: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        score_map: Dict[str, Dict[str, Any]] = {}

        for rank, c in enumerate(vector_candidates, start=1):
            cid = c["chunk_id"]
            base = score_map.setdefault(cid, {"candidate": c, "score": 0.0})
            base["score"] += self.cfg.rrf_vector_weight * (1.0 / (self.cfg.rrf_k + rank))

        for rank, c in enumerate(keyword_candidates, start=1):
            cid = c["chunk_id"]
            base = score_map.setdefault(cid, {"candidate": c, "score": 0.0})
            base["score"] += self.cfg.rrf_keyword_weight * (1.0 / (self.cfg.rrf_k + rank))

        fused = []
        for cid, item in score_map.items():
            row = dict(item["candidate"])
            row["rrf_score"] = item["score"]
            fused.append(row)
        fused.sort(key=lambda x: x["rrf_score"], reverse=True)
        return fused

    def _normalize_chunk_fields(self, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for c in chunks:
            row = dict(c)
            text = row.get("text")
            if text is None:
                text = row.get("content", "")
            row["text"] = text
            # Keep `content` for backward compatibility.
            if "content" not in row:
                row["content"] = text
            normalized.append(row)
        return normalized

    def _rerank_candidates(
        self,
        *,
        query: str,
        candidates: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        meta: Dict[str, Any] = {
            "enabled": self.cfg.enable_rerank,
            "provider": self.cfg.rerank_provider,
            "model": self.cfg.rerank_model,
            "requested_candidates": self.cfg.rerank_candidates,
            "requested_topk": self.cfg.rerank_topk,
            "applied": False,
            "before_ids": [c.get("chunk_id") for c in candidates],
            "after_ids": [c.get("chunk_id") for c in candidates],
        }
        if not self.cfg.enable_rerank or not candidates:
            return candidates, meta
        if self.cfg.rerank_provider != "local":
            meta["error"] = f"unsupported_rerank_provider={self.cfg.rerank_provider}"
            return candidates, meta

        rerank_n = max(1, min(self.cfg.rerank_candidates, len(candidates)))
        head = [dict(c) for c in candidates[:rerank_n]]
        tail = [dict(c) for c in candidates[rerank_n:]]
        try:
            model = self._get_reranker_model()
            pairs = [(query, (c.get("text") or c.get("content") or "")) for c in head]
            scores = model.predict(pairs)
            for c, score in zip(head, scores):
                c["rerank_score"] = float(score)
            head.sort(key=lambda x: x.get("rerank_score", -1e9), reverse=True)
            reranked = head + tail
            meta["applied"] = True
            meta["after_ids"] = [c.get("chunk_id") for c in reranked]
            meta["top_scores"] = [
                {
                    "chunk_id": c.get("chunk_id"),
                    "rerank_score": round(float(c.get("rerank_score", 0.0)), 6),
                }
                for c in head[: min(len(head), 10)]
            ]
            return reranked, meta
        except Exception as exc:
            meta["error"] = str(exc)
            return candidates, meta

    def retrieve(
        self,
        *,
        query: str,
        domain: str,
        lang: Optional[str] = None,
        retrieval_mode: str = "hybrid",
        vector_topk: Optional[int] = None,
        keyword_topk: Optional[int] = None,
        final_topk: Optional[int] = None,
    ) -> Dict[str, Any]:
        lang = lang or self.cfg.default_language
        vector_topk = vector_topk or self.cfg.vector_topk
        keyword_topk = keyword_topk or self.cfg.keyword_topk
        final_topk = final_topk or self.cfg.final_topk

        t0 = time.perf_counter()
        t_vector = time.perf_counter()
        vector_candidates = self._vector_search(
            query=query, domain=domain, lang=lang, top_k=vector_topk
        )
        vector_ms = (time.perf_counter() - t_vector) * 1000.0
        t_keyword = time.perf_counter()
        keyword_candidates = self._keyword_search(
            query=query, domain=domain, lang=lang, top_k=keyword_topk
        )
        keyword_ms = (time.perf_counter() - t_keyword) * 1000.0

        fusion_ms = 0.0
        if retrieval_mode == "vector":
            prefinal_candidates = vector_candidates[: max(final_topk, self.cfg.rerank_candidates)]
        elif retrieval_mode == "keyword":
            prefinal_candidates = keyword_candidates[: max(final_topk, self.cfg.rerank_candidates)]
        else:
            t_fusion = time.perf_counter()
            fused = self._rrf_fuse(vector_candidates, keyword_candidates)
            fusion_ms = (time.perf_counter() - t_fusion) * 1000.0
            prefinal_candidates = fused[: max(final_topk, self.cfg.rerank_candidates)]

        t_rerank = time.perf_counter()
        reranked_candidates, rerank_meta = self._rerank_candidates(
            query=query,
            candidates=prefinal_candidates,
        )
        rerank_ms = (time.perf_counter() - t_rerank) * 1000.0
        effective_topk = min(
            final_topk,
            self.cfg.rerank_topk if self.cfg.enable_rerank else final_topk,
        )
        final_candidates = reranked_candidates[:effective_topk]

        final_candidates = self._normalize_chunk_fields(final_candidates)

        citations = [f"{c['source_name']}#{c['chunk_id']}" for c in final_candidates]
        context = "\n".join([c["text"] for c in final_candidates])
        low_score_threshold = float(os.getenv("RAG_LOW_SCORE_THRESHOLD", "0.2"))
        rerank_scores = [float(x.get("rerank_score", 0.0) or 0.0) for x in (rerank_meta.get("top_scores", []) or [])]
        low_score_ratio = (
            (sum(1 for s in rerank_scores if s < low_score_threshold) / float(len(rerank_scores)))
            if rerank_scores
            else 0.0
        )
        total_ms = (time.perf_counter() - t0) * 1000.0
        return {
            "enabled": True,
            "mode": retrieval_mode,
            "timings_ms": {
                "vector": round(vector_ms, 3),
                "keyword": round(keyword_ms, 3),
                "fusion": round(fusion_ms, 3),
                "rerank": round(rerank_ms, 3),
                "total": round(total_ms, 3),
            },
            "params": {
                "vector_topk": vector_topk,
                "keyword_topk": keyword_topk,
                "final_topk": final_topk,
                "rrf_k": self.cfg.rrf_k,
                "rrf_weights": {
                    "vector": self.cfg.rrf_vector_weight,
                    "keyword": self.cfg.rrf_keyword_weight,
                },
                "rerank": {
                    "enabled": self.cfg.enable_rerank,
                    "provider": self.cfg.rerank_provider,
                    "model": self.cfg.rerank_model,
                    "candidates": self.cfg.rerank_candidates,
                    "topk": self.cfg.rerank_topk,
                    "applied": rerank_meta.get("applied", False),
                    "before_ids": rerank_meta.get("before_ids", []),
                    "after_ids": rerank_meta.get("after_ids", []),
                    "top_scores": rerank_meta.get("top_scores", []),
                    "low_score_ratio": round(float(low_score_ratio), 6),
                    "error": rerank_meta.get("error"),
                },
            },
            "filters": {
                "domain": domain,
                "lang": lang,
                "kb_version": self.cfg.kb_version,
                "is_active": True,
                "embedding_mode": self.cfg.embedding_mode,
                "embedding_model": self.cfg.embedding_model,
                "vector_column": self._active_vector_column(),
            },
            "candidates": {
                "vector": [
                    {"chunk_id": c["chunk_id"], "score": round(c["score"], 6)}
                    for c in vector_candidates
                ],
                "keyword": [
                    {"chunk_id": c["chunk_id"], "score": round(c["score"], 6)}
                    for c in keyword_candidates
                ],
                "fused": [
                    {
                        "chunk_id": c["chunk_id"],
                        "score": round(c.get("rrf_score", c.get("score", 0.0)), 6),
                    }
                    for c in final_candidates
                ],
            },
            "chunks": final_candidates,
            "citations": citations,
            "context": context,
        }

    def health(self) -> Dict[str, Any]:
        try:
            with self._get_conn() as conn, conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) FROM {self.cfg.table_name};")
                count = cur.fetchone()[0]
            return {"ok": True, "table": self.cfg.table_name, "rows": int(count)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def reset_table(self) -> Dict[str, Any]:
        """Truncate retriever table for a clean re-ingest."""
        self.ensure_schema()
        with self._get_conn() as conn, conn.cursor() as cur:
            cur.execute(f"TRUNCATE TABLE {self.cfg.table_name};")
        return {"ok": True, "table": self.cfg.table_name, "reset": True}


RETRIEVER = HybridPgRetriever()


def format_retrieval_summary(result: Dict[str, Any]) -> str:
    chunks = result.get("chunks", [])
    if not chunks:
        return "未检索到相关知识。"
    top = chunks[0]
    return f"检索模式={result.get('mode')}，首条证据来自 {top.get('source_name')}。"


def to_json(data: Dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)

