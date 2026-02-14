#!/usr/bin/env python3
"""Attribute projects based on git authorship.

Partitions projects into:
- my-projects: Substantially authored by Ember.
- contributed: Contributed to, but not primary author.
- interests: Present in workspace, but no (or negligible) contributions.
"""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

from starmap_lib import (
    now_rfc3339,
    read_summary,
    resolve_shadow_root,
    write_summary,
)

# Authorship identities for Ember
EMBER_IDENTITIES = {
    "ember arlynx",
    "cmr",
    "corey richardson",
    "ember@lunar.town",
    "corey@octayn.net",
}


def get_git_authors(repo_path: Path) -> list[tuple[int, str, str]]:
    """Returns list of (commit_count, name, email)."""
    if not (repo_path / ".git").exists():
        return []

    try:
        result = subprocess.run(
            ["git", "shortlog", "-sne", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        authors = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) != 2:
                continue
            count = int(parts[0])
            name_email = parts[1]
            if " <" in name_email:
                name, email = name_email.split(" <", 1)
                email = email.rstrip(">")
            else:
                name = name_email
                email = ""
            authors.append((count, name.strip(), email.strip()))
        return authors
    except Exception:
        return []


def is_ember(name: str, email: str) -> bool:
    name_low = name.lower()
    email_low = email.lower()
    if name_low in EMBER_IDENTITIES:
        return True
    if email_low in EMBER_IDENTITIES:
        return True
    for identity in EMBER_IDENTITIES:
        if "@" not in identity and identity in name_low:
            return True
        if "@" in identity and identity in email_low:
            return True
    return False


def attribute_project(path: Path) -> str:
    authors = get_git_authors(path)
    if not authors:
        return "interests"

    total_commits = sum(count for count, _, _ in authors)
    ember_commits = sum(count for count, name, email in authors if is_ember(name, email))

    if total_commits == 0:
        return "interests"

    ratio = ember_commits / total_commits

    if ratio > 0.4:
        return "my-projects"
    if ember_commits > 0:
        return "contributed"
    return "interests"


def main():
    parser = argparse.ArgumentParser(description="Attribute projects by git authorship.")
    parser.add_argument("--shadow-root", default=None, help="Summary root.")
    parser.add_argument("--output-dir", default=".", help="Where to spit out partition files.")
    args = parser.parse_args()

    shadow_root = resolve_shadow_root(args.shadow_root)
    output_dir = Path(args.output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    if not shadow_root.exists():
        print(f"Error: Shadow root {shadow_root} does not exist.")
        return

    summary_files = sorted(shadow_root.rglob("summary.json"))
    print(f"Scanning {len(summary_files)} projects...")

    partitions: dict[str, list[str]] = {
        "my-projects": [],
        "contributed": [],
        "interests": [],
    }

    for sf in summary_files:
        data = read_summary(sf)
        if data is None:
            continue
        project_path_str = data.get("_path")
        if not project_path_str:
            continue

        project_path = Path(project_path_str)
        if not project_path.exists():
            project_path = Path(os.path.expanduser(project_path_str))
            if not project_path.exists():
                continue

        try:
            attr = attribute_project(project_path)
        except Exception as e:
            print(f"Error processing {sf}: {e}")
            continue

        partitions[attr].append(project_path_str)

        data["attribution"] = attr
        data["_updated_at"] = now_rfc3339()
        write_summary(sf, data)

    for name, paths in partitions.items():
        out_file = output_dir / f"{name}.txt"
        with out_file.open("w") as f:
            for p in sorted(paths):
                f.write(f"{p}\n")
        print(f"Wrote {len(paths)} projects to {out_file}")


if __name__ == "__main__":
    main()
