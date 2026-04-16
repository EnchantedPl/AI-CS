#!/usr/bin/env python3
"""Replay observability coverage dataset directly to local /chat API."""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

import requests


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            raw = line.strip()
            if not raw:
                continue
            rows.append(json.loads(raw))
    return rows


def _build_payload(row: Dict[str, Any], default_tenant_id: str, default_channel: str) -> Dict[str, Any]:
    sample_id = str(row.get("sample_id", "") or "")
    query = str(row.get("query", "") or "")
    history = row.get("history", [])
    if not isinstance(history, list):
        history = []
    stress_tokens_chars = int(row.get("stress_tokens_chars", 0) or 0)
    if stress_tokens_chars > 0:
        query = f"{query}\n\n" + ("压" * stress_tokens_chars)

    request_overrides = row.get("request_overrides", {})
    if not isinstance(request_overrides, dict):
        request_overrides = {}

    payload: Dict[str, Any] = {
        "user_id": str(request_overrides.get("user_id", f"replay_user_{sample_id or 'unknown'}") or f"replay_user_{sample_id or 'unknown'}"),
        "tenant_id": str(request_overrides.get("tenant_id", default_tenant_id) or default_tenant_id),
        "actor_type": str(request_overrides.get("actor_type", "user") or "user"),
        "query": query,
        "channel": str(request_overrides.get("channel", default_channel) or default_channel),
        "history": history,
        "conversation_id": str(
            request_overrides.get("conversation_id", f"replay_conv_{sample_id or 'unknown'}")
            or f"replay_conv_{sample_id or 'unknown'}"
        ),
    }
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
        "replay_experiment",
    ]
    for key in passthrough_fields:
        if key in request_overrides:
            payload[key] = request_overrides[key]
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay dashboard coverage dataset to local /chat API.")
    parser.add_argument("--app-base", default="http://127.0.0.1:8000", help="Local API base URL")
    parser.add_argument("--input", default="data/eval/dataset_dashboard_coverage.jsonl", help="Input JSONL dataset")
    parser.add_argument("--tenant-id", default="demo", help="Default tenant_id")
    parser.add_argument("--channel", default="web", help="Default channel")
    parser.add_argument("--timeout-sec", type=float, default=60.0, help="HTTP timeout")
    parser.add_argument("--sleep-ms", type=float, default=0.0, help="Sleep between requests")
    parser.add_argument("--limit", type=int, default=0, help="Replay first N rows only (0 means all)")
    args = parser.parse_args()

    rows = _read_jsonl(Path(args.input).resolve())
    if args.limit > 0:
        rows = rows[: args.limit]
    if not rows:
        raise RuntimeError("no rows to replay")

    status_counter: Counter[str] = Counter()
    route_counter: Counter[str] = Counter()
    http_counter: Counter[int] = Counter()
    err_counter: Counter[str] = Counter()

    app_base = args.app_base.rstrip("/")
    print(f"[replay] app_base={app_base} rows={len(rows)}")
    for i, row in enumerate(rows, start=1):
        payload = _build_payload(row, default_tenant_id=args.tenant_id, default_channel=args.channel)
        t0 = time.perf_counter()
        try:
            resp = requests.post(f"{app_base}/chat", json=payload, timeout=args.timeout_sec)
            latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            http_counter[int(resp.status_code)] += 1
            body = resp.json() if resp.headers.get("Content-Type", "").startswith("application/json") else {}
            route = str((body or {}).get("route_target", "unknown") or "unknown")
            status = str((body or {}).get("status", "UNKNOWN") or "UNKNOWN")
            route_counter[route] += 1
            status_counter[status] += 1
            print(f"[{i}/{len(rows)}] http={resp.status_code} route={route} status={status} latency_ms={latency_ms}")
        except Exception as exc:
            latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            err_key = str(exc).split(":")[0][:120]
            err_counter[err_key] += 1
            print(f"[{i}/{len(rows)}] error={exc} latency_ms={latency_ms}")
        if args.sleep_ms > 0:
            time.sleep(max(0.0, args.sleep_ms / 1000.0))

    print("\n== replay summary ==")
    print(f"http_status={dict(http_counter)}")
    print(f"route_target={dict(route_counter)}")
    print(f"status={dict(status_counter)}")
    print(f"errors={dict(err_counter)}")


if __name__ == "__main__":
    main()
