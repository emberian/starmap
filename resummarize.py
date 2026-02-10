#!/usr/bin/env python3
"""Incremental recursive project summarization.

Creates a 1:1 shadow directory under ~/.summarization with a summary.json for each
project root discovered in configured target directories.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

HOME = Path.home()
DEFAULT_TARGET_DIRS = [
    HOME / "dev",
    HOME / "shitheap",
    HOME / "hellas",
    HOME / "elide",
    HOME / "src",
]
DEFAULT_SHADOW_ROOT = HOME / ".summarization"
DEFAULT_GEMINI_BIN = "gemini"

PROJECT_INDICATORS = {
    "Cargo.toml",
    "package.json",
    "go.mod",
    "dune-project",
    "requirements.txt",
    "pyproject.toml",
    "flake.nix",
    "mix.exs",
    "Gemfile",
    "pom.xml",
    "README.md",
    "README",
}

SOURCE_EXTENSIONS = {
    ".rs",
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".go",
    ".java",
    ".kt",
    ".swift",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".ml",
    ".mli",
    ".scala",
    ".clj",
    ".cljs",
    ".sh",
    ".nix",
    ".sol",
    ".zig",
}

LANGUAGE_BY_EXTENSION = {
    ".rs": "Rust",
    ".py": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".go": "Go",
    ".java": "Java",
    ".kt": "Kotlin",
    ".swift": "Swift",
    ".c": "C",
    ".cc": "C++",
    ".cpp": "C++",
    ".h": "C/C++",
    ".hpp": "C++",
    ".ml": "OCaml",
    ".mli": "OCaml",
    ".scala": "Scala",
    ".clj": "Clojure",
    ".cljs": "ClojureScript",
    ".sh": "Shell",
    ".nix": "Nix",
    ".sol": "Solidity",
    ".zig": "Zig",
}

IGNORE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".jj",
    "node_modules",
    "target",
    "dist",
    "build",
    "_build",
    ".venv",
    "venv",
    "__pycache__",
    ".next",
    ".turbo",
    ".cache",
    "vendor",
    "coverage",
    "tmp",
}

MAX_HASH_FILES = 6000
MAX_STRUCTURE_FILES = 240
MAX_SOURCE_SNIPPETS = 10
MAX_SNIPPET_CHARS = 3000
MAX_README_CHARS = 6000

CATEGORY_RULES = {
    "Crypto/ZK": ["zk", "zero-knowledge", "proof", "snark", "mina", "cryptography", "signature"],
    "Compilers/PL": ["compiler", "parser", "ast", "bytecode", "interpreter", "typechecker", "language"],
    "Runtimes/Systems": ["runtime", "kernel", "scheduler", "memory", "ffi", "concurrency", "thread"],
    "Web/App": ["http", "web", "frontend", "react", "next", "api", "server", "client"],
    "Developer Tooling": ["cli", "tooling", "lsp", "editor", "build", "automation", "script"],
    "Data/ML": ["model", "training", "inference", "dataset", "vector", "embedding"],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Recursively summarize projects into ~/.summarization")
    parser.add_argument(
        "--target",
        action="append",
        help="Target directory to scan (repeatable). Defaults to configured roots.",
    )
    parser.add_argument(
        "--shadow-root",
        default=str(DEFAULT_SHADOW_ROOT),
        help="Shadow root path for summary output.",
    )
    parser.add_argument("--gemini-bin", default=DEFAULT_GEMINI_BIN, help="Gemini CLI binary to invoke.")
    parser.add_argument("--force", action="store_true", help="Regenerate all summaries regardless of hash.")
    parser.add_argument("--limit", type=int, default=0, help="Only process the first N projects discovered.")
    parser.add_argument("--dry-run", action="store_true", help="Show planned actions without writing files.")
    parser.add_argument("--verbose", action="store_true", help="Enable extra logging.")
    return parser.parse_args()


def list_dir(path: Path) -> tuple[list[str], list[str]]:
    files: list[str] = []
    dirs: list[str] = []
    try:
        for entry in path.iterdir():
            if entry.name.startswith("."):
                continue
            if entry.is_dir():
                dirs.append(entry.name)
            else:
                files.append(entry.name)
    except (PermissionError, FileNotFoundError):
        return [], []
    return files, dirs


def looks_like_project(path: Path, files: list[str], dirs: list[str]) -> bool:
    if any(indicator in files for indicator in PROJECT_INDICATORS):
        return True
    if ".git" in dirs:
        return True

    source_like = [f for f in files if Path(f).suffix in SOURCE_EXTENSIONS]
    if len(source_like) >= 4:
        return True

    if any(d in {"src", "lib", "cmd", "app"} for d in dirs):
        score = len(source_like)
        if any(f.endswith(('.toml', '.yaml', '.yml', '.json')) for f in files):
            score += 2
        return score >= 3

    return False


def iter_project_roots(target: Path) -> Iterable[Path]:
    for root, dirs, files in os.walk(target):
        root_path = Path(root)

        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith('.')]

        if root_path.name.startswith('.'):
            dirs[:] = []
            continue

        visible_files = [f for f in files if not f.startswith('.')]
        if looks_like_project(root_path, visible_files, dirs):
            yield root_path
            dirs[:] = []


def get_files_hash(path: Path) -> str:
    hasher = hashlib.sha256()
    file_count = 0

    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith('.')]

        rel_root = Path(root).relative_to(path)
        for file_name in sorted(files):
            if file_name.startswith('.'):
                continue

            file_count += 1
            if file_count > MAX_HASH_FILES:
                break

            rel_path = rel_root / file_name
            full_path = Path(root) / file_name
            try:
                stat = full_path.stat()
            except (FileNotFoundError, OSError):
                continue

            hasher.update(str(rel_path).encode('utf-8'))
            hasher.update(str(stat.st_mtime_ns).encode('utf-8'))
            hasher.update(str(stat.st_size).encode('utf-8'))

        if file_count > MAX_HASH_FILES:
            break

    hasher.update(str(file_count).encode('utf-8'))
    return hasher.hexdigest()


def safe_read_text(path: Path, max_chars: int) -> str:
    try:
        with path.open('r', encoding='utf-8', errors='ignore') as handle:
            return handle.read(max_chars)
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return ""


def gather_project_context(path: Path) -> dict:
    structure: list[str] = []
    language_counter: Counter[str] = Counter()
    source_candidates: list[tuple[int, Path]] = []
    keywords: Counter[str] = Counter()

    file_count = 0
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith('.')]
        rel_root = Path(root).relative_to(path)

        for file_name in files:
            if file_name.startswith('.'):
                continue

            file_count += 1
            rel_path = rel_root / file_name

            if len(structure) < MAX_STRUCTURE_FILES:
                structure.append(str(rel_path))

            ext = rel_path.suffix.lower()
            language = LANGUAGE_BY_EXTENSION.get(ext)
            if language:
                language_counter[language] += 1

            if ext in SOURCE_EXTENSIONS:
                full_path = Path(root) / file_name
                try:
                    size = full_path.stat().st_size
                except OSError:
                    size = 0
                if 0 < size <= 128_000:
                    source_candidates.append((size, full_path))

            stem_parts = [part.lower() for part in rel_path.stem.replace('-', '_').split('_')]
            for part in stem_parts:
                if len(part) >= 4:
                    keywords[part] += 1

    readme = ""
    for candidate in ("README.md", "README", "readme.md", "README.txt"):
        candidate_path = path / candidate
        if candidate_path.exists():
            readme = safe_read_text(candidate_path, MAX_README_CHARS)
            if readme:
                break

    preferred_files = [
        "Cargo.toml",
        "package.json",
        "pyproject.toml",
        "go.mod",
        "dune-project",
        "flake.nix",
        "Makefile",
    ]
    samples: list[Path] = []
    for name in preferred_files:
        candidate = path / name
        if candidate.exists() and candidate.is_file():
            samples.append(candidate)

    source_candidates.sort(key=lambda x: x[0], reverse=True)
    for _, source_path in source_candidates:
        if len(samples) >= MAX_SOURCE_SNIPPETS:
            break
        if source_path not in samples:
            samples.append(source_path)

    snippets: list[dict] = []
    for sample in samples[:MAX_SOURCE_SNIPPETS]:
        rel = sample.relative_to(path)
        content = safe_read_text(sample, MAX_SNIPPET_CHARS)
        if not content.strip():
            continue
        snippets.append({"path": str(rel), "content": content})

    top_languages = [name for name, _ in language_counter.most_common(6)]
    top_keywords = [name for name, count in keywords.most_common(15) if count > 1]

    return {
        "file_count": file_count,
        "structure": structure,
        "readme": readme,
        "snippets": snippets,
        "top_languages": top_languages,
        "top_keywords": top_keywords,
    }


def infer_category(text: str) -> str:
    lower = text.lower()
    best_category = "Exploratory"
    best_score = 0

    for category, needles in CATEGORY_RULES.items():
        score = sum(1 for needle in needles if needle in lower)
        if score > best_score:
            best_score = score
            best_category = category

    return best_category


def fallback_summary(path: Path, context: dict) -> dict:
    languages = context.get("top_languages", [])
    keywords = context.get("top_keywords", [])

    lang_text = ", ".join(languages[:3]) if languages else "mixed tooling"
    keyword_text = ", ".join(keywords[:4]) if keywords else "repository structure and source files"
    category = infer_category(f"{path.name} {' '.join(keywords)} {' '.join(languages)}")

    description = (
        f"{path.name} appears to be a {category.lower()} project using {lang_text}. "
        f"The available files suggest focus areas around {keyword_text}."
    )

    tags = list(dict.fromkeys((languages + keywords)[:8]))
    concepts = keywords[:6]

    return {
        "name": path.name,
        "description": description,
        "tags": tags,
        "category": category,
        "concepts": concepts,
        "languages": languages,
    }


def build_prompt(path: Path, context: dict) -> str:
    lines: list[str] = []
    lines.append(f"Project name: {path.name}")
    lines.append(f"Project path: {path}")
    lines.append(f"Approx file count: {context['file_count']}")
    lines.append(f"Top languages: {', '.join(context['top_languages']) or 'unknown'}")
    lines.append(f"Top filename keywords: {', '.join(context['top_keywords']) or 'none'}")

    lines.append("\n=== File Structure (sample) ===")
    lines.extend(context["structure"])

    if context["readme"].strip():
        lines.append("\n=== README (truncated) ===")
        lines.append(context["readme"])

    if context["snippets"]:
        lines.append("\n=== Representative File Snippets ===")
        for item in context["snippets"]:
            lines.append(f"\n--- {item['path']} ---")
            lines.append(item["content"])

    instructions = (
        "You are a repository summarization agent. Infer intent even if README is missing. "
        "Return ONLY valid JSON. Do not include markdown or prose outside JSON.\n"
        "Schema:\n"
        "{\n"
        '  "name": "string",\n'
        '  "description": "one coherent paragraph, 2-4 sentences",\n'
        '  "tags": ["string"],\n'
        '  "category": "one of: Crypto/ZK, Compilers/PL, Runtimes/Systems, Web/App, Developer Tooling, Data/ML, Exploratory",\n'
        '  "concepts": ["string"],\n'
        '  "languages": ["string"]\n'
        "}\n"
        "Keep tags/concepts concrete and non-redundant (max 8 each)."
    )

    return instructions + "\n\nContext:\n" + "\n".join(lines)


def extract_json_object(text: str) -> str | None:
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False
    for idx, char in enumerate(text[start:], start=start):
        if in_string:
            if escape:
                escape = False
                continue
            if char == "\\":
                escape = True
                continue
            if char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:idx + 1]

    return None


def sanitize_summary(path: Path, raw: dict, context: dict) -> dict:
    fallback = fallback_summary(path, context)

    name = str(raw.get("name") or fallback["name"]).strip() or path.name
    description = str(raw.get("description") or fallback["description"]).strip()
    if len(description.split()) < 12:
        description = fallback["description"]

    tags_raw = raw.get("tags") if isinstance(raw.get("tags"), list) else []
    tags = [str(tag).strip() for tag in tags_raw if str(tag).strip()]

    concepts_raw = raw.get("concepts") if isinstance(raw.get("concepts"), list) else []
    concepts = [str(concept).strip() for concept in concepts_raw if str(concept).strip()]

    languages_raw = raw.get("languages") if isinstance(raw.get("languages"), list) else []
    languages = [str(lang).strip() for lang in languages_raw if str(lang).strip()]

    if not languages:
        languages = context.get("top_languages", [])[:6]

    category = str(raw.get("category") or "").strip()
    if category not in {
        "Crypto/ZK",
        "Compilers/PL",
        "Runtimes/Systems",
        "Web/App",
        "Developer Tooling",
        "Data/ML",
        "Exploratory",
    }:
        category = infer_category(" ".join([description, " ".join(tags), " ".join(concepts)]))

    if not tags:
        tags = fallback["tags"]
    if not concepts:
        concepts = fallback["concepts"]

    dedup = lambda values: list(dict.fromkeys(v for v in values if v))[:8]

    return {
        "name": name,
        "description": description,
        "tags": dedup(tags),
        "category": category,
        "concepts": dedup(concepts),
        "languages": dedup(languages),
        "evidence": {
            "file_count": context.get("file_count", 0),
            "has_readme": bool(context.get("readme", "").strip()),
            "sample_files": [item["path"] for item in context.get("snippets", [])[:5]],
        },
    }


def generate_summary(path: Path, gemini_bin: str, context: dict, verbose: bool = False) -> dict:
    prompt = build_prompt(path, context)
    cmd = [gemini_bin, "-p", prompt, "--output-format", "text"]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except FileNotFoundError:
        if verbose:
            print(f"[warn] gemini binary not found: {gemini_bin}")
        return fallback_summary(path, context)
    except subprocess.TimeoutExpired:
        if verbose:
            print(f"[warn] gemini timeout: {path}")
        return fallback_summary(path, context)

    if result.returncode != 0:
        if verbose:
            stderr = result.stderr.strip()
            print(f"[warn] gemini failed for {path.name}: {stderr[:400]}")
        return fallback_summary(path, context)

    output = result.stdout.strip()
    json_blob = extract_json_object(output)
    if not json_blob:
        if verbose:
            print(f"[warn] no JSON detected for {path.name}")
        return fallback_summary(path, context)

    try:
        parsed = json.loads(json_blob)
    except json.JSONDecodeError:
        if verbose:
            print(f"[warn] invalid JSON for {path.name}")
        return fallback_summary(path, context)

    if not isinstance(parsed, dict):
        return fallback_summary(path, context)

    return sanitize_summary(path, parsed, context)


def read_existing_hash(summary_file: Path) -> str | None:
    try:
        with summary_file.open('r', encoding='utf-8') as handle:
            existing = json.load(handle)
        if isinstance(existing, dict):
            return existing.get("_hash")
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return None


def process_project(
    path: Path,
    shadow_root: Path,
    gemini_bin: str,
    force: bool,
    dry_run: bool,
    verbose: bool,
) -> str:
    rel_path = path.relative_to(HOME)
    shadow_dir = shadow_root / rel_path
    summary_file = shadow_dir / "summary.json"

    project_hash = get_files_hash(path)
    if not force:
        previous_hash = read_existing_hash(summary_file)
        if previous_hash == project_hash:
            return "skip"

    if dry_run:
        return "would_update"

    context = gather_project_context(path)
    summary = generate_summary(path, gemini_bin, context, verbose=verbose)

    summary["_hash"] = project_hash
    summary["_path"] = str(path)
    summary["_updated_at"] = datetime.now(timezone.utc).isoformat()

    shadow_dir.mkdir(parents=True, exist_ok=True)
    with summary_file.open('w', encoding='utf-8') as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    return "updated"


def normalize_targets(values: list[str] | None) -> list[Path]:
    if not values:
        return [path for path in DEFAULT_TARGET_DIRS if path.exists()]
    return [Path(v).expanduser().resolve() for v in values if Path(v).expanduser().exists()]


def main() -> int:
    args = parse_args()
    targets = normalize_targets(args.target)

    if not targets:
        print("No valid target directories to scan.", file=sys.stderr)
        return 1

    shadow_root = Path(args.shadow_root).expanduser().resolve()
    if not args.dry_run:
        shadow_root.mkdir(parents=True, exist_ok=True)

    project_roots: list[Path] = []
    for target in targets:
        project_roots.extend(iter_project_roots(target))

    project_roots = list(dict.fromkeys(project_roots))
    project_roots.sort()

    if args.limit > 0:
        project_roots = project_roots[: args.limit]

    print(f"Summarization root: {shadow_root}")
    print(f"Targets: {', '.join(str(t) for t in targets)}")
    print(f"Discovered projects: {len(project_roots)}")

    stats = Counter()
    for idx, project in enumerate(project_roots, start=1):
        status = process_project(
            project,
            shadow_root,
            args.gemini_bin,
            force=args.force,
            dry_run=args.dry_run,
            verbose=args.verbose,
        )
        stats[status] += 1
        print(f"[{idx}/{len(project_roots)}] {status:>11}  {project}")

    print("Done.")
    print(
        "Summary: "
        + ", ".join(f"{key}={stats[key]}" for key in ["updated", "would_update", "skip"] if stats[key])
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
