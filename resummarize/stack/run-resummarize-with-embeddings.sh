#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
EMBED_URL="${STARMAP_EMBED_URL:-http://127.0.0.1:8080/embed}"
EMBED_MODEL="${STARMAP_EMBED_MODEL:-Qwen/Qwen3-Embedding-4B}"

exec cargo run --manifest-path "$ROOT_DIR/resummarize/Cargo.toml" -- \
  --embed-url "$EMBED_URL" \
  --embed-model "$EMBED_MODEL" \
  "$@"
