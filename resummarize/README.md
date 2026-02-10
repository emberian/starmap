# resummarize (Rust)

Recursive repository summarizer for Starmap.

## Behavior
- Scans target roots and writes per-project `summary.json` files under `~/.summarization`.
- Uses file hashing for incremental updates.
- Uses `gemini` CLI for semantic summaries with deterministic fallback.
- Treats everything under `~/dev/gh` as project repos (skips project-indicator gating there).
- Optional embedding enrichment + motif mining for Starmap concept expansion.

## Run

```bash
cargo run --manifest-path ~/dev/starmap/resummarize/Cargo.toml -- --verbose
```

Parallel + trace example:

```bash
cargo run --manifest-path ~/dev/starmap/resummarize/Cargo.toml -- \
  --workers 8 \
  --trace-dir ~/.summarization/traces \
  --verbose
```

Or via compatibility shim:

```bash
python ~/dev/starmap/resummarize.py --verbose
```

## Embeddings
See `QWEN_EMBEDDINGS.md` for setup and flags.

High-level:
- Local in-process Rust inference: `--embed-local-model ...` (mistral.rs backend)
- HTTP endpoint mode: `--embed-url ...` (`/v1/embeddings`, `/embed`, `/api/embed`)

## Local stack helpers
- `resummarize/stack/up-tei.sh`
- `resummarize/stack/up-vllm.sh`
- `resummarize/stack/run-resummarize-with-embeddings.sh`
