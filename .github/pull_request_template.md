## Summary

<!-- One or two sentences. What does this PR change, and why? -->

## Area

<!-- Tick the area(s) this PR touches. Don't tick more than is true. -->

- [ ] `sdks/go/` — Go SDK / reference servers / CLI
- [ ] `sdks/go/pgstore/` — Postgres backend submodule
- [ ] `sdks/py/` — Python SDK
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
- [ ] Python: `uv run ruff check .`, `ruff format --check .`, `mypy --strict`, `pytest` all pass.
- [ ] Conformance impact considered (wire-level changes need a parallel `shadownet-specs` PR).

## Notes for reviewers

<!-- Anything reviewers should look at first, anything you're unsure about,
     screenshots if UI, perf numbers if perf, etc. -->
