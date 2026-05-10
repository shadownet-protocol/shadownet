## Summary

<!-- One or two sentences. What does this PR change, and why? -->

## Area

<!-- Tick the area(s) this PR touches. Don't tick more than is true. -->

- [ ] `core/` — Go SDK / reference servers / CLI
- [ ] `core/pgstore/` — Postgres backend submodule
- [ ] `python-sdk/` — Python SDK
- [ ] `conformance/` — interop test suite / fixtures / Docker action
- [ ] `integrations/` — host-agent plugins (Claude Code / Hermes Agent / OpenClaw / skills)
- [ ] `examples/` — runnable end-to-end examples
- [ ] CI / release workflows
- [ ] Top-level docs (`README`, `CONTRIBUTING`, `MIGRATION`, …)
- [ ] Other: <!-- describe -->

## Linked issue

<!-- Closes #NNN, refs #MMM. Open one before sending non-trivial PRs. -->

## Changelog

<!--
For SDK changes, the matching subtree CHANGELOG.md must have an entry under
`## [Unreleased]` (or under the version this PR targets). Quote the entry
line(s) here, or note "n/a — non-shipping change".
-->

## Pre-merge gate

<!-- Tick the local gate for the subtree you touched (CONTRIBUTING.md). -->

- [ ] Go: `go test -race -count=1 ./...`, `go vet`, `gofumpt`, `staticcheck`, `golangci-lint`, `govulncheck` all pass.
- [ ] Python (`python-sdk/` and/or `conformance/`): `uv run ruff check .`, `ruff format --check .`, `mypy --strict`, `pytest` all pass.
- [ ] Integrations (`integrations/plugins/openclaw/`): `pnpm run lint && pnpm run build && pnpm run test` pass.
- [ ] Conformance impact considered: wire-level changes need a parallel `shadownet-specs` PR. If the conformance suite reports a failure, the implementation is wrong, not the test.

## Notes for reviewers

<!-- Anything reviewers should look at first, anything you're unsure about,
     screenshots if UI, perf numbers if perf, etc. -->
