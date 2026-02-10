#!/usr/bin/env python3
"""Align concept/motif terminology with Gemini and build metaconstellations.

This is a standalone post-processing tool, separate from the main `resummarize`
binary. It updates `summary.json` records and emits `metaconstellations.json`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now_rfc3339() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def resolve_shadow_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    env_value = os.environ.get("STARMAP_SHADOW_ROOT")
    if env_value:
        return Path(env_value).expanduser()
    candidates = [
        Path("~/.summarize").expanduser(),
        Path("~/.summarization").expanduser(),
    ]
    best_path: Path | None = None
    best_count = -1
    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            count = sum(1 for _ in candidate.rglob("summary.json"))
        except OSError:
            count = 0
        if count > best_count:
            best_count = count
            best_path = candidate
    if best_path:
        return best_path
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "meta"


def as_string(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def as_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str):
            text = item.strip()
            if text:
                out.append(text)
    return out


def dedup(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def extract_json_object(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None

    depth = 0
    in_string = False
    escaped = False
    for idx in range(start, len(text)):
        ch = text[idx]
        if in_string:
            if escaped:
                escaped = False
                continue
            if ch == "\\":
                escaped = True
                continue
            if ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : idx + 1]
    return None


def run_gemini(
    gemini_bin: str,
    gemini_args: list[str],
    prompt: str,
    verbose: bool,
) -> tuple[dict[str, Any] | None, str | None]:
    cmd = [gemini_bin, *gemini_args, "-p", prompt, "--output-format", "text"]
    if verbose:
        print(f"[debug] running gemini: {gemini_bin}", file=sys.stderr)
    try:
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except FileNotFoundError:
        return None, f"gemini binary not found: {gemini_bin}"
    except OSError as err:
        return None, f"failed to execute gemini: {err}"

    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        return None, f"gemini exit {proc.returncode}: {err[:300]}"

    blob = extract_json_object((proc.stdout or "").strip())
    if not blob:
        return None, "gemini returned no JSON object"

    try:
        parsed = json.loads(blob)
    except json.JSONDecodeError as err:
        return None, f"invalid JSON from gemini: {err}"
    if not isinstance(parsed, dict):
        return None, "gemini JSON root must be an object"
    return parsed, None


@dataclass
class SummaryRecord:
    summary_file: Path
    raw: dict[str, Any]
    project_path: str
    name: str
    category: str
    concepts: list[str]
    tags: list[str]
    mined_motifs: list[str]

    @property
    def terms(self) -> list[str]:
        return dedup([*self.concepts, *self.tags, *self.mined_motifs])


def read_summary(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def write_summary(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=False)
        handle.write("\n")


def load_records(shadow_root: Path, limit: int) -> list[SummaryRecord]:
    files = sorted(shadow_root.rglob("summary.json"))
    if limit > 0:
        files = files[:limit]

    out: list[SummaryRecord] = []
    for file in files:
        raw = read_summary(file)
        if raw is None:
            continue
        project_path = as_string(raw.get("_path")) or str(file)
        name = as_string(raw.get("name")) or Path(project_path).name or "Unnamed"
        category = as_string(raw.get("category")) or "Exploratory"
        concepts = as_string_list(raw.get("concepts"))
        tags = as_string_list(raw.get("tags"))
        mined = as_string_list(raw.get("mined_motifs"))
        out.append(
            SummaryRecord(
                summary_file=file,
                raw=raw,
                project_path=project_path,
                name=name,
                category=category,
                concepts=concepts,
                tags=tags,
                mined_motifs=mined,
            )
        )
    return out


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def top_terms(records: list[SummaryRecord], max_terms: int) -> list[dict[str, Any]]:
    counter: Counter[str] = Counter()
    categories_by_term: dict[str, Counter[str]] = defaultdict(Counter)
    for record in records:
        for term in record.terms:
            counter[term] += 1
            categories_by_term[term][record.category] += 1

    ranked = counter.most_common(max_terms)
    out: list[dict[str, Any]] = []
    for term, count in ranked:
        top_categories = [
            {"name": category, "count": c}
            for category, c in categories_by_term[term].most_common(4)
        ]
        out.append(
            {
                "term": term,
                "count": count,
                "categories": top_categories,
            }
        )
    return out


def project_cards(records: list[SummaryRecord], max_projects: int) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for record in records[:max_projects]:
        cards.append(
            {
                "path": record.project_path,
                "name": record.name,
                "category": record.category,
                "concepts": record.concepts[:16],
                "tags": record.tags[:12],
                "mined_motifs": record.mined_motifs[:12],
            }
        )
    return cards


def build_prompt(
    records: list[SummaryRecord],
    max_terms: int,
    max_projects: int,
) -> str:
    category_counter: Counter[str] = Counter(record.category for record in records)
    category_summary = [{"name": name, "count": count} for name, count in category_counter.most_common(24)]

    instructions = {
        "task": "Align near-duplicate concepts/tags/motifs and propose metaconstellations.",
        "output_schema": {
            "alignments": [
                {
                    "canonical": "string canonical term",
                    "aliases": ["string aliases that should map to canonical"],
                    "confidence": "number 0-1",
                    "rationale": "short string",
                }
            ],
            "metaconstellations": [
                {
                    "id": "kebab-case id",
                    "name": "display name",
                    "description": "1-3 sentence synthesis",
                    "concepts": ["canonical concepts that define this meta cluster"],
                    "categories": ["relevant categories"],
                    "include_terms": ["additional terms that indicate membership"],
                    "exclude_terms": ["terms that should suppress membership"],
                }
            ],
            "notes": ["optional short notes"],
        },
        "rules": [
            "Return JSON only, no markdown.",
            "Do not invent aliases unless likely synonymous by software context.",
            "Keep canonical terms technically specific and stable.",
            "Metaconstellations should be cross-project strategic themes, not single-repo labels.",
            "Prefer 6-18 metaconstellations.",
            "Only use terms from provided inputs for aliases/include/exclude when possible.",
        ],
    }

    payload = {
        "summary_count": len(records),
        "category_distribution": category_summary,
        "top_terms": top_terms(records, max_terms),
        "project_cards": project_cards(records, max_projects),
    }

    return (
        f"{json.dumps(instructions, ensure_ascii=False)}\n\n"
        f"INPUT:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def sanitize_alignment_output(
    raw: dict[str, Any],
    observed_terms: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    observed_lower = {term.casefold(): term for term in observed_terms}

    alignments_raw = raw.get("alignments")
    metaraw = raw.get("metaconstellations")

    alignments: list[dict[str, Any]] = []
    alias_owner: dict[str, tuple[float, str]] = {}
    for item in alignments_raw if isinstance(alignments_raw, list) else []:
        if not isinstance(item, dict):
            continue
        canonical = as_string(item.get("canonical"))
        if not canonical:
            continue

        aliases_input = as_string_list(item.get("aliases"))
        aliases = []
        for alias in aliases_input:
            key = alias.casefold()
            if key == canonical.casefold():
                continue
            if key not in observed_lower:
                continue
            aliases.append(observed_lower[key])
        aliases = dedup(aliases)

        try:
            confidence = float(item.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))

        if not aliases:
            continue

        accepted_aliases: list[str] = []
        for alias in aliases:
            key = alias.casefold()
            prior = alias_owner.get(key)
            if prior and prior[0] >= confidence:
                continue
            alias_owner[key] = (confidence, canonical)
            accepted_aliases.append(alias)

        if not accepted_aliases:
            continue

        alignments.append(
            {
                "canonical": canonical,
                "aliases": accepted_aliases,
                "confidence": confidence,
                "rationale": as_string(item.get("rationale")),
            }
        )

    # Merge duplicate canonical entries.
    merged_by_canonical: dict[str, dict[str, Any]] = {}
    for alignment in alignments:
        canonical = alignment["canonical"]
        bucket = merged_by_canonical.get(canonical)
        if not bucket:
            merged_by_canonical[canonical] = {
                "canonical": canonical,
                "aliases": [],
                "confidence": alignment.get("confidence", 0.5),
                "rationale": alignment.get("rationale", ""),
            }
            bucket = merged_by_canonical[canonical]
        bucket["aliases"] = dedup(bucket["aliases"] + alignment["aliases"])
        bucket["confidence"] = max(bucket["confidence"], alignment.get("confidence", 0.5))

    clean_alignments = list(merged_by_canonical.values())
    clean_alignments.sort(key=lambda x: (x["canonical"].casefold(), -float(x.get("confidence", 0.0))))

    metaconstellations: list[dict[str, Any]] = []
    for item in metaraw if isinstance(metaraw, list) else []:
        if not isinstance(item, dict):
            continue
        name = as_string(item.get("name"))
        if not name:
            continue

        meta_id = as_string(item.get("id")) or slugify(name)
        description = as_string(item.get("description"))
        concepts = dedup(as_string_list(item.get("concepts")))[:40]
        categories = dedup(as_string_list(item.get("categories")))[:20]
        include_terms = dedup(as_string_list(item.get("include_terms")))[:40]
        exclude_terms = dedup(as_string_list(item.get("exclude_terms")))[:30]

        metaconstellations.append(
            {
                "id": slugify(meta_id),
                "name": name,
                "description": description,
                "concepts": concepts,
                "categories": categories,
                "include_terms": include_terms,
                "exclude_terms": exclude_terms,
            }
        )

    # Dedup metaconstellations by id.
    by_id: dict[str, dict[str, Any]] = {}
    for item in metaconstellations:
        existing = by_id.get(item["id"])
        if not existing:
            by_id[item["id"]] = item
            continue
        existing["concepts"] = dedup(existing["concepts"] + item["concepts"])[:40]
        existing["categories"] = dedup(existing["categories"] + item["categories"])[:20]
        existing["include_terms"] = dedup(existing["include_terms"] + item["include_terms"])[:40]
        existing["exclude_terms"] = dedup(existing["exclude_terms"] + item["exclude_terms"])[:30]

    clean_metas = list(by_id.values())
    clean_metas.sort(key=lambda x: x["name"].casefold())
    return clean_alignments, clean_metas


def build_alias_map(alignments: list[dict[str, Any]]) -> dict[str, str]:
    alias_map: dict[str, str] = {}
    for item in alignments:
        canonical = as_string(item.get("canonical"))
        if not canonical:
            continue
        for alias in as_string_list(item.get("aliases")):
            alias_map[alias.casefold()] = canonical
    return alias_map


def normalize_terms(terms: list[str], alias_map: dict[str, str]) -> list[str]:
    mapped = []
    for term in terms:
        canonical = alias_map.get(term.casefold(), term)
        mapped.append(canonical)
    return dedup(mapped)


def score_project_for_meta(
    project_terms: set[str],
    project_category: str,
    meta: dict[str, Any],
) -> tuple[float, list[str]]:
    score = 0.0
    matches: list[str] = []

    meta_concepts = {x.casefold() for x in as_string_list(meta.get("concepts"))}
    include_terms = {x.casefold() for x in as_string_list(meta.get("include_terms"))}
    exclude_terms = {x.casefold() for x in as_string_list(meta.get("exclude_terms"))}
    categories = {x.casefold() for x in as_string_list(meta.get("categories"))}

    if exclude_terms and project_terms.intersection(exclude_terms):
        return 0.0, []

    concept_hits = project_terms.intersection(meta_concepts)
    include_hits = project_terms.intersection(include_terms)
    category_hit = project_category.casefold() in categories if categories else False

    if concept_hits:
        score += 2.8 * len(concept_hits)
        matches.extend(sorted(concept_hits))
    if include_hits:
        score += 1.4 * len(include_hits)
        matches.extend(sorted(include_hits))
    if category_hit:
        score += 1.2
        matches.append(f"category:{project_category}")

    matches = dedup(matches)
    return score, matches


def assign_metaconstellations(
    record: SummaryRecord,
    aligned_concepts: list[str],
    aligned_tags: list[str],
    aligned_mined: list[str],
    metaconstellations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    project_terms = {x.casefold() for x in dedup([*aligned_concepts, *aligned_tags, *aligned_mined])}
    if not project_terms:
        return []

    scored: list[dict[str, Any]] = []
    for meta in metaconstellations:
        score, matches = score_project_for_meta(project_terms, record.category, meta)
        if score < 2.0:
            continue
        scored.append(
            {
                "id": meta["id"],
                "name": meta["name"],
                "score": round(score, 3),
                "matches": matches[:8],
            }
        )

    scored.sort(key=lambda x: (-float(x["score"]), x["name"].casefold()))
    return scored[:6]


def write_metaconstellation_artifact(
    shadow_root: Path,
    metaconstellations: list[dict[str, Any]],
    alignments: list[dict[str, Any]],
    project_meta_index: dict[str, list[dict[str, Any]]],
    dry_run: bool,
) -> Path:
    by_meta: dict[str, dict[str, Any]] = {meta["id"]: dict(meta) for meta in metaconstellations}
    for meta in by_meta.values():
        meta["project_paths"] = []
        meta["project_count"] = 0

    for project_path, metas in project_meta_index.items():
        for meta in metas:
            meta_id = as_string(meta.get("id"))
            if meta_id in by_meta:
                by_meta[meta_id]["project_paths"].append(project_path)

    for meta in by_meta.values():
        meta["project_paths"] = sorted(set(meta["project_paths"]))
        meta["project_count"] = len(meta["project_paths"])

    payload = {
        "generated_at": now_rfc3339(),
        "shadow_root": str(shadow_root),
        "tool": "resummarize-align-concepts",
        "alignments": alignments,
        "metaconstellations": sorted(by_meta.values(), key=lambda x: x["name"].casefold()),
    }

    target = shadow_root / "metaconstellations.json"
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=False)
            handle.write("\n")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Align concept/motif vocabulary with Gemini and generate metaconstellations.",
    )
    parser.add_argument("--shadow-root", default=None, help="Summary root (default: autodetect).")
    parser.add_argument("--gemini-bin", default="gemini", help="Gemini CLI binary.")
    parser.add_argument(
        "--gemini-arg",
        action="append",
        default=[],
        help="Extra Gemini arg (repeatable).",
    )
    parser.add_argument("--max-terms", type=int, default=380)
    parser.add_argument("--max-project-cards", type=int, default=220)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--report-file", default=None)
    args = parser.parse_args()

    shadow_root = resolve_shadow_root(args.shadow_root)
    records = load_records(shadow_root, args.limit)
    if not records:
        print(f"No summary.json files found under {shadow_root}", file=sys.stderr)
        return 1

    observed_terms = {term for record in records for term in record.terms}
    if not observed_terms:
        print("No concepts/tags/motifs found to align.", file=sys.stderr)
        return 1

    print(f"Shadow root: {shadow_root}")
    print(f"Projects loaded: {len(records)}")
    print(f"Unique terms: {len(observed_terms)}")
    if args.dry_run:
        print("Mode: dry-run")

    prompt = build_prompt(records, max(50, args.max_terms), max(50, args.max_project_cards))
    model_output, model_err = run_gemini(args.gemini_bin, args.gemini_arg, prompt, args.verbose)
    if model_output is None:
        print(f"Gemini failed: {model_err}", file=sys.stderr)
        return 2

    alignments, metaconstellations = sanitize_alignment_output(model_output, observed_terms)
    alias_map = build_alias_map(alignments)

    changed_count = 0
    assigned_count = 0
    project_meta_index: dict[str, list[dict[str, Any]]] = {}
    project_events: list[dict[str, Any]] = []

    for index, record in enumerate(records, start=1):
        before = dict(record.raw)
        raw = dict(record.raw)

        aligned_concepts = normalize_terms(record.concepts, alias_map)
        aligned_tags = normalize_terms(record.tags, alias_map)
        aligned_mined = normalize_terms(record.mined_motifs, alias_map)

        raw["concepts"] = aligned_concepts
        raw["tags"] = aligned_tags
        raw["mined_motifs"] = aligned_mined

        assigned = assign_metaconstellations(
            record,
            aligned_concepts,
            aligned_tags,
            aligned_mined,
            metaconstellations,
        )
        raw["metaconstellations"] = assigned
        if assigned:
            assigned_count += 1
        project_meta_index[record.project_path] = assigned

        raw["_updated_at"] = now_rfc3339()
        raw["_alignment"] = {
            "aligned_at": now_rfc3339(),
            "aligner": "gemini",
            "alignment_count": len(alignments),
            "metaconstellation_count": len(metaconstellations),
        }

        changed = canonical_json(before) != canonical_json(raw)
        if changed:
            changed_count += 1
            if not args.dry_run:
                write_summary(record.summary_file, raw)

        project_events.append(
            {
                "path": record.project_path,
                "summary_file": str(record.summary_file),
                "changed": changed,
                "metaconstellations": [item.get("name") for item in assigned],
            }
        )
        print(
            f"[{index}/{len(records)}] {'updated' if changed else 'unchanged':<9} "
            f"{record.project_path}"
        )

    artifact_path = write_metaconstellation_artifact(
        shadow_root=shadow_root,
        metaconstellations=metaconstellations,
        alignments=alignments,
        project_meta_index=project_meta_index,
        dry_run=args.dry_run,
    )

    report = {
        "generated_at": now_rfc3339(),
        "shadow_root": str(shadow_root),
        "dry_run": bool(args.dry_run),
        "gemini_bin": args.gemini_bin,
        "stats": {
            "projects": len(records),
            "unique_terms": len(observed_terms),
            "alignments": len(alignments),
            "metaconstellations": len(metaconstellations),
            "projects_updated": changed_count,
            "projects_with_metaconstellations": assigned_count,
        },
        "artifact_file": str(artifact_path),
        "project_events": project_events,
    }

    report_file = (
        Path(args.report_file).expanduser()
        if args.report_file
        else shadow_root / "align-concepts-report.json"
    )
    if not args.dry_run:
        report_file.parent.mkdir(parents=True, exist_ok=True)
        with report_file.open("w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=False)
            handle.write("\n")

    print("Done.")
    print(
        "Summary: "
        f"projects={len(records)}, terms={len(observed_terms)}, "
        f"alignments={len(alignments)}, metaconstellations={len(metaconstellations)}, "
        f"updated={changed_count}"
    )
    print(f"Artifact: {artifact_path}")
    print(f"Report: {report_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
