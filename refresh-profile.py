#!/usr/bin/env python3
import os
from pathlib import Path

# Paths to scan
ROOT = Path("/Users/ember")
SCAN_DIRS = [
    ROOT / "dev",
    ROOT / "shitheap",
    ROOT / "hellas",
    ROOT / "elide"
]

PROFILE_PATH = ROOT / "dev" / "PROFILE.md"

def get_project_summary(path):
    # Detect languages/tech
    files = os.listdir(path)
    techs = []
    if "Cargo.toml" in files: techs.append("Rust")
    if "package.json" in files: techs.append("Node/TS")
    if "dune-project" in files or any(f.endswith(".opam") for f in files): techs.append("OCaml")
    if "go.mod" in files: techs.append("Go")
    if "requirements.txt" in files: techs.append("Python")
    if "flake.nix" in files: techs.append("Nix")
    
    # Get README snippet
    readme = ""
    for r in ["README.md", "README", "readme.md"]:
        if r in files:
            try:
                with open(path / r, 'r') as f:
                    readme = f.readline().strip("# ").strip()
                    break
            except:
                pass
    
    return techs, readme

def main():
    print("Scanning directories...")
    projects = {}
    
    for base in SCAN_DIRS:
        if not base.exists(): continue
        for item in base.iterdir():
            if item.is_dir() and not item.name.startswith("."):
                try:
                    techs, desc = get_project_summary(item)
                    projects[item.name] = {
                        "path": str(item.relative_to(ROOT)),
                        "techs": techs,
                        "desc": desc
                    }
                except:
                    continue

    # Generate section
    lines = ["\n## Project Inventory (Auto-generated)\n"]
    for name, info in sorted(projects.items()):
        tech_str = f"({', '.join(info['techs'])})" if info['techs'] else ""
        lines.append(f"- **{name}**: {info['desc']} `{info['path']}` {tech_str}")
    
    # Read existing profile
    content = ""
    if PROFILE_PATH.exists():
        with open(PROFILE_PATH, 'r') as f:
            content = f.read()
            # Strip old inventory if it exists
            if "## Project Inventory" in content:
                content = content.split("## Project Inventory")[0]
    
    with open(PROFILE_PATH, 'w') as f:
        f.write(content.strip() + "\n" + "\n".join(lines))
    
    print(f"Updated {PROFILE_PATH}")

if __name__ == "__main__":
    main()
