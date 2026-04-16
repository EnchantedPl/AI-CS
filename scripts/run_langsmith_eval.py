import argparse
import csv
import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from langsmith import Client

try:
    from langsmith.utils import LangSmithConnectionError
except Exception:  # pragma: no cover
    LangSmithConnectionError = OSError  # type: ignore[misc,assignment]

try:
    from dotenv import load_dotenv
except Exception:
    def load_dotenv() -> bool:
        return False


def _now_tag() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _resolve_langsmith_api_url(cli_endpoint: Optional[str]) -> str:
    """CLI > LANGSMITH_ENDPOINT > LANGCHAIN_ENDPOINT > US default."""
    for candidate in (
        (cli_endpoint or "").strip(),
        (os.getenv("LANGSMITH_ENDPOINT") or "").strip(),
        (os.getenv("LANGCHAIN_ENDPOINT") or "").strip(),
    ):
        if candidate:
            return candidate.rstrip("/")
    return "https://api.smith.langchain.com"


def _print_langsmith_ssl_hints(endpoint: str) -> None:
    print(
        "\n[提示] LangSmith HTTPS/SSL 失败常见处理（任选其一或组合）：\n"
        "  1) EU 端点在本机/代理环境下常出现 SSLEOF，可改用美区：\n"
        "     python3 scripts/run_langsmith_eval.py --langsmith-endpoint https://api.smith.langchain.com\n"
        "     或在 .env 设 LANGSMITH_ENDPOINT=https://api.smith.langchain.com（需与账号/数据集所在区域一致）\n"
        "  2) 临时去掉代理再跑：\n"
        "     unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy\n"
        "     export NO_PROXY=api.smith.langchain.com,api.eu.smith.langchain.com,127.0.0.1,localhost\n"
        "     或使用：./scripts/run_langsmith_eval_no_proxy.sh\n"
        "  3) 升级证书链：pip install -U certifi urllib3\n"
        f"  当前 endpoint={endpoint}\n"
    )


def _as_dict_outputs(obj: Any) -> Dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj.get("outputs", obj) if isinstance(obj.get("outputs"), dict) else obj
    outputs = getattr(obj, "outputs", None)
    return outputs if isinstance(outputs, dict) else {}


def _as_dict_inputs(obj: Any) -> Dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj.get("inputs", obj) if isinstance(obj.get("inputs"), dict) else obj
    inputs = getattr(obj, "inputs", None)
    return inputs if isinstance(inputs, dict) else {}


def _as_metadata(obj: Any) -> Dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        meta = obj.get("metadata", {})
        return meta if isinstance(meta, dict) else {}
    meta = getattr(obj, "metadata", None)
    return meta if isinstance(meta, dict) else {}


def _obj_to_dict(obj: Any) -> Dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        try:
            out = obj.model_dump()
            return out if isinstance(out, dict) else {}
        except Exception:
            return {}
    if hasattr(obj, "dict"):
        try:
            out = obj.dict()
            return out if isinstance(out, dict) else {}
        except Exception:
            return {}
    return {}


def _to_jsonable(obj: Any) -> Any:
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_to_jsonable(v) for v in obj]
    if hasattr(obj, "model_dump"):
        try:
            return _to_jsonable(obj.model_dump())
        except Exception:
            pass
    if hasattr(obj, "dict"):
        try:
            return _to_jsonable(obj.dict())
        except Exception:
            pass
    if hasattr(obj, "__dict__"):
        try:
            return _to_jsonable(vars(obj))
        except Exception:
            pass
    return str(obj)


def _git_short_sha(fallback: str = "unknown") -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(os.path.abspath(__file__)) + "/..",
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
        return out.decode("utf-8").strip() or fallback
    except Exception:
        return fallback


