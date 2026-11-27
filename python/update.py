#!/usr/bin/env python3
"""Synchronize managed assets and verify installed versions.

Update extensions, models and user settings independently. Report version
mismatches without automatically upgrading ComfyUI, Python or Torch.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

# Resolve workspace paths independently of the current working directory.
SELF = Path(__file__).resolve().parent
PARENT = SELF.parent.parent
COMFY = PARENT / "comfy-ui"
PY = COMFY / ".venv/bin/python"


def database() -> Path:
    """Locate the unique sibling database directory."""
    dirs = [p for p in PARENT.glob("comfy-db*") if p.is_dir()]
    if len(dirs) != 1:
        raise RuntimeError("Expected exactly one comfy-db directory")
    return dirs[0]


def run(*args, **kwargs):
    """Run a command with stringified arguments and fail on a nonzero exit."""
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def sync(name, *args, **kwargs):
    """Invoke a catalog synchronizer using the managed Python interpreter."""
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
    """Update extensions while preserving the Torch constraint established by setup."""
    # Require the setup-generated pin before installing extension dependencies.
    constraints = COMFY / ".venv/comfy-constraints.txt"
    if not constraints.is_file():
        raise RuntimeError("Missing pip constraints; run setup first")
    sync("extensions", "update", env=dict(os.environ, PIP_CONSTRAINT=str(constraints)))


def main() -> int:
    """Attempt all update phases and return a nonzero status if any phase fails."""
    # Parse help before accessing the installation or database.
    argparse.ArgumentParser(description=__doc__).parse_args()
    db = database()
    phases = (
        ("Extension sync", update_extensions),
        ("Model sync", lambda: sync("models", "update")),
        ("Configuration sync", lambda: sync("config", "update", "--database", db / "config-db.json")),
        ("VERSION MISMATCH OR VERIFICATION", lambda: sync("versions", "verify", "--database", db / "version-db.env")),
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
