# Demo Runbook

## 1) Start local dependencies

```bash
docker compose -f deploy/docker-compose.yml up -d pgvector redis
```

## 2) Choose runtime mode

### Local all-in (local LLM + local embedding)

```bash
".venv/bin/python" scripts/switch_mode.py --mode local
```

### Cloud all-in (cloud LLM + cloud embedding)

```bash
".venv/bin/python" scripts/switch_mode.py --mode cloud
```

### Hybrid demo-fast (local LLM + cloud embedding)

```bash
".venv/bin/python" scripts/switch_mode.py --mode mix
```

## 3) Ingest knowledge base

### Keep existing vectors and update current mode vectors

```bash
".venv/bin/python" scripts/rag_ingest.py --target-chunks 2000
```

### Full reset (use with caution)

```bash
".venv/bin/python" scripts/rag_ingest.py --reset-table --target-chunks 2000
```

## 4) Start API

```bash
".venv/bin/python" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## 5) Health checks

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/debug/llm-health
```

Expected:
- `ok=true`
- runtime mode matches switch script choice

## 6) Chat test

```bash
curl -X POST "http://127.0.0.1:8000/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id":"u1",
    "tenant_id":"demo",
    "actor_type":"user",
    "channel":"web",
    "query":"花吃了那女孩由几段故事组成？",
    "history":[]
  }'
```

Check fields in response:
- `debug.llm.model` (which LLM actually served)
- `debug.rag.embedding_mode`
- `debug.rag.embedding_model`
- `debug.rag.vector_column`

## 7) Troubleshooting

- `connection refused 5433`: pgvector not up, restart compose.
- `ollama command not found`: install Ollama app and ensure CLI in PATH.
- embedding batch size errors on cloud: keep `EMBEDDING_BATCH_SIZE<=10`.
- first local embedding request is slow: model cold load, retry once.

## 8) Rerank Comparison (table report)

Build gold eval set from current KB:

```bash
".venv/bin/python" scripts/build_eval_set.py --limit 500 --align-chunk-gold --chunk-gold-topn 3
```

Run baseline vs rerank and export report:

```bash
".venv/bin/python" scripts/replay_compare.py \
  --query-jsonl data/eval/eval_set.jsonl \
  --mode mix \
  --limit 200 \
  --retrieval-only \
  --rag-vector-topk 20 \
  --rag-keyword-topk 20 \
  --rag-topk 5 \
  --rerank-candidates 30 \
  --rerank-topk 2 \
  --pass-threshold-ndcg5 0.01 \
  --pass-threshold-mrr 0.005 \
  --max-p95-rt-increase-ms 50
```

Outputs:
- `data/eval/reports/replay_compare_rows.csv`
- `data/eval/reports/replay_compare_report.md`