def _collect_repro_metadata() -> Dict[str, Any]:
    """Pinned context for demo-to-prod alignment: compare runs apples-to-apples."""
    return {
        "git_short_sha": _git_short_sha(),
        "kb_version": os.getenv("KB_VERSION", ""),
        "prompt_version": os.getenv("PROMPT_VERSION", ""),
        "policy_version": os.getenv("POLICY_VERSION", ""),
        "embedding_mode": os.getenv("EMBEDDING_MODE", ""),
        "llm_mode": os.getenv("LLM_MODE", ""),
    }


def _normalize_ollama_model(raw_model: str) -> str:
    model = (raw_model or "").strip()
    if "/" in model:
        # e.g. ollama/qwen2.5:0.5b -> qwen2.5:0.5b
        model = model.split("/", 1)[1]
    return model or "qwen2.5:0.5b"


def _extract_json_score(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    # Prefer fenced-json / inline-json extraction for judge stability.
    candidates = [text]
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        candidates.insert(0, m.group(0))
    for c in candidates:
        try:
            obj = json.loads(c)
            if isinstance(obj, dict):
                return obj
        except Exception:
            continue
    return {"score": 0.0, "reason": "judge_output_parse_failed"}


def build_target_fn(
    app_base: str,
    timeout_sec: float,
    tenant_id: str,
    channel: str,
    default_memory_enabled: Optional[bool],
):
    app_base = app_base.rstrip("/")

    def _target(inputs: Dict[str, Any]) -> Dict[str, Any]:
        query = str((inputs or {}).get("query", "") or "").strip()
        history = (inputs or {}).get("history", [])
        if not isinstance(history, list):
            history = []

        sample_id = str((inputs or {}).get("sample_id", "") or "")
        group = str((inputs or {}).get("group", "") or "")
        request_overrides = (inputs or {}).get("request_overrides", {})
        if not isinstance(request_overrides, dict):
            request_overrides = {}
        stress_tokens_chars = int((inputs or {}).get("stress_tokens_chars", 0) or 0)
        if stress_tokens_chars > 0:
            query = f"{query}\n\n" + ("压" * stress_tokens_chars)

        user_id = str(request_overrides.get("user_id", f"eval_user_{sample_id or 'unknown'}") or f"eval_user_{sample_id or 'unknown'}")
        conversation_id = str(
            request_overrides.get("conversation_id", f"eval_conv_{sample_id or 'unknown'}")
            or f"eval_conv_{sample_id or 'unknown'}"
        )
        effective_tenant_id = str(request_overrides.get("tenant_id", tenant_id) or tenant_id)
        effective_channel = str(request_overrides.get("channel", channel) or channel)
        actor_type = str(request_overrides.get("actor_type", "user") or "user")
        payload: Dict[str, Any] = {
            "user_id": user_id,
            "tenant_id": effective_tenant_id,
            "actor_type": actor_type,
            "query": query,
            "channel": effective_channel,
            "history": history,
            "conversation_id": conversation_id,
        }
        if default_memory_enabled is not None:
            payload["memory_enabled"] = bool(default_memory_enabled)
        passthrough_fields = [
            "memory_enabled",
            "action_mode",
            "resume_checkpoint_id",
            "run_id",
            "user_feedback",
            "rewind_stage",
            "reference_run_id",
            "reference_quote_text",
            "human_decision",
            "wait_human_note",
            "trace_id",
        ]
        for key in passthrough_fields:
            if key in request_overrides:
                payload[key] = request_overrides[key]

        t0 = time.perf_counter()
        try:
            resp = requests.post(f"{app_base}/chat", json=payload, timeout=timeout_sec)
            latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict):
                raise RuntimeError("chat_response_not_dict")
            return {
                "answer": data.get("answer", ""),
                "route_target": data.get("route_target", "unknown"),
                "handoff_required": bool(data.get("handoff_required", False)),
                "citations": data.get("citations", []) if isinstance(data.get("citations"), list) else [],
                "status": data.get("status", "UNKNOWN"),
                "trace_id": data.get("trace_id", ""),
                "run_id": data.get("run_id", ""),
                "latency_ms": latency_ms,
                "http_status": resp.status_code,
                "sample_id": sample_id,
                "group": group,
            }
        except requests.HTTPError as exc:
            latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            code = getattr(exc.response, "status_code", 0) or 0
            return {
                "answer": "",
                "route_target": "unknown",
                "handoff_required": True,
                "citations": [],
                "status": "ERROR",
                "error": str(exc),
                "latency_ms": latency_ms,
                "http_status": int(code),
                "sample_id": sample_id,
                "group": group,
            }
        except Exception as exc:
            latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            return {
                "answer": "",
                "route_target": "unknown",
                "handoff_required": True,
                "citations": [],
                "status": "ERROR",
                "error": str(exc),
                "latency_ms": latency_ms,
                "http_status": 0,
                "sample_id": sample_id,
                "group": group,
            }

    return _target


