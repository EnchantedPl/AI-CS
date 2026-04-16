# Runbook（运维 / 排障）

## 常见问题

| 现象 | 可能原因 | 处理 |
|------|----------|------|
| `connection refused` 连 **5433** | pgvector 未启动 | `docker compose -f deploy/docker-compose.yml up -d pgvector` |
| 首次本地 Embedding 很慢 | 模型冷启动 | 等待一次或设 `PRELOAD_LOCAL_EMBEDDING_ON_STARTUP=true`（见 `.env.example`） |
| 云侧 batch 报错 | 批次过大 | 将 `EMBEDDING_BATCH_SIZE` 控制在较小值（如 ≤10） |
| `/metrics` 为空或 200 但无序列 | `ENABLE_PROMETHEUS` 关闭 | 在 `.env` 中开启并重启进程 |
| LangSmith 429 / 限流 | 采样或 QPS 过高 | 降低演示并发，或暂时 `ENABLE_LANGSMITH=false` |

## 降级与韧性（概念 → 配置入口）

运行时策略集中在 `app/stability/runtime_policy.py`，由环境变量驱动（见 `.env.example` 中 `DEGRADE_*`、`WORKFLOW_TIMEOUT_SECONDS` 等）。典型手段：

- **L0/L1/L2 降级**：在依赖异常或负载过高时收紧 RAG、工具、模型路径。
- **超时 / 重试**：LLM、Embedding、工作流层级超时与退避。
- **限流**：RPM/TPM 与并发闸（Redis + Lua，入口在 `app/api/routes_chat.py` 一带）。

排障时优先对照：**trace_id**（响应头 `x-trace-id`）→ 结构化访问日志 → Prometheus 分层指标 →（可选）LangSmith 项目。

## 故障处理建议流程

1. 确认依赖健康：`/health`、`/debug/llm-health`、Postgres/Redis 容器状态。
2. 抽取一条失败请求的 `trace_id`，在日志与（如有）LangSmith 中对齐同一时间线。
3. 看 Grafana：`deploy/grafana/dashboards/memory_observability.json`、`core_ops_business_kpi.json` 是否出现错误率、超时、降级计数异常。
4. 若为质量回归：跑离线分层回放（见 [`layered-replay.md`](layered-replay.md)），避免跨层盲调。

## 演示/截图专用开关

- `DEMO_FIXED_SCENARIO`：固定演示路径，截图稳定；关闭则更贴近真实链路波动。
- `ENABLE_LANGSMITH`：Tracing 开关；大规模压测时可关。

详见 `.env.example` 注释。
