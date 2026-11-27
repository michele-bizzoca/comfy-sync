#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
python="$script_dir/../comfy-ui/.venv/bin/python"

# The first setup run must also work before the virtual environment exists.
if [[ ! -x "$python" ]]; then
    python=python3
fi

exec "$python" "$script_dir/python/setup.py" "$@"