def make_route_accuracy_eval():
    def _eval(run: Any, example: Any) -> Dict[str, Any]:
        run_out = _as_dict_outputs(run)
        ref_out = _as_dict_outputs(example)
        pred = str(run_out.get("route_target", "") or "")
        exp = str(ref_out.get("expected_route_target", "") or "")
        return {"key": "route_accuracy", "score": 1.0 if pred == exp else 0.0, "comment": f"expected={exp},pred={pred}"}

    return _eval


def make_handoff_recall_eval():
    def _eval(run: Any, example: Any) -> Dict[str, Any]:
        run_out = _as_dict_outputs(run)
        ref_out = _as_dict_outputs(example)
        must_handoff = bool(ref_out.get("must_handoff", False))
        pred = bool(run_out.get("handoff_required", False))
        if not must_handoff:
            # Keep non-risk samples neutral for global averaging.
            return {"key": "handoff_recall_high_risk", "score": 1.0, "comment": "not_applicable"}
        return {
            "key": "handoff_recall_high_risk",
            "score": 1.0 if pred else 0.0,
            "comment": f"must_handoff={must_handoff},pred={pred}",
        }

    return _eval


def make_chat_call_success_eval():
    """1 = /chat returned parsed JSON path (no transport/parsing ERROR in target)."""

    def _eval(run: Any, example: Any) -> Dict[str, Any]:
        run_out = _as_dict_outputs(run)
        st = str(run_out.get("status", "") or "")
        if st == "ERROR" or run_out.get("error"):
            return {
                "key": "chat_call_success",
                "score": 0.0,
                "comment": str(run_out.get("error", st))[:500],
            }
        return {"key": "chat_call_success", "score": 1.0, "comment": f"status={st}"}

    return _eval


def make_latency_slo_eval(latency_budget_ms: float):
    """1 = end-to-end /chat latency under budget (demo SLO proxy)."""

    def _eval(run: Any, example: Any) -> Dict[str, Any]:
        run_out = _as_dict_outputs(run)
        try:
            lat = float(run_out.get("latency_ms", 1e9) or 0.0)
        except (TypeError, ValueError):
            lat = 1e9
        ok = lat <= float(latency_budget_ms)
        return {
            "key": "latency_slo_ms",
            "score": 1.0 if ok else 0.0,
            "comment": f"latency_ms={lat},budget_ms={latency_budget_ms}",
        }

    return _eval


def make_expected_status_eval():
    """If reference_outputs.expected_status is set, require exact match (e.g. NEED_HUMAN)."""

    def _eval(run: Any, example: Any) -> Dict[str, Any]:
        run_out = _as_dict_outputs(run)
        ref_out = _as_dict_outputs(example)
        exp = ref_out.get("expected_status", None)
        if exp is None or str(exp).strip() == "":
            return {"key": "expected_status_match", "score": 1.0, "comment": "not_applicable"}
        pred = str(run_out.get("status", "") or "")
        exp_s = str(exp).strip()
        return {
            "key": "expected_status_match",
            "score": 1.0 if pred == exp_s else 0.0,
            "comment": f"expected={exp_s},pred={pred}",
        }

    return _eval


