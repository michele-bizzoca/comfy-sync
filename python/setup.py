#!/usr/bin/env python3
"""Prepare ComfyUI, its Python environment and managed assets."""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SELF = Path(__file__).resolve().parent
PARENT = SELF.parent.parent
COMFY = PARENT / "comfy-ui"
PY = COMFY / ".venv/bin/python"
URL = "https://github.com/Comfy-Org/ComfyUI.git"


def database() -> Path:
    dirs = [p for p in PARENT.glob("comfy-db*") if p.is_dir()]
    if len(dirs) != 1:
        raise RuntimeError("Expected exactly one comfy-db directory")
    return dirs[0]


def run(*args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def capture(*args) -> str:
    return run(*args, stdout=subprocess.PIPE, text=True).stdout.strip()


def versions(db: Path) -> tuple[str, str, str]:
    data = subprocess.check_output(["bash", "-c", 'unset COMFY_VERSION PYTHON_VERSION TORCH_VERSION; set -a; source "$1" >&2 || exit; env -0', "bash", str(db / "version-db.env")])
    env = dict(os.fsdecode(x).split("=", 1) for x in data.split(b"\0") if b"=" in x)
    values = tuple(env.get(k, "") for k in ("COMFY_VERSION", "PYTHON_VERSION", "TORCH_VERSION"))
    if not all(re.fullmatch(p, v) for p, v in zip((r"[0-9]+\.[0-9]+\.[0-9]+", r"3\.[0-9]+", r"[0-9]+\.[0-9]+\.[0-9]+\+cu[0-9]+"), values)):
        raise ValueError("Invalid version-db.env")
    return values


def sync(name, *args, **kwargs):
    run(PY, SELF / ("sync-" + name + ".py"), *args, **kwargs)


def check_driver(torch_version: str) -> tuple[str, str]:
    cuda = torch_version.split("+")[1]
    digits = cuda[2:]
    required = digits[:-1] + "." + digits[-1]
    info = run("nvidia-smi", stdout=subprocess.PIPE, text=True, env=dict(os.environ, LC_ALL="C")).stdout
    match = re.search(r"CUDA Version: ([0-9]+\.[0-9]+)", info)
    if not match or tuple(map(int, match[1].split("."))) < tuple(map(int, required.split("."))):
        raise RuntimeError("Driver CUDA does not meet required CUDA " + required)
    return cuda, required


def checkout_comfy(version: str) -> None:
    tag = f"refs/tags/v{version}"
    run("git", "ls-remote", "--exit-code", URL, tag, stdout=subprocess.DEVNULL)
    if not COMFY.is_dir():
        run("git", "clone", "--no-checkout", URL, COMFY)
    run("git", "-C", COMFY, "fetch", URL, tag)
    run("git", "-C", COMFY, "checkout", "--detach", capture("git", "-C", COMFY, "rev-parse", "FETCH_HEAD^{commit}"))


def prepare_directories() -> None:
    for source, dest in (("models", "models"), ("output", "output"), ("input", "input"), ("custom_nodes", "extensions")):
        src, dst = COMFY / source, PARENT / ("comfy-" + dest)
        if src.is_dir() and not dst.exists() and not dst.is_symlink():
            shutil.move(str(src), str(dst))
        dst.mkdir(parents=True, exist_ok=True)
    (COMFY / "user/default").mkdir(parents=True, exist_ok=True)


def prepare_python(python_version: str) -> None:
    interpreter = f"python{python_version}"
    if not shutil.which(interpreter) or subprocess.run([interpreter, "-c", "import ensurepip"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        run("sudo", "apt", "update")
        run("sudo", "apt", "install", "-y", interpreter, interpreter + "-venv")
    run(interpreter, "-m", "venv", COMFY / ".venv")
    run(PY, "-c", 'import sys; assert ".".join(map(str, sys.version_info[:2])) == sys.argv[1]', python_version)
    run(PY, "-m", "pip", "install", "--upgrade", "pip")


def prepare_torch(tv: str, cuda: str, required: str) -> None:
    if subprocess.run([str(PY), "-c", "import sys\ntry:\n    import torch\n    import torchvision\n    import torchaudio\n    from importlib.metadata import requires\n    from pip._vendor.packaging.requirements import Requirement\n    from pip._vendor.packaging.version import Version\n    assert torch.__version__ == sys.argv[1]\n    assert torch.version.cuda == sys.argv[2]\n    for package in (\"torchvision\", \"torchaudio\"):\n        for entry in requires(package) or ():\n            req = Requirement(entry)\n            if req.name.lower() == \"torch\" and (req.marker is None or req.marker.evaluate()):\n                assert req.specifier.contains(Version(torch.__version__), prereleases=True)\nexcept Exception:\n    sys.exit(1)", tv, required]).returncode:
        packages = ["torch==" + tv, "torchvision", "torchaudio", "--index-url", "https://download.pytorch.org/whl/" + cuda]
        run(PY, "-m", "pip", "install", "--dry-run", "--ignore-installed", "--only-binary=:all:", *packages)
        run(PY, "-m", "pip", "install", *packages)
    constraints = COMFY / ".venv/comfy-constraints.txt"
    constraints.write_text("torch==" + tv + "\n")
    os.environ["PIP_CONSTRAINT"] = str(constraints)


def install_dependencies() -> None:
    run(PY, "-m", "pip", "install", "httpx", "-r", SELF.parent / "requirements.txt", "-r", COMFY / "requirements.txt")
    if (COMFY / "manager_requirements.txt").is_file():
        run(PY, "-m", "pip", "install", "-r", COMFY / "manager_requirements.txt")
    run(PY, "-m", "pip", "install", *"opencv-python-headless ultralytics matrix-nio imageio-ffmpeg onnxruntime-gpu PyOpenGL-accelerate scikit-image".split())


def configure_extensions() -> None:
    (COMFY / "comfy-extension-paths.yaml").write_text("managed_extensions:\n  is_default: true\n  custom_nodes: " + json.dumps(str(PARENT / "comfy-extensions")) + "\n", encoding="utf-8")
    sync("extensions", "update")


def verify_environment(tv: str, required: str) -> None:
    run(PY, "-m", "pip", "check")
    run(PY, "-c", "import sys\nimport torch\nif torch.__version__ != sys.argv[1] or torch.version.cuda != sys.argv[2]:\n    raise SystemExit(\"Installed Torch/CUDA version mismatch\")\nif not torch.cuda.is_available():\n    raise SystemExit(\"Torch cannot access CUDA\")\ntorch.zeros(1, device=\"cuda\").add_(1)\ntorch.cuda.synchronize()", tv, required)


def main() -> None:
    db = database()
    comfy_version, python_version, torch_version = versions(db)
    cuda, required = check_driver(torch_version)
    checkout_comfy(comfy_version)
    prepare_directories()
    prepare_python(python_version)
    prepare_torch(torch_version, cuda, required)
    install_dependencies()
    configure_extensions()
    verify_environment(torch_version, required)
    sync("models", "update")
    shutil.copyfile(db / "config-db.json", COMFY / "user/default/comfy.settings.json")

if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
