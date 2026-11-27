#!/bin/bash

shopt -s nullglob
set -euo pipefail
self_dir="$(dirname "$(realpath "${BASH_SOURCE[0]}")")"
parent_dir="$(dirname "$self_dir")"
db_dirs=("$parent_dir"/comfy-db*/)
if (( ${#db_dirs[@]} != 1 )); then
    echo "Expected exactly one comfy-db directory" >&2
    exit 1
fi
db_dir="${db_dirs[0]}"
comfy_dir="$parent_dir/comfy-ui"
model_dir="$parent_dir/comfy-models"
ext_dir="$parent_dir/comfy-extensions"
output_dir="$parent_dir/comfy-output"
input_dir="$parent_dir/comfy-input"

unset COMFY_VERSION PYTHON_VERSION TORCH_VERSION
source "$db_dir/version-db.env"
[[ ${COMFY_VERSION:-} =~ ^[0-9]+\.[0-9]+\.[0-9]+$ && ${PYTHON_VERSION:-} =~ ^3\.[0-9]+$ && ${TORCH_VERSION:-} =~ ^[0-9]+\.[0-9]+\.[0-9]+\+cu[0-9]+$ ]] || { echo "Invalid version-db.env" >&2; exit 1; }
cuda_tag="${TORCH_VERSION##*+}"
# Require the driver to advertise at least the CUDA version requested by Torch.
if ! command -v nvidia-smi >/dev/null; then
    echo "NVIDIA driver unavailable" >&2; exit 1
fi
driver_info="$(LC_ALL=C nvidia-smi)"
driver_cuda="$(sed -nE 's/.*CUDA Version: ([0-9]+\.[0-9]+).*/\1/p' <<< "$driver_info" | head -n 1)"
cuda_digits="${cuda_tag#cu}"
required_cuda="${cuda_digits:0:-1}.${cuda_digits: -1}"
if [[ -z "$driver_cuda" ]] || ! dpkg --compare-versions "$driver_cuda" ge "$required_cuda"; then
    echo "Driver CUDA ${driver_cuda:-unknown} does not meet required CUDA $required_cuda" >&2
    exit 1
fi
url=https://github.com/Comfy-Org/ComfyUI.git
tag="v$COMFY_VERSION"
git ls-remote --exit-code "$url" "refs/tags/$tag" >/dev/null
if [[ ! -d "$comfy_dir" ]]; then
    git clone --no-checkout "$url" "$comfy_dir"
fi
git -C "$comfy_dir" fetch "$url" "refs/tags/$tag"
commit="$(git -C "$comfy_dir" rev-parse 'FETCH_HEAD^{commit}')"
git -C "$comfy_dir" checkout --detach "$commit"
if [[ -d "$comfy_dir/models" && ! -d "$model_dir" ]]; then
    mv "$comfy_dir/models" "$model_dir"
fi
if [[ -d "$comfy_dir/output" && ! -e "$output_dir" && ! -L "$output_dir" ]]; then
    mv "$comfy_dir/output" "$output_dir"
fi
if [[ -d "$comfy_dir/input" && ! -e "$input_dir" && ! -L "$input_dir" ]]; then
    mv "$comfy_dir/input" "$input_dir"
fi
mkdir -p "$model_dir" "$output_dir" "$input_dir" "$comfy_dir/user/default"
if [[ -d "$comfy_dir/custom_nodes" && ! -e "$ext_dir" && ! -L "$ext_dir" ]]; then
    mv "$comfy_dir/custom_nodes" "$ext_dir"
fi
mkdir -p "$ext_dir"
if ! command -v "python$PYTHON_VERSION" >/dev/null || ! "python$PYTHON_VERSION" -c "import ensurepip" >/dev/null 2>&1; then
    sudo apt update
    sudo apt install -y "python$PYTHON_VERSION" "python$PYTHON_VERSION-venv"
fi
"python$PYTHON_VERSION" -m venv "$comfy_dir/.venv"
py="$comfy_dir/.venv/bin/python"
"$py" -c 'import sys; assert ".".join(map(str, sys.version_info[:2])) == sys.argv[1], "Python version mismatch"' "$PYTHON_VERSION"
"$py" -m pip install --upgrade pip
if ! "$py" - "$TORCH_VERSION" "$required_cuda" <<'PY'
import sys
try:
    import torch
    import torchvision
    import torchaudio
    from importlib.metadata import requires
    from pip._vendor.packaging.requirements import Requirement
    from pip._vendor.packaging.version import Version
    assert torch.__version__ == sys.argv[1]
    assert torch.version.cuda == sys.argv[2]
    for package in ("torchvision", "torchaudio"):
        for entry in requires(package) or ():
            req = Requirement(entry)
            if req.name.lower() == "torch" and (req.marker is None or req.marker.evaluate()):
                assert req.specifier.contains(Version(torch.__version__), prereleases=True)
except Exception:
    sys.exit(1)
PY
then
    "$py" -m pip install --dry-run --ignore-installed --only-binary=:all: "torch==$TORCH_VERSION" torchvision torchaudio --index-url "https://download.pytorch.org/whl/$cuda_tag" || { echo "No compatible Torch wheels for requested Python and CUDA" >&2; exit 1; }
    "$py" -m pip install "torch==$TORCH_VERSION" torchvision torchaudio --index-url "https://download.pytorch.org/whl/$cuda_tag"
fi
printf 'torch==%s\n' "$TORCH_VERSION" > "$comfy_dir/.venv/comfy-constraints.txt"
export PIP_CONSTRAINT="$comfy_dir/.venv/comfy-constraints.txt"
"$py" -m pip install httpx -r "$self_dir/requirements.txt" -r "$comfy_dir/requirements.txt"
if [[ -f "$comfy_dir/manager_requirements.txt" ]]; then
    "$py" -m pip install -r "$comfy_dir/manager_requirements.txt"
fi
"$py" -m pip install opencv-python-headless ultralytics matrix-nio imageio-ffmpeg onnxruntime-gpu PyOpenGL-accelerate scikit-image
"$py" - "$comfy_dir/comfy-extension-paths.yaml" "$ext_dir" <<'PY'
import json
import sys
from pathlib import Path
Path(sys.argv[1]).write_text("managed_extensions:\n  is_default: true\n  custom_nodes: " + json.dumps(sys.argv[2]) + "\n", encoding="utf-8")
PY
"$py" "$self_dir/sync-extensions.py" update
"$py" -m pip check
"$py" - "$TORCH_VERSION" "$required_cuda" <<'PY'
import sys
import torch
if torch.__version__ != sys.argv[1] or torch.version.cuda != sys.argv[2]:
    raise SystemExit("Installed Torch/CUDA version mismatch")
if not torch.cuda.is_available():
    raise SystemExit("Torch cannot access CUDA")
torch.zeros(1, device="cuda").add_(1)
torch.cuda.synchronize()
PY
"$py" "$self_dir/sync-models.py" update
cp "$db_dir/config-db.json" "$comfy_dir/user/default/comfy.settings.json"
