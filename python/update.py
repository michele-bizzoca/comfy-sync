#!/usr/bin/env python3
"""Synchronize managed assets and verify installed versions."""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SELF = Path(__file__).resolve().parent
PARENT = SELF.parent.parent
COMFY = PARENT / "comfy-ui"
PY = COMFY / ".venv/bin/python"
URL = "https://github.com/Comfy-Org/ComfyUI.git"


def database() -> Path:
    dirs = [p for p in PARENT.glob("comfy-db*") if p.is_dir()]
    if len(dirs) != 1:
        raise RuntimeError("Expected exactly one comfy-db directory")
    return dirs[0]


def run(*args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def capture(*args) -> str:
    return run(*args, stdout=subprocess.PIPE, text=True).stdout.strip()


def versions(db: Path) -> tuple[str, str, str]:
    data = subprocess.check_output(["bash", "-c", 'unset COMFY_VERSION PYTHON_VERSION TORCH_VERSION; set -a; source "$1" >&2 || exit; env -0', "bash", str(db / "version-db.env")])
    env = dict(os.fsdecode(x).split("=", 1) for x in data.split(b"\0") if b"=" in x)
    values = tuple(env.get(k, "") for k in ("COMFY_VERSION", "PYTHON_VERSION", "TORCH_VERSION"))
    if not all(re.fullmatch(p, v) for p, v in zip((r"[0-9]+\.[0-9]+\.[0-9]+", r"3\.[0-9]+", r"[0-9]+\.[0-9]+\.[0-9]+\+cu[0-9]+"), values)):
        raise ValueError("Invalid version-db.env")
    return values


def sync(name, *args, **kwargs):
    run(PY, SELF / ("sync-" + name + ".py"), *args, **kwargs)


def attempt(label, action) -> bool:
    """Run one update phase without preventing subsequent phases."""
    try:
        action()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError):
        print(f"{label} failed", file=sys.stderr)
        return False
    return True


def update_extensions() -> None:
    constraints = COMFY / ".venv/comfy-constraints.txt"
    if not constraints.is_file():
        raise RuntimeError("Missing pip constraints; run setup first")
    sync("extensions", "update", env=dict(os.environ, PIP_CONSTRAINT=str(constraints)))


def update_config(db: Path) -> None:
    destination = COMFY / "user/default/comfy.settings.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(db / "config-db.json", destination)


def safe_capture(*args) -> str:
    try:
        return capture(*args)
    except (OSError, subprocess.CalledProcessError):
        return ""


def verify_versions(db: Path) -> None:
    comfy_version, python_version, torch_version = versions(db)
    tag = f"refs/tags/v{comfy_version}"
    refs = safe_capture("git", "ls-remote", URL, tag, tag + "^{}").splitlines()
    expected = refs[-1].split()[0] if refs else ""
    checks = (
        ("COMFYUI", safe_capture("git", "-C", COMFY, "rev-parse", "HEAD"), expected),
        ("PYTHON", safe_capture(PY, "-c", 'import sys; print(".".join(map(str, sys.version_info[:2])))'), python_version),
        ("TORCH", safe_capture(PY, "-c", "import torch; print(torch.__version__)"), torch_version),
    )
    mismatches = [
        (label, actual, required)
        for label, actual, required in checks
        if not required or actual != required
    ]
    for label, actual, required in mismatches:
        print(
            f"{label}: INSTALLED {actual or 'unknown'}; REQUIRED {required or 'unverified'}",
            file=sys.stderr,
        )
    if mismatches:
        raise RuntimeError("Version mismatch")


def main() -> int:
    db = database()
    phases = (
        ("Extension sync", update_extensions),
        ("Model sync", lambda: sync("models", "update")),
        ("Configuration sync", lambda: update_config(db)),
        ("VERSION MISMATCH OR VERIFICATION", lambda: verify_versions(db)),
    )
    # Evaluate every phase, even when an earlier one fails.
    results = [attempt(label, action) for label, action in phases]
    if not all(results):
        print("No automatic version update has been performed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
