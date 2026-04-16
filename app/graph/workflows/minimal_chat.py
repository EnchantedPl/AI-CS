import os
import uuid
import hashlib
import time
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import Any, Dict, List, Optional

from langgraph.graph import END, START, StateGraph

from app.cache.cache_orchestrator import CacheOrchestrator
from app.cache.key_builder import build_cache_keys
from app.cache.stage_result_cache import StageResultCache
from app.core.config import Settings
from app.demo.mock_scenarios import (
    demo_answer,
    demo_aftersales_action_plan,
    demo_product_tool,
    demo_rag_result,
    infer_demo_intent,
)
from app.graph.checkpoint_store import WorkflowCheckpointStore
from app.graph.state import ChatState
from app.mcp_mock.clients import approval_submit_mcp, refund_submit_mcp, ticket_upgrade_mcp
from app.memory.context_builder import build_context_with_budget
from app.memory.store import MemoryStore
from app.models.litellm_client import decide_aftersales_next_step, generate_answer_with_litellm
from app.observability.langsmith_tracing import set_trace_metadata, traceable
from app.rag.hybrid_retriever import RETRIEVER
from app.skills.runtime import progressive_skills
from app.skills.rag_decision_skill import decide_rag_plan
from app.skills.mock_aftersales_skills import evaluate_refund_policy, generate_aftersales_plan
from app.tools.mock_aftersales_tools import logistics_query_tool, order_query_tool, ticket_query_tool
from app.tools.mock_tool_call import run_mock_tool

settings = Settings.from_env()
CACHE_ORCHESTRATOR = CacheOrchestrator(settings)
MEMORY_STORE = MemoryStore()
CHECKPOINT_STORE = WorkflowCheckpointStore()
STAGE_RESULT_CACHE = StageResultCache(default_ttl_seconds=int(os.getenv("STAGE_CACHE_TTL_SECONDS", "600")))


def get_cache_orchestrator() -> CacheOrchestrator:
    return CACHE_ORCHESTRATOR


def get_checkpoint_store() -> WorkflowCheckpointStore:
    return CHECKPOINT_STORE


def _run_with_layer_control(
    *,
    fn,
    timeout_seconds: float,
    retries: int,
    backoff_seconds: float,
    default_value: Any,
) -> tuple[Any, Dict[str, Any]]:
    attempts = max(1, retries + 1)
    last_error: Optional[Exception] = None
    timeout_hit = False
    for attempt in range(1, attempts + 1):
        t_attempt = time.perf_counter()
        try:
            with ThreadPoolExecutor(max_workers=1) as ex:
                future = ex.submit(fn)
                value = future.result(timeout=max(0.05, timeout_seconds))
            return value, {
                "ok": True,
                "timeout": False,
                "error": "",
                "attempts": attempt,
                "retried": max(0, attempt - 1),
                "elapsed_ms": round((time.perf_counter() - t_attempt) * 1000.0, 3),
            }
        except FutureTimeoutError as exc:
            last_error = exc
            timeout_hit = True
        except Exception as exc:
            last_error = exc
        if attempt < attempts:
            time.sleep(max(0.0, backoff_seconds) * attempt)
    return default_value, {
        "ok": False,
        "timeout": timeout_hit,
        "error": str(last_error) if last_error is not None else "unknown_error",
        "attempts": attempts,
        "retried": max(0, attempts - 1),
        "elapsed_ms": 0.0,
    }


