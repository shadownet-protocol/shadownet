#!/usr/bin/env bash
# Verifies that schemas mirrored from the specs repo are still in sync.
#
# Each pair below is "<local copy> <upstream path>". When a mirror drifts,
# this script prints a unified diff and exits non-zero — CI fails the build.
set -euo pipefail

# Default assumes shadownet-specs is cloned alongside the shadownet
# monorepo (i.e. CWD = <workspace>/shadownet/core/ and the specs repo
# lives at <workspace>/shadownet-specs/). CI overrides via
# SHADOWNET_SPECS_DIR.
SPECS_REPO="${SHADOWNET_SPECS_DIR:-../../shadownet-specs}"

if [[ ! -d "$SPECS_REPO" ]]; then
  echo "warn: specs repo not found at $SPECS_REPO; skipping schema-mirror check" >&2
  exit 0
fi

declare -a pairs=()
# v0.1 mirrored api/messages/envelope.schema.json from the upstream spec; the
# Phase 1 cut removed that mirror. Phase 6 will re-introduce mirrors here if
# any of the v0.2 wire artefacts end up duplicated in core/ for build-time
# validation. Until then this loop is intentionally empty.

fail=0
for pair in "${pairs[@]+"${pairs[@]}"}"; do
  local_path="${pair%% *}"
  upstream_path="${pair##* }"
  if ! diff -u \
      <(jq -S 'del(.description)' "$SPECS_REPO/$upstream_path") \
      <(jq -S 'del(.description)' "$local_path"); then
    echo "drift: $local_path differs from $SPECS_REPO/$upstream_path" >&2
    fail=1
  fi
done

exit "$fail"
