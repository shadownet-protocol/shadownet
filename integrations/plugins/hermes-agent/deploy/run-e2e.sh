#!/usr/bin/env bash
# Bring up the Hermes Tier-1 harness, run the pytest driver, tear down.
# Everything runs in Docker on a zero-egress network; nothing touches the host.
set -euo pipefail
cd "$(dirname "$0")"

COMPOSE=(docker compose -f compose.hermes.yml --profile e2e)

cleanup() { "${COMPOSE[@]}" down --remove-orphans >/dev/null 2>&1 || true; }
trap cleanup EXIT

"${COMPOSE[@]}" build
"${COMPOSE[@]}" up -d --wait --wait-timeout 300 seed shadownet-mock stub-llm gateway
"${COMPOSE[@]}" run --rm driver