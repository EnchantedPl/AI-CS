#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -x ".venv/bin/langgraph" ]]; then
  echo "未找到 .venv/bin/langgraph，请先执行: pip install -r requirements.txt"
  exit 1
fi

# 避免系统代理影响本地 Studio 节点中的外部请求（如 HuggingFace / LangSmith）。
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
export NO_PROXY="127.0.0.1,::1,localhost"
export no_proxy="127.0.0.1,::1,localhost"

PORT="${1:-2024}"
exec ".venv/bin/langgraph" dev --config langgraph.json --port "$PORT" --no-browser
