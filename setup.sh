#!/usr/bin/env bash
# Bootstrap the ComfyUI installation and its managed dependencies.
set -euo pipefail

# Locate the repository and its sibling ComfyUI virtual environment.
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
python="$script_dir/../comfy-ui/.venv/bin/python"

# The first setup run must also work before the virtual environment exists.
if [[ ! -x "$python" ]]; then
    python=python3
fi

# Replace this wrapper with Python, forwarding all arguments unchanged.
exec "$python" "$script_dir/python/setup.py" "$@"
