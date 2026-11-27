#!/usr/bin/env bash
set -euo pipefail
self_dir="$(dirname "$(realpath "${BASH_SOURCE[0]}")")"
py="$self_dir/../comfy-ui/.venv/bin/python"
exec "$py" "$self_dir/record.py" "$@"
