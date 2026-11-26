#!/bin/bash

shopt -s nullglob
set -euo pipefail

printf '%s\n' 'nameserver 1.1.1.1' 'nameserver 1.0.0.1' > /etc/resolv.conf

self_dir="$(dirname "$(realpath "${BASH_SOURCE[0]}")")"
parent_dir="$self_dir/../"
t=($parent_dir/comfy-db*/); db_dir={t[0]-}
comfy_dir="$parent_dir/comfy-ui/"
model_dir="$parent_dir/comfy-models/"

cd "$parent_dir"

if [[ ! "$db_dir" ]]; then

    gh auth status --active --hostname github.com >/dev/null 2>&1 ||
        gh auth login --hostname github.com --git-protocol https --web

    git pull https://github.com/michele-bizzoca/comfy-db-main
fi

cd "$comfy_dir"

if [[ -d "$comfy_dir/models" && ! -d "$model_dir" ]]; then
    mv "$comfy_dir/models" "$model_dir"
fi

set -a; source "$db_dir/token-db.env"; set +a
.venv/bin/python "$self_dir/comfy-mod-sync.py" sync
.venv/bin/python "$self_dir/comfy-ext-sync.py" sync

exec .venv/bin/python main.py --preview-method auto      \
     --enable-manager --listen 0.0.0.0 --port ${1:-8188} \
     --verbose DETAIL --models-directory "$models_dir"