def make_citation_presence_eval():
    need_groups = {"AQ_simple", "aftersales_complex_multi_turn", "memory_dependent"}

    def _eval(run: Any, example: Any) -> Dict[str, Any]:
        run_out = _as_dict_outputs(run)
        meta = _as_metadata(example)
        group = str(meta.get("group", "") or "")
        citations = run_out.get("citations", [])
        if not isinstance(citations, list):
            citations = []
        if group not in need_groups:
            return {"key": "citation_presence_when_needed", "score": 1.0, "comment": "not_applicable"}
        return {
            "key": "citation_presence_when_needed",
            "score": 1.0 if len(citations) > 0 else 0.0,
            "comment": f"group={group},citations_count={len(citations)}",
        }

    return _eval


def make_correctness_eval(ollama_base: str, ollama_model: str, timeout_sec: float):
    ollama_base = ollama_base.rstrip("/")
    ollama_model = _normalize_ollama_model(ollama_model)
    judge_prompt = (
        "你是一名专业的数据标注员，负责评估模型输出的正确性。\n"
        "请根据以下标准打分：\n"
        "- 1.0: 核心事实完全一致，覆盖完整，无明显错误\n"
        "- 0.7: 大体正确，但有轻微遗漏或细节偏差\n"
        "- 0.3: 部分相关，但存在关键信息缺失或明显偏差\n"
        "- 0.0: 明显错误、与参考答案冲突，或严重不完整\n\n"
        "请仅输出 JSON，格式为：{\"score\": 0~1小数, \"reason\": \"一句中文原因\"}\n"
    )

    def _eval(run: Any, example: Any) -> Dict[str, Any]:
        run_in = _as_dict_inputs(run)
        run_out = _as_dict_outputs(run)
        ref_out = _as_dict_outputs(example)
        user_prompt = (
            f"<input>\n{json.dumps(run_in, ensure_ascii=False)}\n</input>\n\n"
            f"<output>\n{json.dumps(run_out, ensure_ascii=False)}\n</output>\n\n"
            f"<reference_outputs>\n{json.dumps(ref_out, ensure_ascii=False)}\n</reference_outputs>"
        )
        body = {
            "model": ollama_model,
            "messages": [
                {"role": "system", "content": judge_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.0,
        }
        r = requests.post(
            f"{ollama_base}/chat/completions",
            headers={"Content-Type": "application/json", "Authorization": "Bearer ollama"},
            json=body,
            timeout=timeout_sec,
        )
        r.raise_for_status()
        data = r.json()
        content = ""
        try:
            content = data["choices"][0]["message"]["content"]
        except Exception:
            content = str(data)
        parsed = _extract_json_score(content)
        score = float(parsed.get("score", 0.0) or 0.0)
        score = max(0.0, min(1.0, score))
        reason = str(parsed.get("reason", "") or "")
        return {"key": "correctness", "score": score, "comment": reason}

    return _eval


def _row_is_failure(row: Dict[str, Any]) -> bool:
    run_obj = row.get("run")
    run_data = _obj_to_dict(run_obj)
    run_out = run_data.get("outputs", {}) if isinstance(run_data.get("outputs"), dict) else {}
    if str(run_out.get("status", "") or "") == "ERROR":
        return True
    if run_out.get("error"):
        return True
    try:
        if int(run_out.get("http_status", 0) or 0) >= 500:
            return True
    except Exception:
        pass

    eval_data = row.get("evaluation_results", {}) or {}
    eval_rows = eval_data.get("results", []) if isinstance(eval_data, dict) else []
    if isinstance(eval_rows, list):
        for e in eval_rows:
            if not isinstance(e, dict):
                continue
            key = str(e.get("key", "") or "")
            score = e.get("score", None)
            if key == "chat_call_success" and (score is None or float(score) < 0.5):
                return True
    return False


def _export_failures(
    rows: list[Dict[str, Any]],
    *,
    export_dir: str,
    experiment_prefix: str,
) -> tuple[str, int]:
    failed: list[Dict[str, Any]] = []
    for row in rows:
        if not _row_is_failure(row):
            continue
        run_data = _obj_to_dict(row.get("run"))
        ex_data = _obj_to_dict(row.get("example"))
        eval_data = row.get("evaluation_results", {}) if isinstance(row.get("evaluation_results"), dict) else {}
        ex_inputs = ex_data.get("inputs", {}) if isinstance(ex_data.get("inputs"), dict) else {}
        ex_outputs = ex_data.get("outputs", {}) if isinstance(ex_data.get("outputs"), dict) else {}
        ex_meta = ex_data.get("metadata", {}) if isinstance(ex_data.get("metadata"), dict) else {}
        run_outputs = run_data.get("outputs", {}) if isinstance(run_data.get("outputs"), dict) else {}
        failed.append(
            {
                "sample_id": ex_meta.get("sample_id", ""),
                "group": ex_meta.get("group", ""),
                "query": ex_inputs.get("query", ""),
                "expected_route_target": ex_outputs.get("expected_route_target", ""),
                "must_handoff": ex_outputs.get("must_handoff", False),
                "run_status": run_outputs.get("status", ""),
                "http_status": run_outputs.get("http_status", 0),
                "error": run_outputs.get("error", ""),
                "route_target": run_outputs.get("route_target", ""),
                "handoff_required": run_outputs.get("handoff_required", False),
                "evaluation_results": _to_jsonable(eval_data.get("results", [])),
            }
        )

    target_dir = Path(export_dir).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^a-zA-Z0-9._-]+", "_", experiment_prefix)[:120]
    jsonl_path = target_dir / f"{stem}_failures.jsonl"
    csv_path = target_dir / f"{stem}_failures.csv"

    with jsonl_path.open("w", encoding="utf-8") as f:
        for item in failed:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "sample_id",
                "group",
                "query",
                "expected_route_target",
                "must_handoff",
                "run_status",
                "http_status",
                "error",
                "route_target",
                "handoff_required",
            ],
        )
        writer.writeheader()
        for item in failed:
            row = {k: item.get(k, "") for k in writer.fieldnames}
            writer.writerow(row)
    return str(jsonl_path), len(failed)


