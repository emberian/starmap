#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

echo "[starmap] starting Qwen3 embedding stack via TEI on http://127.0.0.1:8080/embed"
docker compose -f "$SCRIPT_DIR/compose.tei.yml" up -d

echo "[starmap] TEI started. test with:"
echo "curl -sS http://127.0.0.1:8080/embed -H 'Content-Type: application/json' -d '{\"inputs\":\"Instruct: embed\\nQuery: hello\"}'"
