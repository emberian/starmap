#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# Optional deps for live UMAP recomputation: numpy, umap-learn, scikit-learn
# Not needed when pre-baked umap_coords.json is present in the data directory.
import hashlib
import http.server
import json
import os
import socketserver
import sys
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

PORT = int(os.environ.get("STARMAP_PORT", "8317"))
HOME = Path.home()
PROJECT_ROOT = Path(__file__).resolve().parent
DIST_DIR = PROJECT_ROOT / "dist"
WEB_ROOT = DIST_DIR if DIST_DIR.is_dir() else PROJECT_ROOT


def resolve_shadow_root() -> Path:
    env_value = os.environ.get("STARMAP_SHADOW_ROOT", "").strip()
    if env_value:
        return Path(os.path.expanduser(env_value))

    candidates = [
        PROJECT_ROOT / "data" / "summarization",
        Path(os.path.expanduser("~/.summarize")),
        Path(os.path.expanduser("~/.summarization")),
    ]

    best_path = None
    best_count = -1
    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            count = sum(1 for _ in candidate.rglob("summary.json"))
        except Exception:
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


SHADOW_ROOT = resolve_shadow_root()

# Add project root to sys.path so we can import starmap_lib.
sys.path.insert(0, str(WEB_ROOT))

# ---------------------------------------------------------------------------
# UMAP 2D layout computation with caching
# ---------------------------------------------------------------------------

_umap_lock = threading.Lock()
_umap_cache: dict = {"key": "", "coords": {}}
_UMAP_CACHE_FILE = SHADOW_ROOT / ".starmap_umap_cache.json"


def _embedding_cache_key(embeddings: dict[str, list[float]]) -> str:
    import numpy as np

    parts: list[str] = []
    for path in sorted(embeddings.keys()):
        vec_bytes = np.array(embeddings[path], dtype=np.float32).tobytes()
        vec_hash = hashlib.sha256(vec_bytes).hexdigest()[:16]
        parts.append(f"{path}:{vec_hash}")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def _load_disk_cache() -> dict | None:
    if not _UMAP_CACHE_FILE.exists():
        return None
    try:
        with _UMAP_CACHE_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_disk_cache(key: str, coords: dict[str, list[float]]) -> None:
    try:
        _UMAP_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _UMAP_CACHE_FILE.open("w", encoding="utf-8") as f:
            json.dump({"key": key, "coords": coords}, f)
    except Exception:
        pass


def _run_reduction(matrix):
    """Try UMAP, fall back to TSNE, then PCA."""
    import numpy as np

    # Try UMAP
    try:
        import umap as umap_lib

        reducer = umap_lib.UMAP(
            n_components=2,
            n_neighbors=min(15, max(2, len(matrix) - 1)),
            min_dist=0.1,
            metric="cosine",
            random_state=42,
        )
        print("[umap] Computing 2D layout via UMAP...", file=sys.stderr)
        return reducer.fit_transform(matrix)
    except Exception as exc:
        print(f"[umap] UMAP failed ({exc}), trying TSNE...", file=sys.stderr)

    # Try TSNE
    try:
        from sklearn.manifold import TSNE

        perplexity = min(30, max(2, len(matrix) - 1))
        reducer = TSNE(
            n_components=2,
            perplexity=perplexity,
            metric="cosine",
            random_state=42,
            init="random",
        )
        print("[umap] Computing 2D layout via TSNE...", file=sys.stderr)
        return reducer.fit_transform(matrix)
    except Exception as exc:
        print(f"[umap] TSNE failed ({exc}), trying PCA...", file=sys.stderr)

    # PCA fallback
    from sklearn.decomposition import PCA

    reducer = PCA(n_components=2, random_state=42)
    print("[umap] Computing 2D layout via PCA...", file=sys.stderr)
    return reducer.fit_transform(matrix)


def _load_prebaked_coords() -> dict[str, list[float]] | None:
    """Load pre-exported UMAP coordinates from data/summarization/umap_coords.json."""
    coords_file = SHADOW_ROOT / "umap_coords.json"
    if not coords_file.exists():
        return None
    try:
        with coords_file.open("r", encoding="utf-8") as f:
            coords = json.load(f)
        if isinstance(coords, dict) and len(coords) > 0:
            return coords
    except Exception:
        pass
    return None


