# 分层回放：详细操作指南

面向「改 prompt / 改检索 / 改路由」后的**回归与归因**，与 [`layered-replay.md`](layered-replay.md) 配合使用：前者讲原理与读报告，本文讲**怎么跑**。

## 1. 前置条件

- 已配置 `.env`（数据库、LLM/Embedding 模式与 Key）。
- 建议先有一份 **baseline** 快照（升级前的 `layered_replay_report.*.json` 或等价产物）。

## 2. 采集分层快照

在项目根目录执行（参数以 `--help` 为准）：

```bash
python scripts/run_layered_replay.py --help
```

典型流程：

1. 在 **candidate** 环境完成代码/配置变更。
2. 运行 `run_layered_replay.py` 生成与 baseline 可对比的 JSON（`observe` 或 `isolate` 模式由脚本参数决定）。
3. 保留产物路径，便于与 baseline 做 diff。

## 3. 生成归因 Markdown

```bash
python scripts/layered_replay_report.py
```

输出通常包含：`first_drift_layer`、各层 match 统计、逐条 diff 线索。

## 4. 检索质量对比（可选）

```bash
python scripts/replay_compare.py --help
```

用于 MRR/NDCG 等与路由无关的**检索层**对比。

## 5. 调参闭环（建议顺序）

1. 读报告中的 **first_drift_layer**，只在该层改一个变量。
2. 重新跑 `run_layered_replay.py` → `layered_replay_report.py`。
3. 确认 `first_drift_layer` 消失或下移，再处理下一层。
4. 将同一评测集纳入 CI 或发布前门禁（可选）。

## 6. 产物路径参考

- 示例归因报告：`data/eval/reports/layered_replay_attribution_report.md`
- 失败样本、对比 CSV 等以脚本输出为准。
