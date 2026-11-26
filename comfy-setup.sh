#!/bin/bash

set -euo pipefail

printf '%s\n' 'nameserver 1.1.1.1' 'nameserver 1.0.0.1' > /etc/resolv.conf

self_dir="$(dirname "$(realpath "${BASH_SOURCE[0]}")")"
parent_dir="$self_dir/../"
comfy_dir="$parent_dir/comfy-ui/"
models_dir="$parent_dir/comfy-models/"
db_dir="$parent_dir/comfy-db/"

cd "$parent_dir"

cuda_ver="$(scripts/cuda-ver.sh)"; python_ver="$(scripts/python-ver.sh "$cuda_ver")"

sudo apt update && sudo apt install "python$python_ver"

cd "$parent_dir/comfy-ui"

python$python_ver -m venv .venv; sudo chmod +x .venv/bin/python

.venv/bin/python -m pip install -r ../model-sync/requirements.txt
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install -r manager_requirements.txt
.venv/bin/python -m pip install opencv-python-headless ultralytics \
    matrix-nio imageio-ffmpeg onnxruntime-gpu PyOpenGL-accelerate  \
    scikit-image
.venv/bin/python -m pip install torch torchvision torchaudio \
    --index-url "https://download.pytorch.org/whl/cu$cuda_ver"
