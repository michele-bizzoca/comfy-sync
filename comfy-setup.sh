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

unset COMFY_VERSION PYTHON_VERSION TORCH_VERSION
source "$db_dir/version-db.env"
[[ ${COMFY_VERSION:-} =~ ^[0-9]+\.[0-9]+\.[0-9]+$ && ${PYTHON_VERSION:-} =~ ^3\.[0-9]+$ && ${TORCH_VERSION:-} =~ ^[0-9]+\.[0-9]+\.[0-9]+\+cu[0-9]+$ ]] || { echo "Invalid version-db.env" >&2; exit 1; }
cuda_tag="${TORCH_VERSION##*+}"
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
mkdir -p "$model_dir" "$comfy_dir/user/default"
if ! command -v "python$PYTHON_VERSION" >/dev/null; then
    sudo apt update
    sudo apt install -y "python$PYTHON_VERSION" "python$PYTHON_VERSION-venv"
fi
"python$PYTHON_VERSION" -m venv "$comfy_dir/.venv"
py="$comfy_dir/.venv/bin/python"
"$py" -m pip install "torch==$TORCH_VERSION" torchvision torchaudio --index-url "https://download.pytorch.org/whl/$cuda_tag"
printf 'torch==%s\n' "$TORCH_VERSION" > "$comfy_dir/.venv/comfy-constraints.txt"
export PIP_CONSTRAINT="$comfy_dir/.venv/comfy-constraints.txt"
"$py" -m pip install -r "$self_dir/requirements.txt" -r "$comfy_dir/requirements.txt"
if [[ -f "$comfy_dir/manager_requirements.txt" ]]; then
    "$py" -m pip install -r "$comfy_dir/manager_requirements.txt"
fi
"$py" -m pip install opencv-python-headless ultralytics matrix-nio imageio-ffmpeg onnxruntime-gpu PyOpenGL-accelerate scikit-image
"$py" "$self_dir/comfy-ext-sync.py" sync
"$py" "$self_dir/comfy-mod-sync.py" sync
cp "$db_dir/config-db.json" "$comfy_dir/user/default/comfy.settings.json"
