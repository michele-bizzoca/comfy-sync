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
    db = database()
    config = COMFY / "user/default/comfy.settings.json"
    if not config.is_file():
        raise RuntimeError("Missing ComfyUI settings: " + str(config))
    tags = capture("git", "-C", COMFY, "tag", "--points-at", "HEAD").splitlines()
    releases = [tag[1:] for tag in tags if re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag)]
    if len(releases) != 1:
        raise RuntimeError("Expected exactly one ComfyUI release tag at HEAD")
    cv = releases[0]
    pv = capture(PY, "-c", 'import sys; print(".".join(map(str, sys.version_info[:2])))')
    tv = capture(PY, "-c", "import torch; print(torch.__version__)")
    if not re.fullmatch(r"3\.[0-9]+", pv) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+\+cu[0-9]+", tv):
        raise ValueError("Installed Python or Torch version is incompatible with version-db.env")
    with tempfile.TemporaryDirectory(prefix=".record.", dir=db) as directory:
        stage = Path(directory)
        for name in ("model-db.xlsx", "extension-db.xlsx"):
            if (db / name).is_file():
                shutil.copyfile(db / name, stage / name)
        sync("extensions", "record", "--database", stage / "extension-db.xlsx")
        sync("models", "record", "--database", stage / "model-db.xlsx")
        shutil.copyfile(config, stage / "config-db.json")
        (stage / "version-db.env").write_text(f"COMFY_VERSION={cv}\nPYTHON_VERSION={pv}\nTORCH_VERSION={tv}\n")
        for name in ("extension-db.xlsx", "model-db.xlsx", "config-db.json", "version-db.env"):
            os.replace(stage / name, db / name)
    print(f"Recorded ComfyUI v{cv}, Python {pv}, Torch {tv}")

if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
