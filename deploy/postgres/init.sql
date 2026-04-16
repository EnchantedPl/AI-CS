CREATE EXTENSION IF NOT EXISTS vector;

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
    schema_version TEXT NOT NULL DEFAULT 'v1',
    layer_spec_version TEXT NOT NULL DEFAULT 'v1_5layer',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_replay_case_run_id ON replay_case (run_id);
CREATE INDEX IF NOT EXISTS idx_replay_case_created_at ON replay_case (created_at DESC);

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
CREATE INDEX IF NOT EXISTS idx_replay_snapshot_case_layer ON replay_snapshot (case_id, layer_code, seq);

CREATE TABLE IF NOT EXISTS replay_experiment (
    experiment_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    mode TEXT NOT NULL,
    baseline_ref TEXT,
    candidate_ref TEXT,
    global_params_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    meta_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    schema_version TEXT NOT NULL DEFAULT 'v1',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

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
CREATE INDEX IF NOT EXISTS idx_replay_diff_exp_case ON replay_diff (experiment_id, case_id);