def _route_key(route_target: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", str(route_target or "default")).strip("_")
    return (normalized or "default").upper()


def _layer_timeout_budget(layer_name: str, route_target: str, default_timeout: float) -> float:
    route_key = _route_key(route_target)
    route_specific = os.getenv(f"{layer_name.upper()}_LAYER_TIMEOUT_SECONDS_{route_key}")
    if route_specific is not None and route_specific != "":
        return max(0.05, float(route_specific))
    return max(0.05, float(default_timeout))


def _layer_retry_budget(layer_name: str, route_target: str, default_retry: int) -> int:
    route_key = _route_key(route_target)
    route_specific = os.getenv(f"{layer_name.upper()}_LAYER_RETRY_{route_key}")
    if route_specific is not None and route_specific != "":
        return max(0, int(route_specific))
    return max(0, int(default_retry))


def _layer_backoff_budget(layer_name: str, route_target: str, default_backoff: float) -> float:
    route_key = _route_key(route_target)
    route_specific = os.getenv(f"{layer_name.upper()}_LAYER_RETRY_BACKOFF_SECONDS_{route_key}")
    if route_specific is not None and route_specific != "":
        return max(0.0, float(route_specific))
    return max(0.0, float(default_backoff))


def _thread_id(state: ChatState) -> str:
    return (
        state.get("thread_id")
        or state.get("conversation_id")
        or f"{state.get('tenant_id','')}:{state.get('user_id','')}:{state.get('channel','web')}"
    )


def _long_scope(tenant_id: str, user_id: str) -> str:
    return f"long:{tenant_id}:{user_id}"


def _l3_scope(tenant_id: str, user_id: str, conversation_id: str) -> str:
    return f"l3:{tenant_id}:{user_id}:{conversation_id}"


def _memory_enabled(state: ChatState) -> bool:
    return bool(state.get("memory_enabled", settings.enable_memory))


def _runtime_policy(state: ChatState) -> Dict[str, Any]:
    dbg = state.get("debug", {}) if isinstance(state.get("debug"), dict) else {}
    rp = dbg.get("runtime_policy", {}) if isinstance(dbg.get("runtime_policy"), dict) else {}
    return rp


def _degrade_level(state: ChatState) -> str:
    rp = _runtime_policy(state)
    level = str(rp.get("degrade_level", "L0") or "L0").upper()
    return level if level in {"L0", "L1", "L2"} else "L0"


def _chain_control(state: ChatState, layer: str) -> Dict[str, Any]:
    rp = _runtime_policy(state)
    cc = rp.get("chain_controls", {}) if isinstance(rp.get("chain_controls"), dict) else {}
    return cc.get(layer, {}) if isinstance(cc.get(layer), dict) else {}


def _stage_of_node(node_name: str) -> str:
    if node_name in {
        "route_intent",
        "feedback_gate",
        "intent_subgraph_entry",
        "cache_lookup",
        "memory_read",
        "tool_call",
        "aftersales_facts",
    }:
        return "facts"
    if node_name in {"aftersales_subgraph", "aftersales_policy"}:
        return "policy"
    if node_name in {
        "risk_handoff_subgraph",
        "aftersales_action",
        "handoff_decision",
        "memory_write",
        "cache_writeback",
    }:
        return "action"
    return "general"


def _set_stage(
    state: ChatState,
    *,
    stage: str,
    stage_status: str,
    summary: str = "",
    basis: Optional[List[str]] = None,
    required_input: Optional[List[str]] = None,
) -> None:
    state["current_stage"] = stage
    state["stage_status"] = stage_status
    if summary:
        state["stage_summary"] = summary
    if basis is not None:
        state["decision_basis"] = basis
    if required_input is not None:
        state["required_user_input"] = required_input


def _stage_cache_key(state: ChatState, stage: str) -> str:
    q = (state.get("query", "") or "").strip().lower()
    base = "|".join(
        [
            str(state.get("tenant_id", "")),
            str(state.get("actor_type", "")),
            str(state.get("route_target", "")),
            str(state.get("aftersales_mode", "")),
            stage,
            q,
            os.getenv("POLICY_VERSION", "v1"),
            os.getenv("KB_VERSION", "v1"),
            os.getenv("PROMPT_VERSION", "v1"),
        ]
    )
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def resume_entry_node(state: ChatState) -> ChatState:
    # Explicit entry node for true breakpoint resume in main graph.
    state = _append_node_trace(state, "resume_entry")
    return state


def next_after_resume_entry(state: ChatState) -> str:
    nxt = str(state.get("resume_next_node", "") or "").strip()
    allowed = {"route_intent", "memory_read", "aftersales_facts", "aftersales_policy", "aftersales_action", "handoff_decision"}
    if nxt in allowed:
        return nxt
    return "route_intent"


def _is_aftersales_complex(query: str) -> bool:
    text = (query or "").lower()
    complex_markers = ["复杂售后", "工单升级", "人工审核", "仲裁", "批量售后", "升级投诉", "高额退款"]
    if any(k in text for k in complex_markers):
        return True
    # Practical rule: refund requests with damage/quality dispute are treated as complex.
    has_refund = any(k in text for k in ["退款", "退货", "refund", "return"])
    has_dispute = any(k in text for k in ["损坏", "破损", "质量", "纠纷", "投诉", "拒收"])
    return bool(has_refund and has_dispute)


def _feedback_decision(state: ChatState) -> str:
    hd = state.get("human_decision", {}) if isinstance(state.get("human_decision"), dict) else {}
    return str(hd.get("decision", hd.get("satisfaction", "")) or "").strip().lower()


def _feedback_reason(state: ChatState) -> str:
    hd = state.get("human_decision", {}) if isinstance(state.get("human_decision"), dict) else {}
    return str(hd.get("reason", "") or "").strip()


def _rewind_feedback_payload(state: ChatState) -> Dict[str, str]:
    hd = state.get("human_decision", {}) if isinstance(state.get("human_decision"), dict) else {}
    action_mode = str(state.get("action_mode", "auto") or "auto").lower()
    decision = str(hd.get("decision", "") or "").strip().lower()
    if action_mode != "rewind" and not decision.startswith("rewind"):
        return {}
    reason = str(hd.get("reason", "") or "").strip()
    evidence = str(hd.get("evidence", "") or "").strip()
    rewind_stage = str(state.get("rewind_stage", "") or hd.get("rewind_stage", "") or "").strip().lower()
    if not reason and not evidence:
        return {}
    return {
        "decision": decision or "rewind",
        "rewind_stage": rewind_stage or "policy",
        "reason": reason,
        "evidence": evidence,
    }


def _merge_unique(base: List[str], extra: List[str]) -> List[str]:
    out: List[str] = []
    for item in list(base) + list(extra):
        t = str(item or "").strip()
        if t and t not in out:
            out.append(t)
    return out


def _apply_rewind_feedback_mock(skill_result: Dict[str, Any], feedback: Dict[str, str]) -> Dict[str, Any]:
    if not feedback:
        return {}
    enabled = str(os.getenv("AFTERSALES_REWIND_FEEDBACK_MOCK_ENABLED", "true")).strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return {"enabled": False}
    policy = skill_result.get("policy", {}) if isinstance(skill_result.get("policy"), dict) else {}
    plan = skill_result.get("plan", {}) if isinstance(skill_result.get("plan"), dict) else {}
    reason = str(feedback.get("reason", "") or "")
    evidence = str(feedback.get("evidence", "") or "")
    rewrite_notes: List[str] = []
    guidance: List[str] = []

    # Mock "model reflection": rewrite strategy based on newly provided evidence/reason.
    evidence_strong = any(x in evidence for x in ["视频", "照片", "凭证", "单号", "聊天记录", "工单"])
    if evidence_strong:
        policy["manual_required"] = bool(policy.get("manual_required", False)) and bool(policy.get("risk_level", "") == "high")
        if str(policy.get("risk_level", "")) in {"high", "medium"}:
            policy["risk_level"] = "medium" if str(policy.get("risk_level", "")) == "high" else "low"
        rewrite_notes.append("已采纳补充证据，降低风控不确定性并重算审批路径")
        guidance.append("你补充的证据较完整，建议继续提供原始附件（清晰面单/时间戳）。")

    if any(x in reason for x in ["时效", "加急", "尽快", "急", "等待太久"]):
        plan["eta"] = "已触发加急复核：2小时内给出处理结论，到账时效按支付渠道确认"
        rewrite_notes.append("已根据时效诉求切换为加急审核策略")
        guidance.append("若超过2小时未更新，请在工单内@人工客服触发二次催办。")

    if any(x in reason for x in ["退运费", "运费", "快递费"]):
        policy.setdefault("return_freight_rule", {})
        if isinstance(policy.get("return_freight_rule"), dict):
            policy["return_freight_rule"]["feedback_override"] = "已记录用户对退运费诉求，进入费用复核队列。"
        rewrite_notes.append("已新增运费诉求复核分支")
        guidance.append("请保留退货运单与支付截图，系统会按规则核销运费补贴。")

    reason_labels = policy.get("reason_labels", {}) if isinstance(policy.get("reason_labels"), dict) else {}
    reason_labels["rewind_feedback_applied"] = (
        f"SOP-RF01 人工回退反馈生效：阶段={feedback.get('rewind_stage','policy')}；"
        f"理由={reason or '未填写'}；证据={evidence or '未填写'}"
    )
    policy["reason_labels"] = reason_labels
    reasons = [str(x) for x in (policy.get("reasons", []) or [])]
    policy["reasons"] = _merge_unique(reasons, ["rewind_feedback_applied"])

    existing_guide = [str(x) for x in (plan.get("guidance_tips", []) or [])] if isinstance(plan.get("guidance_tips"), list) else []
    plan["guidance_tips"] = _merge_unique(existing_guide, guidance)
    feedback_summary = "；".join(rewrite_notes) if rewrite_notes else "已记录回退反馈，并按补充信息重跑规则判定与行动方案。"
    plan["feedback_change_summary"] = feedback_summary
    base_msg = str(plan.get("customer_message", "") or "")
    plan["customer_message"] = (
        f"已根据你的回退反馈完成二次评估：{feedback_summary}"
        + (f"\n更新后建议：{base_msg}" if base_msg else "")
    )
    base_policy_summary = str(plan.get("policy_summary", "") or "")
    plan["policy_summary"] = "；".join([x for x in [f"反馈重评: {feedback_summary}", base_policy_summary] if x])

    skill_result["policy"] = policy
    skill_result["plan"] = plan
    return {
        "enabled": True,
        "applied": True,
        "change_summary": feedback_summary,
        "guidance_tips": plan.get("guidance_tips", []),
    }


def _mark_handoff_needed(state: ChatState, *, reason: str, answer: str, trigger: str) -> None:
    state["status"] = "NEED_HUMAN"
    state["handoff_required"] = True
    state["pending_action"] = {
        "action_name": "handoff_human",
        "action_args": {"reason": reason},
    }
    _set_stage(
        state,
        stage="action",
        stage_status="waiting_human",
        summary=reason,
        basis=[reason],
        required_input=["human_decision"],
    )
    state["allowed_actions"] = ["handoff"]
    state["answer"] = answer
    state.setdefault("debug", {})
    state["debug"]["handoff_trigger"] = trigger


def _mark_feedback_ack(state: ChatState, *, reason: str) -> None:
    state["status"] = "AUTO_DRAFT"
    state["handoff_required"] = False
    state["pending_action"] = {}
    state["allowed_actions"] = []
    state["rewind_stage_options"] = []
    _set_stage(
        state,
        stage="finalize",
        stage_status="done",
        summary="用户反馈已记录",
        basis=[reason] if reason else [],
    )
    state["answer"] = "收到反馈，感谢你的确认。后续如有需要可随时继续提问。"
    state.setdefault("debug", {})
    state["debug"]["feedback_terminal"] = True
    state["debug"]["handoff_trigger"] = "none"


def _memory_admission_for_long(state: ChatState, *, session_turn_count: int = 0) -> Dict[str, object]:
    if state.get("handoff_required", False):
        return {"passed": False, "reason": "handoff_required"}
    if state.get("served_by_cache", False):
        return {"passed": False, "reason": "served_by_cache"}
    route_target = state.get("route_target", "")
    blocked = {
        x.strip()
        for x in settings.cache_admission_blocked_domains.split(",")
        if x.strip()
    }
    if route_target in blocked:
        return {"passed": False, "reason": "blocked_domain"}
    answer = (state.get("answer", "") or "").strip()
    if len(answer) < settings.cache_admission_min_answer_len:
        return {"passed": False, "reason": "answer_too_short"}
    confidence = float(state.get("intent_confidence", 0.0))
    if confidence < settings.memory_write_score_threshold:
        return {"passed": False, "reason": "low_confidence"}
    query = (state.get("query", "") or "").strip()
    query_text = query.lower()

    # Conservative long-memory admission:
    # only keep preference / stable facts / key decisions / reusable experience.
    small_talk_markers = [
        "好看",
        "天气",
        "你好",
        "在吗",
        "哈哈",
        "谢谢",
        "么",
        "吗",
    ]
    if any(x in query for x in small_talk_markers) and len(query) <= 12:
        return {"passed": False, "reason": "small_talk"}

    preference_markers = [
        "我喜欢",
        "我偏好",
        "偏好",
        "习惯",
        "常用",
        "默认",
    ]
    stable_fact_markers = [
        "我的手机号",
        "我的地址",
        "我的邮箱",
        "收货地址",
        "联系人",
        "开票信息",
    ]
    decision_markers = [
        "已确认",
        "决定",
        "同意",
        "拒绝",
        "审批通过",
        "升级工单",
        "转人工",
    ]
    reusable_experience_markers = [
        "排查步骤",
        "处理流程",
        "解决方案",
        "复现步骤",
        "根因",
    ]
    has_preference_signal = any(m in query_text for m in preference_markers)
    has_strong_signal = any(
        m in query_text for m in (stable_fact_markers + decision_markers + reusable_experience_markers)
    )
    if has_strong_signal:
        return {"passed": True, "reason": "strong_signal"}
    # Preference signal is weaker: only keep once in early turns.
    if has_preference_signal and session_turn_count <= 1:
        return {"passed": True, "reason": "preference_signal_first_turn"}
    if has_preference_signal and session_turn_count > 1:
        return {"passed": False, "reason": "repeated_preference_signal"}
    if not has_preference_signal and not has_strong_signal:
        return {"passed": False, "reason": "not_long_term_worthy"}
    return {"passed": False, "reason": "not_long_term_worthy"}


def _idempotency_key(*, scope_key: str, node_name: str, memory_type: str, content: str) -> str:
    raw = f"{scope_key}:{node_name}:{memory_type}:{content}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _append_node_trace(state: ChatState, node_name: str) -> ChatState:
    state["node_trace"] = [*state.get("node_trace", []), node_name]
    _set_stage(
        state,
        stage=_stage_of_node(node_name),
        stage_status="running",
        summary=f"正在执行节点 {node_name}",
    )
    try:
        snapshot = dict(state)
        if isinstance(snapshot.get("debug"), dict):
            # Avoid oversized checkpoint row due to full context echo.
            dbg = dict(snapshot.get("debug", {}))
            if isinstance(dbg.get("context"), dict):
                ctx = dict(dbg.get("context", {}))
                if isinstance(ctx.get("llm_context"), str) and len(ctx["llm_context"]) > 1200:
                    ctx["llm_context"] = ctx["llm_context"][:1200]
                dbg["context"] = ctx
            snapshot["debug"] = dbg
        checkpoint_id = CHECKPOINT_STORE.save_checkpoint(
            thread_id=_thread_id(state),
            run_id=str(state.get("run_id", "")),
            trace_id=str(state.get("trace_id", "")),
            event_id=str(state.get("event_id", "")),
            node_name=node_name,
            status="ok",
            state=snapshot,
            parent_checkpoint_id=str(state.get("wf_checkpoint_id", "")),
            metadata={
                "node_trace_len": len(state.get("node_trace", [])),
                "stage": state.get("current_stage", ""),
                "stage_status": state.get("stage_status", ""),
                "pending_action_name": (state.get("pending_action", {}) or {}).get("action_name", ""),
            },
        )
        state["wf_parent_checkpoint_id"] = str(state.get("wf_checkpoint_id", ""))
        state["wf_checkpoint_id"] = checkpoint_id
    except Exception:
        state.setdefault("debug", {})
        state["debug"]["checkpoint_write_error"] = True
    return state


def route_intent_node(state: ChatState) -> ChatState:
    state = _append_node_trace(state, "route_intent")
    if state.get("action_mode") in {"continue", "rewind"} and state.get("debug", {}).get("resumed_from_checkpoint"):
        # Keep restored route context in same-run continuation.
        return state
    text = state["query"].lower()
    demo_intent = infer_demo_intent(state.get("query", ""))
    if demo_intent:
        state["route_target"], state["aftersales_mode"] = demo_intent
        state["intent_confidence"] = 0.99
        state.setdefault("debug", {})
        state["debug"]["demo_scenario_hit"] = True
        return state
    aftersales_keywords = [
        "refund",
        "return",
        "after-sales",
        "aftersales",
        "退款",
        "退货",
        "售后",
        "工单",
        "换货",
        "补发",
        "损坏",
        "签收",
        "维修",
    ]
    if any(k in text for k in aftersales_keywords):
        state["route_target"] = "aftersales"
        state["aftersales_mode"] = "complex" if _is_aftersales_complex(text) else "simple"
        state["intent_confidence"] = 0.93 if state["aftersales_mode"] == "complex" else 0.92
    elif any(k in text for k in ["risk", "legal", "complaint", "regulation", "合规", "法律", "投诉"]):
        state["route_target"] = "risk_query"
        state["aftersales_mode"] = ""
        state["intent_confidence"] = 0.90
    elif any(k in text for k in ["price", "spec", "feature", "product", "价格", "参数", "规格"]):
        state["route_target"] = "product_info"
        state["aftersales_mode"] = ""
        state["intent_confidence"] = 0.88
    else:
        state["route_target"] = "faq"
        state["aftersales_mode"] = ""
        state["intent_confidence"] = 0.70
    return state


def feedback_gate_node(state: ChatState) -> ChatState:
    state = _append_node_trace(state, "feedback_gate")
    decision = _feedback_decision(state)
    reason = _feedback_reason(state) or "user_feedback"
    unsatisfied = {
        "unresolved",
        "unsatisfied",
        "not_solved",
        "not_resolved",
        "need_human",
        "transfer_human",
        "manual_service",
        "未解决",
        "不满意",
        "转人工",
    }
    satisfied = {
        "resolved",
        "satisfied",
        "solved",
        "已解决",
        "满意",
    }
    state.setdefault("debug", {})
    if decision in unsatisfied:
        _mark_handoff_needed(
            state,
            reason=f"用户反馈未解决，转人工处理: {reason}",
            answer="已收到“未解决”反馈，正在为你转接人工客服，请稍候。",
            trigger="user_unsatisfied",
        )
        state["debug"]["user_feedback"] = {"satisfaction": "unsatisfied", "decision": decision, "reason": reason}
    elif decision in satisfied:
        _mark_feedback_ack(state, reason=reason)
        state["debug"]["user_feedback"] = {"satisfaction": "satisfied", "decision": decision, "reason": reason}
    return state


def next_after_feedback_gate(state: ChatState) -> str:
    decision = _feedback_decision(state)
    if decision in {
        "unresolved",
        "unsatisfied",
        "not_solved",
        "not_resolved",
        "need_human",
        "transfer_human",
        "manual_service",
        "未解决",
        "不满意",
        "转人工",
        "resolved",
        "satisfied",
        "solved",
        "已解决",
        "满意",
    }:
        return "handoff_decision"
    return "intent_subgraph_entry"


def intent_subgraph_entry_node(state: ChatState) -> ChatState:
    state = _append_node_trace(state, "intent_subgraph_entry")
    return state


def next_after_intent_subgraph_entry(state: ChatState) -> str:
    route_target = str(state.get("route_target", "faq") or "faq")
    if route_target == "risk_query":
        return "risk_handoff_subgraph"
    return "cache_lookup"


def risk_handoff_subgraph_node(state: ChatState) -> ChatState:
    state = _append_node_trace(state, "risk_handoff_subgraph")
    _mark_handoff_needed(
        state,
        reason="风险咨询按业务规则需人工介入",
        answer="该问题属于风险咨询场景，已按规则升级到人工客服处理。",
        trigger="risk_query_policy",
    )
    return state


def cache_lookup_node(state: ChatState) -> ChatState:
    state = _append_node_trace(state, "cache_lookup")
    if state.get("action_mode") in {"continue", "rewind"} and state.get("debug", {}).get("resumed_from_checkpoint"):
        state["cache_result"] = {
            "keys": state.get("cache_result", {}).get("keys", {}),
            "l1_hit": False,
            "l2_hit": False,
            "decision": "RESUME",
            "level": "RUN_CHECKPOINT",
            "details": {},
        }
        state["served_by_cache"] = False
        return state
    if state.get("route_target") == "aftersales" and state.get("aftersales_mode") == "complex":
        state["cache_result"] = {
            "keys": state.get("cache_result", {}).get("keys", {}),
            "l1_hit": False,
            "l2_hit": False,
            "decision": "BYPASS",
            "level": "AFTERSALES_COMPLEX",
            "details": {"reason": "force_execute_full_workflow_for_observability"},
        }
        state["served_by_cache"] = False
        return state
    if infer_demo_intent(state.get("query", "")):
        state["cache_result"] = {
            "keys": state.get("cache_result", {}).get("keys", {}),
            "l1_hit": False,
            "l2_hit": False,
            "decision": "BYPASS",
            "level": "DEMO_SCENARIO",
            "details": {"reason": "force_execute_demo_chain"},
        }
        state["served_by_cache"] = False
        return state
    query = str(state.get("query", "") or "")
    tenant_id = str(state.get("tenant_id", "") or "")
    actor_type = str(state.get("actor_type", "") or "")
    route_target = str(state.get("route_target", "faq") or "faq")
    keys = build_cache_keys(
        query=query,
        tenant_id=tenant_id,
        actor_type=actor_type,
        intent_bucket=route_target,
        settings=settings,
    )
    cache_timeout = _layer_timeout_budget(
        "cache",
        route_target,
        float(os.getenv("CACHE_LAYER_TIMEOUT_SECONDS", "1.8")),
    )
    cache_retries = _layer_retry_budget(
        "cache",
        route_target,
        int(os.getenv("CACHE_LAYER_RETRY", "1")),
    )
    cache_backoff = _layer_backoff_budget(
        "cache",
        route_target,
        float(os.getenv("CACHE_LAYER_RETRY_BACKOFF_SECONDS", "0.2")),
    )
    cache_decision, cache_ctrl = _run_with_layer_control(
        fn=lambda: CACHE_ORCHESTRATOR.lookup(
            query=query,
            tenant_id=tenant_id,
            actor_type=actor_type,
            domain=route_target,
        ),
        timeout_seconds=cache_timeout,
        retries=cache_retries,
        backoff_seconds=cache_backoff,
        default_value={
            "served_by_cache": False,
            "decision": "TIMEOUT_FALLBACK_MISS",
            "level": "BYPASS",
            "debug": {"error": "cache_timeout"},
        },
    )
    if not cache_ctrl.get("ok", False):
        if not cache_ctrl.get("timeout", False):
            cache_decision = {
                "served_by_cache": False,
                "decision": "ERROR_FALLBACK_MISS",
                "level": "BYPASS",
                "debug": {"error": cache_ctrl.get("error", "cache_error")},
            }
        cache_decision.setdefault("debug", {})
        cache_decision["debug"]["layer_control"] = cache_ctrl
    state.setdefault("debug", {})
    state["debug"].setdefault("layer_controls", {})
    state["debug"]["layer_controls"]["cache"] = {
        **cache_ctrl,
        "timeout_budget_s": cache_timeout,
        "retry_budget": cache_retries,
        "backoff_budget_s": cache_backoff,
        "route_target": route_target,
    }
    served_by_cache = bool(cache_decision.get("served_by_cache", False))

    state["cache_result"] = {
        "keys": keys,
        "l1_hit": cache_decision.get("level") == "L1",
        "l2_hit": cache_decision.get("level") in {"L2_HOT", "L2_PERSIST"},
        "decision": cache_decision.get("decision"),
        "level": cache_decision.get("level"),
        "details": cache_decision.get("debug", {}),
    }
    state["served_by_cache"] = served_by_cache
    if served_by_cache:
        state["answer"] = cache_decision.get("answer", "cache_hit")
        state["citations"] = cache_decision.get("citations", [])
    return state


def next_after_cache(state: ChatState) -> str:
    return "handoff_decision" if state.get("served_by_cache") else "memory_read"


def _maybe_observability_seed_memory_items(
    *,
    user_id: str,
    memories: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Optional demo seed when long/session/L3 return empty (local Grafana / verify traffic).

    Cache hits skip memory_read entirely; combine with unique queries in verify scripts.
    """
    flag = os.getenv("OBSERVABILITY_SEED_MEMORY_EFFECTIVE", "").strip().lower()
    if flag not in {"1", "true", "yes", "on"}:
        return memories
    if memories:
        return memories
    demo_uid = os.getenv("OBSERVABILITY_DEMO_USER_ID", "u_obs_verify").strip()
    if (user_id or "").strip() != demo_uid:
        return memories
    return [
        {
            "memory_id": "obs:demo:seed",
            "memory_type": "long",
            "content": "演示记忆：用于观测有效注入率（可设置 OBSERVABILITY_SEED_MEMORY_EFFECTIVE=true）。",
            "summary": "演示记忆：用于观测有效注入率。",
            "citations": [],
            "metadata": {"source": "observability_seed"},
            "confidence": 0.99,
            "importance_score": 0.99,
            "score": 1.0,
        }
    ]


def memory_read_node(state: ChatState) -> ChatState:
    state = _append_node_trace(state, "memory_read")
    if not _memory_enabled(state):
        state["memory_result"] = {
            "items": [],
            "hit": False,
            "hit_count": 0,
            "disabled": True,
            "reason": "memory_disabled",
        }
        return state
    try:
        route_target = str(state.get("route_target", "faq") or "faq")
        mem_timeout = _layer_timeout_budget(
            "memory",
            route_target,
            float(os.getenv("MEMORY_LAYER_TIMEOUT_SECONDS", "2.5")),
        )
        mem_retries = _layer_retry_budget(
            "memory",
            route_target,
            int(os.getenv("MEMORY_LAYER_RETRY", "1")),
        )
        mem_backoff = _layer_backoff_budget(
            "memory",
            route_target,
            float(os.getenv("MEMORY_LAYER_RETRY_BACKOFF_SECONDS", "0.2")),
        )
        session, session_ctrl = _run_with_layer_control(
            fn=lambda: MEMORY_STORE.read_session_memory(
                session_id=state.get("conversation_id") or _thread_id(state),
                trace_id=state.get("trace_id", ""),
                node_name="memory_read",
            ),
            timeout_seconds=mem_timeout,
            retries=mem_retries,
            backoff_seconds=mem_backoff,
            default_value={"found": False, "session_id": ""},
        )
        session_items: List[Dict[str, Any]] = []
        if session.get("found"):
            rolling_summary = session.get("rolling_summary", "")
            if rolling_summary:
                session_items.append(
                    {
                        "memory_id": f"session:{session.get('session_id')}:summary",
                        "memory_type": "short",
                        "content": rolling_summary,
                        "summary": rolling_summary,
                        "citations": [],
                        "metadata": {"source": "session_summary"},
                        "confidence": 1.0,
                        "importance_score": 1.0,
                        "score": 1.2,
                    }
                )
            recent_turns = session.get("recent_turns", [])
            if recent_turns:
                pairs = []
                for t in recent_turns[-3:]:
                    q = (t.get("q") or "").strip()
                    a = (t.get("a") or "").strip()
                    if q or a:
                        pairs.append(f"Q:{q}\nA:{a}")
                if pairs:
                    merged = "\n".join(pairs)
                    session_items.append(
                        {
                            "memory_id": f"session:{session.get('session_id')}:recent",
                            "memory_type": "short",
                            "content": merged,
                            "summary": merged,
                            "citations": [],
                            "metadata": {"source": "session_recent_turns"},
                            "confidence": 1.0,
                            "importance_score": 1.0,
                            "score": 1.1,
                        }
                    )
        long_scope = _long_scope(state.get("tenant_id", ""), state.get("user_id", ""))
        long_memories, long_ctrl = _run_with_layer_control(
            fn=lambda: MEMORY_STORE.query_memory(
                tenant_id=state.get("tenant_id", ""),
                user_id=state.get("user_id", ""),
                thread_id=long_scope,
                query=state.get("query", ""),
                memory_types=["long"],
                top_k=max(1, settings.memory_read_top_k // 2),
                trace_id=state.get("trace_id", ""),
                node_name="memory_read",
            ),
            timeout_seconds=mem_timeout,
            retries=mem_retries,
            backoff_seconds=mem_backoff,
            default_value=[],
        )
        l3_scope = _l3_scope(
            state.get("tenant_id", ""),
            state.get("user_id", ""),
            state.get("conversation_id") or _thread_id(state),
        )
        l3_memories, l3_ctrl = _run_with_layer_control(
            fn=lambda: MEMORY_STORE.query_memory(
                tenant_id=state.get("tenant_id", ""),
                user_id=state.get("user_id", ""),
                thread_id=l3_scope,
                query=state.get("query", ""),
                memory_types=["l3"],
                top_k=max(1, settings.memory_read_top_k // 2),
                trace_id=state.get("trace_id", ""),
                node_name="memory_read",
            ),
            timeout_seconds=mem_timeout,
            retries=mem_retries,
            backoff_seconds=mem_backoff,
            default_value=[],
        )
        memories = _maybe_observability_seed_memory_items(
            user_id=str(state.get("user_id", "") or ""),
            memories=session_items + long_memories + l3_memories,
        )
        mem_ctrl = {
            "ok": bool(
                session_ctrl.get("ok", False) and long_ctrl.get("ok", False) and l3_ctrl.get("ok", False)
            ),
            "timeout": bool(
                session_ctrl.get("timeout", False) or long_ctrl.get("timeout", False) or l3_ctrl.get("timeout", False)
            ),
            "error": ";".join(
                [x for x in [session_ctrl.get("error", ""), long_ctrl.get("error", ""), l3_ctrl.get("error", "")] if x]
            ),
            "attempts": max(
                int(session_ctrl.get("attempts", 1)),
                int(long_ctrl.get("attempts", 1)),
                int(l3_ctrl.get("attempts", 1)),
            ),
            "retried": int(session_ctrl.get("retried", 0))
            + int(long_ctrl.get("retried", 0))
            + int(l3_ctrl.get("retried", 0)),
        }
        state.setdefault("debug", {})
        state["debug"].setdefault("layer_controls", {})
        state["debug"]["layer_controls"]["memory"] = {
            **mem_ctrl,
            "timeout_budget_s": mem_timeout,
            "retry_budget": mem_retries,
            "backoff_budget_s": mem_backoff,
            "route_target": route_target,
        }
        state["memory_result"] = {
            "items": memories,
            "hit": len(memories) > 0,
            "hit_count": len(memories),
            "session_found": bool(session.get("found", False)),
            "short_memory_storage_table": "session_memories",
            "short_memory_session_id": session.get("session_id", ""),
            "session_turn_count": int(session.get("turn_count", 0) or 0),
            "session_compressed_count": int(session.get("compressed_count", 0) or 0),
            "timeout": bool(mem_ctrl.get("timeout", False)),
            "degraded": not bool(mem_ctrl.get("ok", False)),
            "layer_control": mem_ctrl,
        }
    except Exception as exc:
        state["memory_result"] = {
            "items": [],
            "hit": False,
            "hit_count": 0,
            "error": str(exc),
            "degraded": True,
        }
    return state


def next_after_memory_read(state: ChatState) -> str:
    if state.get("route_target") == "aftersales" and state.get("aftersales_mode") == "complex":
        return "aftersales_facts"
    if state.get("route_target") == "product_info":
        return "tool_call"
    return "rag_decision"


def next_after_aftersales_action(state: ChatState) -> str:
    if state.get("status") == "NEED_HUMAN" or state.get("handoff_required", False):
        return "handoff_decision"
    return "rag_decision"


def next_after_rag_decision(state: ChatState) -> str:
    rag_decision = state.get("rag_decision_result", {}) if isinstance(state.get("rag_decision_result"), dict) else {}
    if bool(rag_decision.get("need_rag", False)):
        return "rag_query"
    return "context_build"


def _current_aftersales_stage(tool_result: Dict[str, Any], skill_result: Dict[str, Any]) -> str:
    if "order_query_tool" not in tool_result:
        return "facts"
    if "policy" not in skill_result or "plan" not in skill_result:
        return "policy"
    return "action"


def _force_action_by_stage(
    *,
    stage: str,
    planned_name: str,
    planned_args: Dict[str, Any],
    tool_result: Dict[str, Any],
    skill_result: Dict[str, Any],
) -> tuple[str, Dict[str, Any], str]:
    if stage == "facts":
        if "order_query_tool" not in tool_result:
            return "order_query_tool", {}, "facts_require_order"
        return planned_name, planned_args, "facts_ok"
    if stage == "policy":
        if "policy" not in skill_result:
            return "refund_policy_skill", {}, "policy_require_eval"
        if "plan" not in skill_result:
            return "aftersales_plan_skill", {}, "policy_require_plan"
        return planned_name, planned_args, "policy_ok"
    # action stage
    if planned_name in {"refund_submit_mcp", "ticket_upgrade_mcp", "approval_submit_mcp"}:
        return planned_name, planned_args, "action_use_planner"
    policy_eval = skill_result.get("policy", {}) if isinstance(skill_result.get("policy"), dict) else {}
    order = tool_result.get("order_query_tool", {}) if isinstance(tool_result.get("order_query_tool"), dict) else {}
    if bool(policy_eval.get("manual_required", False)):
        return "approval_submit_mcp", {"reason": "manual_required", "amount": float(order.get("amount", 0) or 0)}, "action_force_approval"
    if bool(policy_eval.get("eligible", False)):
        return "refund_submit_mcp", {"reason": "policy_eligible", "amount": float(order.get("amount", 0) or 0)}, "action_force_refund"
    return "ticket_upgrade_mcp", {"priority": "P1"}, "action_force_upgrade"


def _build_rewind_options(run_id: str) -> List[Dict[str, Any]]:
    options: List[Dict[str, Any]] = []
    for stage in ["facts", "policy", "action"]:
        ckpt = CHECKPOINT_STORE.latest_stage_checkpoint(run_id=run_id, stage=stage)
        if not ckpt:
            continue
        options.append(
            {
                "stage": stage,
                "checkpoint_id": ckpt.get("checkpoint_id", ""),
                "created_at": ckpt.get("created_at", ""),
                "node_name": ckpt.get("node_name", ""),
            }
        )
    return options


def _set_wait_human(
    state: ChatState,
    *,
    reason: str,
    pending_action_name: str,
    pending_action_args: Dict[str, Any],
    checkpoint_id: str,
) -> None:
    state["handoff_required"] = True
    state["status"] = "NEED_HUMAN"
    query_text = str(state.get("query", "") or "").strip()
    state["pending_action"] = {
        "action_name": pending_action_name,
        "action_args": pending_action_args,
        "risk_reasons": [reason],
        "checkpoint_id": checkpoint_id,
    }
    state["allowed_actions"] = [
        "approve",
        "reject",
        "modify",
        "rewind",
        "rewind_facts",
        "rewind_policy",
    ]
    state["rewind_stage_options"] = _build_rewind_options(str(state.get("run_id", "")))
    stage_ckpt_map: Dict[str, str] = {}
    for option in state["rewind_stage_options"]:
        if not isinstance(option, dict):
            continue
        stage_name = str(option.get("stage", "") or "").strip()
        ckpt_id = str(option.get("checkpoint_id", "") or "").strip()
        if stage_name and ckpt_id:
            stage_ckpt_map[stage_name] = ckpt_id
    risk_explain = (
        f"检测到高风险操作，需人工确认后才会执行。\n"
        f"- 风险原因: {reason}\n"
        f"- 待执行动作: {pending_action_name}\n"
        f"- 动作参数: {pending_action_args}\n"
        f"- 用户请求: {query_text}"
    )
    state["answer"] = risk_explain
    _set_stage(
        state,
        stage="action",
        stage_status="waiting_human",
        summary=f"等待人工决策：{pending_action_name}",
        basis=[reason],
        required_input=["human_decision"],
    )
    state.setdefault("debug", {})
    state["debug"]["pending_human_gate"] = {
        "reason": reason,
        "checkpoint_id": checkpoint_id,
        "action": pending_action_name,
        "allowed_actions": state["allowed_actions"],
        "rewind_stage_options": state["rewind_stage_options"],
    }
    state["debug"]["human_gate_card"] = {
        "title": "高风险操作待确认",
        "risk_level": "high",
        "reason": reason,
        "pending_action": pending_action_name,
        "pending_action_args": pending_action_args,
        "explanation": risk_explain,
        "choices": [
            {"label": "通过并继续", "decision": "approve", "require_reason": False, "require_evidence": False},
            {"label": "拒绝并结束", "decision": "reject", "require_reason": True, "require_evidence": False},
            {"label": "回退到 facts", "decision": "rewind_facts", "rewind_stage": "facts", "target_checkpoint_id": stage_ckpt_map.get("facts", ""), "require_reason": True, "require_evidence": False},
            {"label": "回退到 policy", "decision": "rewind_policy", "rewind_stage": "policy", "target_checkpoint_id": stage_ckpt_map.get("policy", ""), "require_reason": True, "require_evidence": False},
        ],
    }
    set_trace_metadata(
        breakpoint_state="WAITING_HUMAN",
        pending_action_name=pending_action_name,
        pending_action_checkpoint_id=checkpoint_id,
        pending_human_gate_checkpoint_id=checkpoint_id,
        pending_human_gate_reason=reason,
    )


@traceable(name="aftersales_facts", run_type="chain")
def aftersales_facts_node(state: ChatState) -> ChatState:
    state = _append_node_trace(state, "aftersales_facts")
    _set_stage(state, stage="facts", stage_status="running", summary="售后事实阶段：收集订单/工单/物流事实")
    cache_key = _stage_cache_key(state, "facts")
    bypass_stage_cache = str(state.get("action_mode", "auto") or "auto") == "rewind"
    cached = None if bypass_stage_cache else STAGE_RESULT_CACHE.get(cache_key)
    if cached:
        state["aftersales_tool_result"] = cached.get("tool_result", {}) if isinstance(cached.get("tool_result"), dict) else {}
        state.setdefault("debug", {})
        state["debug"]["stage_cache_facts"] = {"hit": True, "key": cache_key}
        trace = list(state.get("aftersales_agent_result", {}).get("trace", [])) if isinstance(state.get("aftersales_agent_result"), dict) else []
        trace.append({"stage": "facts", "status": "done", "source": "stage_cache"})
        state["aftersales_agent_result"] = {
            "trace": trace,
            "tool_result": state.get("aftersales_tool_result", {}),
            "skill_result": state.get("aftersales_skill_result", {}),
        }
        return state
    query = state.get("query", "")
    tool_result = dict(state.get("aftersales_tool_result", {}))
    trace = list(state.get("aftersales_agent_result", {}).get("trace", [])) if isinstance(state.get("aftersales_agent_result"), dict) else []
    max_loops = int(os.getenv("AFTERSALES_FACTS_MAX_LOOPS", "4"))

    def _fallback_tool() -> str:
        if "order_query_tool" not in tool_result:
            return "order_query_tool"
        if "ticket_query_tool" not in tool_result:
            return "ticket_query_tool"
        if "logistics_query_tool" not in tool_result:
            return "logistics_query_tool"
        return "stage_done"

    for loop_idx in range(max_loops):
        planner_ctx = build_context_with_budget(
            rag_context="",
            memory_items=state.get("memory_result", {}).get("items", []),
            total_budget_chars=min(1800, settings.context_total_budget_chars),
            memory_budget_ratio=min(0.3, settings.context_memory_budget_ratio),
            short_ratio=settings.context_memory_short_ratio,
            long_ratio=settings.context_memory_long_ratio,
            l3_ratio=settings.context_memory_l3_ratio,
            summarizer_enabled=False,
            summary_max_chars=settings.context_memory_summary_max_chars,
            system_policy="你是售后事实阶段规划器。每轮仅决定一个tool，全部事实完成后输出 stage_done。",
            scenario_rules="阶段=facts；可选动作=order_query_tool/ticket_query_tool/logistics_query_tool/stage_done。",
            tool_facts=f"existing_tool_result={tool_result}",
            user_query=query,
        )
        planned_name = ""
        try:
            planner = decide_aftersales_next_step(
                query=query,
                context={"stage": "facts", "context": planner_ctx.get("context", "")},
                step_idx=loop_idx,
                available_tools=[
                    {"type": "tool", "name": "order_query_tool", "description": "查询订单事实", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}},
                    {"type": "tool", "name": "ticket_query_tool", "description": "查询工单事实", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}},
                    {"type": "tool", "name": "logistics_query_tool", "description": "查询物流事实", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}},
                ],
                available_skills=[],
                available_mcp=[],
                allow_side_effect=False,
            )
            planned_name = str(planner.get("name", "") or "")
        except Exception:
            planned_name = ""

        name = planned_name
        if name in {"stage_done", "finalize", "finish", "done"}:
            if "order_query_tool" in tool_result and "ticket_query_tool" in tool_result and "logistics_query_tool" in tool_result:
                trace.append({"stage": "facts", "loop_idx": loop_idx, "status": "stage_done", "source": "planner"})
                break
            name = _fallback_tool()
        elif name not in {"order_query_tool", "ticket_query_tool", "logistics_query_tool"}:
            name = _fallback_tool()

        set_trace_metadata(facts_loop_idx=loop_idx, facts_tool_name=name)
        if name == "order_query_tool":
            tool_result["order_query_tool"] = order_query_tool(query)
        elif name == "ticket_query_tool":
            tool_result["ticket_query_tool"] = ticket_query_tool(query)
        elif name == "logistics_query_tool":
            tool_result["logistics_query_tool"] = logistics_query_tool(query)
        trace.append({"stage": "facts", "loop_idx": loop_idx, "status": "tool_ok", "tool": name})

    state["aftersales_tool_result"] = tool_result
    state["tool_result"] = {
        "tool_name": "aftersales_facts",
        "tool_status": "success",
        "data": tool_result,
    }
    state["aftersales_agent_result"] = {
        "trace": trace,
        "tool_result": tool_result,
        "skill_result": state.get("aftersales_skill_result", {}),
    }
    STAGE_RESULT_CACHE.set(cache_key, {"tool_result": tool_result}, ttl_seconds=int(os.getenv("FACTS_STAGE_CACHE_TTL_SECONDS", "600")))
    state.setdefault("debug", {})
    state["debug"]["stage_cache_facts"] = {
        "hit": False,
        "key": cache_key,
        "bypassed": bypass_stage_cache,
        "reason": "rewind_replay" if bypass_stage_cache else "",
    }
    return state


@traceable(name="aftersales_policy", run_type="chain")
def aftersales_policy_node(state: ChatState) -> ChatState:
    state = _append_node_trace(state, "aftersales_policy")
    _set_stage(state, stage="policy", stage_status="running", summary="售后策略阶段：规则判定与方案生成")
    cache_key = _stage_cache_key(state, "policy")
    bypass_stage_cache = str(state.get("action_mode", "auto") or "auto") == "rewind"
    cached = None if bypass_stage_cache else STAGE_RESULT_CACHE.get(cache_key)
    if cached:
        state["aftersales_skill_result"] = cached.get("skill_result", {}) if isinstance(cached.get("skill_result"), dict) else {}
        state.setdefault("debug", {})
        state["debug"]["stage_cache_policy"] = {"hit": True, "key": cache_key}
        trace = list(state.get("aftersales_agent_result", {}).get("trace", [])) if isinstance(state.get("aftersales_agent_result"), dict) else []
        trace.append({"stage": "policy", "status": "done", "source": "stage_cache"})
        state["aftersales_agent_result"] = {
            "trace": trace,
            "tool_result": state.get("aftersales_tool_result", {}),
            "skill_result": state.get("aftersales_skill_result", {}),
        }
        return state
    tool_result = state.get("aftersales_tool_result", {})
    query = state.get("query", "")
    skill_result = dict(state.get("aftersales_skill_result", {}))
    trace = list(state.get("aftersales_agent_result", {}).get("trace", [])) if isinstance(state.get("aftersales_agent_result"), dict) else []
    max_loops = int(os.getenv("AFTERSALES_POLICY_MAX_LOOPS", "4"))
    rewind_feedback = _rewind_feedback_payload(state)
    feedback_note = ""
    if rewind_feedback:
        feedback_note = (
            f"rewind_feedback(stage={rewind_feedback.get('rewind_stage','policy')}) "
            f"reason={rewind_feedback.get('reason','')} evidence={rewind_feedback.get('evidence','')}"
        )

    def _fallback_skill() -> str:
        if "policy" not in skill_result:
            return "refund_policy_skill"
        if "plan" not in skill_result:
            return "aftersales_plan_skill"
        return "stage_done"

    for loop_idx in range(max_loops):
        planner_ctx = build_context_with_budget(
            rag_context="",
            memory_items=state.get("memory_result", {}).get("items", []),
            total_budget_chars=min(1800, settings.context_total_budget_chars),
            memory_budget_ratio=min(0.3, settings.context_memory_budget_ratio),
            short_ratio=settings.context_memory_short_ratio,
            long_ratio=settings.context_memory_long_ratio,
            l3_ratio=settings.context_memory_l3_ratio,
            summarizer_enabled=False,
            summary_max_chars=settings.context_memory_summary_max_chars,
            system_policy="你是售后策略阶段规划器。每轮仅决定一个skill，完成后输出 stage_done。",
            scenario_rules=(
                "阶段=policy；可选动作=refund_policy_skill/aftersales_plan_skill/stage_done。"
                + (" 若收到人工回退反馈，必须先反思并在新方案中明确“本次调整点+对用户指导建议”。" if rewind_feedback else "")
            ),
            tool_facts=f"facts={tool_result}\nexisting_skill_result={skill_result}\n{feedback_note}",
            user_query=query,
        )
        planned_name = ""
        try:
            planner = decide_aftersales_next_step(
                query=query,
                context={"stage": "policy", "context": planner_ctx.get("context", "")},
                step_idx=loop_idx,
                available_tools=[],
                available_skills=[
                    {"type": "skill", "name": "refund_policy_skill", "description": "规则判定", "parameters": {"type": "object", "properties": {}}},
                    {"type": "skill", "name": "aftersales_plan_skill", "description": "方案生成", "parameters": {"type": "object", "properties": {}}},
                ],
                available_mcp=[],
                allow_side_effect=False,
            )
            planned_name = str(planner.get("name", "") or "")
        except Exception:
            planned_name = ""

        name = planned_name
        if name in {"stage_done", "finalize", "finish", "done"}:
            if "policy" in skill_result and "plan" in skill_result:
                trace.append({"stage": "policy", "loop_idx": loop_idx, "status": "stage_done", "source": "planner"})
                break
            name = _fallback_skill()
        elif name not in {"refund_policy_skill", "aftersales_plan_skill"}:
            name = _fallback_skill()

        set_trace_metadata(policy_loop_idx=loop_idx, policy_skill_name=name)
        if name == "refund_policy_skill":
            skill_result["policy"] = evaluate_refund_policy(query, tool_result)
        elif name == "aftersales_plan_skill":
            skill_result["plan"] = generate_aftersales_plan(skill_result.get("policy", {}), tool_result)
        trace.append({"stage": "policy", "loop_idx": loop_idx, "status": "skill_ok", "skill": name})

    rewind_reflection = _apply_rewind_feedback_mock(skill_result, rewind_feedback)
    state["aftersales_skill_result"] = skill_result
    state["aftersales_agent_result"] = {
        "trace": trace,
        "tool_result": tool_result,
        "skill_result": skill_result,
    }
    STAGE_RESULT_CACHE.set(cache_key, {"skill_result": skill_result}, ttl_seconds=int(os.getenv("POLICY_STAGE_CACHE_TTL_SECONDS", "900")))
    state.setdefault("debug", {})
    state["debug"]["stage_cache_policy"] = {
        "hit": False,
        "key": cache_key,
        "bypassed": bypass_stage_cache,
        "reason": "rewind_replay" if bypass_stage_cache else "",
    }
    if rewind_feedback:
        state["debug"]["rewind_feedback_reflection"] = rewind_reflection
    return state


@traceable(name="aftersales_action", run_type="chain")
def aftersales_action_node(state: ChatState) -> ChatState:
    state = _append_node_trace(state, "aftersales_action")
    _set_stage(state, stage="action", stage_status="running", summary="售后动作阶段：高风险动作执行前审批")
    query = state.get("query", "")
    tool_result = dict(state.get("aftersales_tool_result", {}))
    skill_result = dict(state.get("aftersales_skill_result", {}))
    human_decision = state.get("human_decision", {}) if isinstance(state.get("human_decision"), dict) else {}
    action_mode = str(state.get("action_mode", "auto") or "auto")
    pending_action = state.get("pending_action", {}) if isinstance(state.get("pending_action"), dict) else {}
    trace = list(state.get("aftersales_agent_result", {}).get("trace", [])) if isinstance(state.get("aftersales_agent_result"), dict) else []
    max_loops = int(os.getenv("AFTERSALES_ACTION_MAX_LOOPS", "5"))
    use_demo_plan = bool(
        state.get("route_target") == "aftersales"
        and state.get("aftersales_mode") == "complex"
    )
    demo_action_plan = demo_aftersales_action_plan(query) if use_demo_plan else None
    decision = str(human_decision.get("decision", "") or "").lower()
    decision_reason = str(human_decision.get("reason", "") or "").strip()
    decision_evidence = str(human_decision.get("evidence", "") or "").strip()
    rewind_feedback = _rewind_feedback_payload(state)
    feedback_note_for_action = ""
    if rewind_feedback:
        feedback_note_for_action = (
            f"rewind_feedback_applied={rewind_feedback.get('rewind_stage','policy')} "
            f"reason={rewind_feedback.get('reason','')} evidence={rewind_feedback.get('evidence','')}"
        )
    decision_consumed = False
    seed_action_name = str(pending_action.get("action_name", "") or "")
    seed_action_args = pending_action.get("action_args", {}) if isinstance(pending_action.get("action_args"), dict) else {}

    # Fast-path for true breakpoint continue: execute the exact pending action first,
    # then continue planner loop for post-action re-evaluation.
    if action_mode == "continue" and decision in {"approve", "modify"} and seed_action_name:
        action_args = dict(seed_action_args)
        if decision == "modify":
            overrides = human_decision.get("overrides", {}) if isinstance(human_decision.get("overrides"), dict) else {}
            if isinstance(overrides.get("action_args"), dict):
                action_args = dict(overrides["action_args"])
        set_trace_metadata(action_loop_idx=0, action_name=seed_action_name, action_source="resume_pending_action_fastpath")
        mcp_resp = _execute_resume_action(
            action_name=seed_action_name,
            action_args=action_args,
            tool_result=tool_result,
            scope_key=_thread_id(state),
            checkpoint_id=str(pending_action.get("checkpoint_id", "")),
        )
        tool_result[seed_action_name] = mcp_resp
        state["aftersales_tool_result"] = tool_result
        state["pending_action"] = {}
        state["allowed_actions"] = []
        state["rewind_stage_options"] = _build_rewind_options(str(state.get("run_id", "")))
        if not mcp_resp.get("ok", False):
            _set_wait_human(
                state,
                reason="mcp_error_need_manual",
                pending_action_name=seed_action_name,
                pending_action_args=action_args,
                checkpoint_id=str(pending_action.get("checkpoint_id", "")),
            )
            trace.append({"stage": "action", "loop_idx": 0, "status": "mcp_error", "action": seed_action_name})
            state["aftersales_agent_result"] = {"trace": trace, "tool_result": tool_result, "skill_result": skill_result}
            return state
        _set_stage(
            state,
            stage="action",
            stage_status="done",
            summary=f"已执行人工批准动作: {seed_action_name}",
        )
        trace.append({"stage": "action", "loop_idx": 0, "status": "mcp_ok", "action": seed_action_name, "source": "fastpath_continue"})
        state["status"] = "IN_PROGRESS"
        state["handoff_required"] = False
        decision_consumed = True
        seed_action_name = ""
        seed_action_args = {}

    def _fallback_action() -> tuple[str, Dict[str, Any]]:
        if demo_action_plan:
            order = tool_result.get("order_query_tool", {}) if isinstance(tool_result.get("order_query_tool"), dict) else {}
            for step in demo_action_plan:
                name = str(step.get("name", "") or "")
                if not name or name in tool_result:
                    continue
                args = dict(step.get("args", {})) if isinstance(step.get("args"), dict) else {}
                if name == "refund_submit_mcp" and "amount" not in args:
                    args["amount"] = float(order.get("amount", 0) or 0)
                return name, args
            return "stage_done", {}
        policy = skill_result.get("policy", {}) if isinstance(skill_result.get("policy"), dict) else {}
        order = tool_result.get("order_query_tool", {}) if isinstance(tool_result.get("order_query_tool"), dict) else {}
        preferred = "ticket_upgrade_mcp"
        preferred_args: Dict[str, Any] = {"priority": "P1"}
        if bool(policy.get("manual_required", False)):
            preferred = "approval_submit_mcp"
            preferred_args = {"reason": "manual_required", "amount": float(order.get("amount", 0) or 0)}
        elif bool(policy.get("eligible", False)):
            preferred = "refund_submit_mcp"
            preferred_args = {"reason": "policy_eligible", "amount": float(order.get("amount", 0) or 0)}
        if preferred in tool_result:
            return "stage_done", {}
        return preferred, preferred_args

    def _demo_action_args(name: str) -> Dict[str, Any]:
        if not demo_action_plan:
            return {}
        order = tool_result.get("order_query_tool", {}) if isinstance(tool_result.get("order_query_tool"), dict) else {}
        for step in demo_action_plan:
            if str(step.get("name", "") or "") != name:
                continue
            args = dict(step.get("args", {})) if isinstance(step.get("args"), dict) else {}
            if name == "refund_submit_mcp" and "amount" not in args:
                args["amount"] = float(order.get("amount", 0) or 0)
            return args
        return {}

    for loop_idx in range(max_loops):
        if demo_action_plan:
            next_demo_name, _ = _fallback_action()
            if next_demo_name in {"stage_done", "finalize", "finish", "done"}:
                trace.append({"stage": "action", "loop_idx": loop_idx, "status": "stage_done", "source": "demo_plan_complete"})
                state["status"] = "IN_PROGRESS"
                state["handoff_required"] = False
                break
        executed_actions = [k for k in ["refund_submit_mcp", "ticket_upgrade_mcp", "approval_submit_mcp"] if k in tool_result]
        available_mcp_actions = [
            {"type": "mcp", "name": "refund_submit_mcp", "description": "提交退款", "parameters": {"type": "object", "properties": {"reason": {"type": "string"}, "amount": {"type": "number"}}}},
            {"type": "mcp", "name": "ticket_upgrade_mcp", "description": "升级工单", "parameters": {"type": "object", "properties": {"priority": {"type": "string"}}}},
            {"type": "mcp", "name": "approval_submit_mcp", "description": "提交审批", "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}}},
        ]
        if demo_action_plan:
            next_demo_name, _ = _fallback_action()
            available_mcp_actions = [x for x in available_mcp_actions if x.get("name") == next_demo_name]
        available_mcp_actions = [x for x in available_mcp_actions if x.get("name") not in executed_actions]
        allowed_mcp_names = {str(x.get("name", "")) for x in available_mcp_actions if x.get("name")}
        planner_ctx = build_context_with_budget(
            rag_context="",
            memory_items=state.get("memory_result", {}).get("items", []),
            total_budget_chars=min(2400, settings.context_total_budget_chars),
            memory_budget_ratio=min(0.35, settings.context_memory_budget_ratio),
            short_ratio=settings.context_memory_short_ratio,
            long_ratio=settings.context_memory_long_ratio,
            l3_ratio=settings.context_memory_l3_ratio,
            summarizer_enabled=False,
            summary_max_chars=settings.context_memory_summary_max_chars,
            system_policy="你是售后动作阶段规划器。每次只输出下一步动作；若动作阶段已完成，输出 stage_done。",
            scenario_rules=(
                "阶段=action；仅可输出 refund_submit_mcp/ticket_upgrade_mcp/approval_submit_mcp/stage_done。"
                + (" 当前演示场景需按顺序执行：approval_submit_mcp -> refund_submit_mcp -> stage_done。" if demo_action_plan else "")
            ),
            tool_facts=(
                f"policy={skill_result.get('policy', {})}\n"
                f"plan={skill_result.get('plan', {})}\n"
                f"tools={tool_result}\n"
                f"executed_actions={executed_actions}\n"
                f"last_trace={trace[-3:]}\n"
                f"{feedback_note_for_action}"
            ),
            user_query=query,
        )
        planner_name = ""
        planner_args: Dict[str, Any] = {}
        try:
            planner = decide_aftersales_next_step(
                query=query,
                context={"stage": "action", "context": planner_ctx.get("context", "")},
                step_idx=loop_idx,
                available_tools=[],
                available_skills=[],
                available_mcp=available_mcp_actions,
                allow_side_effect=True,
            )
            planner_name = str(planner.get("name", "") or "")
            planner_args = planner.get("arguments", {}) if isinstance(planner.get("arguments"), dict) else {}
        except Exception:
            planner_name = ""
            planner_args = {}

        if loop_idx == 0 and action_mode == "continue" and seed_action_name:
            action_name, action_args = seed_action_name, dict(seed_action_args)
            action_source = "resume_pending_action"
        elif planner_name in {"stage_done", "finalize", "finish", "done"}:
            trace.append({"stage": "action", "loop_idx": loop_idx, "status": "stage_done"})
            state["status"] = "IN_PROGRESS"
            state["handoff_required"] = False
            break
        elif planner_name in allowed_mcp_names:
            action_name, action_args = planner_name, dict(planner_args)
            action_source = "planner"
        else:
            action_name, action_args = _fallback_action()
            action_source = "policy_fallback"

        if demo_action_plan and action_name in {"refund_submit_mcp", "ticket_upgrade_mcp", "approval_submit_mcp"}:
            baseline_demo_args = _demo_action_args(action_name)
            if baseline_demo_args:
                action_args = {**baseline_demo_args, **(action_args if isinstance(action_args, dict) else {})}

        if (
            decision_consumed
            and action_mode == "continue"
            and action_name in {"refund_submit_mcp", "ticket_upgrade_mcp", "approval_submit_mcp"}
            and action_name in tool_result
        ):
            # Keep loop model-driven after resume: skip duplicated action and let planner
            # decide whether to stop or choose another action in following rounds.
            trace.append({"stage": "action", "loop_idx": loop_idx, "status": "duplicate_action_skip", "action": action_name})
            continue

        if action_name in {"stage_done", "finalize", "finish", "done"}:
            trace.append({"stage": "action", "loop_idx": loop_idx, "status": "stage_done", "source": action_source})
            state["status"] = "IN_PROGRESS"
            state["handoff_required"] = False
            break

        set_trace_metadata(action_loop_idx=loop_idx, action_name=action_name, action_source=action_source)
        if (
            not decision_consumed
            and action_mode == "continue"
            and decision in {"reject", "拒绝", "驳回"}
            and action_name == seed_action_name
        ):
            reject_notes: List[str] = []
            if decision_reason:
                reject_notes.append(f"拒绝理由: {decision_reason}")
            if decision_evidence:
                reject_notes.append(f"补充证据: {decision_evidence}")
            notes_text = "\n".join(reject_notes)
            state["answer"] = (
                f"人工已拒绝执行高风险动作 {action_name}，系统已终止自动副作用执行，转人工工单处理。"
                + (f"\n{notes_text}" if notes_text else "")
            )
            state["status"] = "HUMAN_REJECTED"
            state["handoff_required"] = True
            state["pending_action"] = {}
            state["allowed_actions"] = []
            state["rewind_stage_options"] = _build_rewind_options(str(state.get("run_id", "")))
            state.setdefault("debug", {})
            state["debug"]["human_decision_notes"] = {
                "decision": decision,
                "reason": decision_reason,
                "evidence": decision_evidence,
            }
            _set_stage(
                state,
                stage="action",
                stage_status="done",
                summary=f"人工拒绝动作: {action_name}",
                basis=[x for x in [decision_reason, decision_evidence] if x],
            )
            trace.append({"stage": "action", "loop_idx": loop_idx, "status": "human_reject", "action": action_name})
            state["aftersales_agent_result"] = {"trace": trace, "tool_result": tool_result, "skill_result": skill_result}
            return state
        need_human_gate = True
        if (
            not decision_consumed
            and action_mode == "continue"
            and decision in {"approve", "modify"}
            and action_name == seed_action_name
        ):
            need_human_gate = False
            if decision == "modify":
                overrides = human_decision.get("overrides", {}) if isinstance(human_decision.get("overrides"), dict) else {}
                if isinstance(overrides.get("action_args"), dict):
                    action_args = overrides["action_args"]
            decision_consumed = True

        if need_human_gate:
            approval_reason = str(
                action_args.get("risk_tip")
                or action_args.get("business_desc")
                or "side_effect_requires_approval"
            ).strip()
            pre_ckpt = CHECKPOINT_STORE.save_checkpoint(
                thread_id=_thread_id(state),
                run_id=str(state.get("run_id", "")),
                trace_id=str(state.get("trace_id", "")),
                event_id=str(state.get("event_id", "")),
                node_name="aftersales_pre_action_gate",
                status="wait_human",
                state=dict(state),
                parent_checkpoint_id=str(state.get("wf_checkpoint_id", "")),
                metadata={
                    "pending_action": action_name,
                    "pending_action_args": action_args,
                    "stage": "action",
                    "stage_status": "waiting_human",
                    "action_loop_idx": loop_idx,
                },
            )
            _set_wait_human(
                state,
                reason=approval_reason or "side_effect_requires_approval",
                pending_action_name=action_name,
                pending_action_args=action_args,
                checkpoint_id=pre_ckpt,
            )
            trace.append({"stage": "action", "loop_idx": loop_idx, "status": "wait_human", "action": action_name})
            state["aftersales_agent_result"] = {"trace": trace, "tool_result": tool_result, "skill_result": skill_result}
            return state

        mcp_resp = _execute_resume_action(
            action_name=action_name,
            action_args=action_args,
            tool_result=tool_result,
            scope_key=_thread_id(state),
            checkpoint_id=str(pending_action.get("checkpoint_id", "")),
        )
        tool_result[action_name] = mcp_resp
        state["aftersales_tool_result"] = tool_result
        state["pending_action"] = {}
        state["allowed_actions"] = []
        state["rewind_stage_options"] = _build_rewind_options(str(state.get("run_id", "")))
        if not mcp_resp.get("ok", False):
            _set_wait_human(
                state,
                reason="mcp_error_need_manual",
                pending_action_name=action_name,
                pending_action_args=action_args,
                checkpoint_id=str(pending_action.get("checkpoint_id", "")),
            )
            trace.append({"stage": "action", "loop_idx": loop_idx, "status": "mcp_error", "action": action_name})
            state["aftersales_agent_result"] = {"trace": trace, "tool_result": tool_result, "skill_result": skill_result}
            return state
        trace.append({"stage": "action", "loop_idx": loop_idx, "status": "mcp_ok", "action": action_name})
        state["status"] = "IN_PROGRESS"
        state["handoff_required"] = False

    state["aftersales_agent_result"] = {"trace": trace, "tool_result": tool_result, "skill_result": skill_result}
    return state


def rag_decision_node(state: ChatState) -> ChatState:
    state = _append_node_trace(state, "rag_decision")
    result = decide_rag_plan(
        query=state.get("query", ""),
        route_target=state.get("route_target", ""),
        aftersales_mode=state.get("aftersales_mode", ""),
        tool_result=state.get("aftersales_tool_result", {}),
        policy_result=state.get("aftersales_skill_result", {}).get("policy", {}),
    )
    state["rag_decision_result"] = result
    set_trace_metadata(
        need_rag=bool(result.get("need_rag", False)),
        rag_decision_reason=result.get("reason", ""),
        rag_decision_mode=result.get("mode", ""),
    )
    return state


def context_build_node(state: ChatState) -> ChatState:
    state = _append_node_trace(state, "context_build")
    t0 = time.perf_counter()
    rag_result = state.get("rag_result", {}) if isinstance(state.get("rag_result"), dict) else {}
    rag_context = rag_result.get("context", "")
    memory_items = state.get("memory_result", {}).get("items", [])
    route_target = state.get("route_target", "")
    is_aftersales_complex = route_target == "aftersales" and state.get("aftersales_mode") == "complex"
    aftersales_skill = state.get("aftersales_skill_result", {}) if is_aftersales_complex else {}
    policy = aftersales_skill.get("policy", {})
    plan = aftersales_skill.get("plan", {})
    fixed_policy = (
        "你是售后专家助手。优先遵守退款政策与风险分级。"
        "金额高或规则冲突时必须建议人工复核。"
    )
    ctx_policy = _chain_control(state, "context")
    ctx_budget_scale = max(0.3, float(ctx_policy.get("budget_scale", 1.0) or 1.0))
    ctx_memory_scale = max(0.1, float(ctx_policy.get("memory_budget_scale", 1.0) or 1.0))
    drop_tool_facts = bool(ctx_policy.get("drop_tool_facts", False))
    scenario_rules = (
        "场景: after_sales_complex\n"
        "输出必须包含: 判定结果、依据、下一步行动、预计时效。"
    ) if is_aftersales_complex else ""
    tool_facts = ""
    if is_aftersales_complex and not drop_tool_facts:
        tool_facts = (
            f"policy={policy}\n"
            f"plan={plan}\n"
            f"tools={state.get('aftersales_tool_result', {})}\n"
            f"trace={state.get('aftersales_agent_result', {}).get('trace', [])[-3:]}"
        )
    dbg = state.get("debug", {}) if isinstance(state.get("debug"), dict) else {}
    ref_inj = dbg.get("reference_injection") if isinstance(dbg.get("reference_injection"), dict) else {}
    quoted_lines: List[str] = []
    if str(ref_inj.get("snippet") or "").strip():
        qt = str(ref_inj.get("quote_text") or "").strip()
        if qt:
            quoted_lines.append(f"用户引用文字：{qt}")
        quoted_lines.append(str(ref_inj.get("snippet") or "").strip())
    quoted_context = "\n".join(quoted_lines) if quoted_lines else ""
    context_built = build_context_with_budget(
        rag_context=rag_context,
        memory_items=memory_items,
        total_budget_chars=max(400, int(settings.context_total_budget_chars * ctx_budget_scale)),
        memory_budget_ratio=max(0.05, min(0.8, settings.context_memory_budget_ratio * ctx_memory_scale)),
        short_ratio=settings.context_memory_short_ratio,
        long_ratio=settings.context_memory_long_ratio,
        l3_ratio=settings.context_memory_l3_ratio,
        summarizer_enabled=settings.context_memory_summarizer_enabled,
        summary_max_chars=settings.context_memory_summary_max_chars,
        system_policy=fixed_policy if is_aftersales_complex else "",
        scenario_rules=scenario_rules,
        tool_facts=tool_facts,
        quoted_context=quoted_context,
        user_query=state.get("query", ""),
    )
    context_built.setdefault("debug", {})
    context_built["debug"]["degrade_level"] = _degrade_level(state)
    context_built["debug"]["degrade_context_control"] = {
        "budget_scale": ctx_budget_scale,
        "memory_budget_scale": ctx_memory_scale,
        "drop_tool_facts": drop_tool_facts,
    }
    try:
        context_built["debug"]["context_build_latency_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
    except Exception:
        pass
    state["context_result"] = context_built
    state.setdefault("debug", {})
    state["debug"].setdefault("layer_controls", {})
    state["debug"]["layer_controls"]["context"] = {
        "ok": True,
        "degrade_level": _degrade_level(state),
        "budget_scale": ctx_budget_scale,
        "memory_budget_scale": ctx_memory_scale,
        "drop_tool_facts": drop_tool_facts,
    }
    return state


@traceable(name="aftersales_resume_executor", run_type="tool")
def _execute_resume_action(
    *,
    action_name: str,
    action_args: Dict[str, Any],
    tool_result: Dict[str, Any],
    scope_key: str,
    checkpoint_id: str,
    mcp_enabled: bool = True,
) -> Dict[str, Any]:
    if not mcp_enabled:
        return {
            "ok": False,
            "status_code": 429,
            "error": "mcp_disabled_by_degrade_policy",
        }
    order = tool_result.get("order_query_tool", {})
    order_id = str(order.get("order_id", "MOCK-AF-10086"))
    amount = float(action_args.get("amount", order.get("amount", 0) or 0))
    idem = _idempotency_key(
        scope_key=scope_key,
        node_name="aftersales_subgraph",
        memory_type=action_name,
        content=f"{order_id}:{amount}:{action_args}",
    )
    set_trace_metadata(
        resume_executor=True,
        resume_action_name=action_name,
        resume_action_checkpoint_id=checkpoint_id,
        resume_action_order_id=order_id,
    )
    if action_name == "refund_submit_mcp":
        return refund_submit_mcp(
            order_id=order_id,
            amount=amount,
            reason=str(action_args.get("reason", "policy_eligible")),
            idempotency_key=idem,
        )
    if action_name == "ticket_upgrade_mcp":
        return ticket_upgrade_mcp(
            order_id=order_id,
            priority=str(action_args.get("priority", "P1")),
            note="resume from human breakpoint",
            idempotency_key=idem,
        )
    return approval_submit_mcp(
        order_id=order_id,
        amount=amount,
        reason=str(action_args.get("reason", "manual_review")),
        idempotency_key=idem,
    )


@traceable(name="aftersales_subgraph", run_type="chain")
def aftersales_subgraph_node(state: ChatState) -> ChatState:
    state = _append_node_trace(state, "aftersales_subgraph")
    _set_stage(
        state,
        stage="facts",
        stage_status="running",
        summary="复杂售后子图启动，正在收集事实与制定策略",
    )
    query = state.get("query", "")
    trace: List[Dict[str, Any]] = []
    tool_result = dict(state.get("aftersales_tool_result", {}))
    skill_result = dict(state.get("aftersales_skill_result", {}))
    human_decision = state.get("human_decision", {}) or {}
    rewind_feedback = _rewind_feedback_payload(state)
    action_mode = str(state.get("action_mode", "auto") or "auto")
    pending_action = state.get("pending_action", {}) if isinstance(state.get("pending_action"), dict) else {}
    max_steps = int(os.getenv("AFTERSALES_MAX_STEPS", "5"))
    tool_policy = _chain_control(state, "tool")
    mcp_enabled = bool(tool_policy.get("enabled", True))
    state.setdefault("debug", {})
    state["debug"].setdefault("layer_controls", {})
    state["debug"]["layer_controls"]["tool_subgraph"] = {
        "enabled": mcp_enabled,
        "degrade_level": _degrade_level(state),
        "policy": tool_policy,
    }

    # Resume path: consume human decision at the exact breakpoint action first.
    if action_mode == "continue" and pending_action:
        pending_name = str(pending_action.get("action_name", "") or "")
        pending_args = pending_action.get("action_args", {}) if isinstance(pending_action.get("action_args"), dict) else {}
        decision = str(human_decision.get("decision", "") or "").lower()
        if decision not in {"approve", "reject", "modify"}:
            state["status"] = "NEED_HUMAN"
            state["handoff_required"] = True
            _set_stage(
                state,
                stage="action",
                stage_status="waiting_human",
                summary=f"断点动作待确认：{pending_name}",
                basis=["missing_human_decision"],
                required_input=["human_decision"],
            )
            state["allowed_actions"] = ["approve", "reject", "modify", "rewind"]
            state["rewind_stage_options"] = _build_rewind_options(str(state.get("run_id", "")))
            state.setdefault("debug", {})
            state["debug"]["breakpoint_state"] = {
                "final_state": "WAITING_HUMAN",
                "reason": "missing_human_decision",
                "pending_action_name": pending_name,
            }
            return state
        if decision == "reject":
            state["status"] = "NEED_HUMAN"
            state["handoff_required"] = True
            _set_stage(
                state,
                stage="action",
                stage_status="waiting_human",
                summary=f"人工拒绝动作：{pending_name}",
                basis=["human_reject"],
                required_input=["human_decision"],
            )
            state["allowed_actions"] = ["rewind", "modify"]
            state["rewind_stage_options"] = _build_rewind_options(str(state.get("run_id", "")))
            state.setdefault("debug", {})
            state["debug"]["breakpoint_state"] = {
                "final_state": "WAITING_HUMAN",
                "reason": "human_reject",
                "pending_action_name": pending_name,
            }
            return state
        if decision == "modify":
            overrides = human_decision.get("overrides", {}) if isinstance(human_decision.get("overrides"), dict) else {}
            if "action_args" in overrides and isinstance(overrides["action_args"], dict):
                pending_args = overrides["action_args"]
            if "manual_required" in overrides:
                skill_result.setdefault("policy", {})
                skill_result["policy"]["manual_required"] = bool(overrides["manual_required"])
        if decision in {"approve", "modify"} and pending_name in {"refund_submit_mcp", "ticket_upgrade_mcp", "approval_submit_mcp"}:
            if not mcp_enabled:
                _set_wait_human(
                    state,
                    reason="degraded_policy_mcp_disabled",
                    pending_action_name=pending_name,
                    pending_action_args=pending_args,
                    checkpoint_id=str(pending_action.get("checkpoint_id", "")),
                )
                return state
            _set_stage(
                state,
                stage="action",
                stage_status="running",
                summary=f"断点恢复，执行动作：{pending_name}",
                basis=["resume_from_breakpoint"],
            )
            mcp_resp = _execute_resume_action(
                action_name=pending_name,
                action_args=pending_args,
                tool_result=tool_result,
                scope_key=_thread_id(state),
                checkpoint_id=str(pending_action.get("checkpoint_id", "")),
                mcp_enabled=mcp_enabled,
            )
            tool_result[pending_name] = mcp_resp
            state["aftersales_tool_result"] = tool_result
            state["aftersales_skill_result"] = skill_result
            state["pending_action"] = {}
            state["allowed_actions"] = []
            state["rewind_stage_options"] = _build_rewind_options(str(state.get("run_id", "")))
            state["status"] = "IN_PROGRESS" if mcp_resp.get("ok", False) else "NEED_HUMAN"
            state["handoff_required"] = not bool(mcp_resp.get("ok", False))
            if state["handoff_required"]:
                _set_stage(
                    state,
                    stage="action",
                    stage_status="waiting_human",
                    summary=f"断点动作失败，等待人工处理：{pending_name}",
                    basis=["mcp_error_need_manual"],
                    required_input=["human_decision"],
                )
                state["allowed_actions"] = ["rewind", "modify"]
                state.setdefault("debug", {})
                state["debug"]["breakpoint_state"] = {
                    "final_state": "WAITING_HUMAN",
                    "reason": "mcp_error_need_manual",
                    "pending_action_name": pending_name,
                }
                return state
            state.setdefault("debug", {})
            state["debug"]["breakpoint_state"] = {
                "final_state": "RESUMED",
                "reason": "human_approved_or_modified",
                "pending_action_name": pending_name,
                "mcp_ok": True,
            }
            state["aftersales_agent_result"] = {
                "trace": [
                    {
                        "step": -1,
                        "planner": {"name": pending_name, "arguments": pending_args, "source": "resume"},
                        "status": "resumed_mcp_ok",
                    }
                ],
                "tool_result": tool_result,
                "skill_result": skill_result,
            }
            return state

    step = 0
    while step < max_steps:
        biz_stage = _current_aftersales_stage(tool_result, skill_result)
        _set_stage(
            state,
            stage=biz_stage,
            stage_status="running",
            summary=f"复杂售后阶段执行中: {biz_stage}",
            basis=[f"step={step}"],
        )
        skills = progressive_skills(step)
        available_tools = [
            {
                "type": "tool",
                "name": "order_query_tool",
                "description": "查询订单支付、签收、金额与品类事实",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
            },
            {
                "type": "tool",
                "name": "ticket_query_tool",
                "description": "查询历史工单、升级状态、人工审批需求",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
            },
            {
                "type": "tool",
                "name": "logistics_query_tool",
                "description": "查询物流签收与破损反馈事实",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
            },
        ]
        available_skills = [
            {"type": "skill", "name": s.get("skill_name", ""), "description": s.get("when_to_use", ""), "parameters": s.get("inputs_schema", {"type": "object", "properties": {}})}
            for s in skills
            if s.get("skill_name")
        ]
        available_mcp = [
            {
                "type": "mcp",
                "name": "refund_submit_mcp",
                "description": "向外部退款系统提交退款申请（副作用）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {"type": "string"},
                        "amount": {"type": "number"},
                    },
                },
            },
            {
                "type": "mcp",
                "name": "ticket_upgrade_mcp",
                "description": "向工单系统提交升级操作（副作用）",
                "parameters": {"type": "object", "properties": {"priority": {"type": "string"}}},
            },
            {
                "type": "mcp",
                "name": "approval_submit_mcp",
                "description": "向审批系统提交审核（副作用）",
                "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}},
            },
        ]
        if not mcp_enabled:
            available_mcp = []
        planner = decide_aftersales_next_step(
            query=query,
            context={
                "tool_result": tool_result,
                "policy_eval": skill_result.get("policy", {}),
                "plan": skill_result.get("plan", {}),
                "trace": trace[-3:],
                "rewind_feedback": rewind_feedback,
            },
            step_idx=step,
            available_tools=available_tools,
            available_skills=available_skills,
            available_mcp=available_mcp,
            allow_side_effect=True,
        )
        name = str(planner.get("name", "") or "")
        args = planner.get("arguments", {}) if isinstance(planner.get("arguments"), dict) else {}
        name, args, stage_gate_reason = _force_action_by_stage(
            stage=biz_stage,
            planned_name=name,
            planned_args=args,
            tool_result=tool_result,
            skill_result=skill_result,
        )
        trace_item: Dict[str, Any] = {"step": step, "planner": planner, "status": "ok"}
        set_trace_metadata(
            subgraph_step=step,
            action_name=name,
            aftersales_stage=biz_stage,
            stage_gate_reason=stage_gate_reason,
            route_target=state.get("route_target", ""),
            aftersales_mode=state.get("aftersales_mode", ""),
        )
        trace_item["stage"] = biz_stage
        trace_item["stage_gate_reason"] = stage_gate_reason

        if name == "order_query_tool":
            tool_result["order_query_tool"] = order_query_tool(query)
        elif name == "ticket_query_tool":
            tool_result["ticket_query_tool"] = ticket_query_tool(query)
        elif name == "logistics_query_tool":
            tool_result["logistics_query_tool"] = logistics_query_tool(query)
        elif name == "refund_policy_skill":
            skill_result["policy"] = evaluate_refund_policy(query, tool_result)
        elif name == "aftersales_plan_skill":
            skill_result["plan"] = generate_aftersales_plan(skill_result.get("policy", {}), tool_result)
        elif name == "human_gate":
            if not human_decision:
                state["handoff_required"] = True
                state["status"] = "NEED_HUMAN"
                pre_ckpt = CHECKPOINT_STORE.save_checkpoint(
                    thread_id=_thread_id(state),
                    run_id=str(state.get("run_id", "")),
                    trace_id=str(state.get("trace_id", "")),
                    event_id=str(state.get("event_id", "")),
                    node_name="aftersales_pre_action_gate",
                    status="wait_human",
                    state=dict(state),
                    parent_checkpoint_id=str(state.get("wf_checkpoint_id", "")),
                    metadata={
                        "pending_action": "human_gate",
                        "pending_action_args": args,
                        "stage": "action",
                        "stage_status": "waiting_human",
                    },
                )
                state.setdefault("debug", {})
                state["debug"]["pending_human_gate"] = {
                    "reason": args.get("reason", "manual_required"),
                    "checkpoint_id": pre_ckpt,
                }
                trace_item["status"] = "wait_human"
                trace.append(trace_item)
                state["aftersales_agent_result"] = {
                    "trace": trace,
                    "tool_result": tool_result,
                    "skill_result": skill_result,
                }
                return state
            decision = str(human_decision.get("decision", "") or "").lower()
            if decision == "reject":
                state["answer"] = "该复杂售后请求已由人工拒绝自动执行，建议走人工工单流程。"
                trace_item["status"] = "human_reject"
                trace.append(trace_item)
                break
            if decision == "modify":
                overrides = human_decision.get("overrides", {}) if isinstance(human_decision.get("overrides"), dict) else {}
                if "manual_required" in overrides:
                    skill_result.setdefault("policy", {})
                    skill_result["policy"]["manual_required"] = bool(overrides["manual_required"])
                trace_item["status"] = "human_modify"
                trace_item["overrides"] = overrides
            else:
                trace_item["status"] = "human_approve"
        elif name in {"refund_submit_mcp", "ticket_upgrade_mcp", "approval_submit_mcp"}:
            if not mcp_enabled:
                _set_wait_human(
                    state,
                    reason="degraded_policy_mcp_disabled",
                    pending_action_name=name,
                    pending_action_args=args,
                    checkpoint_id="",
                )
                trace_item["status"] = "mcp_disabled_wait_human"
                trace.append(trace_item)
                state["aftersales_agent_result"] = {
                    "trace": trace,
                    "tool_result": tool_result,
                    "skill_result": skill_result,
                }
                return state
            _set_stage(
                state,
                stage="action",
                stage_status="running",
                summary=f"准备执行高风险动作: {name}",
            )
            policy_eval = skill_result.get("policy", {})
            decision = str(human_decision.get("decision", "") or "").lower()
            manual_required = bool(policy_eval.get("manual_required", False))
            must_gate = True  # all side effects must pass human gate
            if action_mode == "continue" and decision == "approve" and state.get("pending_action", {}).get("action_name") == name:
                must_gate = False
            if must_gate:
                pre_ckpt = CHECKPOINT_STORE.save_checkpoint(
                    thread_id=_thread_id(state),
                    run_id=str(state.get("run_id", "")),
                    trace_id=str(state.get("trace_id", "")),
                    event_id=str(state.get("event_id", "")),
                    node_name="aftersales_pre_action_gate",
                    status="wait_human",
                    state=dict(state),
                    parent_checkpoint_id=str(state.get("wf_checkpoint_id", "")),
                    metadata={
                        "pending_action": name,
                        "pending_action_args": args,
                        "stage": "action",
                        "stage_status": "waiting_human",
                    },
                )
                reason = "manual_required_before_side_effect" if manual_required else "side_effect_requires_approval"
                _set_wait_human(
                    state,
                    reason=reason,
                    pending_action_name=name,
                    pending_action_args=args,
                    checkpoint_id=pre_ckpt,
                )
                trace_item["status"] = "wait_human_before_mcp"
                trace.append(trace_item)
                state["aftersales_agent_result"] = {
                    "trace": trace,
                    "tool_result": tool_result,
                    "skill_result": skill_result,
                }
                return state
            pre_ckpt = CHECKPOINT_STORE.save_checkpoint(
                thread_id=_thread_id(state),
                run_id=str(state.get("run_id", "")),
                trace_id=str(state.get("trace_id", "")),
                event_id=str(state.get("event_id", "")),
                node_name="aftersales_pre_action_checkpoint",
                status="ok",
                state=dict(state),
                parent_checkpoint_id=str(state.get("wf_checkpoint_id", "")),
                metadata={"pending_action": name, "stage": "action", "stage_status": "running"},
            )
            trace_item["pre_action_checkpoint_id"] = pre_ckpt
            order = tool_result.get("order_query_tool", {})
            order_id = str(order.get("order_id", "MOCK-AF-10086"))
            amount = float(args.get("amount", order.get("amount", 0) or 0))
            idem = _idempotency_key(
                scope_key=_thread_id(state),
                node_name="aftersales_subgraph",
                memory_type=name,
                content=f"{order_id}:{amount}:{args}",
            )
            if name == "refund_submit_mcp":
                mcp_resp = refund_submit_mcp(order_id=order_id, amount=amount, reason=str(args.get("reason", "policy_eligible")), idempotency_key=idem)
            elif name == "ticket_upgrade_mcp":
                mcp_resp = ticket_upgrade_mcp(order_id=order_id, priority=str(args.get("priority", "P1")), note="auto from aftersales complex", idempotency_key=idem)
            else:
                mcp_resp = approval_submit_mcp(order_id=order_id, amount=amount, reason=str(args.get("reason", "manual_review")), idempotency_key=idem)
            tool_result[name] = mcp_resp
            trace_item["mcp_resp"] = {"ok": mcp_resp.get("ok", False), "status_code": mcp_resp.get("status_code", 0)}
            if not mcp_resp.get("ok", False):
                _set_wait_human(
                    state,
                    reason="mcp_error_need_manual",
                    pending_action_name=name,
                    pending_action_args=args,
                    checkpoint_id=pre_ckpt,
                )
                trace_item["status"] = "mcp_error_wait_human"
                trace.append(trace_item)
                state["aftersales_agent_result"] = {"trace": trace, "tool_result": tool_result, "skill_result": skill_result}
                return state
        else:
            # finalize_answer or unknown action
            trace_item["status"] = "finalize"
            trace.append(trace_item)
            break
        trace.append(trace_item)
        step += 1

    state["aftersales_tool_result"] = tool_result
    rewind_reflection = _apply_rewind_feedback_mock(skill_result, rewind_feedback)
    state["aftersales_skill_result"] = skill_result
    state["aftersales_agent_result"] = {"trace": trace, "tool_result": tool_result, "skill_result": skill_result}
    state["pending_action"] = {}
    state["allowed_actions"] = []
    state["rewind_stage_options"] = _build_rewind_options(str(state.get("run_id", "")))
    _set_stage(
        state,
        stage="finalize",
        stage_status="done",
        summary="复杂售后子图执行完成",
        basis=skill_result.get("policy", {}).get("reasons", []) if isinstance(skill_result.get("policy", {}), dict) else [],
    )
    state["tool_result"] = {
        "tool_name": "aftersales_subgraph_executor",
        "tool_status": "success",
        "data": {"tool_result": tool_result, "skill_result": skill_result},
    }
    if rewind_feedback:
        state.setdefault("debug", {})
        state["debug"]["rewind_feedback_reflection"] = rewind_reflection
    return state


def tool_call_node(state: ChatState) -> ChatState:
    state = _append_node_trace(state, "tool_call")
    route_target = str(state.get("route_target", "faq") or "faq")
    tool_policy = _chain_control(state, "tool")
    tool_enabled = bool(tool_policy.get("enabled", True))
    tool_timeout_scale = float(tool_policy.get("timeout_scale", 1.0) or 1.0)
    tool_retry_cap = int(tool_policy.get("retry_cap", 1) or 1)
    if not tool_enabled:
        state["tool_result"] = {
            "tool_name": "mock_tool",
            "tool_status": "degraded",
            "data": {},
            "error": "tool_disabled_by_degrade_policy",
        }
        state.setdefault("debug", {})
        state["debug"].setdefault("layer_controls", {})
        state["debug"]["layer_controls"]["tool"] = {
            "ok": False,
            "timeout": False,
            "error": "tool_disabled_by_degrade_policy",
            "attempts": 0,
            "retried": 0,
            "timeout_budget_s": 0.0,
            "retry_budget": 0,
            "backoff_budget_s": 0.0,
            "route_target": route_target,
            "degrade_level": _degrade_level(state),
        }
        return state
    demo_tool = demo_product_tool(state.get("query", "")) if route_target == "product_info" else None
    if demo_tool is not None:
        state["tool_result"] = demo_tool
        state.setdefault("debug", {})
        state["debug"].setdefault("layer_controls", {})
        state["debug"]["layer_controls"]["tool"] = {
            "ok": True,
            "timeout": False,
            "error": "",
            "attempts": 1,
            "retried": 0,
            "timeout_budget_s": 0.0,
            "retry_budget": 0,
            "backoff_budget_s": 0.0,
            "route_target": route_target,
            "source": "demo_mock",
        }
        return state
    tool_timeout = _layer_timeout_budget(
        "tool",
        route_target,
        float(os.getenv("TOOL_LAYER_TIMEOUT_SECONDS", "1.5")),
    )
    tool_timeout = max(0.05, float(tool_timeout) * max(0.1, tool_timeout_scale))
    tool_retries = _layer_retry_budget(
        "tool",
        route_target,
        int(os.getenv("TOOL_LAYER_RETRY", "1")),
    )
    tool_retries = max(0, min(tool_retries, tool_retry_cap))
    tool_backoff = _layer_backoff_budget(
        "tool",
        route_target,
        float(os.getenv("TOOL_LAYER_RETRY_BACKOFF_SECONDS", "0.15")),
    )
    tool_result, tool_ctrl = _run_with_layer_control(
        fn=lambda: run_mock_tool(state["route_target"], state["query"]),
        timeout_seconds=tool_timeout,
        retries=tool_retries,
        backoff_seconds=tool_backoff,
        default_value={
            "tool_name": "mock_tool",
            "tool_status": "timeout_fallback",
            "data": {},
            "error": "tool_timeout",
        },
    )
    if not tool_ctrl.get("ok", False):
        tool_result = dict(tool_result)
        tool_result["tool_status"] = "degraded"
        tool_result["error"] = tool_ctrl.get("error", "tool_error")
        tool_result["timeout"] = bool(tool_ctrl.get("timeout", False))
    state.setdefault("debug", {})
    state["debug"].setdefault("layer_controls", {})
    state["debug"]["layer_controls"]["tool"] = {
        **tool_ctrl,
        "timeout_budget_s": tool_timeout,
        "retry_budget": tool_retries,
        "backoff_budget_s": tool_backoff,
        "route_target": route_target,
        "degrade_level": _degrade_level(state),
    }
    state["tool_result"] = tool_result
    return state


def rag_query_node(state: ChatState) -> ChatState:
    state = _append_node_trace(state, "rag_query")
    route_target = state.get("route_target")
    rag_policy = _chain_control(state, "rag")
    rag_enabled_by_policy = bool(rag_policy.get("enabled", True))
    rag_max_chunks = int(rag_policy.get("max_chunks", 0) or 0)
    if not rag_enabled_by_policy:
        state["rag_result"] = {
            "enabled": False,
            "reason": "degraded_policy_skip_rag",
            "degraded": True,
            "chunks": [],
            "citations": [],
            "context": "",
        }
        state.setdefault("debug", {})
        state["debug"].setdefault("layer_controls", {})
        state["debug"]["layer_controls"]["rag"] = {
            "ok": False,
            "timeout": False,
            "error": "degraded_policy_skip_rag",
            "attempts": 0,
            "retried": 0,
            "timeout_budget_s": 0.0,
            "retry_budget": 0,
            "backoff_budget_s": 0.0,
            "route_target": str(route_target or "faq"),
            "degrade_level": _degrade_level(state),
        }
        return state
    if route_target == "risk_query":
        state["rag_result"] = {"enabled": False, "reason": "risk_query_skip"}
        return state

    mode = os.getenv("RAG_RETRIEVAL_MODE", "hybrid")
    demo_rag = demo_rag_result(
        query=state.get("query", ""),
        domain=str(route_target or "faq"),
        mode=mode,
    )
    if demo_rag is not None:
        state["rag_result"] = demo_rag
        state.setdefault("debug", {})
        state["debug"].setdefault("layer_controls", {})
        state["debug"]["layer_controls"]["rag"] = {
            "ok": True,
            "timeout": False,
            "error": "",
            "attempts": 1,
            "retried": 0,
            "timeout_budget_s": 0.0,
            "retry_budget": 0,
            "backoff_budget_s": 0.0,
            "route_target": str(route_target or "faq"),
            "source": "demo_mock",
        }
        return state
    rag_timeout = _layer_timeout_budget(
        "rag",
        str(route_target or "faq"),
        float(os.getenv("RAG_LAYER_TIMEOUT_SECONDS", "2.2")),
    )
    rag_retries = _layer_retry_budget(
        "rag",
        str(route_target or "faq"),
        int(os.getenv("RAG_LAYER_RETRY", "1")),
    )
    rag_backoff = _layer_backoff_budget(
        "rag",
        str(route_target or "faq"),
        float(os.getenv("RAG_LAYER_RETRY_BACKOFF_SECONDS", "0.2")),
    )
    rag_result, rag_ctrl = _run_with_layer_control(
        fn=lambda: RETRIEVER.retrieve(
            query=state["query"],
            domain=route_target,
            retrieval_mode=mode,
        ),
        timeout_seconds=rag_timeout,
        retries=rag_retries,
        backoff_seconds=rag_backoff,
        default_value={
            "enabled": False,
            "error": "rag_timeout",
            "mode": mode,
            "params": {"vector_topk": None, "keyword_topk": None, "final_topk": None},
            "candidates": {"vector": [], "keyword": [], "fused": []},
            "chunks": [],
            "citations": [],
            "context": "",
        },
    )
    if not rag_ctrl.get("ok", False):
        rag_result = dict(rag_result)
        rag_result["enabled"] = False
        rag_result["error"] = rag_ctrl.get("error", "rag_error")
        rag_result["timeout"] = bool(rag_ctrl.get("timeout", False))
        rag_result["degraded"] = True
    if rag_max_chunks > 0 and isinstance(rag_result.get("chunks"), list):
        chunks = list(rag_result.get("chunks", []))
        if len(chunks) > rag_max_chunks:
            chunks = chunks[:rag_max_chunks]
            rag_result["chunks"] = chunks
            rag_result["context"] = "\n\n".join(
                str(c.get("text") or c.get("content") or "") for c in chunks
            ).strip()
            rag_result["citations"] = [
                str(c.get("source_name") or c.get("chunk_id") or "")
                for c in chunks
                if str(c.get("source_name") or c.get("chunk_id") or "")
            ]
            rag_result["degraded"] = True
            rag_result["degrade_reason"] = "rag_chunk_cap"
    state.setdefault("debug", {})
    state["debug"].setdefault("layer_controls", {})
    state["debug"]["layer_controls"]["rag"] = {
        **rag_ctrl,
        "timeout_budget_s": rag_timeout,
        "retry_budget": rag_retries,
        "backoff_budget_s": rag_backoff,
        "route_target": str(route_target or "faq"),
        "degrade_level": _degrade_level(state),
    }
    state["rag_result"] = rag_result
    return state


def draft_answer_node(state: ChatState) -> ChatState:
    state = _append_node_trace(state, "draft_answer")
    route_target = state["route_target"]
    tool_result = state.get("tool_result", {})
    rag_result = state.get("rag_result", {})
    rag_chunks = rag_result.get("chunks", [])
    if rag_chunks:
        first_chunk = rag_chunks[0]
        chunk_text = first_chunk.get("text") or first_chunk.get("content") or ""
        rag_hint = chunk_text[:80] if chunk_text else "暂无检索证据"
    else:
        rag_hint = "暂无检索证据"
    if route_target == "risk_query":
        state["answer"] = "该问题命中风险场景，建议转人工客服确认后答复。"
        state["citations"] = rag_result.get("citations", [])
        return state

    demo_ans = demo_answer(state.get("query", ""))
    if demo_ans:
        state["answer"] = demo_ans
        state["citations"] = rag_result.get("citations", [])
        state.setdefault("debug", {})
        state["debug"]["llm"] = {
            "provider": "demo_mock",
            "model": "demo_answer_template",
            "fallback": False,
            "timeout": False,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
        state["debug"].setdefault("layer_controls", {})
        state["debug"]["layer_controls"]["llm"] = {
            "ok": True,
            "timeout": False,
            "error": "",
            "attempts": 1,
            "retried": 0,
            "timeout_budget_s": 0.0,
            "retry_budget": 0,
            "backoff_budget_s": 0.0,
            "route_target": route_target,
            "source": "demo_mock",
        }
        return state

    rag_context = rag_result.get("context", "")
    is_aftersales_complex = route_target == "aftersales" and state.get("aftersales_mode") == "complex"
    aftersales_skill = state.get("aftersales_skill_result", {}) if is_aftersales_complex else {}
    policy = aftersales_skill.get("policy", {})
    plan = aftersales_skill.get("plan", {})
    context_built = state.get("context_result", {}) if isinstance(state.get("context_result"), dict) else {}
    if not context_built:
        context_built = {"context": rag_context, "debug": {}}
        state["context_result"] = context_built
    llm_timeout = _layer_timeout_budget(
        "llm",
        route_target,
        float(os.getenv("LLM_LAYER_TIMEOUT_SECONDS", os.getenv("LLM_TIMEOUT", "60"))),
    )
    llm_retries = _layer_retry_budget(
        "llm",
        route_target,
        int(os.getenv("LLM_LAYER_RETRY", "0")),
    )
    llm_backoff = _layer_backoff_budget(
        "llm",
        route_target,
        float(os.getenv("LLM_LAYER_RETRY_BACKOFF_SECONDS", "0.3")),
    )
    llm_ctrl: Dict[str, Any] = {}
    try:
        llm_result, llm_ctrl = _run_with_layer_control(
            fn=lambda: generate_answer_with_litellm(
                query=state["query"],
                route_target=route_target,
                rag_context=context_built.get("context", rag_context),
                tool_result=tool_result,
            ),
            timeout_seconds=llm_timeout,
            retries=llm_retries,
            backoff_seconds=llm_backoff,
            default_value={},
        )
        if not llm_ctrl.get("ok", False):
            raise RuntimeError(llm_ctrl.get("error", "llm_timeout_or_error"))
        state["answer"] = llm_result["answer"]
        state.setdefault("debug", {})
        state["debug"].setdefault("layer_controls", {})
        state["debug"]["layer_controls"]["llm"] = {
            **llm_ctrl,
            "timeout_budget_s": llm_timeout,
            "retry_budget": llm_retries,
            "backoff_budget_s": llm_backoff,
            "route_target": route_target,
        }
        state["debug"]["llm"] = {
            "provider": llm_result.get("provider", "litellm"),
            "model": llm_result.get("model", ""),
            "fallback": llm_result.get("fallback", False),
            "timeout": False,
            "usage": llm_result.get("usage", {}),
        }
    except Exception as exc:
        # Keep service stable when local model is unavailable.
        fallback_answers: Dict[str, str] = {
            "faq": f"这是 FAQ 场景回复。根据知识库检索，关键信息：{rag_hint}",
            "aftersales": (
                "这是售后场景的兜底回复。"
                f"Mock 工具结果: {tool_result.get('data', {}).get('refund_status', 'unknown')}。"
            ),
            "product_info": (
                "这是商品咨询场景的兜底回复。"
                f"Mock 工具价格: {tool_result.get('data', {}).get('price', 'N/A')}。"
                f" 知识库参考: {rag_hint}"
            ),
            "after_sales_complex": (
                f"复杂售后建议：{plan.get('customer_message', '建议转人工复核')} "
                f"依据: {policy.get('reasons', [])}"
            ),
        }
        if is_aftersales_complex:
            state["answer"] = fallback_answers["after_sales_complex"]
        else:
            state["answer"] = fallback_answers.get(route_target, "已收到你的问题。")
        state.setdefault("debug", {})
        state["debug"].setdefault("layer_controls", {})
        base_ctrl = llm_ctrl or {
            "ok": False,
            "timeout": "timeout" in str(exc).lower(),
            "error": str(exc),
            "attempts": 1,
            "retried": 0,
        }
        state["debug"]["layer_controls"]["llm"] = {
            **base_ctrl,
            "timeout_budget_s": llm_timeout,
            "retry_budget": llm_retries,
            "backoff_budget_s": llm_backoff,
            "route_target": route_target,
        }
        state["debug"]["llm"] = {
            "provider": "litellm",
            "fallback": True,
            "error": str(exc),
            "timeout": bool((llm_ctrl or {}).get("timeout", False) or ("timeout" in str(exc).lower())),
        }
    state["citations"] = rag_result.get("citations", [])
    return state


def handoff_decision_node(state: ChatState) -> ChatState:
    state = _append_node_trace(state, "handoff_decision")
    if state.get("status") == "NEED_HUMAN":
        state["handoff_required"] = True
        _set_stage(
            state,
            stage="action",
            stage_status="waiting_human",
            summary="命中高危断点，等待人工输入决策",
            required_input=["human_decision"],
        )
        existing_debug = state.get("debug", {})
        llm_context = state.get("context_result", {}).get("context", "")
        state["debug"] = {
            "cache": state.get("cache_result", {}),
            "tool": state.get("tool_result", {}),
            "aftersales_skill": state.get("aftersales_skill_result", {}),
            "aftersales_agent": state.get("aftersales_agent_result", {}),
            "memory": {
                "hit": state.get("memory_result", {}).get("hit", False),
                "hit_count": state.get("memory_result", {}).get("hit_count", 0),
                "context_debug": state.get("context_result", {}).get("debug", {}),
            },
            "context": {
                "llm_context_chars": len(llm_context or ""),
                "llm_context": llm_context if settings.enable_debug else "",
            },
            "llm": existing_debug.get("llm"),
            "layer_controls": existing_debug.get("layer_controls", {}),
            "next_node": "human_handoff",
            "node_trace": state.get("node_trace", []),
            "breakpoint_state": {
                "final_state": "WAITING_HUMAN",
                "reason": "high_risk_breakpoint",
                "pending_action": state.get("pending_action", {}),
            },
        }
        return state
    # Business-first handoff policy:
    # - risk_query is routed to handoff in intent subgraph.
    # - aftersales_complex keeps planner-executor path and may pause at action breakpoint.
    # - faq unresolved feedback can actively trigger handoff in feedback_gate.
    handoff_required = bool(state.get("handoff_required", False))
    state["handoff_required"] = handoff_required
    state["status"] = "NEED_HUMAN" if handoff_required else "AUTO_DRAFT"
    existing_debug = state.get("debug", {})
    llm_context = state.get("context_result", {}).get("context", "")
    state["debug"] = {
        "cache": state.get("cache_result", {}),
        "rag": {
            "enabled": state.get("rag_result", {}).get("enabled", False),
            "mode": state.get("rag_result", {}).get("mode"),
            "params": state.get("rag_result", {}).get("params", {}),
            "rerank": state.get("rag_result", {}).get("params", {}).get("rerank", {}),
            "filters": state.get("rag_result", {}).get("filters", {}),
            "embedding_mode": state.get("rag_result", {}).get("filters", {}).get("embedding_mode"),
            "embedding_model": state.get("rag_result", {}).get("filters", {}).get("embedding_model"),
            "vector_column": state.get("rag_result", {}).get("filters", {}).get("vector_column"),
            "retrieved_count": len(state.get("rag_result", {}).get("chunks", [])),
            "candidates": state.get("rag_result", {}).get("candidates", {}),
        },
        "routing": {
            "intent_confidence": state.get("intent_confidence"),
            "intent_conf_threshold": settings.intent_conf_threshold,
        },
        "tool": state.get("tool_result", {}),
        "aftersales_skill": state.get("aftersales_skill_result", {}),
        "aftersales_agent": state.get("aftersales_agent_result", {}),
        "memory": {
            "hit": state.get("memory_result", {}).get("hit", False),
            "hit_count": state.get("memory_result", {}).get("hit_count", 0),
            "session_found": state.get("memory_result", {}).get("session_found", False),
            "short_memory_storage_table": state.get("memory_result", {}).get("short_memory_storage_table", ""),
            "short_memory_session_id": state.get("memory_result", {}).get("short_memory_session_id", ""),
            "session_turn_count": state.get("memory_result", {}).get("session_turn_count", 0),
            "session_compressed_count": state.get("memory_result", {}).get("session_compressed_count", 0),
            "error": state.get("memory_result", {}).get("error"),
            "context_debug": state.get("context_result", {}).get("debug", {}),
        },
        "context": {
            "llm_context_chars": len(llm_context or ""),
            "llm_context": llm_context if settings.enable_debug else "",
        },
        "llm": existing_debug.get("llm"),
        "layer_controls": existing_debug.get("layer_controls", {}),
        "next_node": "human_handoff" if handoff_required else "auto_reply",
        "node_trace": state.get("node_trace", []),
        "workflow_control": {
            "run_id": state.get("run_id", ""),
            "current_stage": state.get("current_stage", ""),
            "stage_status": state.get("stage_status", ""),
            "stage_summary": state.get("stage_summary", ""),
            "decision_basis": state.get("decision_basis", []),
            "required_user_input": state.get("required_user_input", []),
            "allowed_actions": state.get("allowed_actions", []),
            "rewind_stage_options": state.get("rewind_stage_options", []),
            "pending_action": state.get("pending_action", {}),
        },
    }
    return state


def memory_write_node(state: ChatState) -> ChatState:
    state = _append_node_trace(state, "memory_write")
    if not _memory_enabled(state):
        state["memory_write_result"] = {"writes": [], "disabled": True, "reason": "memory_disabled"}
        return state
    result: Dict[str, Any] = {"writes": []}
    query = (state.get("query", "") or "").strip()
    answer = (state.get("answer", "") or "").strip()
    if not query or not answer:
        state["memory_write_result"] = {"writes": [], "skipped_reason": "empty_turn"}
        return state
    tenant_id = state.get("tenant_id", "")
    user_id = state.get("user_id", "")
    thread_id = _thread_id(state)
    trace_id = state.get("trace_id", "")
    event_id = state.get("event_id", "")
    citations = state.get("citations", [])
    turn_content = f"Q: {query}\nA: {answer}"
    session_write: Dict[str, Any] = {}
    try:
        session_write = MEMORY_STORE.upsert_session_turn(
            session_id=state.get("conversation_id") or thread_id,
            tenant_id=tenant_id,
            user_id=user_id,
            thread_id=thread_id,
            query=query,
            answer=answer,
            trace_id=trace_id,
            node_name="memory_write",
            max_recent_turns=settings.memory_recent_turns,
            metadata={"route_target": state.get("route_target", "faq")},
        )
    except Exception as exc:
        # Memory write path must not break answer serving.
        result["session_write_error"] = str(exc)
    result["session_write"] = session_write

    # Cache HIT should still update short/session memory.
    if state.get("served_by_cache", False):
        result["long_skipped"] = {"passed": False, "reason": "served_by_cache"}
        result["l3_skipped"] = {"reason": "served_by_cache"}
        state["memory_write_result"] = result
        return state

    long_admission = _memory_admission_for_long(
        state,
        session_turn_count=int(session_write.get("turn_count", 0) or 0),
    )
    if bool(long_admission.get("passed", False)):
        long_scope = _long_scope(tenant_id, user_id)
        try:
            long_write = MEMORY_STORE.write_memory(
                tenant_id=tenant_id,
                user_id=user_id,
                thread_id=long_scope,
                memory_type="long",
                source_node="memory_write",
                source_event_id=event_id,
                trace_id=trace_id,
                content=turn_content,
                summary=answer[:200],
                citations=citations,
                metadata={"route_target": state.get("route_target", "faq")},
                confidence=float(state.get("intent_confidence", 0.0)),
                importance_score=0.8,
                admission_passed=True,
                admission_reason=str(long_admission.get("reason", "pass")),
                idempotency_key=_idempotency_key(
                    scope_key=long_scope,
                    node_name="memory_write",
                    memory_type="long",
                    content=turn_content,
                ),
                ttl_seconds=settings.memory_long_ttl_seconds,
            )
            result["writes"].append(long_write)
        except Exception as exc:
            result["long_write_error"] = str(exc)
    else:
        result["long_skipped"] = long_admission

    rag_chunks = state.get("rag_result", {}).get("chunks", [])
    if rag_chunks:
        l3_content = "\n".join([(c.get("text") or c.get("content") or "")[:120] for c in rag_chunks[:3]])
        if l3_content.strip():
            l3_scope = _l3_scope(tenant_id, user_id, state.get("conversation_id") or thread_id)
            try:
                l3_write = MEMORY_STORE.write_memory(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    thread_id=l3_scope,
                    memory_type="l3",
                    source_node="memory_write",
                    source_event_id=event_id,
                    trace_id=trace_id,
                    content=f"query={query}\nretrieved={l3_content}",
                    summary=l3_content[:160],
                    citations=citations,
                    metadata={"route_target": state.get("route_target", "faq")},
                    confidence=0.6,
                    importance_score=0.6,
                    admission_passed=True,
                    admission_reason="l3_from_rag",
                    idempotency_key=_idempotency_key(
                        scope_key=l3_scope,
                        node_name="memory_write",
                        memory_type="l3",
                        content=f"{query}:{l3_content}",
                    ),
                    ttl_seconds=settings.memory_l3_ttl_seconds,
                )
                result["writes"].append(l3_write)
            except Exception as exc:
                result["l3_write_error"] = str(exc)
    else:
        result["l3_skipped"] = {"reason": "no_rag_chunks"}
    error_fields = [k for k in ("session_write_error", "long_write_error", "l3_write_error") if result.get(k)]
    if error_fields:
        result["error"] = True
        result["error_fields"] = error_fields
    else:
        result["error"] = False
    state["memory_write_result"] = result
    return state


def cache_writeback_node(state: ChatState) -> ChatState:
    state = _append_node_trace(state, "cache_writeback")
    state.setdefault("debug", {})
    state["debug"]["memory_write"] = state.get("memory_write_result", {})
    state["debug"].setdefault("memory", {})
    session_write = state.get("memory_write_result", {}).get("session_write", {})
    writes = state.get("memory_write_result", {}).get("writes", [])
    dedupe_count = sum(1 for w in writes if w.get("dedupe_hit"))
    written_count = sum(1 for w in writes if w.get("written"))
    state["debug"]["memory"]["session_write"] = session_write
    state["debug"]["memory"]["llm_summary_used"] = bool(session_write.get("llm_summary_used", False))
    state["debug"]["memory"]["llm_summary_error"] = session_write.get("llm_summary_error", "")
    state["debug"]["memory"]["memory_write_count"] = written_count
    state["debug"]["memory"]["memory_dedupe_count"] = dedupe_count
    cache_result = state.get("cache_result", {})
    keys = cache_result.get("keys", {})
    l1_key = keys.get("l1_key")
    can_writeback = (
        bool(l1_key)
        and (not state.get("served_by_cache", False))
        and (not state.get("handoff_required", False))
    )
    if can_writeback:
        try:
            writeback_result = CACHE_ORCHESTRATOR.writeback(
                query=state.get("query", ""),
                tenant_id=state.get("tenant_id", ""),
                actor_type=state.get("actor_type", ""),
                domain=state.get("route_target", "faq"),
                answer=state.get("answer", ""),
                citations=state.get("citations", []),
                source_trace_id=state.get("trace_id", ""),
                source_event_id=state.get("event_id", ""),
            )
        except Exception as exc:
            writeback_result = {"ok": False, "error": str(exc)}
        state["debug"]["cache"]["writeback_result"] = writeback_result
        state["debug"]["cache"]["admitted"] = bool(writeback_result.get("admitted", False))
        state["debug"]["cache"]["admission"] = writeback_result.get("admission", {})
    state["debug"]["cache"]["writeback"] = can_writeback
    return state


def next_after_handoff(state: ChatState) -> str:
    if state.get("handoff_required"):
        return "end"
    return "memory_write"


def build_workflow():
    graph = StateGraph(ChatState)
    graph.add_node("resume_entry", resume_entry_node)
    graph.add_node("route_intent", route_intent_node)
    graph.add_node("feedback_gate", feedback_gate_node)
    graph.add_node("intent_subgraph_entry", intent_subgraph_entry_node)
    graph.add_node("risk_handoff_subgraph", risk_handoff_subgraph_node)
    graph.add_node("cache_lookup", cache_lookup_node)
    graph.add_node("memory_read", memory_read_node)
    graph.add_node("aftersales_facts", aftersales_facts_node)
    graph.add_node("aftersales_policy", aftersales_policy_node)
    graph.add_node("aftersales_action", aftersales_action_node)
    graph.add_node("tool_call", tool_call_node)
    graph.add_node("rag_decision", rag_decision_node)
    graph.add_node("rag_query", rag_query_node)
    graph.add_node("context_build", context_build_node)
    graph.add_node("draft_answer", draft_answer_node)
    graph.add_node("handoff_decision", handoff_decision_node)
    graph.add_node("memory_write", memory_write_node)
    graph.add_node("cache_writeback", cache_writeback_node)

    graph.add_edge(START, "resume_entry")
    graph.add_conditional_edges(
        "resume_entry",
        next_after_resume_entry,
        {
            "route_intent": "route_intent",
            "memory_read": "memory_read",
            "aftersales_facts": "aftersales_facts",
            "aftersales_policy": "aftersales_policy",
            "aftersales_action": "aftersales_action",
            "handoff_decision": "handoff_decision",
        },
    )
    graph.add_edge("route_intent", "feedback_gate")
    graph.add_conditional_edges(
        "feedback_gate",
        next_after_feedback_gate,
        {
            "handoff_decision": "handoff_decision",
            "intent_subgraph_entry": "intent_subgraph_entry",
        },
    )
    graph.add_conditional_edges(
        "intent_subgraph_entry",
        next_after_intent_subgraph_entry,
        {
            "risk_handoff_subgraph": "risk_handoff_subgraph",
            "cache_lookup": "cache_lookup",
        },
    )
    graph.add_edge("risk_handoff_subgraph", "handoff_decision")
    graph.add_conditional_edges(
        "cache_lookup",
        next_after_cache,
        {
            "memory_read": "memory_read",
            "handoff_decision": "handoff_decision",
        },
    )
    graph.add_conditional_edges(
        "memory_read",
        next_after_memory_read,
        {
            "aftersales_facts": "aftersales_facts",
            "tool_call": "tool_call",
            "rag_decision": "rag_decision",
        },
    )
    graph.add_edge("aftersales_facts", "aftersales_policy")
    graph.add_edge("aftersales_policy", "aftersales_action")
    graph.add_conditional_edges(
        "aftersales_action",
        next_after_aftersales_action,
        {
            "handoff_decision": "handoff_decision",
            "rag_decision": "rag_decision",
        },
    )
    graph.add_edge("tool_call", "rag_decision")
    graph.add_conditional_edges(
        "rag_decision",
        next_after_rag_decision,
        {
            "rag_query": "rag_query",
            "context_build": "context_build",
        },
    )
    graph.add_edge("rag_query", "context_build")
    graph.add_edge("context_build", "draft_answer")
    graph.add_edge("draft_answer", "handoff_decision")
    graph.add_conditional_edges(
        "handoff_decision",
        next_after_handoff,
        {
            "memory_write": "memory_write",
            "end": END,
        },
    )
    graph.add_edge("memory_write", "cache_writeback")
    graph.add_edge("cache_writeback", END)
    return graph.compile()


WORKFLOW = build_workflow()


def get_workflow_mermaid() -> str:
    """Return Mermaid definition for the current workflow graph."""
    return WORKFLOW.get_graph().draw_mermaid()


def render_workflow_png(png_path: Optional[str] = None) -> bytes:
    """Render workflow graph as PNG bytes and optionally save to disk."""
    png_bytes = WORKFLOW.get_graph().draw_mermaid_png()
    if png_path:
        target = Path(png_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(png_bytes)
    return png_bytes


def display_workflow_graph(png_path: Optional[str] = None) -> bytes:
    """Display workflow graph via IPython display/Image when available."""
    png_bytes = render_workflow_png(png_path=png_path)
    try:
        from IPython.display import Image, display  # type: ignore

        display(Image(data=png_bytes))
    except Exception:
        # Fallback for non-notebook environments.
        pass
    return png_bytes


if __name__ == "__main__":
    output = "data/eval/workflow_minimal_chat.png"
    render_workflow_png(output)
    print(f"workflow graph saved to {output}")

def run_workflow(
    *,
    trace_id: str,
    event_id: str,
    conversation_id: str = "",
    user_id: str,
    tenant_id: str,
    actor_type: str,
    channel: str,
    query: str,
    history: list,
    memory_enabled: Optional[bool] = None,
    resume_checkpoint_id: str = "",
    run_id: str = "",
    action_mode: str = "auto",
    rewind_stage: str = "",
    human_decision: Optional[Dict[str, Any]] = None,
    reference_injection: Optional[Dict[str, Any]] = None,
    runtime_policy: Optional[Dict[str, Any]] = None,
) -> ChatState:
    human_decision = human_decision or {}
    normalized_action_mode = action_mode or "auto"
    normalized_rewind_stage = rewind_stage or ""
    normalized_resume_checkpoint_id = resume_checkpoint_id or ""
    decision = str(human_decision.get("decision", "") or "").lower()
    continue_decisions = {"approve", "approved", "continue", "resume", "同意", "通过"}
    reject_decisions = {"reject", "rejected", "deny", "refuse", "拒绝", "驳回"}
    rewind_decisions = {
        "rewind",
        "rewind_to_stage",
        "rewind-stage",
        "back",
        "rollback",
        "退回",
        "回退",
        "驳回",
        "rewind_facts",
        "rewind_policy",
        "rewind_action",
    }

    def _latest_human_gate_checkpoint_by_run(current_run_id: str) -> Optional[Dict[str, Any]]:
        if not current_run_id:
            return None
        try:
            checkpoints = CHECKPOINT_STORE.list_checkpoints_by_run(current_run_id, limit=200)
        except Exception:
            return None
        fallback_waiting_stage: Optional[Dict[str, Any]] = None
        for item in checkpoints:
            md = item.get("metadata", {}) if isinstance(item.get("metadata"), dict) else {}
            status = str(item.get("status", "") or "").lower()
            if status == "wait_human":
                return item
            if md.get("stage_status") == "waiting_human":
                if fallback_waiting_stage is None:
                    fallback_waiting_stage = item
        return fallback_waiting_stage

    def _first_non_empty_str(*vals: Any) -> str:
        for v in vals:
            if isinstance(v, str) and v.strip():
                return v.strip()
        return ""

    # Support both flat and nested payload forms.
    rewind_payload = human_decision.get("rewind", {}) if isinstance(human_decision.get("rewind"), dict) else {}
    hd_stage = _first_non_empty_str(
        human_decision.get("rewind_stage"),
        human_decision.get("rewind_to_stage"),
        human_decision.get("stage"),
        rewind_payload.get("stage"),
        rewind_payload.get("rewind_stage"),
        rewind_payload.get("rewind_to_stage"),
    )
    if not hd_stage and decision in {"rewind_facts", "rewind_policy", "rewind_action"}:
        hd_stage = decision.replace("rewind_", "")
    hd_ckpt = _first_non_empty_str(
        human_decision.get("target_checkpoint_id"),
        human_decision.get("checkpoint_id"),
        human_decision.get("rewind_checkpoint_id"),
        rewind_payload.get("target_checkpoint_id"),
        rewind_payload.get("checkpoint_id"),
        rewind_payload.get("rewind_checkpoint_id"),
    )
    # UX compatibility: allow rewind through human_decision payload.
    if normalized_action_mode in {"auto", "continue"} and decision in rewind_decisions:
        normalized_action_mode = "rewind"
    # UX compatibility: allow continue through human_decision payload.
    if normalized_action_mode == "auto" and decision in (continue_decisions | reject_decisions) and run_id:
        normalized_action_mode = "continue"
    if normalized_action_mode == "rewind":
        if hd_stage:
            normalized_rewind_stage = hd_stage
        if hd_ckpt:
            normalized_resume_checkpoint_id = hd_ckpt
    auto_inferred_resume_checkpoint_id = ""
    auto_inferred_rewind_stage = ""
    if not normalized_resume_checkpoint_id and run_id and normalized_action_mode in {"continue", "rewind"}:
        gate_ckpt = _latest_human_gate_checkpoint_by_run(run_id)
        if gate_ckpt:
            normalized_resume_checkpoint_id = str(gate_ckpt.get("checkpoint_id", "") or "")
            auto_inferred_resume_checkpoint_id = normalized_resume_checkpoint_id
            if normalized_action_mode == "rewind" and not normalized_rewind_stage:
                # Default rewind target for one-click "退回" is facts stage.
                normalized_rewind_stage = "facts"
                auto_inferred_rewind_stage = normalized_rewind_stage

    set_trace_metadata(
        resume_mode=bool(normalized_resume_checkpoint_id),
        action_mode=normalized_action_mode,
        rewind_stage=normalized_rewind_stage,
        rewind_decision=decision,
        human_reason=str(human_decision.get("reason", "") or ""),
        human_evidence=str(human_decision.get("evidence", "") or ""),
        rewind_human_stage=hd_stage,
        rewind_human_checkpoint_id=hd_ckpt,
        auto_inferred_resume_checkpoint_id=auto_inferred_resume_checkpoint_id,
        auto_inferred_rewind_stage=auto_inferred_rewind_stage,
    )
    if normalized_resume_checkpoint_id:
        ckpt = CHECKPOINT_STORE.get_checkpoint(normalized_resume_checkpoint_id)
        if ckpt and isinstance(ckpt.get("state"), dict):
            if run_id and str(ckpt.get("run_id", "")) != run_id:
                raise ValueError("run_id mismatch for resume checkpoint")
            target_ckpt = ckpt
            if normalized_action_mode == "rewind":
                if not normalized_rewind_stage:
                    # Derive rewind stage from selected checkpoint when omitted.
                    ckpt_meta = ckpt.get("metadata", {}) if isinstance(ckpt.get("metadata"), dict) else {}
                    normalized_rewind_stage = str(ckpt_meta.get("stage", "") or "")
                if not normalized_rewind_stage:
                    raise ValueError("rewind_stage is required when action_mode=rewind")
                same_run_id = str(ckpt.get("run_id", ""))
                stage_ckpt = CHECKPOINT_STORE.latest_stage_checkpoint(run_id=same_run_id, stage=normalized_rewind_stage)
                if not stage_ckpt:
                    raise ValueError(f"no checkpoint found for rewind stage={normalized_rewind_stage}")
                target_ckpt = stage_ckpt
            restored = dict(ckpt["state"])
            if target_ckpt is not ckpt:
                restored = dict(target_ckpt.get("state", {}))
            restored["trace_id"] = trace_id
            restored["event_id"] = event_id or f"evt_{uuid.uuid4().hex[:12]}"
            restored["query"] = query
            restored["history"] = history
            restored["memory_enabled"] = settings.enable_memory if memory_enabled is None else bool(memory_enabled)
            restored["human_decision"] = human_decision or {}
            restored["action_mode"] = normalized_action_mode
            restored["rewind_stage"] = normalized_rewind_stage
            restored["run_id"] = str(ckpt.get("run_id", "")) or restored.get("run_id", "")
            restored.setdefault("debug", {})
            rp = runtime_policy if isinstance(runtime_policy, dict) else {}
            if rp:
                restored["debug"]["runtime_policy"] = rp
            restored["debug"]["resumed_from_checkpoint"] = normalized_resume_checkpoint_id
            if normalized_action_mode == "rewind":
                restored["debug"]["rewind_to_stage"] = normalized_rewind_stage
                restored["debug"]["rewind_checkpoint_id"] = target_ckpt.get("checkpoint_id", "")
            restored["debug"]["normalized_control"] = {
                "action_mode": normalized_action_mode,
                "rewind_stage": normalized_rewind_stage,
                "resume_checkpoint_id": normalized_resume_checkpoint_id,
                "human_decision": decision,
            }
            # Recover pending action from checkpoint metadata for true executor resume.
            ckpt_meta = target_ckpt.get("metadata", {}) if isinstance(target_ckpt.get("metadata"), dict) else {}
            if normalized_action_mode == "continue" and not isinstance(restored.get("pending_action"), dict):
                restored["pending_action"] = {}
            if normalized_action_mode == "continue" and not (restored.get("pending_action") or {}).get("action_name"):
                pending_name = str(ckpt_meta.get("pending_action", ckpt_meta.get("pending_action_name", "")) or "")
                pending_args = ckpt_meta.get("pending_action_args", {}) if isinstance(ckpt_meta.get("pending_action_args"), dict) else {}
                if pending_name:
                    restored["pending_action"] = {
                        "action_name": pending_name,
                        "action_args": pending_args,
                        "risk_reasons": ["resume_from_checkpoint_metadata"],
                        "checkpoint_id": str(target_ckpt.get("checkpoint_id", "")),
                    }
                    restored.setdefault("debug", {})
                    restored["debug"]["pending_human_gate"] = {
                        "reason": "resume_from_checkpoint_metadata",
                        "checkpoint_id": str(target_ckpt.get("checkpoint_id", "")),
                        "action": pending_name,
                    }
            restored["wf_parent_checkpoint_id"] = normalized_resume_checkpoint_id
            restored["wf_checkpoint_id"] = ""
            restored["served_by_cache"] = False
            # Keep resumed run trace clean: only show nodes executed after resume/rewind.
            restored["node_trace"] = []
            # Main-graph true breakpoint resume: jump directly to a specific node.
            target_stage = normalized_rewind_stage if normalized_action_mode == "rewind" else str(target_ckpt.get("metadata", {}).get("stage", "") or "")
            if normalized_action_mode == "continue":
                restored["resume_next_node"] = "aftersales_action"
            elif normalized_action_mode == "rewind":
                if target_stage == "facts":
                    restored["resume_next_node"] = "aftersales_facts"
                elif target_stage == "action":
                    restored["resume_next_node"] = "aftersales_action"
                else:
                    restored["resume_next_node"] = "aftersales_policy"
            else:
                restored["resume_next_node"] = "route_intent"
            set_trace_metadata(
                resume_mode=True,
                resume_next_node=restored["resume_next_node"],
                resumed_from_checkpoint_id=normalized_resume_checkpoint_id,
                resume_run_id=restored.get("run_id", ""),
            )
            return WORKFLOW.invoke(restored)
    thread_id = conversation_id.strip() if conversation_id and conversation_id.strip() else f"{tenant_id}:{user_id}:{channel}"
    state: ChatState = {
        "trace_id": trace_id,
        "event_id": event_id or f"evt_{uuid.uuid4().hex[:12]}",
        "user_id": user_id,
        "conversation_id": thread_id,
        "thread_id": thread_id,
        "tenant_id": tenant_id,
        "actor_type": actor_type,
        "channel": channel,
        "query": query,
        "history": history,
        "route_target": "faq",
        "aftersales_mode": "",
        "intent_confidence": 0.0,
        "served_by_cache": False,
        "cache_result": {},
        "status": "NEW",
        "handoff_required": False,
        "answer": "",
        "citations": [],
        "tool_result": {},
        "aftersales_tool_result": {},
        "aftersales_skill_result": {},
        "aftersales_agent_result": {},
        "rag_decision_result": {},
        "rag_result": {},
        "memory_result": {},
        "memory_write_result": {},
        "context_result": {},
        "memory_enabled": settings.enable_memory if memory_enabled is None else bool(memory_enabled),
        "human_decision": human_decision,
        "action_mode": normalized_action_mode,
        "rewind_stage": normalized_rewind_stage,
        "current_stage": "facts",
        "stage_status": "running",
        "stage_summary": "开始执行",
        "decision_basis": [],
        "required_user_input": [],
        "allowed_actions": [],
        "rewind_stage_options": [],
        "pending_action": {},
        "resume_next_node": "route_intent",
        "run_id": f"run_{uuid.uuid4().hex[:16]}",
        "wf_checkpoint_id": "",
        "wf_parent_checkpoint_id": "",
        "debug": {},
        "node_trace": [],
    }
    rp0 = runtime_policy if isinstance(runtime_policy, dict) else {}
    if rp0:
        state["debug"]["runtime_policy"] = rp0
    ri0 = reference_injection if isinstance(reference_injection, dict) else {}
    if ri0:
        state.setdefault("debug", {})
        state["debug"]["reference_injection"] = {
            "referenced_run_id": str(ri0.get("referenced_run_id", "") or ""),
            "quote_text": str(ri0.get("quote_text", "") or ""),
            "snippet": str(ri0.get("snippet", "") or ""),
            "follow_up_after_resolved": bool(ri0.get("follow_up_after_resolved", False)),
            "verification": ri0.get("verification") if isinstance(ri0.get("verification"), dict) else {},
        }
    set_trace_metadata(
        resume_mode=False,
        resume_next_node="route_intent",
        resume_run_id=state.get("run_id", ""),
    )
    return WORKFLOW.invoke(state)

