#!/bin/bash

index="https://download.pytorch.org/whl/cu${1//./}"

while IFS= read -r py; do
    if "$py" -m pip install --dry-run --ignore-installed --only-binary=:all: \
        --index-url "$index" torch torchvision torchaudio >/dev/null 2>&1; then
        printf '%s\n' "${py#python}"
        exit 0
    fi
done < <(compgen -c python3. | awk '/^python3\.[0-9]+$/' | sort -Vru)
exit 1
