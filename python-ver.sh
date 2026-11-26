#!/bin/bash

local py
while IFS= read -r py; do
    if "$py" -m pip install --dry-run --ignore-installed --only-binary=:all: \
        --index-url "${1//./}" torch torchvision torchaudio >/dev/null 2>&1; then
        printf '%s\n' "${py#python}"
        return 0
    fi
done < <(compgen -c python3. | awk '/^python3\.[0-9]+$/' | sort -Vru)
return 1
