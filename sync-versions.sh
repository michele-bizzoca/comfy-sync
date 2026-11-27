#!/usr/bin/env bash
# Verify or record runtime version pins.
set -euo pipefail

# Locate the repository and its sibling ComfyUI virtual environment.
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
python="$script_dir/../comfy-ui/.venv/bin/python"

# Replace this wrapper with Python, forwarding all arguments unchanged.
exec "$python" "$script_dir/python/sync-versions.py" "$@"
