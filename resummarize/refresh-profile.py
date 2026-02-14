#!/usr/bin/env python3
"""Refresh PROFILE.md with an auto-generated project inventory.

Special handling:
- `~/dev/gh` is treated as a repository bucket where every child directory
  is considered a project (without project-indicator heuristics).
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path.home()
DEV_ROOT = ROOT / "dev"
GH_ROOT = DEV_ROOT / "gh"

SCAN_DIRS = [
    DEV_ROOT,
    ROOT / "shitheap",
    ROOT / "hellas",
]

PROFILE_PATH = DEV_ROOT / "PROFILE.md"

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


def list_visible(path: Path) -> tuple[list[str], list[Path]]:
    files: list[str] = []
    dirs: list[Path] = []
    try:
        for entry in path.iterdir():
            if entry.name.startswith("."):
                continue
            if entry.is_dir():
                dirs.append(entry)
            else:
                files.append(entry.name)
    except (PermissionError, FileNotFoundError):
        return [], []

    files.sort()
    dirs.sort(key=lambda p: p.name.lower())
    return files, dirs


def iter_visible_dirs(base: Path) -> list[Path]:
    try:
        entries = sorted(base.iterdir(), key=lambda p: p.name.lower())
    except (PermissionError, FileNotFoundError):
        return []
    return [entry for entry in entries if entry.is_dir() and not entry.name.startswith(".")]


def looks_like_project(path: Path) -> bool:
    files, dirs = list_visible(path)

    if any(indicator in files for indicator in PROJECT_INDICATORS):
        return True
    if (path / ".git").is_dir():
        return True

    source_like = [name for name in files if Path(name).suffix in SOURCE_EXTENSIONS]
    if len(source_like) >= 4:
        return True

    dir_names = {d.name for d in dirs}
    if {"src", "lib", "cmd", "app"} & dir_names:
        score = len(source_like)
        if any(name.endswith((".toml", ".yaml", ".yml", ".json")) for name in files):
            score += 2
        return score >= 3

    return False


def discover_gh_projects() -> list[Path]:
    """Treat directories in ~/dev/gh as repos without indicator checks."""
    projects: list[Path] = []

    if not GH_ROOT.exists():
        return projects

    for candidate in iter_visible_dirs(GH_ROOT):

        files, dirs = list_visible(candidate)

        # Handle owner/org folders by descending one level.
        if not files and not (candidate / ".git").is_dir() and dirs:
            for repo in dirs:
                if repo.is_dir() and not repo.name.startswith("."):
                    projects.append(repo)
            continue

        projects.append(candidate)

    return projects


def detect_tech(path: Path, files: list[str]) -> list[str]:
    techs: list[str] = []
    if "Cargo.toml" in files:
        techs.append("Rust")
    if "package.json" in files:
        techs.append("Node/TS")
    if "dune-project" in files or any(name.endswith(".opam") for name in files):
        techs.append("OCaml")
    if "go.mod" in files:
        techs.append("Go")
    if "requirements.txt" in files or "pyproject.toml" in files:
        techs.append("Python")
    if "flake.nix" in files:
        techs.append("Nix")
    if "mix.exs" in files:
        techs.append("Elixir")
    if "Gemfile" in files:
        techs.append("Ruby")
    if "pom.xml" in files:
        techs.append("Java")

    if not techs:
        extension_hits = {
            Path(name).suffix for name in files if Path(name).suffix in SOURCE_EXTENSIONS
        }
        if ".rs" in extension_hits:
            techs.append("Rust")
        if {".ts", ".tsx", ".js", ".jsx"} & extension_hits:
            techs.append("JS/TS")
        if ".py" in extension_hits:
            techs.append("Python")

    return techs


def read_readme_summary(path: Path, files: list[str]) -> str:
    for readme_name in ("README.md", "README", "readme.md", "README.txt"):
        if readme_name not in files:
            continue
        try:
            first_lines = []
            with (path / readme_name).open("r", encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    stripped = line.strip().strip("# ")
                    if stripped:
                        first_lines.append(stripped)
                    if len(" ".join(first_lines)) >= 180:
                        break
            if first_lines:
                return " ".join(first_lines)[:180]
        except OSError:
            continue
    return ""


def iter_projects() -> list[Path]:
    projects: list[Path] = []

    for base in SCAN_DIRS:
        if not base.exists():
            continue

        if base == DEV_ROOT:
            for item in iter_visible_dirs(base):
                if item == GH_ROOT:
                    projects.extend(discover_gh_projects())
                    continue
                if looks_like_project(item):
                    projects.append(item)
            continue

        for item in iter_visible_dirs(base):
            if looks_like_project(item):
                projects.append(item)

    dedup = {p.resolve(): p for p in projects}
    return [dedup[key] for key in sorted(dedup, key=lambda p: str(p).lower())]


def main() -> None:
    print("Scanning directories...")

    projects = {}
    for project_path in iter_projects():
        files, _ = list_visible(project_path)
        techs = detect_tech(project_path, files)
        desc = read_readme_summary(project_path, files)

        try:
            rel_path = project_path.relative_to(ROOT)
            path_display = str(rel_path)
        except ValueError:
            path_display = str(project_path)

        projects[project_path.name] = {
            "path": path_display,
            "techs": techs,
            "desc": desc,
        }

    lines = ["", "## Project Inventory (Auto-generated)", ""]
    for name, info in sorted(projects.items(), key=lambda kv: kv[0].lower()):
        tech_str = f"({', '.join(info['techs'])})" if info["techs"] else ""
        desc = info["desc"] or "No README summary yet."
        lines.append(f"- **{name}**: {desc} `{info['path']}` {tech_str}".rstrip())

    content = ""
    if PROFILE_PATH.exists():
        try:
            content = PROFILE_PATH.read_text(encoding="utf-8")
            if "## Project Inventory" in content:
                content = content.split("## Project Inventory", 1)[0]
        except OSError:
            content = ""

    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(content.strip() + "\n" + "\n".join(lines) + "\n", encoding="utf-8")

    print(f"Updated {PROFILE_PATH}")


if __name__ == "__main__":
    main()
