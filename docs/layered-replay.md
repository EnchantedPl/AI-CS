# 归因与调参（分层回放）

## 原理（简述）

把一次回答拆成可对比的**层次**（路由 / 缓存 / RAG / 终态等）。发布新版本（prompt、检索器、模型、策略）后，对比 baseline 与 candidate，用 **first_drift_layer** 先定位「哪一层开始不一致」，避免跨层盲调。

## 实现方式（简述）

- **采集**：`scripts/run_layered_replay.py` 对同一批输入跑 baseline/candidate，写出分层快照 JSON。
- **归因**：`scripts/layered_replay_report.py` 汇总首漂移层与各层 match/diff。
- **检索专项**：`scripts/replay_compare.py` 输出 MRR/NDCG 等（与路由层正交）。

**逐步命令与调参闭环**见 **[`layered-replay-guide.md`](layered-replay-guide.md)**。

## 常用命令

```bash
python scripts/run_layered_replay.py --help
python scripts/layered_replay_report.py --help
python scripts/replay_compare.py --help
```

示例报告：`data/eval/reports/layered_replay_attribution_report.md`。

## 报告怎么读

1. **first_drift_layer**：从哪一层开始与 baseline 不一致。
2. **各层 match / diff 汇总**：路由是否飘、缓存是否误命中、检索是否空、最终答案是否偏离。
3. 结合 **版本号**（`model_version`、`prompt_template_version`、`retriever_version` 等）做变更关联。

## 典型调参路径

| 漂移层 | 可能动作 |
|--------|----------|
| 路由 | 调意图阈值、补充路由特征、收紧 risk 规则 |
| 缓存 | 调 TTL、语义阈值、缓存键维度 |
| RAG | 调 TopK、混合权重、rerank、chunk 策略 |
| 终态 | 调 prompt、输出约束、工具策略 |

原则：**单层单变量**优先；改完后用同一套评测集回归。
