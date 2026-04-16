# 可观测性、Eval 与 Tracing

## 运行时观测对象（精选）

| 观测项 | 说明 |
|--------|------|
| 成功率 / 错误码 | HTTP 与业务状态（如 `NEED_HUMAN`） |
| **p95 延迟** | 端到端与关键节点（Histogram） |
| 限流 / 降级 | `ai_cs_budget_limit_*`、`ai_cs_degrade_level_*` 等 |
| RAG 耗时 | `ai_cs_rag_timing_seconds` |
| LLM 调用 | `ai_cs_llm_call_total`、延迟直方图 |
| 缓存 | 命中与绕过（`ai_cs_cache_*`） |
| 工作流阶段 | 节点 trace、resume/continue、checkpoint |

完整指标以 `GET /metrics`（默认 `METRICS_PATH=/metrics`）为准。

## 实现方式

| 类型 | 位置 / 说明 |
|------|----------------|
| Prometheus | `prometheus-client`；聚合在 `app/api/routes_chat.py`（业务）与 `app/main.py`（HTTP 壳） |
| Grafana 仪表盘 | `deploy/grafana/dashboards/memory_observability.json`、`core_ops_business_kpi.json`；Provisioning：`deploy/grafana/provisioning/` |
| 结构化访问日志 | `app/main.py` 中间件：`type=http_access`，字段含 `trace_id`、`latency_ms` |
| LangSmith（可选） | `.env` 中 `ENABLE_LANGSMITH`、`LANGSMITH_*`；与 LangChain/LangGraph 调用链集成 |

本地查看：

```bash
docker compose -f deploy/docker-compose.yml --profile obs up -d
curl -s http://127.0.0.1:8000/metrics | head
```

## 与离线评测的关系

- **运行时指标**：反映线上健康度、成本与延迟（Grafana）。
- **离线 replay / 分层归因**：反映版本变更后质量漂移位置（`scripts/run_layered_replay.py`、`scripts/layered_replay_report.py`）。

二者互补：前者回答「是否稳定」，后者回答「为什么变差」。

## LangSmith Tracing（`/chat`）

**实现位置**：`app/observability/langsmith_tracing.py`（`tracing_context` + `LANGCHAIN_TRACING_V2`）；`app/api/routes_chat.py` 中 `chat_workflow` 使用 `@traceable`，**与 `tracing_context` 同一线程执行**（避免 `asyncio.to_thread` 导致 Run Tree 丢失）。

**排查「Tracing 无数据」**：

1. `.env` 中 `ENABLE_LANGSMITH=true`，且 `LANGSMITH_API_KEY` 有效（可选同步设置 `LANGCHAIN_API_KEY`，代码会在启用时自动补齐）。
2. **LangSmith 控制台**：若出现 **Billing / rate limit / monthly traces quota** 等提示，新写入可能被拒绝或延迟，Tracing 列表为空属正常。
3. **项目**：`LANGSMITH_PROJECT` 与控制台所选项目一致；时间范围设为 **Last 7 days** 等。
4. 响应中 `debug.langsmith_meta_write_ok`：若为 `false`，多为当次未建立活跃 run（或配额问题）。

## 访问日志 JSON 字段说明

每条请求一行 JSON（`app/main.py`）：

| 字段 | 含义 |
|------|------|
| `type` | 固定 `http_access` |
| `trace_id` | 与响应头 `x-trace-id` 一致，可对齐 Tracing |
| `method` / `path` | HTTP 方法与路径 |
| `status_code` | HTTP 状态码 |
| `latency_ms` | 请求耗时（毫秒） |

异常路径另有 `type=unhandled_exception` 日志，含 `trace_id` 与 `error`。

## 响应体 `debug`（概要）

`/chat` 响应中 `debug` 通常包含多段（具体随路由变化）：路由与 RAG、上下文与 token、工作流与工具 trace、guardrail 等。字段较多时建议只截 **run_id、route_target、关键 stage** 用于面试说明；完整结构见 [`project-detail.md`](project-detail.md)。
