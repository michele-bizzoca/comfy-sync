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

set -a; source "$db_dir/token-db.env"; set +a
exec "$comfy_dir/.venv/bin/python" "$comfy_dir/main.py" --preview-method auto \
    --enable-manager --listen 0.0.0.0 --port "${1:-8188}" \
    --verbose DETAIL --models-directory "$model_dir"
