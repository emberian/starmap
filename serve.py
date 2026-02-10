#!/usr/bin/env python3
import http.server
import json
import os
import socketserver
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

PORT = int(os.environ.get("STARMAP_PORT", "8317"))
HOME = Path.home()
WEB_ROOT = Path(__file__).resolve().parent


def resolve_shadow_root() -> Path:
    env_value = os.environ.get("STARMAP_SHADOW_ROOT", "").strip()
    if env_value:
        return Path(os.path.expanduser(env_value))

    candidates = [
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


def normalize_project(summary: dict) -> dict:
    project = dict(summary)
    path_value = str(project.get("_path", ""))

    try:
        display_path = "~" + str(Path(path_value).resolve().relative_to(HOME))
    except Exception:
        display_path = path_value or "~"

    project["display_path"] = display_path
    project["name"] = project.get("name") or Path(path_value).name or "Unnamed"
    project["description"] = project.get("description") or "No summary available yet."
    project["category"] = project.get("category") or "Exploratory"
    project["tags"] = normalize_string_list(project.get("tags"))
    project["concepts"] = normalize_string_list(project.get("concepts"))
    project["languages"] = normalize_string_list(project.get("languages"))
    project["mined_motifs"] = normalize_string_list(project.get("mined_motifs"))
    project["motif_neighbors"] = normalize_motif_neighbors(project.get("_motif_neighbors"))
    project["metaconstellations"] = normalize_project_metaconstellations(
        project.get("metaconstellations")
    )
    project["embedding"] = normalize_embedding(project.get("_embedding"))
    project["embedding_ready"] = bool(project.get("embedding"))
    return project


def load_projects() -> list[dict]:
    if not SHADOW_ROOT.exists():
        return []

    projects: list[dict] = []
    for summary_path in SHADOW_ROOT.rglob("summary.json"):
        try:
            with summary_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                projects.append(normalize_project(data))
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
    projects = load_projects()
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

    def do_GET(self):
        parsed = urlparse(self.path)
        route = parsed.path

        if route == "/api/data":
            payload = build_payload()
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if route == "/api/metaconstellations":
            payload = load_metaconstellations()
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if route == "/api/health":
            body = json.dumps({"ok": True, "shadow_exists": SHADOW_ROOT.exists()}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        return super().do_GET()


class ThreadingTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True


def main() -> None:
    print(f"Serving Starmap at http://localhost:{PORT}")
    with ThreadingTCPServer(("", PORT), StarmapHandler) as httpd:
        httpd.serve_forever()


if __name__ == "__main__":
    main()