def compute_umap_coords() -> dict[str, list[float]]:
    """Return {project_path: [x, y]} with 2D coordinates normalized to [0, 1]."""
    global _umap_cache

    from starmap_lib import load_embeddings

    embeddings = load_embeddings(SHADOW_ROOT)
    if len(embeddings) < 3:
        # No embedding vectors available — use pre-baked coords if present
        prebaked = _load_prebaked_coords()
        if prebaked:
            print(f"[umap] Using pre-baked coordinates for {len(prebaked)} projects", file=sys.stderr)
            return prebaked
        return {}

    import numpy as np

    key = _embedding_cache_key(embeddings)

    with _umap_lock:
        # In-memory cache
        if _umap_cache["key"] == key:
            return _umap_cache["coords"]

        # Disk cache
        disk = _load_disk_cache()
        if disk and disk.get("key") == key:
            _umap_cache = {"key": key, "coords": disk["coords"]}
            return disk["coords"]

        # Compute
        paths = sorted(embeddings.keys())
        matrix = np.array([embeddings[p] for p in paths], dtype=np.float32)

        raw_coords = _run_reduction(matrix)

        # Normalize to [0, 1]
        mins = raw_coords.min(axis=0)
        maxs = raw_coords.max(axis=0)
        ranges = maxs - mins
        ranges[ranges < 1e-12] = 1.0
        normalized = (raw_coords - mins) / ranges

        coords = {
            path: [round(float(normalized[i, 0]), 6), round(float(normalized[i, 1]), 6)]
            for i, path in enumerate(paths)
        }

        _umap_cache = {"key": key, "coords": coords}
        _save_disk_cache(key, coords)
        print(
            f"[umap] Computed 2D layout for {len(paths)} projects",
            file=sys.stderr,
        )
        return coords


def normalize_string_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            out.append(text)
    return out


def normalize_motif_neighbors(value) -> list[dict]:
    if not isinstance(value, list):
        return []

    neighbors: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        path = str(item.get("path", "")).strip()
        similarity = item.get("similarity")
        if not isinstance(similarity, (int, float)):
            similarity = None
        if not name and not path:
            continue
        neighbors.append(
            {
                "name": name or "Unnamed",
                "path": path,
                "similarity": similarity,
            }
        )
    return neighbors[:8]


def normalize_embedding(value) -> dict | None:
    if not isinstance(value, dict):
        return None

    model = str(value.get("model", "")).strip()
    backend_url = str(value.get("backend_url", "")).strip()
    instruction = str(value.get("instruction", "")).strip()
    updated_at = str(value.get("updated_at", "")).strip()
    dim = value.get("dim")
    if not isinstance(dim, int):
        dim = None

    if not model and not backend_url and dim is None:
        return None

    return {
        "model": model,
        "backend_url": backend_url,
        "instruction": instruction,
        "updated_at": updated_at,
        "dim": dim,
    }


def normalize_project_metaconstellations(value) -> list[dict]:
    if not isinstance(value, list):
        return []

    out: list[dict] = []
    for item in value:
        if isinstance(item, str):
            name = item.strip()
            if not name:
                continue
            out.append({"id": "", "name": name, "score": None, "matches": []})
            continue

        if not isinstance(item, dict):
            continue

        meta_id = str(item.get("id", "")).strip()
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        score = item.get("score")
        if not isinstance(score, (int, float)):
            score = None
        out.append(
            {
                "id": meta_id,
                "name": name,
                "score": score,
                "matches": normalize_string_list(item.get("matches"))[:10],
            }
        )

    return out[:12]


def normalize_metaconstellation(value) -> dict | None:
    if not isinstance(value, dict):
        return None

    name = str(value.get("name", "")).strip()
    if not name:
        return None

    meta_id = str(value.get("id", "")).strip()
    description = str(value.get("description", "")).strip()
    project_count = value.get("project_count")
    if not isinstance(project_count, int):
        project_count = 0

    return {
        "id": meta_id,
        "name": name,
        "description": description,
        "concepts": normalize_string_list(value.get("concepts"))[:50],
        "categories": normalize_string_list(value.get("categories"))[:30],
        "include_terms": normalize_string_list(value.get("include_terms"))[:50],
        "exclude_terms": normalize_string_list(value.get("exclude_terms"))[:30],
        "project_paths": normalize_string_list(value.get("project_paths"))[:500],
        "project_count": project_count,
    }


