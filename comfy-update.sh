#!/bin/bash

shopt -s nullglob
set -uo pipefail
self_dir="$(dirname "$(realpath "${BASH_SOURCE[0]}")")" || exit 1
parent_dir="$(dirname "$self_dir")"
db_dirs=("$parent_dir"/comfy-db*/)
if (( ${#db_dirs[@]} != 1 )); then
    echo "Expected exactly one comfy-db directory" >&2
    exit 1
fi
db_dir="${db_dirs[0]}"
comfy_dir="$parent_dir/comfy-ui"
py="$comfy_dir/.venv/bin/python"
status=0

sync_extensions() {
    export PIP_CONSTRAINT="$comfy_dir/.venv/comfy-constraints.txt"
    [[ -f "$PIP_CONSTRAINT" ]] || { echo "Missing pip constraints; run setup first" >&2; return 1; }
    "$py" "$self_dir/comfy-ext-sync.py" sync
}

sync_config() {
    mkdir -p "$comfy_dir/user/default" &&
        cp "$db_dir/config-db.json" "$comfy_dir/user/default/comfy.settings.json"
}

check_versions() (
    unset COMFY_VERSION PYTHON_VERSION TORCH_VERSION
    source "$db_dir/version-db.env" || exit 1
    [[ ${COMFY_VERSION:-} =~ ^[0-9]+\.[0-9]+\.[0-9]+$ && ${PYTHON_VERSION:-} =~ ^3\.[0-9]+$ && ${TORCH_VERSION:-} =~ ^[0-9]+\.[0-9]+\.[0-9]+\+cu[0-9]+$ ]] || { echo "Invalid version-db.env" >&2; exit 1; }
    local_status=0
    actual="$(git -C "$comfy_dir" rev-parse HEAD)" || actual=unknown
    refs="$(git ls-remote https://github.com/Comfy-Org/ComfyUI.git "refs/tags/v$COMFY_VERSION" "refs/tags/v$COMFY_VERSION^{}")" || refs=""
    expected="$(awk 'END {print $1}' <<< "$refs")"
    if [[ -z "$expected" || "$actual" != "$expected" ]]; then
        printf 'COMFYUI: INSTALLED COMMIT %s; REQUIRED v%s (%s)\n' "$actual" "$COMFY_VERSION" "${expected:-unverified}" >&2
        local_status=1
    fi
    actual="$("$py" -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')" || actual=unknown
    if [[ "$actual" != "$PYTHON_VERSION" ]]; then
        printf 'PYTHON: INSTALLED %s; REQUIRED %s\n' "$actual" "$PYTHON_VERSION" >&2
        local_status=1
    fi
    actual="$("$py" -c 'import torch; print(torch.__version__)')" || actual=unknown
    if [[ "$actual" != "$TORCH_VERSION" ]]; then
        printf 'TORCH: INSTALLED %s; REQUIRED %s\n' "$actual" "$TORCH_VERSION" >&2
        local_status=1
    fi
    exit "$local_status"
)

sync_extensions || { echo "Extension sync failed" >&2; status=1; }
"$py" "$self_dir/comfy-mod-sync.py" sync || { echo "Model sync failed" >&2; status=1; }
sync_config || { echo "Configuration sync failed" >&2; status=1; }
if ! check_versions; then
    printf '\n============================================================\nERROR: VERSION MISMATCH OR VERIFICATION FAILURE\nCHECK COMFYUI, PYTHON AND TORCH AGAINST VERSION-DB.\nNO AUTOMATIC VERSION UPDATE HAS BEEN PERFORMED.\n============================================================\n' >&2
    status=1
fi
exit "$status"
