# refund_policy_skill

用于判断退款资格、风险等级、是否需要人工审批。

优先使用事实字段：
- `days_since_delivery`
- `damage_reported`
- `amount`
- `open_ticket`

输出要求：
- 给出 `eligible/manual_required/risk_level/reasons/confidence`
