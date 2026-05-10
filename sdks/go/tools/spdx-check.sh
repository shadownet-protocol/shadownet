#!/usr/bin/env bash
# Fail if any tracked *.go file lacks an SPDX-License-Identifier line.
set -euo pipefail

missing=()
while IFS= read -r -d '' f; do
  if ! head -3 "$f" | grep -q '^// SPDX-License-Identifier: MIT$'; then
    missing+=("$f")
  fi
done < <(find . \
  -path ./.git -prune -o \
  -name '*.go' -type f -print0)

if (( ${#missing[@]} > 0 )); then
  printf 'missing SPDX header in:\n' >&2
  printf '  %s\n' "${missing[@]}" >&2
  exit 1
fi
