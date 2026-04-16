# Complex Aftersales Rationality Evaluation

- `aftersales_complex_rationality_score`: **100.0**

| query | score | action1 | action2 | loop_sequence_ok | completion_ok |
|---|---:|---|---|---|---|
| 商品破损，要求退款 | 100.0 | approval_submit_mcp | refund_submit_mcp | True | True |
| 收货后发现商品损坏，申请退款 | 100.0 | approval_submit_mcp | refund_submit_mcp | True | True |

## Rule

- 目标链路：`action1 -> 同意 -> action2 -> 同意 -> 结束`。
- 该评测依赖 `DEMO_FIXED_SCENARIO=true` 的固定复杂售后 mock 场景。
