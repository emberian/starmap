#!/usr/bin/env python3
"""Align concept/motif terminology with Gemini and build metaconstellations.

This is a standalone post-processing tool, separate from the main `resummarize`
binary. It updates `summary.json` records and emits `metaconstellations.json`.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from starmap_lib import (
    GeminiClient,
    as_string,
    as_string_list,
    cosine_similarity,
    dedup,
    load_embeddings,
    now_rfc3339,
    read_summary,
    resolve_shadow_root,
    slugify,
    write_summary,
)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


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


@dataclass
class AliasEntry:
    canonical: str
    confidence: float


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_records(
    shadow_root: Path,
    limit: int,
    since: str | None = None,
) -> list[SummaryRecord]:
    files = sorted(shadow_root.rglob("summary.json"))
    if limit > 0:
        files = files[:limit]

    out: list[SummaryRecord] = []
    for file in files:
        raw = read_summary(file)
        if raw is None:
            continue
        # Incremental: only include projects updated after `since`.
        if since:
            updated_at = as_string(raw.get("_updated_at"))
            if updated_at and updated_at <= since:
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


# ---------------------------------------------------------------------------
# Embedding-based term clustering
# ---------------------------------------------------------------------------


def compute_term_embeddings(
    records: list[SummaryRecord],
    project_embeddings: dict[str, list[float]],
) -> dict[str, list[float]]:
    """Compute a pseudo-embedding for each term by averaging project embeddings."""
    term_vectors: dict[str, list[list[float]]] = defaultdict(list)

    for record in records:
        vec = project_embeddings.get(record.project_path)
        if vec is None:
            continue
        for term in record.terms:
            term_vectors[term.casefold()].append(vec)

    term_embeddings: dict[str, list[float]] = {}
    for term_key, vecs in term_vectors.items():
        if not vecs:
            continue
        dim = len(vecs[0])
        avg = [sum(v[d] for v in vecs) / len(vecs) for d in range(dim)]
        term_embeddings[term_key] = avg

    return term_embeddings


def cluster_terms_by_embedding(
    term_embeddings: dict[str, list[float]],
    threshold: float = 0.82,
) -> list[list[str]]:
    """Greedy clustering: pick seed, absorb similar terms, repeat."""
    remaining = set(term_embeddings.keys())
    clusters: list[list[str]] = []

    while remaining:
        seed = remaining.pop()
        cluster = [seed]
        seed_vec = term_embeddings[seed]
        to_remove = []
        for other in remaining:
            sim = cosine_similarity(seed_vec, term_embeddings[other])
            if sim is not None and sim >= threshold:
                cluster.append(other)
                to_remove.append(other)
        for item in to_remove:
            remaining.discard(item)
        if len(cluster) > 1:
            clusters.append(sorted(cluster))

    return clusters


# ---------------------------------------------------------------------------
# Prompt building (chunked)
# ---------------------------------------------------------------------------


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
        out.append({"term": term, "count": count, "categories": top_categories})
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


def build_chunk_prompt(
    records: list[SummaryRecord],
    embedding_clusters: list[list[str]] | None,
    max_terms: int,
    max_projects: int,
    is_reconciliation: bool = False,
) -> str:
    category_counter: Counter[str] = Counter(record.category for record in records)
    observed_categories = [name for name, _ in category_counter.most_common(200)]
    category_summary = [
        {"name": name, "count": count} for name, count in category_counter.most_common(50)
    ]

    instructions: dict[str, Any] = {
        "task": "Align near-duplicate concepts/tags/motifs AND categories, and propose metaconstellations.",
        "output_schema": {
            "alignments": [
                {
                    "canonical": "string canonical term",
                    "aliases": ["string aliases that should map to canonical"],
                    "confidence": "number 0-1",
                    "rationale": "short string",
                }
            ],
            "category_alignments": [
                {
                    "canonical": "string canonical category",
                    "aliases": ["string aliases/near-duplicates that should map to canonical"],
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
            "Align overlapping categories (e.g. 'Zero Knowledge' -> 'Zero-Knowledge Proofs').",
            "Use the embedding_clusters hint to validate which terms are likely synonyms.",
        ],
    }

    payload: dict[str, Any] = {
        "summary_count": len(records),
        "observed_categories": observed_categories,
        "category_distribution": category_summary,
        "top_terms": top_terms(records, max_terms),
        "project_cards": project_cards(records, max_projects),
    }

    if embedding_clusters:
        payload["embedding_clusters"] = [
            {"terms": cluster} for cluster in embedding_clusters[:60]
        ]

    return (
        f"{json.dumps(instructions, ensure_ascii=False)}\n\n"
        f"INPUT:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


# ---------------------------------------------------------------------------
# Sanitization
# ---------------------------------------------------------------------------


def sanitize_alignment_output(
    raw: dict[str, Any],
    observed_terms: set[str],
    observed_categories: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    observed_lower = {term.casefold(): term for term in observed_terms}
    cat_lower = {c.casefold(): c for c in observed_categories}

    alignments_raw = raw.get("alignments")
    cat_alignments_raw = raw.get("category_alignments")
    metaraw = raw.get("metaconstellations")

    category_map: dict[str, str] = {}
    for item in cat_alignments_raw if isinstance(cat_alignments_raw, list) else []:
        canonical = as_string(item.get("canonical"))
        if not canonical:
            continue
        for alias in as_string_list(item.get("aliases")):
            if alias.casefold() in cat_lower:
                category_map[alias.casefold()] = canonical

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
    return clean_alignments, clean_metas, category_map


def merge_chunk_results(
    chunk_results: list[tuple[list[dict], list[dict], dict[str, str]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    """Merge alignment results from multiple chunks."""
    all_alignments: dict[str, dict[str, Any]] = {}
    all_metas: dict[str, dict[str, Any]] = {}
    all_category_map: dict[str, str] = {}

    for alignments, metas, cat_map in chunk_results:
        # Merge alignments by canonical — higher confidence wins.
        for a in alignments:
            canonical = a["canonical"]
            existing = all_alignments.get(canonical)
            if not existing:
                all_alignments[canonical] = dict(a)
            else:
                existing["aliases"] = dedup(existing["aliases"] + a["aliases"])
                existing["confidence"] = max(
                    existing.get("confidence", 0.5),
                    a.get("confidence", 0.5),
                )

        # Merge metaconstellations by id.
        for m in metas:
            meta_id = m["id"]
            existing = all_metas.get(meta_id)
            if not existing:
                all_metas[meta_id] = dict(m)
            else:
                existing["concepts"] = dedup(existing["concepts"] + m.get("concepts", []))[:40]
                existing["categories"] = dedup(existing["categories"] + m.get("categories", []))[:20]
                existing["include_terms"] = dedup(existing["include_terms"] + m.get("include_terms", []))[:40]
                existing["exclude_terms"] = dedup(existing["exclude_terms"] + m.get("exclude_terms", []))[:30]

        all_category_map.update(cat_map)

    merged_alignments = sorted(all_alignments.values(), key=lambda x: x["canonical"].casefold())
    merged_metas = sorted(all_metas.values(), key=lambda x: x["name"].casefold())
    return merged_alignments, merged_metas, all_category_map


# ---------------------------------------------------------------------------
# Alias map + normalization
# ---------------------------------------------------------------------------


def build_alias_map(alignments: list[dict[str, Any]]) -> dict[str, AliasEntry]:
    alias_map: dict[str, AliasEntry] = {}
    for item in alignments:
        canonical = as_string(item.get("canonical"))
        if not canonical:
            continue
        confidence = float(item.get("confidence", 0.5))
        for alias in as_string_list(item.get("aliases")):
            alias_map[alias.casefold()] = AliasEntry(canonical=canonical, confidence=confidence)
    return alias_map


def normalize_terms(
    terms: list[str],
    alias_map: dict[str, AliasEntry],
    min_confidence: float = 0.6,
) -> list[str]:
    mapped = []
    for term in terms:
        entry = alias_map.get(term.casefold())
        if entry and entry.confidence >= min_confidence:
            mapped.append(entry.canonical)
        else:
            mapped.append(term)
    return dedup(mapped)


# ---------------------------------------------------------------------------
# Metaconstellation scoring (hybrid: lexical + embedding)
# ---------------------------------------------------------------------------


def lexical_score_for_meta(
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


def compute_meta_centroids(
    metaconstellations: list[dict[str, Any]],
    records: list[SummaryRecord],
    alias_map: dict[str, AliasEntry],
    project_embeddings: dict[str, list[float]],
) -> dict[str, list[float]]:
    """Compute centroids for each metaconstellation via first-pass lexical assignment."""
    meta_projects: dict[str, list[str]] = {m["id"]: [] for m in metaconstellations}

    for record in records:
        aligned_terms = normalize_terms(record.concepts + record.tags + record.mined_motifs, alias_map)
        project_terms = {x.casefold() for x in aligned_terms}
        for meta in metaconstellations:
            lex_score, _ = lexical_score_for_meta(project_terms, record.category, meta)
            if lex_score >= 2.0:
                meta_projects[meta["id"]].append(record.project_path)

    centroids: dict[str, list[float]] = {}
    for meta_id, paths in meta_projects.items():
        vecs = [project_embeddings[p] for p in paths if p in project_embeddings]
        if not vecs:
            continue
        dim = len(vecs[0])
        avg = [sum(v[d] for v in vecs) / len(vecs) for d in range(dim)]
        centroids[meta_id] = avg

    return centroids


def score_project_for_meta(
    project_path: str,
    project_terms: set[str],
    project_category: str,
    meta: dict[str, Any],
    project_embeddings: dict[str, list[float]],
    meta_centroids: dict[str, list[float]],
) -> tuple[float, list[str]]:
    lex_score, matches = lexical_score_for_meta(project_terms, project_category, meta)

    # Embedding-based boost.
    embed_score = 0.0
    meta_id = meta.get("id", "")
    centroid = meta_centroids.get(meta_id)
    proj_vec = project_embeddings.get(project_path)
    if centroid is not None and proj_vec is not None:
        sim = cosine_similarity(proj_vec, centroid)
        if sim is not None and sim > 0.5:
            embed_score = sim * 3.0

    return lex_score + embed_score, matches


def assign_metaconstellations(
    record: SummaryRecord,
    aligned_concepts: list[str],
    aligned_tags: list[str],
    aligned_mined: list[str],
    metaconstellations: list[dict[str, Any]],
    project_embeddings: dict[str, list[float]],
    meta_centroids: dict[str, list[float]],
) -> list[dict[str, Any]]:
    project_terms = {x.casefold() for x in dedup([*aligned_concepts, *aligned_tags, *aligned_mined])}
    if not project_terms:
        return []

    scored: list[dict[str, Any]] = []
    for meta in metaconstellations:
        score, matches = score_project_for_meta(
            record.project_path,
            project_terms,
            record.category,
            meta,
            project_embeddings,
            meta_centroids,
        )
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


# ---------------------------------------------------------------------------
# Artifact writing
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Align concept/motif vocabulary with Gemini and generate metaconstellations.",
    )
    parser.add_argument("--shadow-root", default=None, help="Summary root (default: autodetect).")
    parser.add_argument("--gemini-bin", default="gemini", help="Gemini CLI binary.")
    parser.add_argument("--gemini-arg", action="append", default=[], help="Extra Gemini arg (repeatable).")
    parser.add_argument("--max-terms", type=int, default=380)
    parser.add_argument("--max-project-cards", type=int, default=220)
    parser.add_argument("--chunk-size", type=int, default=80, help="Projects per Gemini chunk.")
    parser.add_argument("--min-confidence", type=float, default=0.6, help="Min alignment confidence.")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--since", default=None, help="ISO timestamp for incremental alignment.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--report-file", default=None)
    args = parser.parse_args()

    shadow_root = resolve_shadow_root(args.shadow_root)
    records = load_records(shadow_root, args.limit, since=args.since)
    if not records:
        print(f"No summary.json files found under {shadow_root}", file=sys.stderr)
        return 1

    observed_terms = {term for record in records for term in record.terms}
    observed_categories = {record.category for record in records}
    if not observed_terms:
        print("No concepts/tags/motifs found to align.", file=sys.stderr)
        return 1

    print(f"Shadow root: {shadow_root}")
    print(f"Projects loaded: {len(records)}")
    print(f"Unique terms: {len(observed_terms)}")
    print(f"Unique categories: {len(observed_categories)}")
    print(f"Gemini: {args.gemini_bin}")
    if args.since:
        print(f"Incremental since: {args.since}")
    if args.dry_run:
        print("Mode: dry-run")

    # Load project embeddings.
    project_embeddings = load_embeddings(shadow_root)
    embed_count = sum(1 for r in records if r.project_path in project_embeddings)
    print(f"Embeddings available: {embed_count}/{len(records)}")

    # Pre-cluster terms using embedding similarity.
    embedding_clusters: list[list[str]] | None = None
    if project_embeddings:
        term_embeddings = compute_term_embeddings(records, project_embeddings)
        if term_embeddings:
            embedding_clusters = cluster_terms_by_embedding(term_embeddings)
            print(f"Embedding term clusters: {len(embedding_clusters)}")

    # Chunk records for Gemini calls.
    gemini = GeminiClient(gemini_bin=args.gemini_bin, gemini_args=args.gemini_arg, verbose=args.verbose)
    chunk_size = max(20, args.chunk_size)
    chunks = [records[i : i + chunk_size] for i in range(0, len(records), chunk_size)]
    print(f"Gemini chunks: {len(chunks)} (size {chunk_size})")

    chunk_results: list[tuple[list[dict], list[dict], dict[str, str]]] = []

    if len(chunks) == 1:
        # Single chunk — simple path.
        prompt = build_chunk_prompt(
            chunks[0], embedding_clusters, max(50, args.max_terms), max(50, args.max_project_cards)
        )
        model_output, model_err = gemini.generate_json(prompt)
        if model_output is None:
            print(f"Gemini failed: {model_err}", file=sys.stderr)
            return 2
        result = sanitize_alignment_output(model_output, observed_terms, observed_categories)
        chunk_results.append(result)
    else:
        # Multi-chunk: build prompts and run concurrently.
        prompts = []
        for chunk in chunks:
            prompt = build_chunk_prompt(
                chunk, embedding_clusters, max(50, args.max_terms // len(chunks)), max(50, args.max_project_cards // len(chunks))
            )
            prompts.append(prompt)

        print(f"Running {len(prompts)} Gemini calls concurrently...")
        batch_results = gemini.generate_json_batch(prompts, concurrency=3)

        for i, (model_output, model_err) in enumerate(batch_results):
            if model_output is None:
                print(f"Gemini chunk {i+1} failed: {model_err}", file=sys.stderr)
                continue
            result = sanitize_alignment_output(model_output, observed_terms, observed_categories)
            chunk_results.append(result)

        if not chunk_results:
            print("All Gemini chunks failed.", file=sys.stderr)
            return 2

    # Merge chunk results.
    alignments, metaconstellations, category_map = merge_chunk_results(chunk_results)
    alias_map = build_alias_map(alignments)

    # Compute metaconstellation centroids for hybrid scoring.
    meta_centroids = compute_meta_centroids(
        metaconstellations, records, alias_map, project_embeddings,
    )
    print(f"Metaconstellation centroids computed: {len(meta_centroids)}/{len(metaconstellations)}")

    changed_count = 0
    assigned_count = 0
    project_meta_index: dict[str, list[dict[str, Any]]] = {}
    project_events: list[dict[str, Any]] = []

    for index, record in enumerate(records, start=1):
        raw = dict(record.raw)

        aligned_concepts = normalize_terms(record.concepts, alias_map, args.min_confidence)
        aligned_tags = normalize_terms(record.tags, alias_map, args.min_confidence)
        aligned_mined = normalize_terms(record.mined_motifs, alias_map, args.min_confidence)
        aligned_category = category_map.get(record.category.casefold(), record.category)

        assigned = assign_metaconstellations(
            record,
            aligned_concepts,
            aligned_tags,
            aligned_mined,
            metaconstellations,
            project_embeddings,
            meta_centroids,
        )
        if assigned:
            assigned_count += 1
        project_meta_index[record.project_path] = assigned

        proposed_alignment = {
            "category": aligned_category,
            "concepts": aligned_concepts,
            "tags": aligned_tags,
            "mined_motifs": aligned_mined,
            "metaconstellations": assigned,
            "aligner": "gemini",
            "alignment_count": len(alignments),
            "metaconstellation_count": len(metaconstellations),
        }

        existing = raw.get("_alignment") or {}
        existing_comparable = {
            k: v for k, v in existing.items() if k != "aligned_at"
        }
        changed = existing_comparable != proposed_alignment
        if changed:
            proposed_alignment["aligned_at"] = now_rfc3339()
            raw["_alignment"] = proposed_alignment
            raw["_updated_at"] = now_rfc3339()
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
            "embedding_coverage": f"{embed_count}/{len(records)}",
            "embedding_clusters": len(embedding_clusters) if embedding_clusters else 0,
            "meta_centroids": len(meta_centroids),
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
        f"updated={changed_count}, embeddings={embed_count}/{len(records)}"
    )
    print(f"Artifact: {artifact_path}")
    print(f"Report: {report_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
