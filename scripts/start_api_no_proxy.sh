#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -x ".venv/bin/uvicorn" ]]; then
  echo "未找到 .venv/bin/uvicorn，请先执行: pip install -r requirements.txt"
  exit 1
fi

# 避免系统代理影响本地 API 到本地依赖(ollama/redis/pg)和模型下载。
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
export NO_PROXY="127.0.0.1,::1,localhost,host.docker.internal"
export no_proxy="127.0.0.1,::1,localhost,host.docker.internal"

PORT="${1:-8000}"
exec ".venv/bin/uvicorn" app.main:app --reload --port "$PORT"
