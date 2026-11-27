#!/usr/bin/env bash
# Dispatch lifecycle and synchronization commands to their Python implementations.
set -euo pipefail

usage() {
    printf '%s\n' 'Usage: comfy-sync.sh COMMAND [ARGUMENTS...]' \
        'Commands: setup update record start sync-models sync-extensions sync-versions sync-config sync-tokens'
}

# Restrict dispatch to known entry points rather than accepting arbitrary paths.
case "${1:-}" in
    -h|--help)
        usage
        exit 0
        ;;
    setup|update|record|start|sync-models|sync-extensions|sync-versions|sync-config|sync-tokens)
        command="$1"
        shift
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac

# Resolve the actual script location, including invocation through a symlink.
script_dir="$(dirname -- "$(realpath -- "${BASH_SOURCE[0]}")")"
python="$script_dir/../comfy-ui/.venv/bin/python"

# Setup alone can bootstrap a missing managed virtual environment.
if [[ "$command" == setup && ! -x "$python" ]]; then
    python=python3
fi

# Forward every remaining argument and preserve the child process exit status.
exec "$python" "$script_dir/python/$command.py" "$@"
