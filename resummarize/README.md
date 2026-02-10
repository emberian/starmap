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

## Post-Processing Tools

Run these after a full summarization pass:
- Shadow root auto-detection prefers `~/.summarize` or `~/.summarization` (whichever has data).
- You can force a root with `--shadow-root ...` or `STARMAP_SHADOW_ROOT=...`.

### 1) Repair weak summaries

Uses Gemini to detect weak summaries and dispatch a deeper Gemini rewrite only for weak entries.

```bash
~/dev/starmap/resummarize-repair --verbose
```

Optional:

```bash
~/dev/starmap/resummarize-repair \
  --shadow-root ~/.summarization \
  --gemini-bin gemini \
  --dry-run
```

### 2) Align concepts + build metaconstellations

Uses Gemini to:
- Align near-duplicate terms across `concepts`, `tags`, and `mined_motifs`.
- Assign per-project `metaconstellations`.
- Write `~/.summarization/metaconstellations.json` for serving/rendering.

```bash
~/dev/starmap/resummarize-align-concepts --verbose
```

Optional:

```bash
~/dev/starmap/resummarize-align-concepts \
  --shadow-root ~/.summarization \
  --gemini-bin gemini \
  --dry-run
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
