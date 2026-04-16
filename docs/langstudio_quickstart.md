# LangStudio 接入与使用

本项目已接入 LangGraph Studio（LangStudio）配置，可直接可视化 `chat_workflow` 的执行路径。

## 1. 安装依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2. 校验配置

```bash
langgraph validate --config langgraph.json
```

如果输出 `Configuration is valid`，说明配置可用。

## 3. 启动 LangStudio 开发服务

```bash
langgraph dev --config langgraph.json --port 2024
```

说明：
- `langgraph.json` 已声明图入口：`app/graph/workflows/minimal_chat.py:WORKFLOW`
- 环境变量来源：项目根目录 `.env`

### 3.1 代理环境下推荐启动方式（避免 403）

如果本机开了全局代理，Studio 节点内请求可能报 `ProxyError('403 Forbidden')`。  
推荐使用项目脚本启动（会自动清理代理变量）：

```bash
./scripts/start_langgraph_dev_no_proxy.sh 2024
```

## 4. 在 UI 中运行并查看路径

在 Studio 里选择 `chat_workflow`，输入示例：

```json
{
  "trace_id": "trace_ui_demo_01",
  "event_id": "evt_ui_demo_01",
  "conversation_id": "sess_ui_demo_01",
  "thread_id": "sess_ui_demo_01",
  "user_id": "u_demo",
  "tenant_id": "demo",
  "actor_type": "agent",
  "channel": "web",
  "query": "客户反馈商品破损，申请退款并要求人工审核",
  "history": [],
  "memory_enabled": true,
  "action_mode": "auto",
  "rewind_stage": "",
  "human_decision": {}
}
```

你会在 UI 中看到节点链路（如 `cache_lookup -> memory_read -> ...`）和分支走向。

## 5. 常见问题

- 端口冲突：改用 `--port 2025`（或其他空闲端口）
- `.env` 未生效：确认 `langgraph.json` 的 `"env": "./.env"` 未被改动
- 看不到断点恢复路径：使用 `action_mode=continue/rewind` 并传入有效 `resume_checkpoint_id`

## 6. /chat 一键同意/退回（同一 run_id）

已支持简化控制：在有 `run_id` 的前提下，仅传 `human_decision.decision` 也可自动推断恢复点。

### 6.1 触发人工闸门

```bash
curl -s 'http://127.0.0.1:8000/chat' \
  -H 'Content-Type: application/json' \
  -d '{
    "conversation_id":"sess_ui_demo_click_01",
    "user_id":"u_demo",
    "tenant_id":"demo",
    "actor_type":"agent",
    "channel":"web",
    "query":"客户反馈商品破损，申请退款并要求人工审核",
    "history":[]
  }'
```

记录返回中的 `run_id`。

### 6.2 同意继续（无需 action_mode / checkpoint）

```bash
curl -s 'http://127.0.0.1:8000/chat' \
  -H 'Content-Type: application/json' \
  -d '{
    "conversation_id":"sess_ui_demo_click_01",
    "user_id":"u_demo",
    "tenant_id":"demo",
    "actor_type":"agent",
    "channel":"web",
    "query":"同意执行",
    "history":[],
    "run_id":"<上一步run_id>",
    "human_decision":{"decision":"approve"}
  }'
```

### 6.3 退回重跑（默认回退到 facts）

```bash
curl -s 'http://127.0.0.1:8000/chat' \
  -H 'Content-Type: application/json' \
  -d '{
    "conversation_id":"sess_ui_demo_click_01",
    "user_id":"u_demo",
    "tenant_id":"demo",
    "actor_type":"agent",
    "channel":"web",
    "query":"退回重跑",
    "history":[],
    "run_id":"<同一个run_id>",
    "human_decision":{"decision":"退回"}
  }'
```

说明：若提供明确 `rewind_stage/target_checkpoint_id`，仍会优先按显式参数执行。
