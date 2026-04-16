from typing import Any, Dict, List, TypedDict


class ChatState(TypedDict):
    trace_id: str
    event_id: str
    user_id: str
    conversation_id: str
    thread_id: str
    tenant_id: str
    actor_type: str
    channel: str
    query: str
    history: List[Dict[str, Any]]
    route_target: str
    aftersales_mode: str
    intent_confidence: float
    served_by_cache: bool
    cache_result: Dict[str, Any]
    status: str
    handoff_required: bool
    answer: str
    citations: List[str]
    tool_result: Dict[str, Any]
    aftersales_tool_result: Dict[str, Any]
    aftersales_skill_result: Dict[str, Any]
    aftersales_agent_result: Dict[str, Any]
    rag_decision_result: Dict[str, Any]
    rag_result: Dict[str, Any]
    memory_result: Dict[str, Any]
    memory_write_result: Dict[str, Any]
    context_result: Dict[str, Any]
    memory_enabled: bool
    human_decision: Dict[str, Any]
    action_mode: str
    rewind_stage: str
    current_stage: str
    stage_status: str
    stage_summary: str
    decision_basis: List[str]
    required_user_input: List[str]
    allowed_actions: List[str]
    rewind_stage_options: List[Dict[str, Any]]
    pending_action: Dict[str, Any]
    resume_next_node: str
    run_id: str
    wf_checkpoint_id: str
    wf_parent_checkpoint_id: str
    debug: Dict[str, Any]
    node_trace: List[str]

