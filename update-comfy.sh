#!/bin/bash

set -euo pipefail

printf '%s\n' 'nameserver 1.1.1.1' 'nameserver 1.0.0.1' > /etc/resolv.conf

sh_dir="$(dirname "$(realpath "${BASH_SOURCE[0]}")")"
cd "$sh_dir/../comfy-ui"

cuda_ver="$($sh_dir/cuda-version.sh)"
python_ver="$($sh_dir/python-version.sh $cuda_ver)"

sudo apt update
sudo apt install python$python_ver

python$python_ver -m venv .venv
sudo chmod +x .venv/bin/python3.12 .venv/bin/python

.venv/bin/python -m pip install -r ../model-sync/requirements.txt
.venv/bin/python -m pip install -r ../tokens-db-main/requirements.txt
.venv/bin/python -m pip install -r ../models-db-main/requirements.txt
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install -r manager_requirements.txt
.venv/bin/python -m pip install opencv-python-headless ultralytics matrix-nio imageio-ffmpeg onnxruntime-gpu PyOpenGL-accelerate scikit-image
.venv/bin/python -m pip install torch torchvision torchaudio --index-url "https://download.pytorch.org/whl/cu$cuda_ver"
