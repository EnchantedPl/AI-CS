# 快速开始

## 环境要求

- Python **3.11+**
- Docker / Docker Compose（Postgres **pgvector**、Redis；可选 Prometheus/Grafana）
- 可选：本地 [Ollama](https://ollama.com/)（`LLM_MODE=local` 时使用）

## 安装与配置

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# 填写 LLM / Embedding API Key（云模式）等；勿提交真实密钥
```

## 启动依赖

```bash
docker compose -f deploy/docker-compose.yml up -d pgvector redis
```

可选观测栈（Prometheus + Grafana，profile `obs`）：

```bash
docker compose -f deploy/docker-compose.yml --profile obs up -d
```

- Grafana 默认：`http://127.0.0.1:3000`（见 `deploy/docker-compose.yml` 中 `GF_SECURITY_ADMIN_*`）
- Prometheus：`http://127.0.0.1:9090`

## 运行模式与知识库

```bash
python scripts/switch_mode.py --mode cloud   # 或 local / mix
python scripts/rag_ingest.py --target-chunks 2000
```

## 启动 API

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
# 或：./scripts/start_api_no_proxy.sh 8000
```

## 健康检查

```bash
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/debug/llm-health
```

## Demo UI

浏览器打开：`http://127.0.0.1:8000/demo/agent-console`

## 指标（Prometheus）

```bash
curl -s http://127.0.0.1:8000/metrics | head
```

（默认路径由 `METRICS_PATH` 控制，见 `.env.example`。）

更多排障与 Rerank 对比见根目录 [`demo_runbook.md`](../demo_runbook.md)。