def load_metaconstellations() -> dict:
    default = {
        "generated_at": "",
        "tool": "",
        "alignments": [],
        "metaconstellations": [],
    }
    path = SHADOW_ROOT / "metaconstellations.json"
    if not path.exists():
        return default

    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return default

    if not isinstance(data, dict):
        return default

    metas: list[dict] = []
    for item in data.get("metaconstellations", []):
        normalized = normalize_metaconstellation(item)
        if normalized:
            metas.append(normalized)

    alignments: list[dict] = []
    for item in data.get("alignments", []):
        if not isinstance(item, dict):
            continue
        canonical = str(item.get("canonical", "")).strip()
        if not canonical:
            continue
        alignments.append(
            {
                "canonical": canonical,
                "aliases": normalize_string_list(item.get("aliases"))[:30],
                "confidence": item.get("confidence"),
            }
        )

    return {
        "generated_at": str(data.get("generated_at", "")).strip(),
        "tool": str(data.get("tool", "")).strip(),
        "alignments": alignments[:1000],
        "metaconstellations": sorted(metas, key=lambda x: x.get("name", "")),
    }


def normalize_project(
    summary: dict,
    umap_coords: dict[str, list[float]] | None = None,
) -> dict:
    project = dict(summary)
    path_value = str(project.get("_path", ""))

    try:
        display_path = "~" + str(Path(path_value).resolve().relative_to(HOME))
    except Exception:
        display_path = path_value or "~"

    project["display_path"] = display_path
    project["name"] = project.get("name") or Path(path_value).name or "Unnamed"
    project["description"] = project.get("description") or "No summary available yet."
    project["languages"] = normalize_string_list(project.get("languages"))
    project["attribution"] = str(project.get("attribution", "interests")).strip()
    project["motif_neighbors"] = normalize_motif_neighbors(project.get("_motif_neighbors"))

    # Raw ground-truth fields (from initial LLM scan).
    raw_category = project.get("category") or "Exploratory"
    raw_concepts = normalize_string_list(project.get("concepts"))
    raw_tags = normalize_string_list(project.get("tags"))
    raw_mined = normalize_string_list(project.get("mined_motifs"))

    # Prefer aligned overlay when available, fall back to raw.
    alignment = project.get("_alignment") or {}
    aligned_at = str(alignment.get("aligned_at", "")).strip()
    updated_at = str(project.get("_updated_at", "")).strip()

    # Staleness: project was re-summarized after alignment was computed.
    alignment_stale = bool(aligned_at and updated_at and updated_at > aligned_at)

    if alignment_stale:
        # Alignment is outdated — use raw fields until next alignment pass.
        project["category"] = raw_category
        project["concepts"] = raw_concepts
        project["tags"] = raw_tags
        project["mined_motifs"] = raw_mined
        project["metaconstellations"] = normalize_project_metaconstellations(
            project.get("metaconstellations")
        )
    else:
        project["category"] = alignment.get("category") or raw_category
        project["concepts"] = normalize_string_list(alignment.get("concepts")) or raw_concepts
        project["tags"] = normalize_string_list(alignment.get("tags")) or raw_tags
        project["mined_motifs"] = normalize_string_list(alignment.get("mined_motifs")) or raw_mined
        project["metaconstellations"] = normalize_project_metaconstellations(
            alignment.get("metaconstellations") or project.get("metaconstellations")
        )

    project["alignment_stale"] = alignment_stale

    # Pass raw values through so the frontend can show original terminology.
    project["raw_category"] = raw_category
    project["raw_concepts"] = raw_concepts
    project["raw_tags"] = raw_tags
    project["raw_mined_motifs"] = raw_mined
    project["embedding"] = normalize_embedding(project.get("_embedding"))
    project["embedding_ready"] = bool(project.get("embedding"))

    # UMAP 2D coordinates
    if umap_coords and path_value in umap_coords:
        xy = umap_coords[path_value]
        project["umap_x"] = xy[0]
        project["umap_y"] = xy[1]
    else:
        project["umap_x"] = None
        project["umap_y"] = None

    # Strip internal/heavy fields the frontend doesn't need.
    # Keep _path, _hash, _updated_at (used by frontend for keying and diff detection).
    for key in list(project.keys()):
        if key.startswith("_") and key not in ("_path", "_hash", "_updated_at"):
            del project[key]
    project.pop("evidence", None)

    return project


