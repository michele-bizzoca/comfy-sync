#!/usr/bin/env python3
"""Record installed ComfyUI assets, configuration and versions.

Capture the current installation into the sibling database, preserving
existing catalog metadata and staging all outputs before publication.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Resolve workspace paths independently of the current working directory.
SELF = Path(__file__).resolve().parent
PARENT = SELF.parent.parent
COMFY = PARENT / "comfy-ui"
PY = COMFY / ".venv/bin/python"
CATALOGS = ("extension-db.xlsx", "model-db.xlsx")
# Only these files are replaced; the token database is left untouched.
RECORD_FILES = (*CATALOGS, "config-db.json", "version-db.env")


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


def main() -> None:
    """Validate the installation and record its current state."""
    db = database()
    config = COMFY / "user/default/comfy.settings.json"
    if not config.is_file():
        raise RuntimeError(f"Missing ComfyUI settings: {config}")
    record_snapshot(db)


def record_snapshot(db: Path) -> None:
    """Stage updated catalogs, settings and version pins before replacing database files."""
    # Stage all outputs before replacing any existing database file.
    with tempfile.TemporaryDirectory(prefix=".record.", dir=db) as directory:
        stage = Path(directory)
        # Probe versions before recording catalogs, preserving the original preflight order.
        sync("versions", "record", "--database", stage / "version-db.env")
        # Seed the staged catalogs with existing metadata when available.
        for name in reversed(CATALOGS):
            if (db / name).is_file():
                shutil.copyfile(db / name, stage / name)
        # Record against staged copies so synchronization failures leave the originals intact.
        sync("extensions", "record", "--database", stage / "extension-db.xlsx")
        sync("models", "record", "--database", stage / "model-db.xlsx")
        sync("config", "record", "--database", stage / "config-db.json")
        # Publish each file atomically on the same filesystem; the group is not transactional.
        for name in RECORD_FILES:
            os.replace(stage / name, db / name)

if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
