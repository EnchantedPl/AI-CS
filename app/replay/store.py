import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import psycopg


def _safe_json(data: Any) -> str:
    return json.dumps(data if data is not None else {}, ensure_ascii=False)


@dataclass(frozen=True)
class ReplayStoreConfig:
    enabled: bool
    schema_version: str
    layer_spec_version: str
    pg_host: str
    pg_port: int
    pg_db: str
    pg_user: str
    pg_password: str

    @classmethod
    def from_env(cls) -> "ReplayStoreConfig":
        return cls(
            enabled=os.getenv("ENABLE_LAYERED_REPLAY", "true").strip().lower() in {"1", "true", "yes", "on"},
            schema_version=os.getenv("REPLAY_SCHEMA_VERSION", "v1"),
            layer_spec_version=os.getenv("REPLAY_LAYER_SPEC_VERSION", "v1_5layer"),
            pg_host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
            pg_port=int(os.getenv("POSTGRES_PORT", "5433")),
            pg_db=os.getenv("POSTGRES_DB", "ai_cs"),
            pg_user=os.getenv("POSTGRES_USER", "postgres"),
            pg_password=os.getenv("POSTGRES_PASSWORD", "postgres"),
        )


class LayeredReplayStore:
    def __init__(self, cfg: Optional[ReplayStoreConfig] = None) -> None:
        self.cfg = cfg or ReplayStoreConfig.from_env()
        self._ready = False

    def _conn(self):
        return psycopg.connect(
            host=self.cfg.pg_host,
            port=self.cfg.pg_port,
            dbname=self.cfg.pg_db,
            user=self.cfg.pg_user,
            password=self.cfg.pg_password,
            autocommit=True,
        )

    def ensure_schema(self) -> None:
        if self._ready:
            return
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS replay_case (
                    case_id TEXT PRIMARY KEY,
                    trace_id TEXT,
                    run_id TEXT,
                    tenant_id TEXT,
                    user_id TEXT,
                    actor_type TEXT,
                    channel TEXT,
                    scenario_tags JSONB NOT NULL DEFAULT '[]'::jsonb,
                    input_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                    expected_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                    env_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                    schema_version TEXT NOT NULL,
                    layer_spec_version TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_replay_case_run_id ON replay_case (run_id);
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_replay_case_created_at ON replay_case (created_at DESC);
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS replay_snapshot (
                    snapshot_id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES replay_case(case_id) ON DELETE CASCADE,
                    layer_code TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ok',
                    error_code TEXT,
                    degrade_reason TEXT,
                    config_fingerprint TEXT,
                    input_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                    output_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                    decision_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                    params_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                    metrics_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                    started_at TIMESTAMPTZ,
                    finished_at TIMESTAMPTZ,
                    latency_ms DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_replay_snapshot_case_layer ON replay_snapshot (case_id, layer_code, seq);
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS replay_experiment (
                    experiment_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    baseline_ref TEXT,
                    candidate_ref TEXT,
                    global_params_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                    meta_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                    schema_version TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS replay_diff (
                    diff_id TEXT PRIMARY KEY,
                    experiment_id TEXT NOT NULL REFERENCES replay_experiment(experiment_id) ON DELETE CASCADE,
                    case_id TEXT NOT NULL,
                    first_drift_layer TEXT NOT NULL,
                    severity TEXT NOT NULL DEFAULT 'medium',
                    layer_diffs_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                    score_delta_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                    summary_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_replay_diff_exp_case ON replay_diff (experiment_id, case_id);
                """
            )
        self._ready = True

    def create_case(
        self,
        *,
        trace_id: str,
        run_id: str,
        tenant_id: str,
        user_id: str,
        actor_type: str,
        channel: str,
        input_json: Dict[str, Any],
        expected_json: Optional[Dict[str, Any]] = None,
        env_json: Optional[Dict[str, Any]] = None,
        scenario_tags: Optional[List[str]] = None,
    ) -> str:
        self.ensure_schema()
        case_id = f"rcase_{uuid.uuid4().hex[:16]}"
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO replay_case (
                    case_id, trace_id, run_id, tenant_id, user_id, actor_type, channel,
                    scenario_tags, input_json, expected_json, env_json, schema_version, layer_spec_version
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s);
                """,
                (
                    case_id,
                    trace_id,
                    run_id,
                    tenant_id,
                    user_id,
                    actor_type,
                    channel,
                    _safe_json(scenario_tags or []),
                    _safe_json(input_json),
                    _safe_json(expected_json or {}),
                    _safe_json(env_json or {}),
                    self.cfg.schema_version,
                    self.cfg.layer_spec_version,
                ),
            )
        return case_id

    def save_snapshots(self, *, case_id: str, layers: List[Dict[str, Any]]) -> None:
        self.ensure_schema()
        now_ms = time.time() * 1000.0
        with self._conn() as conn, conn.cursor() as cur:
            for idx, layer in enumerate(layers, start=1):
                snapshot_id = f"rsnap_{uuid.uuid4().hex[:16]}"
                cur.execute(
                    """
                    INSERT INTO replay_snapshot (
                        snapshot_id, case_id, layer_code, seq, status, error_code, degrade_reason,
                        config_fingerprint, input_json, output_json, decision_json, params_json, metrics_json,
                        latency_ms
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s
                    );
                    """,
                    (
                        snapshot_id,
                        case_id,
                        str(layer.get("layer_code", f"LX_{idx}")),
                        idx,
                        str(layer.get("status", "ok")),
                        str(layer.get("error_code", "")) or None,
                        str(layer.get("degrade_reason", "")) or None,
                        str(layer.get("config_fingerprint", "")) or None,
                        _safe_json(layer.get("input_json", {})),
                        _safe_json(layer.get("output_json", {})),
                        _safe_json(layer.get("decision_json", {})),
                        _safe_json(layer.get("params_json", {})),
                        _safe_json(layer.get("metrics_json", {"captured_at_ms": round(now_ms, 2)})),
                        float(layer.get("latency_ms", 0.0) or 0.0),
                    ),
                )

    def create_experiment(
        self,
        *,
        name: str,
        mode: str,
        baseline_ref: str,
        candidate_ref: str,
        global_params_json: Optional[Dict[str, Any]] = None,
        meta_json: Optional[Dict[str, Any]] = None,
    ) -> str:
        self.ensure_schema()
        experiment_id = f"rexp_{uuid.uuid4().hex[:16]}"
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO replay_experiment (
                    experiment_id, name, mode, baseline_ref, candidate_ref, global_params_json, meta_json, schema_version
                ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s);
                """,
                (
                    experiment_id,
                    name,
                    mode,
                    baseline_ref or None,
                    candidate_ref or None,
                    _safe_json(global_params_json or {}),
                    _safe_json(meta_json or {}),
                    self.cfg.schema_version,
                ),
            )
        return experiment_id

    def save_diff(
        self,
        *,
        experiment_id: str,
        case_id: str,
        first_drift_layer: str,
        layer_diffs_json: Dict[str, Any],
        score_delta_json: Dict[str, Any],
        summary_json: Dict[str, Any],
        severity: str = "medium",
    ) -> str:
        self.ensure_schema()
        diff_id = f"rdiff_{uuid.uuid4().hex[:16]}"
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO replay_diff (
                    diff_id, experiment_id, case_id, first_drift_layer, severity,
                    layer_diffs_json, score_delta_json, summary_json
                ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb);
                """,
                (
                    diff_id,
                    experiment_id,
                    case_id,
                    first_drift_layer,
                    severity,
                    _safe_json(layer_diffs_json),
                    _safe_json(score_delta_json),
                    _safe_json(summary_json),
                ),
            )
        return diff_id

    def list_cases(self, *, limit: int = 200, run_id: str = "", tenant_id: str = "") -> List[Dict[str, Any]]:
        self.ensure_schema()
        sql = """
            SELECT case_id, trace_id, run_id, tenant_id, user_id, actor_type, channel, scenario_tags, input_json, env_json, created_at
            FROM replay_case
            WHERE (%s = '' OR run_id = %s)
              AND (%s = '' OR tenant_id = %s)
            ORDER BY created_at DESC
            LIMIT %s;
        """
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(sql, (run_id, run_id, tenant_id, tenant_id, max(1, limit)))
            rows = cur.fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "case_id": str(r[0]),
                    "trace_id": str(r[1] or ""),
                    "run_id": str(r[2] or ""),
                    "tenant_id": str(r[3] or ""),
                    "user_id": str(r[4] or ""),
                    "actor_type": str(r[5] or ""),
                    "channel": str(r[6] or ""),
                    "scenario_tags": r[7] if isinstance(r[7], list) else [],
                    "input_json": r[8] if isinstance(r[8], dict) else {},
                    "env_json": r[9] if isinstance(r[9], dict) else {},
                    "created_at": str(r[10]),
                }
            )
        return out

    def get_case_snapshots(self, case_id: str) -> List[Dict[str, Any]]:
        self.ensure_schema()
        sql = """
            SELECT layer_code, seq, status, error_code, degrade_reason, config_fingerprint,
                   input_json, output_json, decision_json, params_json, metrics_json, latency_ms
            FROM replay_snapshot
            WHERE case_id = %s
            ORDER BY seq ASC;
        """
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(sql, (case_id,))
            rows = cur.fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "layer_code": str(r[0]),
                    "seq": int(r[1]),
                    "status": str(r[2] or "ok"),
                    "error_code": str(r[3] or ""),
                    "degrade_reason": str(r[4] or ""),
                    "config_fingerprint": str(r[5] or ""),
                    "input_json": r[6] if isinstance(r[6], dict) else {},
                    "output_json": r[7] if isinstance(r[7], dict) else {},
                    "decision_json": r[8] if isinstance(r[8], dict) else {},
                    "params_json": r[9] if isinstance(r[9], dict) else {},
                    "metrics_json": r[10] if isinstance(r[10], dict) else {},
                    "latency_ms": float(r[11] or 0.0),
                }
            )
        return out


REPLAY_STORE = LayeredReplayStore()

