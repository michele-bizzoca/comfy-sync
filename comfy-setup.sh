#!/bin/bash

shopt -s nullglob
set -euo pipefail

printf '%s\n' 'nameserver 1.1.1.1' 'nameserver 1.0.0.1' > /etc/resolv.conf

self_dir="$(dirname "$(realpath "${BASH_SOURCE[0]}")")"
parent_dir="$self_dir/../"
comfy_dir="$parent_dir/comfy-ui/"
model_dir="$parent_dir/comfy-models/"

cd "$comfy_dir"

cuda_ver="$("$self_dir/scripts/cuda-ver.sh")"
python_ver="$("$self_dir/scripts/python-ver.sh" "$cuda_ver")"

sudo apt update && sudo apt install "python$python_ver" "python$python_ver-venv"

"python$python_ver" -m venv .venv

.venv/bin/python -m pip install -r "$self_dir/requirements.txt"
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install -r manager_requirements.txt
.venv/bin/python -m pip install opencv-python-headless ultralytics \
    matrix-nio imageio-ffmpeg onnxruntime-gpu PyOpenGL-accelerate  \
    scikit-image
.venv/bin/python -m pip install torch torchvision torchaudio \
    --index-url "https://download.pytorch.org/whl/cu${cuda_ver//./}"

for extension in "$comfy_dir"/custom_nodes/*/; do
    if [[ -f "$extension/requirements.txt" ]]; then
        .venv/bin/python -m pip install -r "$extension/requirements.txt"
    fi
done
