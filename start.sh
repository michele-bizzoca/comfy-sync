#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
python="$script_dir/../comfy-ui/.venv/bin/python"

exec "$python" "$script_dir/python/start.py" "$@"
