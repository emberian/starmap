#!/usr/bin/env python3
"""Repair weak repository summaries under a shadow root using Gemini.

This script is intentionally standalone from the Rust `resummarize` binary.
Run it after a full summarization pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from starmap_lib import (
    GeminiClient,
    as_string,
    as_string_list,
    canonical_json,
    dedup,
    now_rfc3339,
    read_summary,
    resolve_shadow_root,
    write_summary,
)

IGNORE_DIRS = {
    ".git", ".hg", ".svn", ".jj", "node_modules", "target", "dist", "build",
    "_build", ".venv", "venv", "__pycache__", ".next", ".turbo", ".cache",
    "vendor", "coverage", "tmp",
}

SOURCE_EXTENSIONS = {
    ".rs", ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".java", ".kt",
    ".swift", ".c", ".cc", ".cpp", ".h", ".hpp", ".ml", ".mli", ".scala",
    ".clj", ".cljs", ".sh", ".nix", ".sol", ".zig",
}

MAX_STRUCTURE_FILES = 220
MAX_SOURCE_SNIPPETS = 12
MAX_SNIPPET_CHARS = 2600
MAX_README_CHARS = 7000
MAX_CONCEPTS = 24
MAX_TAGS = 16
MAX_LANGUAGES = 12

LANGUAGE_BY_EXTENSION = {
    ".rs": "Rust", ".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript",
    ".js": "JavaScript", ".jsx": "JavaScript", ".go": "Go", ".java": "Java",
    ".kt": "Kotlin", ".swift": "Swift", ".c": "C", ".cc": "C++", ".cpp": "C++",
    ".h": "C/C++", ".hpp": "C++", ".ml": "OCaml", ".mli": "OCaml",
    ".scala": "Scala", ".clj": "Clojure", ".cljs": "ClojureScript",
    ".sh": "Shell", ".nix": "Nix", ".sol": "Solidity", ".zig": "Zig",
}

STOPWORDS = {
    "the", "and", "for", "with", "from", "into", "this", "that", "repo",
    "project", "src", "lib", "app", "core", "test", "tests", "docs",
    "examples", "example", "bin", "cmd", "tool", "tools", "internal", "common",
}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_text(path: Path, max_chars: int) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            return handle.read(max_chars)
    except OSError:
        return ""


def is_likely_text(path: Path) -> bool:
    if path.suffix.lower() in SOURCE_EXTENSIONS:
        return True
    try:
        with path.open("rb") as handle:
            chunk = handle.read(1024)
    except OSError:
        return False
    if b"\x00" in chunk:
        return False
    return True


def iter_project_files(project_path: Path) -> list[Path]:
    out: list[Path] = []
    stack = [project_path]
    while stack:
        root = stack.pop()
        try:
            entries = sorted(root.iterdir(), key=lambda p: p.name)
        except OSError:
            continue
        for entry in entries:
            name = entry.name
            if name.startswith("."):
                continue
            if entry.is_dir():
                if name in IGNORE_DIRS:
                    continue
                stack.append(entry)
                continue
            if entry.is_file():
                out.append(entry)
    return out


def tokenize_filename(path: Path) -> list[str]:
    stem = path.stem.lower()
    tokens = re.findall(r"[a-z0-9]+", stem)
    return [tok for tok in tokens if len(tok) >= 3 and tok not in STOPWORDS]


def gather_project_context(project_path: Path) -> dict[str, Any]:
    if not project_path.exists() or not project_path.is_dir():
        return {
            "path": str(project_path),
            "exists": False,
            "file_count": 0,
            "structure": [],
            "readme": "",
            "snippets": [],
            "top_languages": [],
            "top_keywords": [],
        }

    files = iter_project_files(project_path)

    structure = [
        str(path.relative_to(project_path))
        for path in sorted(files, key=lambda p: str(p.relative_to(project_path)))[:MAX_STRUCTURE_FILES]
    ]

    readme = ""
    for name in ("README.md", "README", "Readme.md", "readme.md"):
        candidate = project_path / name
        if candidate.exists() and candidate.is_file():
            readme = read_text(candidate, MAX_README_CHARS)
            break

    lang_counter: Counter[str] = Counter()
    keyword_counter: Counter[str] = Counter()

    source_candidates: list[Path] = []
    for path in files:
        ext = path.suffix.lower()
        language = LANGUAGE_BY_EXTENSION.get(ext)
        if language:
            lang_counter[language] += 1
        for token in tokenize_filename(path):
            keyword_counter[token] += 1
        if ext in SOURCE_EXTENSIONS and is_likely_text(path):
            source_candidates.append(path)

    snippets = []
    for path in source_candidates[:MAX_SOURCE_SNIPPETS]:
        content = read_text(path, MAX_SNIPPET_CHARS).strip()
        if not content:
            continue
        snippets.append(
            {
                "path": str(path.relative_to(project_path)),
                "content": content,
            }
        )

    return {
        "path": str(project_path),
        "exists": True,
        "file_count": len(files),
        "structure": structure,
        "readme": readme,
        "snippets": snippets,
        "top_languages": [name for name, _ in lang_counter.most_common(8)],
        "top_keywords": [name for name, _ in keyword_counter.most_common(12)],
    }


def compact_summary_for_judge(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": summary.get("name"),
        "description": summary.get("description"),
        "category": summary.get("category"),
        "maturity": summary.get("maturity"),
        "tags": summary.get("tags", []),
        "concepts": summary.get("concepts", []),
        "languages": summary.get("languages", []),
        "mined_motifs": summary.get("mined_motifs", []),
        "attribution": summary.get("attribution", "interests"),
    }


def build_judge_prompt(summary: dict[str, Any], context: dict[str, Any]) -> str:
    context_view = {
        "path": context.get("path"),
        "exists": context.get("exists"),
        "file_count": context.get("file_count"),
        "top_languages": context.get("top_languages", []),
        "top_keywords": context.get("top_keywords", []),
        "structure_sample": context.get("structure", [])[:120],
        "readme_excerpt": (context.get("readme") or "")[:1800],
        "sample_files": [s.get("path") for s in context.get("snippets", [])[:8]],
    }

    instructions = {
        "task": "Judge if this repository summary is weak.",
        "weak_definition": [
            "generic/vague description with low technical signal",
            "important repo evidence missing",
            "wrong or broad category",
            "concepts/tags too shallow for discovery and clustering",
        ],
        "output_schema": {
            "weak": "boolean",
            "score": "integer 0-100 where lower means weaker",
            "confidence": "number 0-1",
            "reasons": ["string"],
            "missing_topics": ["string"],
            "repair_brief": "short instruction string",
        },
        "rules": [
            "Return JSON only.",
            "Do not use markdown.",
            "Be strict but avoid false positives.",
        ],
    }

    return (
        f"{json.dumps(instructions, ensure_ascii=False)}\n\n"
        f"SUMMARY:\n{json.dumps(compact_summary_for_judge(summary), ensure_ascii=False, indent=2)}\n\n"
        f"REPOSITORY_CONTEXT:\n{json.dumps(context_view, ensure_ascii=False, indent=2)}"
    )


def build_repair_prompt(
    current_summary: dict[str, Any],
    judge: dict[str, Any],
    context: dict[str, Any],
) -> str:
    instructions = {
        "task": "Produce a stronger, technically specific repository summary JSON.",
        "requirements": [
            "Use only evidence from provided context.",
            "Prefer precise technical language over generic wording.",
            "If uncertain, state plausible scope conservatively.",
            "Do not include markdown or prose outside JSON.",
        ],
        "schema": {
            "name": "string",
            "description": "single paragraph, 3-6 sentences, technical",
            "tags": "array of concrete tags, up to 16",
            "category": "specific domain label",
            "concepts": "array of concrete concepts, target 12-24 when evidence supports it",
            "languages": "array of languages",
            "maturity": "one of exploratory/prototype/active/stable/archival",
            "evidence": {
                "file_count": "integer",
                "has_readme": "boolean",
                "sample_files": ["string"],
            },
        },
    }

    context_payload = {
        "path": context.get("path"),
        "file_count": context.get("file_count"),
        "top_languages": context.get("top_languages", []),
        "top_keywords": context.get("top_keywords", []),
        "structure_sample": context.get("structure", [])[:MAX_STRUCTURE_FILES],
        "readme_excerpt": context.get("readme", "")[:MAX_README_CHARS],
        "snippets": context.get("snippets", [])[:MAX_SOURCE_SNIPPETS],
    }

    payload = {
        "current_summary": compact_summary_for_judge(current_summary),
        "judge": judge,
        "repository_context": context_payload,
    }

    return (
        f"{json.dumps(instructions, ensure_ascii=False)}\n\n"
        f"INPUT:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def dedup_strings(values: list[str], max_len: int) -> list[str]:
    result = dedup(values)
    if max_len > 0:
        result = result[:max_len]
    return result


def sanitize_repair(
    existing: dict[str, Any],
    repaired: dict[str, Any],
    judge: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    out = dict(existing)

    existing_name = as_string(existing.get("name")) or "Unnamed"
    existing_desc = as_string(existing.get("description"))
    existing_category = as_string(existing.get("category"))
    existing_maturity = as_string(existing.get("maturity")) or "exploratory"
    existing_tags = as_string_list(existing.get("tags"))
    existing_concepts = as_string_list(existing.get("concepts"))
    existing_languages = as_string_list(existing.get("languages"))

    name = as_string(repaired.get("name")) or existing_name

    description = as_string(repaired.get("description"))
    if len(description.split()) < 18:
        description = existing_desc or description
    if not description:
        description = "No summary available."

    tags = dedup_strings(
        as_string_list(repaired.get("tags")) + existing_tags,
        MAX_TAGS,
    )
    concepts = dedup_strings(
        as_string_list(repaired.get("concepts"))
        + as_string_list(judge.get("missing_topics"))
        + existing_concepts,
        MAX_CONCEPTS,
    )
    languages = dedup_strings(
        as_string_list(repaired.get("languages"))
        + context.get("top_languages", [])
        + existing_languages,
        MAX_LANGUAGES,
    )

    category = as_string(repaired.get("category")) or existing_category
    if not category:
        category = "Exploratory Software"

    maturity = as_string(repaired.get("maturity")) or existing_maturity
    if maturity not in {"exploratory", "prototype", "active", "stable", "archival"}:
        maturity = existing_maturity

    evidence = repaired.get("evidence")
    if not isinstance(evidence, dict):
        evidence = existing.get("evidence", {})
        if not isinstance(evidence, dict):
            evidence = {}
    evidence = dict(evidence)
    evidence.setdefault("file_count", context.get("file_count", 0))
    evidence.setdefault("has_readme", bool(context.get("readme", "").strip()))
    evidence.setdefault(
        "sample_files",
        [snippet.get("path") for snippet in context.get("snippets", [])[:5] if snippet.get("path")],
    )

    out["name"] = name
    out["description"] = description
    out["tags"] = tags
    out["category"] = category
    out["concepts"] = concepts
    out["languages"] = languages
    out["maturity"] = maturity
    out["evidence"] = evidence
    out["_updated_at"] = now_rfc3339()

    for key in (
        "mined_motifs",
        "_motif_neighbors",
        "attribution",
        "_embedding",
        "_alignment",
    ):
        if key in existing and key not in out:
            out[key] = existing[key]

    prior_hash = sha256_text(json.dumps(existing, sort_keys=True, ensure_ascii=False))
    out["_repair"] = {
        "repaired_at": now_rfc3339(),
        "method": "gemini_deep_repair",
        "previous_summary_hash": prior_hash,
        "judge": {
            "weak": bool(judge.get("weak", False)),
            "score": judge.get("score"),
            "confidence": judge.get("confidence"),
            "reasons": as_string_list(judge.get("reasons")),
            "missing_topics": as_string_list(judge.get("missing_topics")),
        },
    }

    return out


def detect_weak(judge: dict[str, Any], min_confidence: float, max_weak_score: int) -> bool:
    weak = bool(judge.get("weak", False))
    confidence = judge.get("confidence")
    score = judge.get("score")

    try:
        confidence_value = float(confidence)
    except (TypeError, ValueError):
        confidence_value = 0.0
    try:
        score_value = int(score)
    except (TypeError, ValueError):
        score_value = 100

    if confidence_value < min_confidence:
        return False
    return weak or score_value <= max_weak_score


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Repair weak summaries under ~/.summarization with Gemini.",
    )
    parser.add_argument("--shadow-root", default=None, help="Root with summary.json files.")
    parser.add_argument("--gemini-bin", default="gemini", help="Gemini CLI binary.")
    parser.add_argument("--gemini-arg", action="append", default=[], help="Extra Gemini arg (repeatable).")
    parser.add_argument("--min-confidence", type=float, default=0.55)
    parser.add_argument("--max-weak-score", type=int, default=60)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--report-file", default=None)
    args = parser.parse_args()

    root = resolve_shadow_root(args.shadow_root)
    files = sorted(root.rglob("summary.json")) if root.exists() else []
    if args.limit > 0:
        files = files[: args.limit]

    if not files:
        print(f"No summary files found under {root}", file=sys.stderr)
        return 1

    gemini = GeminiClient(gemini_bin=args.gemini_bin, gemini_args=args.gemini_arg, verbose=args.verbose)

    print(f"Shadow root: {root}")
    print(f"Summary files: {len(files)}")
    print(f"Gemini: {args.gemini_bin}")
    if args.dry_run:
        print("Mode: dry-run")

    report_events: list[dict[str, Any]] = []
    stats: Counter[str] = Counter()

    for idx, summary_path in enumerate(files, start=1):
        summary = read_summary(summary_path)
        if summary is None:
            stats["invalid_json"] += 1
            report_events.append({"file": str(summary_path), "status": "invalid_json"})
            print(f"[{idx}/{len(files)}] invalid_json  {summary_path}")
            continue

        project_path = Path(as_string(summary.get("_path")))
        context = gather_project_context(project_path)

        judge_prompt = build_judge_prompt(summary, context)
        judge, judge_err = gemini.generate_json(judge_prompt)
        if judge is None:
            stats["judge_error"] += 1
            report_events.append({
                "file": str(summary_path),
                "path": as_string(summary.get("_path")),
                "status": "judge_error",
                "error": judge_err,
            })
            print(f"[{idx}/{len(files)}] judge_error   {as_string(summary.get('_path'))}")
            continue

        is_weak = detect_weak(judge, args.min_confidence, args.max_weak_score)
        if not is_weak:
            stats["strong"] += 1
            report_events.append({
                "file": str(summary_path),
                "path": as_string(summary.get("_path")),
                "status": "strong",
                "judge": {"score": judge.get("score"), "confidence": judge.get("confidence")},
            })
            print(f"[{idx}/{len(files)}] strong        {as_string(summary.get('_path'))}")
            continue

        repair_prompt = build_repair_prompt(summary, judge, context)
        repaired_raw, repair_err = gemini.generate_json(repair_prompt)
        if repaired_raw is None:
            stats["repair_error"] += 1
            report_events.append({
                "file": str(summary_path),
                "path": as_string(summary.get("_path")),
                "status": "repair_error",
                "judge": judge,
                "error": repair_err,
            })
            print(f"[{idx}/{len(files)}] repair_error  {as_string(summary.get('_path'))}")
            continue

        repaired = sanitize_repair(summary, repaired_raw, judge, context)
        changed = canonical_json(summary) != canonical_json(repaired)
        if changed and not args.dry_run:
            write_summary(summary_path, repaired)

        if changed:
            stats["repaired"] += 1
            status = "repaired"
        else:
            stats["weak_unchanged"] += 1
            status = "weak_unchanged"

        report_events.append({
            "file": str(summary_path),
            "path": as_string(summary.get("_path")),
            "status": status,
            "judge": judge,
            "changed": changed,
        })
        print(f"[{idx}/{len(files)}] {status:<13} {as_string(summary.get('_path'))}")

    report = {
        "generated_at": now_rfc3339(),
        "shadow_root": str(root),
        "dry_run": bool(args.dry_run),
        "gemini_bin": args.gemini_bin,
        "stats": dict(stats),
        "events": report_events,
    }

    report_file = Path(args.report_file).expanduser() if args.report_file else root / "repair-report.json"
    if not args.dry_run:
        report_file.parent.mkdir(parents=True, exist_ok=True)
        with report_file.open("w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=False)
            handle.write("\n")

    summary_parts = ", ".join(f"{key}={value}" for key, value in sorted(stats.items()))
    print("Done.")
    if summary_parts:
        print(f"Summary: {summary_parts}")
    print(f"Report: {report_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
