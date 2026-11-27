#!/usr/bin/env bash
# Record the current installation state in the databases.
set -euo pipefail

# Locate the repository and its sibling ComfyUI virtual environment.
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
python="$script_dir/../comfy-ui/.venv/bin/python"

# Replace this wrapper with Python, forwarding all arguments unchanged.
exec "$python" "$script_dir/python/record.py" "$@"
