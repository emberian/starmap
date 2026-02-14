#!/usr/bin/env python3
"""Assign broad taxonomy categories to projects via keyword scoring.

Replaces the LLM-generated per-project categories (which produced 556 unique
values for 628 projects) with ~18 hand-crafted categories suitable for
visualization and navigation.

Only updates ``_alignment.category`` — preserves existing concepts, tags,
mined_motifs, metaconstellations, and other alignment fields.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from starmap_lib import (
    as_string,
    as_string_list,
    dedup,
    now_rfc3339,
    read_summary,
    resolve_shadow_root,
    write_summary,
)

# ---------------------------------------------------------------------------
# Taxonomy definition
# ---------------------------------------------------------------------------

# Each category maps to keyword tiers:
#   strong  → 3.0 points
#   moderate → 1.5 points
#   weak    → 0.5 points

TAXONOMY: dict[str, dict[str, list[str]]] = {
    "Cryptography & Security": {
        "strong": [
            "zero-knowledge", "elliptic-curve", "pairing", "zksnark", "zk-snark",
            "halo2", "groth16", "plonk", "encryption", "rsa", "aes", "chacha",
            "ed25519", "signature", "cipher", "x25519", "curve25519",
            "pairing-friendly", "bn254", "bls12-381", "bulletproofs",
            "commitment", "secret-sharing", "homomorphic", "oblivious",
            "zkp", "zk-proof", "snark", "stark",
        ],
        "moderate": [
            "cryptography", "crypto", "tls-certificate", "pki", "authentication",
            "authorization", "oauth", "credential", "token-rotation",
            "hash", "hashing", "hmac", "digest", "merkle",
            "sealed-tokens", "capability-security", "access-control",
            "certificate", "x509", "openpgp", "gpg", "keyring",
        ],
        "weak": [
            "security", "secure", "password", "key-management", "salt",
            "blake3", "sha256", "sha2", "argon2", "bcrypt", "scrypt",
            "identity", "trust", "attestation", "audit",
        ],
    },
    "Systems & Embedded": {
        "strong": [
            "bare-metal", "firmware", "kernel", "avr", "arm", "no_std",
            "embedded-hal", "driver", "cgroup", "microcontroller", "rtos",
            "cortex-m", "stm32", "esp32", "riscv", "risc-v",
            "bootloader", "uefi", "bios",
        ],
        "moderate": [
            "embedded", "operating-system", "syscall", "interrupt",
            "memory-mapped", "register", "gpio", "spi", "i2c", "uart",
            "dma", "linker-script", "cross-compilation", "hal",
            "baremetal", "real-time", "memory-management",
        ],
        "weak": [
            "low-level", "systems-programming", "asm", "assembly",
            "ffi", "c-interop", "memory-layout", "bitfield",
            "native", "platform", "arch",
        ],
    },
    "Compilers & Languages": {
        "strong": [
            "compiler", "parser", "interpreter", "ast", "lexer",
            "type-system", "language-design", "bytecode", "codegen",
            "ir", "intermediate-representation", "grammar", "syntax-tree",
            "type-checker", "type-inference", "hindley-milner",
        ],
        "moderate": [
            "macro", "transpiler", "jit", "virtual-machine", "vm",
            "tokenizer", "semantic-analysis", "name-resolution",
            "parsing", "peg", "lalr", "ll-parser", "earley",
            "tree-sitter", "lsp", "language-server",
            "dsl", "domain-specific-language", "repl",
        ],
        "weak": [
            "programming-language", "syntax", "expression", "evaluation",
            "scope", "binding", "lambda", "closure", "pattern-matching",
            "generics", "polymorphism", "trait-system",
        ],
    },
    "Games": {
        "strong": [
            "game-engine", "board-game", "roguelike", "card-game",
            "mcts", "game-ai", "ecs", "bevy", "godot",
            "chess", "puzzle-game", "platformer", "rpg",
        ],
        "moderate": [
            "game", "game-development", "game-loop", "sprite",
            "tilemap", "pathfinding", "collision-detection",
            "turn-based", "real-time-strategy", "procedural-generation",
            "entity-component-system", "physics-engine",
        ],
        "weak": [
            "player", "level", "score", "enemy", "inventory",
            "game-state", "minimax", "alpha-beta",
        ],
    },
    "Simulation & Modeling": {
        "strong": [
            "cellular-automata", "agent-based", "n-body", "fluid-dynamics",
            "physics-simulation", "monte-carlo", "finite-element",
            "lattice-boltzmann", "sph", "molecular-dynamics",
        ],
        "moderate": [
            "simulation", "modeling", "numerical", "solver",
            "differential-equation", "ode", "pde", "integrator",
            "particle-system", "computational-physics",
            "stochastic", "markov-chain",
        ],
        "weak": [
            "model", "dynamics", "evolution", "population",
            "equilibrium", "trajectory", "field", "propagation",
        ],
    },
    "Graphics & Rendering": {
        "strong": [
            "vulkan", "wgpu", "opengl", "shader", "gpu", "ray-tracing",
            "spirv", "3d-rendering", "rasterization", "directx",
            "metal-api", "compute-shader", "fragment-shader",
        ],
        "moderate": [
            "graphics", "rendering", "mesh", "texture", "framebuffer",
            "render-pipeline", "deferred-rendering", "pbr",
            "physically-based-rendering", "vertex", "polygon",
            "scene-graph", "camera", "lighting", "normal-map",
        ],
        "weak": [
            "3d", "2d-graphics", "draw", "pixel", "color",
            "transform", "matrix", "quaternion", "geometry",
            "anti-aliasing", "post-processing",
        ],
    },
    "Web & Services": {
        "strong": [
            "http-server", "react", "leptos", "rest-api", "web-framework",
            "frontend", "static-site", "actix-web", "axum", "warp",
            "express", "fastapi", "django", "flask",
        ],
        "moderate": [
            "http", "web", "html", "css", "javascript", "typescript",
            "wasm", "webassembly", "spa", "ssr", "server-side-rendering",
            "graphql", "api-gateway", "middleware", "routing",
            "template-engine", "jsx", "svelte", "vue", "angular",
        ],
        "weak": [
            "endpoint", "request", "response", "cookie", "session",
            "cors", "cdn", "static-files", "url", "dom",
            "browser", "client-server",
        ],
    },
    "AI & Machine Learning": {
        "strong": [
            "llm", "transformer", "neural-network", "inference",
            "embedding", "diffusion", "nlp", "mcp",
            "large-language-model", "gpt", "bert", "attention",
            "model-context-protocol", "ai-agent",
        ],
        "moderate": [
            "machine-learning", "deep-learning", "training", "backpropagation",
            "gradient-descent", "loss-function", "optimizer",
            "classification", "regression", "clustering",
            "reinforcement-learning", "rl", "reward",
            "natural-language-processing", "tokenization",
            "fine-tuning", "prompt-engineering", "rag",
            "retrieval-augmented", "vector-search", "anthropic",
        ],
        "weak": [
            "ai", "artificial-intelligence", "prediction", "feature",
            "dataset", "model", "tensor", "batch", "epoch",
            "accuracy", "precision", "recall", "f1",
        ],
    },
    "Data & Storage": {
        "strong": [
            "database", "key-value", "sql", "persistence",
            "event-sourcing", "query-engine", "caching",
            "b-tree", "lsm-tree", "wal", "write-ahead-log",
            "sqlite", "postgres", "mysql", "rocksdb", "redb",
        ],
        "moderate": [
            "storage", "index", "table", "schema", "migration",
            "olap", "oltp", "column-store", "row-store",
            "transaction", "acid", "mvcc", "snapshot-isolation",
            "data-lake", "data-pipeline", "etl",
            "time-series", "content-addressed", "cas",
        ],
        "weak": [
            "data", "record", "query", "insert", "update", "delete",
            "cursor", "iterator", "page", "block", "compaction",
        ],
    },
    "Serialization & Formats": {
        "strong": [
            "capnproto", "protobuf", "serde", "binary-format", "codec",
            "xml-parser", "json-parser", "encoding",
            "flatbuffers", "msgpack", "cbor", "avro", "thrift",
        ],
        "moderate": [
            "serialization", "deserialization", "marshal", "unmarshal",
            "wire-format", "schema-evolution", "zero-copy",
            "binary-encoding", "text-format",
            "toml", "yaml", "csv", "parquet", "arrow",
        ],
        "weak": [
            "format", "parse", "encode", "decode", "byte",
            "protocol", "specification", "interchange",
        ],
    },
    "Networking": {
        "strong": [
            "tcp", "libp2p", "quic", "dns", "tls", "socket",
            "websocket", "transport", "udp", "icmp",
            "http2", "http3", "grpc",
        ],
        "moderate": [
            "networking", "network", "packet", "connection",
            "proxy", "load-balancer", "firewall", "vpn",
            "tunnel", "nat", "dhcp", "ip",
            "bandwidth", "latency", "throughput",
            "protocol-stack", "network-stack",
        ],
        "weak": [
            "port", "address", "route", "gateway", "interface",
            "stream", "datagram", "handshake", "keepalive",
        ],
    },
    "Distributed Systems": {
        "strong": [
            "consensus", "byzantine", "raft", "blockchain",
            "mina-protocol", "replication", "fault-tolerance",
            "paxos", "pbft", "bft", "validator",
            "smart-contract", "ledger", "finality",
        ],
        "moderate": [
            "distributed-systems", "distributed", "decentralized",
            "p2p", "peer-to-peer", "gossip", "crdt",
            "sharding", "partition-tolerance", "cap-theorem",
            "quorum", "epoch", "slot", "node",
            "state-machine-replication", "total-order",
        ],
        "weak": [
            "cluster", "replica", "leader", "follower",
            "election", "heartbeat", "membership", "coordination",
            "consistency", "availability", "eventual-consistency",
        ],
    },
    "Developer Tools": {
        "strong": [
            "build-tool", "linter", "debugger", "package-manager",
            "cli", "testing", "documentation", "editor",
            "formatter", "profiler", "coverage",
            "cargo", "npm", "pip", "brew",
        ],
        "moderate": [
            "developer-tools", "tooling", "dev-tool", "devtool",
            "static-analysis", "code-generation", "scaffolding",
            "benchmark", "test-framework", "test-runner",
            "cli-tool", "command-line", "terminal",
            "ide", "code-editor", "plugin", "extension",
        ],
        "weak": [
            "tool", "utility", "helper", "scaffold",
            "workflow", "automation", "productivity",
            "configuration", "dotfiles",
        ],
    },
    "Infrastructure & Ops": {
        "strong": [
            "nix", "docker", "kubernetes", "ci-cd",
            "deployment", "config-management", "terraform",
            "ansible", "helm", "container", "k8s",
        ],
        "moderate": [
            "infrastructure", "devops", "cloud", "aws", "gcp", "azure",
            "serverless", "lambda", "orchestration",
            "monitoring", "observability", "logging", "metrics",
            "service-mesh", "istio", "envoy",
            "gitops", "argocd", "fluxcd",
        ],
        "weak": [
            "ops", "operations", "provision", "scale",
            "uptime", "sla", "incident", "runbook",
            "artifact", "registry", "release",
        ],
    },
    "Concurrency & Async": {
        "strong": [
            "tokio", "async-runtime", "mutex", "spinlock",
            "threading", "synchronization", "parallel",
            "async-std", "smol", "rayon",
        ],
        "moderate": [
            "concurrency", "async", "await", "future", "promise",
            "channel", "mpsc", "actor", "actor-model",
            "lock-free", "wait-free", "atomic",
            "thread-pool", "work-stealing", "scheduler",
            "coroutine", "green-thread", "fiber",
        ],
        "weak": [
            "concurrent", "parallel-computing", "multithread",
            "spawn", "join", "barrier", "semaphore",
            "deadlock", "race-condition", "contention",
        ],
    },
    "Formal Verification": {
        "strong": [
            "theorem-proving", "proof-assistant", "coq", "lean",
            "isabelle", "smt", "sat-solver", "e-graph",
            "agda", "idris", "z3", "cvc5",
            "dependent-types", "formal-proof",
        ],
        "moderate": [
            "formal-verification", "verification", "model-checking",
            "property-testing", "invariant", "specification",
            "correctness", "soundness", "completeness",
            "propositional-logic", "first-order-logic",
            "automated-reasoning", "decision-procedure",
            "term-rewriting", "unification",
        ],
        "weak": [
            "proof", "theorem", "lemma", "axiom",
            "predicate", "quantifier", "satisfiability",
            "logical", "deduction", "induction",
        ],
    },
    "Multimedia & Creative": {
        "strong": [
            "audio", "dsp", "music", "video", "visualization",
            "creative-coding", "image-processing", "animation",
            "synthesizer", "midi", "wav", "mp3", "codec",
        ],
        "moderate": [
            "multimedia", "media", "sound", "visual",
            "rendering", "canvas", "svg", "webgl",
            "generative-art", "procedural-art",
            "signal-processing", "fft", "filter",
            "camera", "photo", "font", "typography",
        ],
        "weak": [
            "art", "creative", "design", "aesthetic",
            "color", "palette", "gradient", "pattern",
            "playback", "recording", "streaming",
        ],
    },
}

# Priority order for tie-breaking: more specific categories win.
PRIORITY: list[str] = [
    "Cryptography & Security",
    "Formal Verification",
    "Systems & Embedded",
    "Compilers & Languages",
    "Graphics & Rendering",
    "Simulation & Modeling",
    "Games",
    "Multimedia & Creative",
    "Distributed Systems",
    "Networking",
    "Serialization & Formats",
    "Data & Storage",
    "Concurrency & Async",
    "AI & Machine Learning",
    "Infrastructure & Ops",
    "Web & Services",
    "Developer Tools",
    "Exploratory",
]

DEFAULT_CATEGORY = "Exploratory"
SCORE_THRESHOLD = 2.0

# ---------------------------------------------------------------------------
# Keyword tier weights
# ---------------------------------------------------------------------------

TIER_WEIGHT = {"strong": 3.0, "moderate": 1.5, "weak": 0.5}

# ---------------------------------------------------------------------------
# Field weights
# ---------------------------------------------------------------------------

FIELD_WEIGHTS = {
    "category": 10.0,
    "tags": 3.0,
    "concepts_motifs": 1.5,
    "description_name": 0.5,
}

# ---------------------------------------------------------------------------
# Precompile keyword sets per category per tier
# ---------------------------------------------------------------------------


def _build_keyword_index() -> dict[str, dict[str, set[str]]]:
    """Return {category: {tier: {keyword, ...}}}."""
    index: dict[str, dict[str, set[str]]] = {}
    for category, tiers in TAXONOMY.items():
        index[category] = {}
        for tier_name, keywords in tiers.items():
            index[category][tier_name] = {kw.casefold() for kw in keywords}
    return index


KEYWORD_INDEX = _build_keyword_index()


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _count_substring_hits(
    text: str, keywords: set[str],
) -> int:
    """Count how many keywords appear as substrings in text."""
    text_lower = text.casefold()
    return sum(1 for kw in keywords if kw in text_lower)


def _count_set_hits(
    tokens: set[str], keywords: set[str],
) -> int:
    """Count set intersection size."""
    return len(tokens & keywords)


def score_project(
    category_text: str,
    tags_set: set[str],
    concepts_motifs_set: set[str],
    desc_name_text: str,
) -> list[tuple[str, float]]:
    """Score a project against all taxonomy categories.

    Returns sorted list of (category, score) pairs, highest first.
    """
    scores: list[tuple[str, float]] = []

    for cat_name, tier_sets in KEYWORD_INDEX.items():
        total = 0.0
        for tier_name, keywords in tier_sets.items():
            tier_w = TIER_WEIGHT[tier_name]

            # Category field (substring match).
            cat_hits = _count_substring_hits(category_text, keywords)
            total += FIELD_WEIGHTS["category"] * tier_w * cat_hits

            # Tags (set membership).
            tag_hits = _count_set_hits(tags_set, keywords)
            total += FIELD_WEIGHTS["tags"] * tier_w * tag_hits

            # Concepts + mined_motifs (set membership).
            cm_hits = _count_set_hits(concepts_motifs_set, keywords)
            total += FIELD_WEIGHTS["concepts_motifs"] * tier_w * cm_hits

            # Description + name (substring match).
            dn_hits = _count_substring_hits(desc_name_text, keywords)
            total += FIELD_WEIGHTS["description_name"] * tier_w * dn_hits

        if total > 0:
            scores.append((cat_name, total))

    # Sort by score descending, then by priority (lower index = higher priority).
    priority_map = {name: i for i, name in enumerate(PRIORITY)}
    scores.sort(key=lambda x: (-x[1], priority_map.get(x[0], 999)))
    return scores


# ---------------------------------------------------------------------------
# Project data extraction
# ---------------------------------------------------------------------------


def extract_fields(data: dict[str, Any]) -> tuple[str, set[str], set[str], str]:
    """Extract scoring fields from a summary record.

    Returns (category_text, tags_set, concepts_motifs_set, desc_name_text).
    """
    category_text = as_string(data.get("category"))

    tags = as_string_list(data.get("tags"))
    tags_set = {t.casefold() for t in tags}

    concepts = as_string_list(data.get("concepts"))
    mined_motifs = as_string_list(data.get("mined_motifs"))
    concepts_motifs_set = {t.casefold() for t in concepts + mined_motifs}

    name = as_string(data.get("name"))
    description = as_string(data.get("description"))
    desc_name_text = f"{name} {description}"

    return category_text, tags_set, concepts_motifs_set, desc_name_text


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Assign taxonomy categories to projects via keyword scoring.",
    )
    parser.add_argument("--shadow-root", default=None, help="Summary root (default: autodetect).")
    parser.add_argument("--dry-run", action="store_true", help="Report without writing.")
    parser.add_argument("--verbose", action="store_true", help="Print per-project scoring details.")
    parser.add_argument("--limit", type=int, default=0, help="Only process first N projects.")
    args = parser.parse_args()

    shadow_root = resolve_shadow_root(args.shadow_root)
    files = sorted(shadow_root.rglob("summary.json"))
    if args.limit > 0:
        files = files[: args.limit]

    if not files:
        print(f"No summary.json files found under {shadow_root}", file=sys.stderr)
        return 1

    print(f"Shadow root: {shadow_root}")
    print(f"Projects: {len(files)}")
    if args.dry_run:
        print("Mode: dry-run")
    print()

    category_counter: Counter[str] = Counter()
    changed_count = 0
    unchanged_count = 0
    skipped_count = 0

    for index, path in enumerate(files, start=1):
        data = read_summary(path)
        if data is None:
            skipped_count += 1
            continue

        category_text, tags_set, concepts_motifs_set, desc_name_text = extract_fields(data)
        scores = score_project(category_text, tags_set, concepts_motifs_set, desc_name_text)

        if scores and scores[0][1] >= SCORE_THRESHOLD:
            new_category = scores[0][0]
        else:
            new_category = DEFAULT_CATEGORY

        category_counter[new_category] += 1

        # Check if this is actually a change.
        alignment = data.get("_alignment") or {}
        old_category = alignment.get("category", data.get("category", ""))

        is_change = old_category != new_category
        if is_change:
            changed_count += 1
        else:
            unchanged_count += 1

        project_name = as_string(data.get("name")) or Path(as_string(data.get("_path")) or str(path)).name
        label = "CHANGE" if is_change else "ok"

        if args.verbose:
            top3 = scores[:3] if scores else []
            top3_str = ", ".join(f"{c}={s:.1f}" for c, s in top3)
            print(
                f"[{index}/{len(files)}] {label:<6} {project_name:<40} "
                f"{old_category!r} -> {new_category!r}  ({top3_str})"
            )
        elif is_change:
            print(
                f"[{index}/{len(files)}] {label:<6} {project_name:<40} "
                f"{old_category!r} -> {new_category!r}"
            )

        if not args.dry_run and is_change:
            ts = now_rfc3339()
            alignment = data.get("_alignment", {})
            alignment["category"] = new_category
            alignment["aligner"] = "taxonomy-v1"
            alignment["aligned_at"] = ts
            data["_alignment"] = alignment
            data["_updated_at"] = ts
            write_summary(path, data)

    # Distribution table.
    print()
    print("=" * 60)
    print(f"{'Category':<35} {'Count':>6} {'%':>6}")
    print("-" * 60)
    total = sum(category_counter.values())
    for cat_name in PRIORITY:
        count = category_counter.get(cat_name, 0)
        pct = (count / total * 100) if total else 0
        bar = "#" * int(pct)
        print(f"{cat_name:<35} {count:>6} {pct:>5.1f}% {bar}")
    print("-" * 60)
    print(f"{'Total':<35} {total:>6}")
    print()

    print(f"Changed:   {changed_count}")
    print(f"Unchanged: {unchanged_count}")
    print(f"Skipped:   {skipped_count}")
    print(f"Categories used: {len(category_counter)}/{len(PRIORITY)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
