#!/usr/bin/env python3
"""Validate, verify and record ComfyUI runtime version pins."""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

# Resolve workspace paths independently of the current working directory.
SELF = Path(__file__).resolve().parent
PARENT = SELF.parent.parent
COMFY = PARENT / "comfy-ui"
PY = COMFY / ".venv/bin/python"
URL = "https://github.com/Comfy-Org/ComfyUI.git"


def database() -> Path:
    """Locate the unique sibling database directory."""
    dirs = [p for p in PARENT.glob("comfy-db*") if p.is_dir()]
    if len(dirs) != 1:
        raise RuntimeError("Expected exactly one comfy-db directory")
    return dirs[0]

def run(*args, **kwargs):
    """Run a command with stringified arguments and fail on a nonzero exit."""
    return subprocess.run([str(a) for a in args], check=True, **kwargs)

def capture(*args) -> str:
    """Return stripped standard output from a successful command."""
    return run(*args, stdout=subprocess.PIPE, text=True).stdout.strip()

def versions(path: Path) -> tuple[str, str, str]:
    """Load and validate the ComfyUI, Python and CUDA-enabled Torch version pins."""
    # Clear inherited version pins so missing database entries cannot be masked.
    data = subprocess.check_output(["bash", "-c", 'unset COMFY_VERSION PYTHON_VERSION TORCH_VERSION; set -a; source "$1" >&2 || exit; env -0', "bash", str(path)])
    env = dict(os.fsdecode(x).split("=", 1) for x in data.split(b"\0") if b"=" in x)
    values = tuple(env.get(k, "") for k in ("COMFY_VERSION", "PYTHON_VERSION", "TORCH_VERSION"))
    if not all(re.fullmatch(p, v) for p, v in zip((r"[0-9]+\.[0-9]+\.[0-9]+", r"3\.[0-9]+", r"[0-9]+\.[0-9]+\.[0-9]+\+cu[0-9]+"), values)):
        raise ValueError("Invalid version-db.env")
    return values

def safe_capture(*args) -> str:
    """Return empty output when a version probe cannot be executed successfully."""
    try:
        return capture(*args)
    except (OSError, subprocess.CalledProcessError):
        return ""

def verify_versions(path: Path) -> None:
    """Compare installed versions with database pins and report every mismatch."""
    comfy_version, python_version, torch_version = versions(path)
    tag = f"refs/tags/v{comfy_version}"
    # Request both the tag and its peeled commit for annotated release tags.
    refs = safe_capture("git", "ls-remote", URL, tag, tag + "^{}").splitlines()
    expected = refs[-1].split()[0] if refs else ""
    checks = (
        ("COMFYUI", safe_capture("git", "-C", COMFY, "rev-parse", "HEAD"), expected),
        ("PYTHON", safe_capture(PY, "-c", 'import sys; print(".".join(map(str, sys.version_info[:2])))'), python_version),
        ("TORCH", safe_capture(PY, "-c", "import torch; print(torch.__version__)"), torch_version),
    )
    # Treat an unavailable required version as a verification failure, not a match.
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

def installed_versions() -> tuple[str, str, str]:
    """Read installed release and runtime versions in the database format."""
    # Require an exact release tag at HEAD rather than guessing a version.
    tags = capture("git", "-C", COMFY, "tag", "--points-at", "HEAD").splitlines()
    releases = [tag[1:] for tag in tags if re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag)]
    if len(releases) != 1:
        raise RuntimeError("Expected exactly one ComfyUI release tag at HEAD")
    comfy_version = releases[0]
    # Probe the managed interpreter, not the Python running this script.
    python_version = capture(PY, "-c", 'import sys; print(".".join(map(str, sys.version_info[:2])))')
    torch_version = capture(PY, "-c", "import torch; print(torch.__version__)")
    if not re.fullmatch(r"3\.[0-9]+", python_version) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+\+cu[0-9]+", torch_version):
        raise ValueError("Installed Python or Torch version is incompatible with version-db.env")
    return comfy_version, python_version, torch_version

def main(argv=None):
    """Expose version operations without automatically upgrading the runtime."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "verify", "record"))
    parser.add_argument("--database", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.dry_run:
        return 0
    path = args.database if args.database is not None else database() / "version-db.env"
    if args.command == "check":
        # Machine-readable pins for setup, which may run before the managed venv exists.
        print(*versions(path), sep="\n")
    elif args.command == "verify":
        verify_versions(path)
    else:
        cv, pv, tv = installed_versions()
        path.write_text(f"COMFY_VERSION={cv}\nPYTHON_VERSION={pv}\nTORCH_VERSION={tv}\n")
        print(f"Recorded ComfyUI v{cv}, Python {pv}, Torch {tv}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
