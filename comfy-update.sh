#!/bin/bash

shopt -s nullglob
set -euo pipefail

printf '%s\n' 'nameserver 1.1.1.1' 'nameserver 1.0.0.1' > /etc/resolv.conf

self_dir="$(dirname "$(realpath "${BASH_SOURCE[0]}")")"
parent_dir="$self_dir/../"
db_dirs=("$parent_dir"/comfy-db*/); db_dir="${db_dirs[0]}"
comfy_dir="$parent_dir/comfy-ui/"
model_dir="$parent_dir/comfy-models/"

$comfy_dir/.venv/bin/python "$self_dir/comfy-ext-sync.py" sync
$comfy_dir/.venv/bin/python "$self_dir/comfy-mod-sync.py" sync
cp "$db_dir/config-db.json" "$comfy_dir/user/default/comfy.settings.json"

