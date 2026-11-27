#!/usr/bin/env python3
"""Launch ComfyUI with the managed directories and token environment.

Load the token database, assemble the launch arguments and replace this
process with the ComfyUI interpreter, forwarding all caller arguments.
"""

import os
import subprocess
import sys
from pathlib import Path

# Resolve the workspace independently of the caller’s working directory.
WORKSPACE = Path(__file__).resolve().parents[2]
COMFY = WORKSPACE / "comfy-ui"
PYTHON = COMFY / ".venv/bin/python"
DEFAULT_PORT = "8188"


def database() -> Path:
    """Locate the unique sibling database directory or reject ambiguity."""
    directories = [path for path in WORKSPACE.glob("comfy-db*") if path.is_dir()]
    if len(directories) != 1:
        raise RuntimeError("Expected exactly one comfy-db directory")
    return directories[0]


def envfile(path: Path) -> dict[str, str]:
    """Source a trusted Bash environment file and capture its exported variables."""
    # Preserve shell expansion and exported environment variables.
    data = subprocess.check_output(
        ["bash", "-c", 'set -a; source "$1" >&2 || exit; env -0', "bash", str(path)]
    )
    # NUL delimiters preserve values containing whitespace and newlines.
    return dict(
        os.fsdecode(entry).split("=", 1)
        for entry in data.split(b"\0")
        if b"=" in entry
    )


def main() -> None:
    """Build the managed launch configuration and execute ComfyUI."""
    env = envfile(database() / "token-db.env")
    forwarded = sys.argv[1:]
    # Bind to all interfaces and load the custom-node path configuration.
    args = [
        str(PYTHON),
        str(COMFY / "main.py"),
        "--enable-manager",
        "--extra-model-paths-config", str(COMFY / "comfy-extension-paths.yaml"),
        "--verbose", "DETAIL",
    ]

    # Keep persistent data outside the ComfyUI checkout.
    for name in ("models", "output", "input"):
        args.extend([f"--{name}-directory", str(WORKSPACE / f"comfy-{name}")])

    # Supply defaults only when the caller has not specified the corresponding option.
    # Recognize both --option value and --option=value, including bare --listen.
    for option, value in (("--preview-method", "auto"), ("--listen", "0.0.0.0"), ("--port", DEFAULT_PORT)):
        if not any(arg == option or arg.startswith(option + "=") for arg in forwarded):
            args.extend([option, value])
    args.extend(forwarded)

    # Preserve the process ID and let ComfyUI receive signals directly.
    os.execve(str(PYTHON), args, env)


if __name__ == "__main__":
    try:
        main()
    # Report startup failures without an unnecessary traceback.
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
