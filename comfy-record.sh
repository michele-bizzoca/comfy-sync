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
py="$comfy_dir/.venv/bin/python"
config="$comfy_dir/user/default/comfy.settings.json"

# Detect versions before changing the catalog. Setup requires an exact release tag.
[[ -f "$config" ]] || { echo "Missing ComfyUI settings: $config" >&2; exit 1; }
tags="$(git -C "$comfy_dir" tag --points-at HEAD)"
mapfile -t versions < <(printf '%s\n' "$tags" | sed -nE 's/^v([0-9]+\.[0-9]+\.[0-9]+)$/\1/p')
if (( ${#versions[@]} != 1 )); then
    echo "Expected exactly one ComfyUI release tag at HEAD" >&2
    exit 1
fi
comfy_version="${versions[0]}"
python_version="$("$py" -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')"
torch_version="$("$py" -c 'import torch; print(torch.__version__)')"
[[ $python_version =~ ^3\.[0-9]+$ && $torch_version =~ ^[0-9]+\.[0-9]+\.[0-9]+\+cu[0-9]+$ ]] || {
    echo "Installed Python or Torch version is incompatible with version-db.env" >&2
    exit 1
}

# Stage all four files before publishing them.
stage="$(mktemp -d "$db_dir/.record.XXXXXXXX")"
trap 'rm -rf -- "$stage"' EXIT
for name in model-db.xlsx extension-db.xlsx; do
    if [[ -f "$db_dir/$name" ]]; then
        cp -- "$db_dir/$name" "$stage/$name"
    fi
done
"$py" "$self_dir/comfy-sync-extensions.py" record --database "$stage/extension-db.xlsx"
"$py" "$self_dir/comfy-sync-models.py" record --database "$stage/model-db.xlsx"
cp -- "$config" "$stage/config-db.json"
printf 'COMFY_VERSION=%s\nPYTHON_VERSION=%s\nTORCH_VERSION=%s\n' \
    "$comfy_version" "$python_version" "$torch_version" > "$stage/version-db.env"
for name in extension-db.xlsx model-db.xlsx config-db.json version-db.env; do
    mv -f -- "$stage/$name" "$db_dir/$name"
done
echo "Recorded ComfyUI v$comfy_version, Python $python_version, Torch $torch_version"
