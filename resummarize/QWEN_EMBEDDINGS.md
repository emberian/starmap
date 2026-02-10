# Qwen3 Embedding Setup for Starmap

`resummarize` supports two embedding modes:
- **Local in-process Rust inference** via `mistral.rs` (`--embed-local-model`) (recommended)
- **HTTP embedding endpoints** (`--embed-url`) if you still want to plug in external services

## Direct local Rust inference (no server/container)

This uses `mistral.rs` inside `resummarize` itself.

```bash
cargo run --manifest-path ~/dev/starmap/resummarize/Cargo.toml -- \
  --embed-local-model Qwen/Qwen3-Embedding-4B \
  --verbose
```

Optional knobs:

```bash
cargo run --manifest-path ~/dev/starmap/resummarize/Cargo.toml -- \
  --embed-local-model Qwen/Qwen3-Embedding-4B \
  --embed-local-isq Q4_K_M \
  --embed-local-force-cpu \
  --embed-local-hf-cache ~/.cache/huggingface/hub
```

Notes:
- `--embed-local-isq` accepts common ISQ names like `Q4_K_M`, `Q8_0`, etc.
- `--embed-local-force-cpu` is useful if GPU/Metal setup is unavailable.

## HTTP mode (generic)

Supported endpoint styles:
- OpenAI-compatible: `/v1/embeddings`
- TEI: `/embed`
- Ollama-style: `/api/embed` or `/api/embeddings`

Example:

```bash
cargo run --manifest-path ~/dev/starmap/resummarize/Cargo.toml -- \
  --embed-url http://127.0.0.1:8080/embed \
  --embed-model Qwen/Qwen3-Embedding-4B
```

## Motif mining knobs

```bash
cargo run --manifest-path ~/dev/starmap/resummarize/Cargo.toml -- \
  --embed-local-model Qwen/Qwen3-Embedding-4B \
  --workers 8 \
  --trace-dir ~/.summarization/traces \
  --motif-neighbors 8 \
  --motif-min-sim 0.60
```

- Higher `--motif-neighbors`: broader motif transfer.
- Lower `--motif-min-sim`: more aggressive transfer (can add noise).

## Fields written to each summary
- `_embedding`: backend/model/dim/updated metadata + vector
- `mined_motifs`: motif terms mined from semantic neighbors
- `_motif_neighbors`: nearest project links with cosine similarity

## Env vars
- `STARMAP_EMBED_URL`
- `STARMAP_EMBED_LOCAL_MODEL`
- `STARMAP_EMBED_LOCAL_ISQ`
- `STARMAP_EMBED_LOCAL_FORCE_CPU`
- `STARMAP_EMBED_LOCAL_HF_CACHE`
- `STARMAP_EMBED_MODEL`
- `STARMAP_EMBED_INSTRUCTION`
- `STARMAP_EMBED_TIMEOUT`
- `STARMAP_MOTIF_NEIGHBORS`
- `STARMAP_MOTIF_MIN_SIM`
- `STARMAP_WORKERS`
- `STARMAP_TRACE_DIR`

## References
- https://huggingface.co/Qwen/Qwen3-Embedding-4B
- https://github.com/EricLBuehler/mistral.rs
