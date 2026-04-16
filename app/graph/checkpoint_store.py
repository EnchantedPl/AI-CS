import json
import os
import uuid
from typing import Any, Dict, List, Optional

import psycopg


class WorkflowCheckpointStore:
    def __init__(self, table_name: str = "workflow_checkpoints") -> None:
        self._table_name = table_name
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
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._table_name} (
                    checkpoint_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    node_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    parent_checkpoint_id TEXT,
                    state_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    metadata_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                """
            )
            cur.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_{self._table_name}_thread_created
                ON {self._table_name} (thread_id, created_at DESC);
                """
            )
            cur.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_{self._table_name}_run_created
                ON {self._table_name} (run_id, created_at DESC);
                """
            )
        self._ready = True

    def save_checkpoint(
        self,
        *,
        thread_id: str,
        run_id: str,
        trace_id: str,
        event_id: str,
        node_name: str,
        status: str,
        state: Dict[str, Any],
        parent_checkpoint_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        self.ensure_schema()
        checkpoint_id = f"ckpt_{uuid.uuid4().hex[:16]}"
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {self._table_name}
                (checkpoint_id, thread_id, run_id, trace_id, event_id, node_name, status,
                 parent_checkpoint_id, state_json, metadata_json, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, now());
                """,
                (
                    checkpoint_id,
                    thread_id,
                    run_id,
                    trace_id,
                    event_id,
                    node_name,
                    status,
                    parent_checkpoint_id or None,
                    json.dumps(state, ensure_ascii=False),
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
        return checkpoint_id

    def latest_for_thread(self, thread_id: str) -> Optional[Dict[str, Any]]:
        self.ensure_schema()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT checkpoint_id, thread_id, run_id, trace_id, event_id, node_name, status,
                       parent_checkpoint_id, state_json, metadata_json, created_at
                  FROM {self._table_name}
                 WHERE thread_id = %s
                 ORDER BY created_at DESC
                 LIMIT 1;
                """,
                (thread_id,),
            )
            row = cur.fetchone()
        if not row:
            return None
        return self._to_dict(row)

    def get_checkpoint(self, checkpoint_id: str) -> Optional[Dict[str, Any]]:
        self.ensure_schema()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT checkpoint_id, thread_id, run_id, trace_id, event_id, node_name, status,
                       parent_checkpoint_id, state_json, metadata_json, created_at
                  FROM {self._table_name}
                 WHERE checkpoint_id = %s
                 LIMIT 1;
                """,
                (checkpoint_id,),
            )
            row = cur.fetchone()
        if not row:
            return None
        return self._to_dict(row)

    def list_checkpoints(self, thread_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        self.ensure_schema()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT checkpoint_id, thread_id, run_id, trace_id, event_id, node_name, status,
                       parent_checkpoint_id, state_json, metadata_json, created_at
                  FROM {self._table_name}
                 WHERE thread_id = %s
                 ORDER BY created_at DESC
                 LIMIT %s;
                """,
                (thread_id, max(1, int(limit))),
            )
            rows = cur.fetchall()
        return [self._to_dict(r) for r in rows]

    def execution_path(self, thread_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        """Return lightweight ordered execution path for visualization."""
        self.ensure_schema()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT checkpoint_id, node_name, status, parent_checkpoint_id, metadata_json, created_at
                  FROM {self._table_name}
                 WHERE thread_id = %s
                 ORDER BY created_at ASC
                 LIMIT %s;
                """,
                (thread_id, max(1, int(limit))),
            )
            rows = cur.fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "checkpoint_id": str(r[0]),
                    "node_name": str(r[1]),
                    "status": str(r[2]),
                    "parent_checkpoint_id": str(r[3] or ""),
                    "metadata": r[4] if isinstance(r[4], dict) else {},
                    "created_at": str(r[5]),
                }
            )
        return out

    def list_checkpoints_by_run(self, run_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        self.ensure_schema()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT checkpoint_id, thread_id, run_id, trace_id, event_id, node_name, status,
                       parent_checkpoint_id, state_json, metadata_json, created_at
                  FROM {self._table_name}
                 WHERE run_id = %s
                 ORDER BY created_at DESC
                 LIMIT %s;
                """,
                (run_id, max(1, int(limit))),
            )
            rows = cur.fetchall()
        return [self._to_dict(r) for r in rows]

    def latest_stage_checkpoint(self, *, run_id: str, stage: str) -> Optional[Dict[str, Any]]:
        self.ensure_schema()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT checkpoint_id, thread_id, run_id, trace_id, event_id, node_name, status,
                       parent_checkpoint_id, state_json, metadata_json, created_at
                  FROM {self._table_name}
                 WHERE run_id = %s
                   AND metadata_json->>'stage' = %s
                 ORDER BY created_at DESC
                 LIMIT 1;
                """,
                (run_id, stage),
            )
            row = cur.fetchone()
        if not row:
            return None
        return self._to_dict(row)

    def _to_dict(self, row: Any) -> Dict[str, Any]:
        return {
            "checkpoint_id": str(row[0]),
            "thread_id": str(row[1]),
            "run_id": str(row[2]),
            "trace_id": str(row[3]),
            "event_id": str(row[4]),
            "node_name": str(row[5]),
            "status": str(row[6]),
            "parent_checkpoint_id": str(row[7] or ""),
            "state": row[8] if isinstance(row[8], dict) else {},
            "metadata": row[9] if isinstance(row[9], dict) else {},
            "created_at": str(row[10]),
        }
