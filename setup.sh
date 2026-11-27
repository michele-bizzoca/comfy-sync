#!/usr/bin/env bash
set -euo pipefail
self_dir="$(dirname "$(realpath "${BASH_SOURCE[0]}")")"
py="$self_dir/../comfy-ui/.venv/bin/python"
if [[ ! -x "$py" ]]; then py=python3; fi
exec "$py" "$self_dir/setup.py" "$@"