def main() -> None:
    # Force project .env to override stale shell-level endpoints/keys.
    load_dotenv(dotenv_path=".env", override=True)
    parser = argparse.ArgumentParser(description="Run LangSmith experiment for dataset F against local /chat + local Ollama judge.")
    parser.add_argument("--dataset-name", default="F", help="LangSmith dataset name")
    parser.add_argument(
        "--langsmith-endpoint",
        default=None,
        help="LangSmith API URL；不设则用 LANGSMITH_ENDPOINT / LANGCHAIN_ENDPOINT，否则默认美区",
    )
    parser.add_argument("--app-base", default=os.getenv("APP_BASE", "http://127.0.0.1:8000"), help="Local API base URL")
    parser.add_argument("--tenant-id", default=os.getenv("EVAL_TENANT_ID", "demo"), help="tenant_id sent to /chat")
    parser.add_argument("--channel", default=os.getenv("EVAL_CHANNEL", "web"), help="channel sent to /chat")
    parser.add_argument("--memory-enabled", choices=["true", "false", "auto"], default="auto", help="Force memory_enabled in chat payload")
    parser.add_argument("--ollama-base", default=os.getenv("OLLAMA_BASE", "http://127.0.0.1:11434/v1"), help="OpenAI-compatible Ollama base URL")
    parser.add_argument("--ollama-model", default=os.getenv("OLLAMA_MODEL", os.getenv("LOCAL_LLM_MODEL", "qwen2.5:0.5b")), help="Judge model name")
    parser.add_argument("--timeout-sec", type=float, default=120.0, help="HTTP timeout seconds")
    parser.add_argument(
        "--latency-budget-ms",
        type=float,
        default=120_000.0,
        help="SLO for latency_slo_ms evaluator (default generous for cold-start demo).",
    )
    parser.add_argument("--max-concurrency", type=int, default=4, help="LangSmith evaluation max concurrency")
    parser.add_argument("--max-examples", type=int, default=0, help="Run only first N examples (0 means all)")
    parser.add_argument(
        "--export-failures-dir",
        default="data/eval/reports/failures",
        help="Directory to export failed samples (jsonl/csv). Empty to disable.",
    )
    parser.add_argument("--experiment-prefix", default=f"F_local_sdk_eval_{_now_tag()}", help="Experiment name prefix")
    parser.add_argument("--description", default="Local /chat evaluated with local Ollama judge", help="Experiment description")
    args = parser.parse_args()

    if args.memory_enabled == "auto":
        mem = None
    else:
        mem = args.memory_enabled == "true"

    key = os.getenv("LANGSMITH_API_KEY", "")
    if not key:
        raise RuntimeError("LANGSMITH_API_KEY is empty. Please configure it in .env or environment.")

    api_url = _resolve_langsmith_api_url(args.langsmith_endpoint)

    target_fn = build_target_fn(
        app_base=args.app_base,
        timeout_sec=args.timeout_sec,
        tenant_id=args.tenant_id,
        channel=args.channel,
        default_memory_enabled=mem,
    )
    evaluators = [
        make_chat_call_success_eval(),
        make_latency_slo_eval(args.latency_budget_ms),
        make_expected_status_eval(),
        make_route_accuracy_eval(),
        make_handoff_recall_eval(),
        make_citation_presence_eval(),
        make_correctness_eval(
            ollama_base=args.ollama_base,
            ollama_model=args.ollama_model,
            timeout_sec=args.timeout_sec,
        ),
    ]

    client = Client(api_url=api_url, api_key=key)
    print(f"dataset={args.dataset_name}")
    print(f"langsmith_endpoint={api_url}")
    print(f"app_base={args.app_base}")
    print(f"ollama_base={args.ollama_base}")
    print(f"ollama_model={_normalize_ollama_model(args.ollama_model)}")
    print(f"experiment_prefix={args.experiment_prefix}")

    data_ref: Any = args.dataset_name
    try:
        if args.max_examples > 0:
            data_ref = list(client.list_examples(dataset_name=args.dataset_name, limit=max(1, args.max_examples)))
            print(f"max_examples={len(data_ref)}")

        results = client.evaluate(
            target_fn,
            data=data_ref,
            evaluators=evaluators,
            max_concurrency=max(1, args.max_concurrency),
            experiment_prefix=args.experiment_prefix,
            description=args.description,
            metadata={
                "dataset_name": args.dataset_name,
                "app_base": args.app_base,
                "ollama_base": args.ollama_base,
                "ollama_model": _normalize_ollama_model(args.ollama_model),
                "memory_enabled": args.memory_enabled,
                "latency_budget_ms": args.latency_budget_ms,
                **_collect_repro_metadata(),
            },
            blocking=True,
            upload_results=True,
            error_handling="log",
        )
    except (LangSmithConnectionError, requests.exceptions.SSLError) as exc:
        _print_langsmith_ssl_hints(api_url)
        raise RuntimeError(f"LangSmith 连接失败: {exc}") from exc

    experiment_name = getattr(results, "experiment_name", "")
    experiment_id = getattr(results, "experiment_id", "")
    print(f"experiment_name={experiment_name}")
    print(f"experiment_id={experiment_id}")
    if args.export_failures_dir.strip():
        rows = list(results)
        jsonl_path, fail_count = _export_failures(
            rows,
            export_dir=args.export_failures_dir.strip(),
            experiment_prefix=experiment_name or args.experiment_prefix,
        )
        print(f"failure_samples={fail_count}")
        print(f"failure_export_jsonl={jsonl_path}")
    if experiment_id:
        print(f"experiment_url=https://smith.langchain.com/o/{os.getenv('LANGSMITH_PROJECT', '')}/datasets")
    print("done")


if __name__ == "__main__":
    main()
