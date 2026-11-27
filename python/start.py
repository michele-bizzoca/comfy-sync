#!/usr/bin/env python3
"""Launch ComfyUI with the managed directories and token environment."""

import os
import subprocess
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[2]
COMFY = WORKSPACE / "comfy-ui"
PYTHON = COMFY / ".venv/bin/python"
DEFAULT_PORT = "8188"


def database() -> Path:
    directories = [path for path in WORKSPACE.glob("comfy-db*") if path.is_dir()]
    if len(directories) != 1:
        raise RuntimeError("Expected exactly one comfy-db directory")
    return directories[0]


def envfile(path: Path) -> dict[str, str]:
    # Preserve shell expansion and exported environment variables.
    data = subprocess.check_output(
        ["bash", "-c", 'set -a; source "$1" >&2 || exit; env -0', "bash", str(path)]
    )
    return dict(
        os.fsdecode(entry).split("=", 1)
        for entry in data.split(b"\0")
        if b"=" in entry
    )


def main() -> None:
    env = envfile(database() / "token-db.env")
    port = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PORT
    args = [
        str(PYTHON),
        str(COMFY / "main.py"),
        "--preview-method", "auto",
        "--enable-manager",
        "--listen", "0.0.0.0",
        "--port", port,
        "--extra-model-paths-config", str(COMFY / "comfy-extension-paths.yaml"),
        "--verbose", "DETAIL",
    ]

    for name in ("models", "output", "input"):
        args.extend([f"--{name}-directory", str(WORKSPACE / f"comfy-{name}")])

    os.execve(str(PYTHON), args, env)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
