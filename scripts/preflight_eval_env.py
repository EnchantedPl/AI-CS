import argparse
import os
from typing import Any, Dict, Tuple

import requests

try:
    from dotenv import load_dotenv
except Exception:
    def load_dotenv(*args: Any, **kwargs: Any) -> bool:
        return False


def _ok(name: str, detail: str) -> None:
    print(f"[OK]   {name}: {detail}")


def _warn(name: str, detail: str) -> None:
    print(f"[WARN] {name}: {detail}")


def _fail(name: str, detail: str) -> None:
    print(f"[FAIL] {name}: {detail}")


def _check_http(name: str, url: str, timeout: float, expected_status: Tuple[int, ...]) -> bool:
    try:
        resp = requests.get(url, timeout=timeout)
        if resp.status_code in expected_status:
            _ok(name, f"status={resp.status_code}")
            return True
        _fail(name, f"status={resp.status_code}, expected={expected_status}")
        return False
    except Exception as exc:
        _fail(name, str(exc))
        return False


def _check_chat(app_base: str, timeout: float) -> bool:
    payload: Dict[str, Any] = {
        "user_id": "preflight_user",
        "tenant_id": "demo",
        "actor_type": "user",
        "query": "你好，请回复一句系统联通性检查通过。",
        "channel": "web",
        "history": [],
        "conversation_id": "preflight_conv",
        "memory_enabled": False,
    }
    try:
        resp = requests.post(f"{app_base.rstrip('/')}/chat", json=payload, timeout=timeout)
        if resp.status_code != 200:
            _fail("chat_api", f"status={resp.status_code}, body={resp.text[:300]}")
            return False
        data = resp.json() if resp.text else {}
        if not isinstance(data, dict):
            _fail("chat_api", "response is not JSON object")
            return False
        route_target = str(data.get("route_target", ""))
        status = str(data.get("status", ""))
        _ok("chat_api", f"status=200, route_target={route_target}, workflow_status={status}")
        return True
    except Exception as exc:
        _fail("chat_api", str(exc))
        return False


def _check_langsmith(endpoint: str, timeout: float) -> bool:
    key = os.getenv("LANGSMITH_API_KEY", "")
    if not key:
        _fail("langsmith_key", "LANGSMITH_API_KEY is empty")
        return False
    masked = f"{key[:8]}...{key[-4:]}" if len(key) >= 12 else "***"
    _ok("langsmith_key", masked)
    return _check_http("langsmith_info", f"{endpoint.rstrip('/')}/info", timeout, (200,))


def main() -> None:
    # Force project .env to override stale shell-level endpoints/keys.
    load_dotenv(dotenv_path=".env", override=True)
    parser = argparse.ArgumentParser(description="Preflight checks for local eval pipeline.")
    parser.add_argument("--app-base", default=os.getenv("APP_BASE", "http://127.0.0.1:8000"))
    parser.add_argument("--ollama-base", default=os.getenv("OLLAMA_BASE", "http://127.0.0.1:11434/v1"))
    parser.add_argument("--langsmith-endpoint", default=os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com"))
    parser.add_argument("--timeout-sec", type=float, default=10.0)
    args = parser.parse_args()

    print("== Preflight: local eval environment ==")
    print(f"app_base={args.app_base}")
    print(f"ollama_base={args.ollama_base}")
    print(f"langsmith_endpoint={args.langsmith_endpoint}")

    ok = True
    ok &= _check_http("app_health", f"{args.app_base.rstrip('/')}/health", args.timeout_sec, (200,))
    ok &= _check_chat(args.app_base, args.timeout_sec)
    ok &= _check_http("ollama_models", f"{args.ollama_base.rstrip('/')}/models", args.timeout_sec, (200,))
    ok &= _check_langsmith(args.langsmith_endpoint, args.timeout_sec)

    if ok:
        _ok("summary", "all preflight checks passed")
        raise SystemExit(0)
    _warn("summary", "some checks failed; fix these before full eval")
    raise SystemExit(1)


if __name__ == "__main__":
    main()
