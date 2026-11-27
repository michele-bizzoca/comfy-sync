#!/usr/bin/env python3
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SELF = Path(__file__).resolve().parent
PARENT = SELF.parent.parent
COMFY = PARENT / "comfy-ui"
PY = COMFY / ".venv/bin/python"
URL = "https://github.com/Comfy-Org/ComfyUI.git"

def database():
    dirs = [p for p in PARENT.glob("comfy-db*") if p.is_dir()]
    if len(dirs) != 1:
        raise RuntimeError("Expected exactly one comfy-db directory")
    return dirs[0]

def run(*args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)

def capture(*args):
    return run(*args, stdout=subprocess.PIPE, text=True).stdout.strip()

def envfile(path):
    # Preserve the existing shell environment-file semantics.
    data = subprocess.check_output(["bash", "-c", 'set -a; source "$1" >&2 || exit; env -0', "bash", str(path)])
    return dict(os.fsdecode(x).split("=", 1) for x in data.split(b"\0") if b"=" in x)

def versions(db):
    data = subprocess.check_output(["bash", "-c", 'unset COMFY_VERSION PYTHON_VERSION TORCH_VERSION; set -a; source "$1" >&2 || exit; env -0', "bash", str(db / "version-db.env")])
    env = dict(os.fsdecode(x).split("=", 1) for x in data.split(b"\0") if b"=" in x)
    values = tuple(env.get(k, "") for k in ("COMFY_VERSION", "PYTHON_VERSION", "TORCH_VERSION"))
    if not all(re.fullmatch(p, v) for p, v in zip((r"[0-9]+\.[0-9]+\.[0-9]+", r"3\.[0-9]+", r"[0-9]+\.[0-9]+\.[0-9]+\+cu[0-9]+"), values)):
        raise ValueError("Invalid version-db.env")
    return values

def sync(name, *args, **kwargs):
    run(PY, SELF / ("sync-" + name + ".py"), *args, **kwargs)

def main():
    env = envfile(database() / "token-db.env")
    port = sys.argv[1] if len(sys.argv) > 1 else "8188"
    args = [str(PY), str(COMFY / "main.py"), "--preview-method", "auto", "--enable-manager", "--listen", "0.0.0.0", "--port", port, "--extra-model-paths-config", str(COMFY / "comfy-extension-paths.yaml"), "--verbose", "DETAIL"]
    for name in ("models", "output", "input"):
        args.extend(["--" + name + "-directory", str(PARENT / ("comfy-" + name))])
    os.execve(str(PY), args, env)

if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
