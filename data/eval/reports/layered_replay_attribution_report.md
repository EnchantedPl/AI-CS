# Layered Replay Attribution Report

## Experiment Metadata

- observe_experiment_id: `rexp_494034c102804939`
- isolate_experiment_id: `rexp_e1c01975fad44d3f`
- baseline_tag: `demo_base`
- candidate_tag: `demo_cand`
- isolate_target_layer: `L3`

## Drift Distribution

| mode | cases | top_drift_layer | top_drift_count | top_drift_ratio | distribution |
|---|---:|---|---:|---:|---|
| observe | 4 | L4 | 2 | 50.0% | {"L4": 2, "none": 2} |
| isolate | 4 | L4 | 2 | 50.0% | {"L4": 2, "none": 2} |

## Layer Match Score

| mode | route_match | cache_match | rag_match | final_match |
|---|---:|---:|---:|---:|
| observe | 1.0 | 1.0 | 1.0 | 0.5 |
| isolate | 1.0 | 1.0 | 1.0 | 0.5 |

## Parameter Suggestions

- observe 模式主漂移层为 `L4`（50.0%），建议优先调该层参数，避免跨层盲调。
- 优先参数：LLM_MODE / LOCAL_LLM_MODEL / CLOUD_LLM_MODEL, PROMPT_VERSION / POLICY_VERSION, LLM_MAX_CONTEXT_CHARS, CONTEXT_TOTAL_BUDGET_CHARS。
- isolate 模式主漂移层为 `L4`（50.0%），说明在屏蔽上游影响后该层/下游仍是主要变化来源。
- 最终答案一致率偏低，优先检查模型版本、上下文预算和 prompt/policy 版本一致性。

## Output Field Glossary (observe / isolate)

- `experiment_id`: 本次 compare 的唯一标识，可关联数据库中的 `replay_experiment/replay_diff`。
- `mode`: `observe` 或 `isolate`。
- `target_layer`: 仅 `isolate` 有值，表示从该层开始看漂移（上游视为冻结影响）。
- `baseline_tag` / `candidate_tag`: 两次采集批次标签，用于对比。
- `cases`: 本次成功匹配并比较的 query 数量。
- `drift_distribution`: 首漂移层分布统计，`none` 表示该样本四层都一致。
- `rows[].query`: 样本 query 文本。
- `rows[].first_drift_layer`: 该样本第一处漂移层（或 `none`）。
- `rows[].route_match/cache_match/rag_match/final_match`: 各层是否一致（1=一致，0=不一致）。
