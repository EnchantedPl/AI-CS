# After Sales Complex 验证步骤

## 1) 触发复杂售后场景

```bash
curl --location 'http://127.0.0.1:8000/chat' \
  --header 'Content-Type: application/json' \
  --data '{
    "conversation_id":"conv_af_complex_01",
    "user_id":"u_demo",
    "tenant_id":"demo",
    "actor_type":"user",
    "channel":"web",
    "query":"这是一个复杂售后场景：订单损坏并且我要工单升级，请人工审核",
    "history":[],
    "memory_enabled": true
  }'
```

预期：

- `route_target=aftersales`
- `aftersales_mode=complex`
- `debug.aftersales_skill.policy` 存在
- `node_trace` 包含 `aftersales_subgraph`
- 如 `manual_required=true`，则 `handoff_required=true`

## 2) 查看 checkpoint 列表

```bash
curl --location 'http://127.0.0.1:8000/debug/workflow/checkpoints' \
  --header 'Content-Type: application/json' \
  --data '{
    "thread_id":"conv_af_complex_01",
    "limit":20
  }'
```

预期：

- 返回多个 checkpoint，按节点顺序可看到 route/cache/memory/tools/skill/handoff 等阶段

## 3) 读取某个 checkpoint

```bash
curl --location 'http://127.0.0.1:8000/debug/workflow/checkpoint/get' \
  --header 'Content-Type: application/json' \
  --data '{
    "checkpoint_id":"<上一步返回的 checkpoint_id>"
  }'
```

预期：

- `found=true`
- `item.state` 中包含该节点时的 workflow state 快照

## 4) 从 checkpoint 恢复执行（time-travel 基础）

```bash
curl --location 'http://127.0.0.1:8000/chat' \
  --header 'Content-Type: application/json' \
  --data '{
    "conversation_id":"conv_af_complex_01",
    "user_id":"u_demo",
    "tenant_id":"demo",
    "actor_type":"user",
    "channel":"web",
    "query":"继续这个复杂售后流程，给我最终执行建议",
    "history":[],
    "memory_enabled": true,
    "human_decision":{"decision":"approve","operator_id":"agent_001"},
    "resume_checkpoint_id":"<checkpoint_id>"
  }'
```

预期：

- `debug.resumed_from_checkpoint` 存在
- 流程继续执行并输出回复

## 5) 验证 context 分层组装（prompt caching 友好）

在 `/chat` 响应里检查：

- `debug.memory.context_debug.fixed_prefix_used_chars`
- `debug.memory.context_debug.tool_used_chars`
- `debug.memory.context_debug.rag_used_chars`
- `debug.memory.context_debug.memory_used_chars`

预期：

- 固定块（system/scenario）前置
- 动态块（tool/rag/memory/query）后置
- 各层在预算内裁剪
