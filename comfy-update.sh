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

py="$comfy_dir/.venv/bin/python"
[[ -x "$py" && -d "$model_dir" ]] || { echo "Run comfy-setup.sh first" >&2; exit 1; }
export PIP_CONSTRAINT="$comfy_dir/.venv/comfy-constraints.txt"
[[ -f "$PIP_CONSTRAINT" ]] || { echo "Run comfy-setup.sh first" >&2; exit 1; }
"$py" "$self_dir/comfy-ext-sync.py" sync
"$py" "$self_dir/comfy-mod-sync.py" sync
mkdir -p "$comfy_dir/user/default"
cp "$db_dir/config-db.json" "$comfy_dir/user/default/comfy.settings.json"
