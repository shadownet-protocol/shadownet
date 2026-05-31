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

# Verify register()'s other surface landed: all four skills materialized into
# Hermes's own skills dir (accessible to the agent), not just the MCP adapter.
gw=$("${COMPOSE[@]}" ps -q gateway)
for s in shadownet-setup shadownet-inbox shadownet-reach-out shadownet-coordinate; do
  docker exec "$gw" test -f "/opt/data/skills/shadownet/${s}/SKILL.md" \
    || { echo "MISSING materialized skill: ${s}" >&2; exit 1; }
done
echo "verified: 4 shadownet skills materialized under /opt/data/skills/shadownet/"