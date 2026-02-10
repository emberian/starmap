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
SHADOW_ROOT = Path(os.path.expanduser("~/.summarization"))
HOME = Path.home()
WEB_ROOT = Path(__file__).resolve().parent


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
        "top_languages": top_items(languages, 10),
        "embedding_coverage": {
            "ready": embeddings_ready,
            "total": project_count,
            "pct": embedding_coverage,
        },
    }


def build_payload() -> dict:
    projects = load_projects()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "shadow_root": str(SHADOW_ROOT),
        "project_count": len(projects),
        "projects": projects,
        "interests": derive_interests(projects),
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
