#!/bin/bash

shopt -s nullglob
cd "$(dirname "$(realpath "${BASH_SOURCE[0]}")")/../"

if [[ ! -d "model-sync" ]]; then
    git clone https://github.com/michele-bizzoca/model-sync
fi

if [[ ! -d "models-db-main" ]]; then
    git clone https://github.com/michele-bizzoca/models-db-main
fi

if [[ ! -d "tokens-db-main" ]]; then
    git clone https://github.com/michele-bizzoca/tokens-db-main
fi

if [[ -d "comfy-ui/models" && ! -d "models" ]]; then
    mv comfy-ui/models models
fi

set -a; source "tokens-db-main/tokens-db-main.env"; set +a

comfy-ui/.venv/bin/python3.12 /model-sync/model-sync.py sync

cd comfy-ui
exec .venv/bin/python3.12  main.py --preview-method auto \
     --enable-manager --listen 0.0.0.0 --port 8188 --verbose DETAIL \
     --models-directory ../models
