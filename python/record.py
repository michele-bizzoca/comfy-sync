#!/usr/bin/env python3
"""Record installed ComfyUI assets, configuration and versions."""

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
CATALOGS = ("extension-db.xlsx", "model-db.xlsx")
RECORD_FILES = (*CATALOGS, "config-db.json", "version-db.env")


def database() -> Path:
    dirs = [p for p in PARENT.glob("comfy-db*") if p.is_dir()]
    if len(dirs) != 1:
        raise RuntimeError("Expected exactly one comfy-db directory")
    return dirs[0]


def run(*args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def capture(*args) -> str:
    return run(*args, stdout=subprocess.PIPE, text=True).stdout.strip()


def sync(name, *args, **kwargs):
    run(PY, SELF / ("sync-" + name + ".py"), *args, **kwargs)


def installed_versions() -> tuple[str, str, str]:
    tags = capture("git", "-C", COMFY, "tag", "--points-at", "HEAD").splitlines()
    releases = [tag[1:] for tag in tags if re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag)]
    if len(releases) != 1:
        raise RuntimeError("Expected exactly one ComfyUI release tag at HEAD")
    comfy_version = releases[0]
    python_version = capture(PY, "-c", 'import sys; print(".".join(map(str, sys.version_info[:2])))')
    torch_version = capture(PY, "-c", "import torch; print(torch.__version__)")
    if not re.fullmatch(r"3\.[0-9]+", python_version) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+\+cu[0-9]+", torch_version):
        raise ValueError("Installed Python or Torch version is incompatible with version-db.env")
    return comfy_version, python_version, torch_version


def main() -> None:
    db = database()
    config = COMFY / "user/default/comfy.settings.json"
    if not config.is_file():
        raise RuntimeError(f"Missing ComfyUI settings: {config}")
    comfy_version, python_version, torch_version = installed_versions()
    record_snapshot(db, config, comfy_version, python_version, torch_version)
    print(f"Recorded ComfyUI v{comfy_version}, Python {python_version}, Torch {torch_version}")


def record_snapshot(db: Path, config: Path, comfy_version: str, python_version: str, torch_version: str) -> None:
    # Stage all outputs before replacing any existing database file.
    with tempfile.TemporaryDirectory(prefix=".record.", dir=db) as directory:
        stage = Path(directory)
        for name in reversed(CATALOGS):
            if (db / name).is_file():
                shutil.copyfile(db / name, stage / name)
        sync("extensions", "record", "--database", stage / "extension-db.xlsx")
        sync("models", "record", "--database", stage / "model-db.xlsx")
        shutil.copyfile(config, stage / "config-db.json")
        (stage / "version-db.env").write_text(f"COMFY_VERSION={comfy_version}\nPYTHON_VERSION={python_version}\nTORCH_VERSION={torch_version}\n")
        for name in RECORD_FILES:
            os.replace(stage / name, db / name)

if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
