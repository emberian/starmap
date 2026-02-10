#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

echo "[starmap] starting Qwen3 embedding stack via vLLM on http://127.0.0.1:8000/v1/embeddings"
docker compose -f "$SCRIPT_DIR/compose.vllm.yml" up -d

echo "[starmap] vLLM started. test with:"
echo "curl -sS http://127.0.0.1:8000/v1/embeddings -H 'Content-Type: application/json' -d '{\"model\":\"Qwen/Qwen3-Embedding-4B\",\"input\":[\"Instruct: embed\\nQuery: hello\"]}'"
