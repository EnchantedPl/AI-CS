# 项目详解（流程、目录与脚本）

> 本文是 README 的展开版：面向希望快速「点开代码」的面试官或协作者。

## 端到端主路径（概念）

1. **接入**：鉴权与会话字段、`x-trace-id`、限流与并发闸（`app/api/routes_chat.py`）。
2. **策略**：路由分桶、优先级、降级（`app/stability/runtime_policy.py`）。
3. **编排**：LangGraph 风格工作流（`app/graph/workflows/minimal_chat.py`）：意图分支、缓存、记忆、RAG、复杂售后 **facts → policy → action**、Checkpoint / HITL。
4. **上下文**：在 token 预算内拼装 policy / RAG / memory / tool（`app/memory/context_builder.py`）。
5. **世界接口**：混合检索（`app/rag/hybrid_retriever.py`）、MCP mock（`app/mcp_mock/`）、技能（`app/skills/`）。
6. **终答与写回**：响应、状态写回、指标与（可选）LangSmith。

## 目录速查

| 路径 | 说明 |
|------|------|
| `app/main.py` | FastAPI 入口、访问日志、`/metrics`、`/health` |
| `app/api/routes_chat.py` | `/chat` 主 API、大量 Prometheus 业务指标 |
| `app/graph/workflows/minimal_chat.py` | 核心工作流与节点 |
| `app/rag/` | 混合检索、ingest 相关 |
| `app/memory/` | 记忆存储、上下文拼装 |
| `app/cache/` | 缓存与 embedding 运行时 |
| `app/stability/` | 运行时策略与降级 |
| `deploy/docker-compose.yml` | pgvector、redis、ollama、prometheus、grafana |
| `deploy/grafana/dashboards/` | 仪表盘 JSON |
| `scripts/` | 回放、对比、灌数、模式切换等 |

## 脚本索引（高频）

| 脚本 | 用途 |
|------|------|
| `scripts/switch_mode.py` | 切换 local/cloud/mix |
| `scripts/rag_ingest.py` | 知识库入库 |
| `scripts/run_layered_replay.py` | 分层回放采集 |
| `scripts/layered_replay_report.py` | 归因 Markdown |
| `scripts/replay_compare.py` | 检索/全链路对比指标 |
| `scripts/verify_layered_observability.py` | 造流量、验证 `/metrics` 分层字段 |

## 配置

- 全局：`app/core/config.py`、`.env.example`
- 演示：`DEMO_FIXED_SCENARIO`、各类 `DEGRADE_*`、LangSmith 开关