def load_projects(
    umap_coords: dict[str, list[float]] | None = None,
) -> list[dict]:
    if not SHADOW_ROOT.exists():
        return []

    projects: list[dict] = []
    for summary_path in SHADOW_ROOT.rglob("summary.json"):
        try:
            with summary_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                projects.append(normalize_project(data, umap_coords))
        except Exception:
            continue

    projects.sort(key=lambda p: (p.get("category", ""), p.get("name", "")))
    return projects


def top_items(counter: Counter[str], limit: int) -> list[dict]:
    return [{"name": key, "count": count} for key, count in counter.most_common(limit)]


def derive_interests(projects: list[dict]) -> dict:
    categories: Counter[str] = Counter()
    concepts: Counter[str] = Counter()
    tags: Counter[str] = Counter()
    mined_motifs: Counter[str] = Counter()
    metaconstellations: Counter[str] = Counter()
    languages: Counter[str] = Counter()
    embeddings_ready = 0

    for project in projects:
        if project.get("category"):
            categories[project["category"]] += 1
        for concept in project.get("concepts", []):
            concepts[str(concept)] += 1
        for tag in project.get("tags", []):
            tags[str(tag)] += 1
        for motif in project.get("mined_motifs", []):
            mined_motifs[str(motif)] += 1
        for meta in project.get("metaconstellations", []):
            if isinstance(meta, dict):
                name = str(meta.get("name", "")).strip()
                if name:
                    metaconstellations[name] += 1
        for language in project.get("languages", []):
            languages[str(language)] += 1
        if project.get("embedding_ready"):
            embeddings_ready += 1

    project_count = len(projects)
    embedding_coverage = 0.0 if project_count == 0 else round((embeddings_ready / project_count) * 100.0, 2)

    return {
        "top_categories": top_items(categories, 8),
        "top_concepts": top_items(concepts, 14),
        "top_tags": top_items(tags, 14),
        "top_mined_motifs": top_items(mined_motifs, 14),
        "top_metaconstellations": top_items(metaconstellations, 10),
        "top_languages": top_items(languages, 10),
        "embedding_coverage": {
            "ready": embeddings_ready,
            "total": project_count,
            "pct": embedding_coverage,
        },
    }


def build_payload() -> dict:
    umap_coords = compute_umap_coords()
    projects = load_projects(umap_coords)
    meta_payload = load_metaconstellations()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "shadow_root": str(SHADOW_ROOT),
        "project_count": len(projects),
        "projects": projects,
        "interests": derive_interests(projects),
        "metaconstellations_generated_at": meta_payload.get("generated_at", ""),
        "metaconstellations": meta_payload.get("metaconstellations", []),
        "metaconstellation_alignments": meta_payload.get("alignments", []),
    }


class StarmapHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def _send_json(self, payload: dict | list, *, cache: bool = False) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if not cache:
            self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        route = parsed.path

        if route == "/api/data":
            return self._send_json(build_payload())

        if route == "/api/metaconstellations":
            return self._send_json(load_metaconstellations())

        if route == "/api/health":
            return self._send_json({"ok": True, "shadow_exists": SHADOW_ROOT.exists()})

        # SPA fallback: if serving from dist/ and the path isn't a real file,
        # serve index.html so client-side routing works.
        if DIST_DIR.is_dir():
            file_path = DIST_DIR / route.lstrip("/")
            if not file_path.is_file() and not route.startswith("/api/"):
                self.path = "/index.html"

        return super().do_GET()


class ThreadingTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True


def main() -> None:
    print(f"Shadow root: {SHADOW_ROOT}")
    print(f"Web root: {WEB_ROOT}")
    print(f"Serving Starmap at http://localhost:{PORT}")
    # Pre-warm UMAP cache on startup
    coords = compute_umap_coords()
    print(f"UMAP layout: {len(coords)} projects positioned")
    with ThreadingTCPServer(("", PORT), StarmapHandler) as httpd:
        httpd.serve_forever()


if __name__ == "__main__":
    main()
