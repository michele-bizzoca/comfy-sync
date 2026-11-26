#!/bin/bash

cd "$(dirname "$(realpath "${BASH_SOURCE[0]}")")/../comfy-ui"

cuda_ver=$(nvidia-smi | sed -nE 's/.*CUDA Version: ([0-9]+)\.([0-9]+).*/\1\2/p')


python3.12 -m venv .venv
sudo chmod +x .venv/bin/python3.12 .venv/bin/python

.venv/bin/python3.12 -m pip install -r ../model-sync/requirements.txt
.venv/bin/python3.12 -m pip install -r ../tokens-db-main/requirements.txt
.venv/bin/python3.12 -m pip install -r ../models-db-main/requirements.txt
.venv/bin/python3.12 -m pip install -r requirements.txt
.venv/bin/python3.12 -m pip install -r manager_requirements.txt
.venv/bin/python3.12 -m pip install opencv-python-headless ultralytics matrix-nio imageio-ffmpeg onnxruntime-gpu PyOpenGL-accelerate scikit-image
.venv/bin/python3.12 -m pip install torch torchvision torchaudio --index-url "https://download.pytorch.org/whl/cu$cuda_ver"
