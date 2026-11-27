#!/bin/bash

shopt -s nullglob
set -euo pipefail

set -a; source "$db_dir/token-db.env"; set +a
exec $comfy_dir/.venv/bin/python "$comfy_dir/main.py" --preview-method auto      \
     --enable-manager --listen 0.0.0.0 --port "${1:-8188}" \
     --verbose DETAIL --models-directory "$model_dir"


