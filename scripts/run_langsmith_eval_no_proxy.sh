#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# 避免公司代理对 LangSmith HTTPS 做 MITM/截断导致 SSLEOF。
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
export NO_PROXY="api.smith.langchain.com,api.eu.smith.langchain.com,127.0.0.1,localhost,::1"
export no_proxy="$NO_PROXY"

# 若 EU 端点 SSL 仍失败，可显式改美区（与账号/数据集区域一致即可）。
: "${LANGSMITH_ENDPOINT:=https://api.smith.langchain.com}"

exec python3 scripts/run_langsmith_eval.py "$@"
