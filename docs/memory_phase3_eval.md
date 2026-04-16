# Memory Phase3 评估与监控

## 1) 回放报告：Memory ON vs OFF

运行：

```bash
python3 scripts/replay_memory_compare.py \
  --query-jsonl data/eval/eval_set.jsonl \
  --limit 200 \
  --output-dir data/eval/reports
```

产物：

- `data/eval/reports/replay_memory_compare_rows.csv`
- `data/eval/reports/replay_memory_compare_report.md`

核心指标：

- `memory_hit_rate`
- `effective_injection_rate`
- `hallucination_proxy_rate`（代理指标）
- `recovery_success_rate`（代理指标）
- `avg/p95_latency_ms`
- `avg_llm_context_chars`（成本代理）
- `citation_hit@1/@k`, `MRR`, `nDCG@5`（有 gold 时）

## 2) 实时监控：Grafana Dashboard

模板：

- `deploy/grafana/dashboards/memory_observability.json`

覆盖面板：

- Memory Hit Rate (ON)
- Effective Injection Rate (ON)
- Hallucination Proxy Rate (ON)
- Recovery Success Rate (ON)
- P95 Latency ON vs OFF
- Selected Memory Count
- LLM Context Chars ON vs OFF
- Traffic Split ON vs OFF

二期新增 Prometheus 指标（已埋点）：

- `ai_cs_memory_admission_total{memory_enabled,decision,reason}`
- `ai_cs_memory_admission_precision_proxy_total{memory_enabled,effective}`
- `ai_cs_memory_noise_proxy_total{memory_enabled,reason}`
- `ai_cs_memory_freshness_seconds_bucket{memory_enabled,memory_type,le}`

推荐 Grafana PromQL（可直接新建面板）：

- Admission 通过率（ON）  
  `sum(rate(ai_cs_memory_admission_total{memory_enabled="true",decision="accepted"}[5m])) / clamp_min(sum(rate(ai_cs_memory_admission_total{memory_enabled="true"}[5m])), 1e-6)`
- Admission Precision Proxy（ON）  
  `sum(rate(ai_cs_memory_admission_precision_proxy_total{memory_enabled="true",effective="true"}[5m])) / clamp_min(sum(rate(ai_cs_memory_admission_precision_proxy_total{memory_enabled="true"}[5m])), 1e-6)`
- Noise Proxy Rate（ON）  
  `sum(rate(ai_cs_memory_noise_proxy_total{memory_enabled="true"}[5m])) / clamp_min(sum(rate(ai_cs_memory_requests_total{memory_enabled="true"}[5m])), 1e-6)`
- Freshness p50/p95（ON）  
  `histogram_quantile(0.5, sum(rate(ai_cs_memory_freshness_seconds_bucket{memory_enabled="true"}[5m])) by (le))`  
  `histogram_quantile(0.95, sum(rate(ai_cs_memory_freshness_seconds_bucket{memory_enabled="true"}[5m])) by (le))`

## 3) /chat 实验开关

`/chat` 支持传入：

```json
{
  "memory_enabled": true
}
```

用于在线灰度或对照实验（on/off 同口径）。

## 3.1) 快速造 ON/OFF 对照流量（推荐）

```bash
python3 scripts/generate_memory_ab_traffic.py \
  --url http://127.0.0.1:8000/chat \
  --count 300 \
  --concurrency 12 \
  --timeout-seconds 60 \
  --retries 1 \
  --batch-size 120 \
  --batch-sleep-ms 800 \
  --pattern journey \
  --journey-steps 6 \
  --session-pool-size 12
```

说明：

- 默认会同时发 ON/OFF 成对请求（总请求数 = `count * 2`）。
- 建议优先用 `--pattern journey`：先写入偏好/事实，再追问回忆，更容易看到直观的 memory 提升效果。
- `session-pool-size` 越小，会话复用越高，更容易触发 memory hit / effective injection。
- 为了提速，脚本不会解析响应内容，只统计成功率与延迟。
- 建议先跑 200~500 对请求再看 Grafana，曲线更稳定。
- 如果本机资源紧张（本地模型/Embedding），优先降低 `--concurrency` 到 `8` 或提高 `--timeout-seconds` 到 `90`。

## 4) Grafana 结果怎么看

- **先看趋势**：`Hit Rate / Effective Injection / Hallucination Proxy / P95` 是否同时向好。
- **再看代价**：`avg_llm_context_chars` 与 `p95_latency` 是否在可接受范围。
- **最后看质量**：离线报告中的 `nDCG@5/MRR` 是否不下降。
- **判定建议**：若 ON 相对 OFF 满足“质量不降 + 幻觉代理不升 + p95 增量可控”，可扩大流量。
