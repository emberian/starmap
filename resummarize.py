#!/usr/bin/env python3
"""Compatibility shim for the Rust `resummarize` binary.

This keeps old invocations like `python resummarize.py` working while routing
execution to the Rust implementation under `./resummarize`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CRATE_DIR = ROOT / "resummarize"
MANIFEST_PATH = CRATE_DIR / "Cargo.toml"


def _binary_name() -> str:
    return "resummarize.exe" if os.name == "nt" else "resummarize"


def _find_resummarize_binary() -> list[str] | None:
    env_bin = os.environ.get("RESUMMARIZE_BIN")
    if env_bin:
        candidate = Path(env_bin).expanduser()
        if candidate.exists():
            return [str(candidate)]

    release_bin = CRATE_DIR / "target" / "release" / _binary_name()
    if release_bin.exists():
        return [str(release_bin)]

    debug_bin = CRATE_DIR / "target" / "debug" / _binary_name()
    if debug_bin.exists():
        return [str(debug_bin)]

    path_bin = shutil.which("resummarize")
    if path_bin:
        return [path_bin]

    return None


def main() -> int:
    args = sys.argv[1:]

    binary_cmd = _find_resummarize_binary()
    if binary_cmd:
        cmd = [*binary_cmd, *args]
        return subprocess.call(cmd)

    cargo = shutil.which("cargo")
    if not cargo:
        print(
            "error: could not find `resummarize` binary or `cargo` to build it",
            file=sys.stderr,
        )
        return 1

    cmd = [
        cargo,
        "run",
        "--manifest-path",
        str(MANIFEST_PATH),
        "--",
        *args,
    ]
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
