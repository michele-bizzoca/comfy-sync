#!/usr/bin/env python3
"""Copy settings between the database and the default ComfyUI profile."""

import argparse
import shutil
import sys
from pathlib import Path

# Resolve workspace paths independently of the current working directory.
SELF = Path(__file__).resolve().parent
PARENT = SELF.parent.parent
COMFY = PARENT / "comfy-ui"

def database() -> Path:
    """Locate the unique sibling database directory."""
    dirs = [p for p in PARENT.glob("comfy-db*") if p.is_dir()]
    if len(dirs) != 1:
        raise RuntimeError("Expected exactly one comfy-db directory")
    return dirs[0]

def main(argv=None):
    """Select the copy direction; record never changes the installed settings."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("update", "record"))
    parser.add_argument("--database", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    path = args.database if args.database is not None else database() / "config-db.json"
    settings = COMFY / "user/default/comfy.settings.json"
    source, destination = (path, settings) if args.command == "update" else (settings, path)
    if not source.is_file():
        raise RuntimeError(f"Missing configuration: {source}")
    if args.dry_run:
        print(f"COPY {source} -> {destination}")
        return 0
    if args.command == "update":
        destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
