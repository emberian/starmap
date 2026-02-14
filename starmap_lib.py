"""Shared infrastructure for the starmap pipeline scripts.

Provides common I/O helpers, shadow-root resolution, the GeminiClient
(google-genai SDK), embedding loaders, and cosine similarity.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------


def now_rfc3339() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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


def dedup(values: list[str], *, key=str.casefold) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        k = key(value)
        if k in seen:
            continue
        seen.add(k)
        out.append(value)
    return out


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "meta"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Shadow root resolution
# ---------------------------------------------------------------------------


def resolve_shadow_root(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    env_value = os.environ.get("STARMAP_SHADOW_ROOT")
    if env_value:
        return Path(env_value).expanduser()
    candidates = [
        Path(__file__).resolve().parent / "data" / "summarization",
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


# ---------------------------------------------------------------------------
# Summary I/O
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# JSON extraction (fallback for non-SDK paths)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Gemini Client (shells out to gemini CLI)
# ---------------------------------------------------------------------------


class GeminiClient:
    """Wrapper that shells out to the gemini CLI binary for JSON generation."""

    def __init__(
        self,
        gemini_bin: str = "gemini",
        gemini_args: list[str] | None = None,
        verbose: bool = False,
        # model param accepted for interface compat but unused (CLI picks its own model)
        model: str = "",
    ):
        self.gemini_bin = gemini_bin
        self.gemini_args = gemini_args or []
        self.verbose = verbose

    def generate_json(self, prompt: str) -> tuple[dict[str, Any] | None, str | None]:
        """Single structured JSON response. Returns (parsed_dict, error_string)."""
        import subprocess

        cmd = [self.gemini_bin, *self.gemini_args, "-p", prompt, "--output-format", "text"]
        if self.verbose:
            print(f"[gemini] running {self.gemini_bin} ({len(prompt)} chars)", file=sys.stderr)
        try:
            proc = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
        except FileNotFoundError:
            return None, f"gemini binary not found: {self.gemini_bin}"
        except OSError as err:
            return None, f"failed to execute gemini: {err}"

        if proc.returncode != 0:
            err_text = (proc.stderr or "").strip()
            return None, f"gemini exit {proc.returncode}: {err_text[:300]}"

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

    def generate_json_batch(
        self,
        prompts: list[str],
        *,
        concurrency: int = 3,
    ) -> list[tuple[dict[str, Any] | None, str | None]]:
        """Run multiple prompts concurrently via thread pool."""
        import asyncio

        async def _run() -> list[tuple[dict[str, Any] | None, str | None]]:
            sem = asyncio.Semaphore(concurrency)

            async def _one(prompt: str) -> tuple[dict[str, Any] | None, str | None]:
                async with sem:
                    return await asyncio.to_thread(self.generate_json, prompt)

            return await asyncio.gather(*[_one(p) for p in prompts])

        return asyncio.run(_run())


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------


def load_embeddings(shadow_root: Path) -> dict[str, list[float]]:
    """Load all project embeddings from summary.json files.

    Returns a map of project_path -> embedding vector.
    """
    embeddings: dict[str, list[float]] = {}
    if not shadow_root.exists():
        return embeddings

    for summary_path in shadow_root.rglob("summary.json"):
        data = read_summary(summary_path)
        if data is None:
            continue
        project_path = as_string(data.get("_path"))
        if not project_path:
            continue
        embedding = data.get("_embedding")
        if not isinstance(embedding, dict):
            continue
        vector = embedding.get("vector")
        if not isinstance(vector, list) or len(vector) == 0:
            continue
        # Validate it's actually floats.
        try:
            vec = [float(x) for x in vector]
        except (TypeError, ValueError):
            continue
        embeddings[project_path] = vec

    return embeddings


def cosine_similarity(a: list[float], b: list[float]) -> float | None:
    """Cosine similarity between two vectors. Returns None if degenerate."""
    if len(a) != len(b) or len(a) == 0:
        return None
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a < 1e-12 or norm_b < 1e-12:
        return None
    return dot / (norm_a * norm_b)
