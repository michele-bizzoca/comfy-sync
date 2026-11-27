#!/usr/bin/env python3
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SELF = Path(__file__).resolve().parent
PARENT = SELF.parent
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
    db = database()
    status = 0
    def attempt(label, action):
        nonlocal status
        try:
            action()
        except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError):
            print(label + " failed", file=sys.stderr)
            status = 1
    def extensions():
        constraints = COMFY / ".venv/comfy-constraints.txt"
        if not constraints.is_file():
            raise RuntimeError("Missing pip constraints; run setup first")
        sync("extensions", "update", env=dict(os.environ, PIP_CONSTRAINT=str(constraints)))
    def config():
        (COMFY / "user/default").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(db / "config-db.json", COMFY / "user/default/comfy.settings.json")
    def check():
        cv, pv, tv = versions(db)
        def safe(*args):
            try:
                return capture(*args)
            except (OSError, subprocess.CalledProcessError):
                return ""
        tag = "refs/tags/v" + cv
        refs = safe("git", "ls-remote", URL, tag, tag + "^{}").splitlines()
        expected = refs[-1].split()[0] if refs else ""
        checks = [("COMFYUI", safe("git", "-C", COMFY, "rev-parse", "HEAD"), expected),
                  ("PYTHON", safe(PY, "-c", 'import sys; print(".".join(map(str, sys.version_info[:2])))'), pv),
                  ("TORCH", safe(PY, "-c", "import torch; print(torch.__version__)"), tv)]
        bad = False
        for label, actual, required in checks:
            if not required or actual != required:
                print(f"{label}: INSTALLED {actual or 'unknown'}; REQUIRED {required or 'unverified'}", file=sys.stderr)
                bad = True
        if bad:
            raise RuntimeError("Version mismatch")
    attempt("Extension sync", extensions)
    attempt("Model sync", lambda: sync("models", "update"))
    attempt("Configuration sync", config)
    attempt("VERSION MISMATCH OR VERIFICATION", check)
    if status:
        print("No automatic version update has been performed.", file=sys.stderr)
    return status

if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
