#!/bin/bash

shopt -s nullglob
set -euo pipefail

printf '%s\n' 'nameserver 1.1.1.1' 'nameserver 1.0.0.1' > /etc/resolv.conf

self_dir="$(dirname "$(realpath "${BASH_SOURCE[0]}")")"
parent_dir="$self_dir/../"
db_dirs=("$parent_dir"/comfy-db*/); db_dir="${db_dirs[0]-}"
comfy_dir="$parent_dir/comfy-ui/"
model_dir="$parent_dir/comfy-models/"

if (( ${#db_dirs[@]} > 1 )); then
    echo "Multiple comfy-db directories found" >&2
    exit 1
elif [[ ${#db_dirs[@]} == 0 ]]; then
    echo "No comfy-db directories found" >&2
    exit 1
fi

if [[ -d "$comfy_dir/models" && ! -d "$model_dir" ]]; then
    mv "$comfy_dir/models" "$model_dir"
fi

cuda_ver="$("$self_dir/scripts/cuda-ver.sh")"
python_ver="$("$self_dir/scripts/python-ver.sh" "$cuda_ver")"

sudo apt update && sudo apt install "python$python_ver" "python$python_ver-venv"

"python$python_ver" -m venv "$comfy_dir/.venv"

.venv/bin/python -m pip install -r "$self_dir/requirements.txt"
$comfy_dir/.venv/bin/python -m pip install -r "$comfy_dir/requirements.txt"
$comfy_dir/.venv/bin/python -m pip install -r "$comfy_dir/manager_requirements.txt"
$comfy_dir/.venv/bin/python -m pip install opencv-python-headless \
    ultralytics matrix-nio imageio-ffmpeg onnxruntime-gpu         \
    PyOpenGL-accelerate scikit-image
$comfy_dir/.venv/bin/python -m pip install torch torchvision torchaudio \
    --index-url "https://download.pytorch.org/whl/cu${cuda_ver//./}"

$comfy_dir/.venv/bin/python "$self_dir/comfy-ext-sync.py" sync
$comfy_dir/.venv/bin/python "$self_dir/comfy-mod-sync.py" sync
cp "$db_dir/config-db.json" "$comfy_dir/user/default/comfy.settings.json"
