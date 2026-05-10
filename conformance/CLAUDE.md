# CLAUDE.md

Development guide for `shadownet-conformance`. Project information lives in
[README.md](./README.md). The protocol's normative source is
[`../shadownet-specs/`](../shadownet-specs/) — RFCs win, every time.

This file documents *how* code in this repo is written, structured, and
reviewed. Read it before opening a PR.

## Audience and posture

This repo is the contract that catches **two implementations disagreeing about
the wire**. Other implementations (`shadownet-go`, `shadownet-py`,
`shadownet-ts`, `hermes-social`) gate their CI on this suite. Code quality is
judged by senior Python developers and by anyone debugging an interop failure
at 2am. Optimize for: legibility, byte-level precision, no silent fallbacks,
no fixture drift.

When the spec is silent or ambiguous, **ask** (or open an issue against
`shadownet-specs`). Do not invent semantics — a conformance suite that invents
behavior is worse than no suite.

## Non-negotiables

| Decision        | Value                                                      |
| --------------- | ---------------------------------------------------------- |
| Python          | 3.12+                                                      |
| Package manager | [`uv`](https://docs.astral.sh/uv/)                         |
| Concurrency     | Async (`asyncio` + `httpx.AsyncClient`)                    |
| HTTP client     | `httpx`                                                    |
| Test runner     | `pytest` + `pytest-asyncio` (mode = auto)                  |
| Schema validation | `jsonschema` against `../shadownet-specs/schemas/`       |
| Crypto / JWT    | via `shadownet-py` SDK (Ed25519, EdDSA JWS, DID, VC, VP)   |
| Lint / format   | `ruff` (lint + format)                                     |
| Type checker    | `mypy --strict` on `src/`                                  |
| Logging         | Stdlib `logging`. One logger per module. No `print`.       |
| License         | MIT                                                        |
| Public typing   | All public APIs fully type-annotated. Ship `py.typed`.     |

## Scope

`shadownet-conformance` is a **wire-level interop test suite**. It runs
against any URL implementing one of the Shadownet conformance classes
(SCA, SNS, Sidecar) and asserts that the responses match the RFCs *to
the byte* where the spec is byte-determined, and to the documented
semantics where it isn't.

What we ship:

- **`pytest` test packages** for SCA (RFC-0004), SNS (RFC-0005),
  Sidecar (RFC-0006 + RFC-0007), and predicate evaluation
  (RFC-0004 §Required-level predicates).
- **An async CLI** (`shadownet-conformance`) that takes `--target` flags,
  configures pytest, and emits JUnit XML + an RFC-keyed JSON report
  + GitHub Actions step-summary markdown.
- **Canonical fixtures** under `fixtures/`: deterministic Ed25519
  keypairs from fixed seeds, credential JWTs, freshness proofs, VPs,
  SNS records, status-list credentials, predicate eval pairs, and
  malformed inputs paired with their expected error code.
- **A fixture regeneration CLI** (`shadownet-conformance-fixtures regen`)
  that re-derives every fixture from the seed manifest and **refuses
  to write** unless `shadownet-py` and `shadownet-go` (invoked as
  subprocess) produce byte-identical output. This is the safety net.
- **An in-process A2A test peer** used by Sidecar tests as the other
  side of the handshake.
- **A Docker image** (`ghcr.io/shadownet-protocol/conformance`) and
  **GitHub Action** wrapping it.

### Round-trip coverage (`--peer-target`)

The runner accepts a second URL per role via `--peer-target` (e.g.
`--target sca=URL_A --peer-target sca=URL_B`). Round-trip tests
exercise: issue with A → verify with B, and the symmetric path. The
flag is uniformly accepted for any role so the surface is stable.

At v0.1 there are two SCA impls (`shadownet-go`, `shadownet-py`) and
two SNS impls (same), so SCA and SNS round-trip tests run when both
targets are configured. There is **one** Sidecar impl (`hermes-social`),
so Sidecar round-trip is **degenerate** at v0.1 — those tests are
registered but skip with a single-line log
(`sidecar round-trip skipped: only one v0.1 Sidecar impl exists`)
until a second Sidecar impl lands. v0.2 lights them up automatically;
no flag redesign required.

What we do **not** ship:

- An SCA, SNS, or Sidecar runtime — those live in `shadownet-go` and
  `hermes-social`.
- Storage, persistence, or any state outside a single test run.
- A library API. The package is consumed via the CLI; importable names
  inside `shadownet_conformance.*` are not semver-stable.
- Conformance for unreleased RFCs. Each conformance test cites the RFC
  + section it asserts; tests for draft-only behavior live behind a
  `@pytest.mark.draft` marker and are skipped by default.

## Fixture discipline (the safety net)

The hardest thing this suite has to get right is *not* the tests — it's
the fixtures. A fixture is the protocol's empirical contract: a credential
JWT that *is* what an `L2`-from-cloud-SCA looks like on the wire. If a
fixture is wrong, every implementation is wrong-by-our-test.

Two rules:

1. **Fixtures are committed.** Tests assert byte-for-byte against
   committed JSON / JWT files. No regeneration during a test run.
2. **Fixtures are reproducibly regenerable.** A single command rebuilds
   every fixture from `fixtures/seeds.toml`. The command refuses to
   overwrite a committed fixture unless **both** `shadownet-py` and
   `shadownet-go` produce identical output for that fixture's
   inputs. The cross-SDK check is the protection against an SDK bug
   silently encoding into a "canonical" fixture.

The seed manifest, the regen script, and the cross-check runner all live
under `fixtures/_regen/` and are reviewed with the fixtures themselves.
Changing a seed is a deliberate, reviewed event.

## Repository layout

`src/`-layout, single distribution package `shadownet_conformance`.

```
shadownet-conformance/
├── src/shadownet_conformance/
│   ├── __init__.py            re-exports nothing public; entry point lives in cli.py
│   ├── _version.py            single source of truth for the package version
│   ├── py.typed
│   ├── cli.py                 argparse entry: `shadownet-conformance`
│   ├── config.py              parsed --target/env config (Pydantic; no I/O)
│   ├── reporters/
│   │   ├── junit.py           JUnit XML (pytest-native)
│   │   ├── rfc_json.py        RFC-keyed JSON report (pytest plugin)
│   │   └── gha_summary.py     $GITHUB_STEP_SUMMARY markdown
│   ├── fixtures.py            loader for fixtures/ from a runtime path
│   ├── http.py                shared async client factory + retry policy
│   ├── peer/
│   │   ├── __init__.py
│   │   ├── server.py          in-process A2A test peer (FastAPI/Starlette ASGI)
│   │   └── identity.py        the peer's canonical DID + credential
│   └── _markers.py            shared pytest markers (rfc, draft, network)
├── tests/
│   ├── conftest.py            target wiring, fixtures, markers
│   ├── sca/                   per-endpoint tests for any SCA URL (RFC-0004)
│   ├── sns/                   per-endpoint tests for any SNS URL (RFC-0005)
│   ├── sidecar/               A2A handshake + MCP tool tests (RFC-0006/0007)
│   ├── predicate/             pure-logic tests (RFC-0004 §Predicate)
│   └── e2e/                   round-trip / multi-target scenarios (opt-in)
├── fixtures/
│   ├── README.md              fixture inventory + how to regenerate
│   ├── seeds.toml             versioned seed manifest
│   ├── keys/                  derived Ed25519 keypairs (JWK + raw)
│   ├── credentials/           canonical SubjectCredential JWTs (one per level)
│   ├── presentations/         canonical VP JWTs
│   ├── sns_records/           signed SNS record JWTs
│   ├── status_lists/          BitstringStatusList VCs
│   ├── predicates/            (predicate, presentation, expected_bool) triples
│   ├── malformed/             negative inputs paired with expected error_code
│   └── _regen/                regen script + cross-check runner (NOT shipped)
├── action/
│   ├── action.yml             GitHub Action descriptor (Docker action)
│   └── Dockerfile             ghcr.io/shadownet-protocol/conformance image
├── pyproject.toml
├── uv.lock
├── README.md
├── CHANGELOG.md
├── CLAUDE.md
└── LICENSE                    MIT
```

Layout rules:

- Tests live under `tests/`; they are not part of the published wheel.
- `src/shadownet_conformance/` is the published wheel. Its public surface
  is the `shadownet-conformance` CLI entry point, nothing else. Internal
  module names are not semver-promised; do not document them as API.
- Optional integrations (e.g. extra reporters, MCP transports) live behind
  extras in `pyproject.toml`. Lazy-import at the call site.
- One concept per module. If a file grows past ~400 lines, split it.

## Coding conventions

### General

- **Type hints everywhere.** `from __future__ import annotations`. Modern
  syntax (`list[str]`, `X | None`).
- **Pydantic v2** for any model that crosses an I/O boundary (CLI config,
  RFC-keyed JSON report). Plain dataclasses for purely-internal value
  objects.
- **No global mutable state.** Test peer state lives on an instance owned
  by the conftest fixture.
- **No silent fallbacks.** A test that cannot determine pass/fail
  (e.g. status list unfetchable, target unreachable) MUST `pytest.fail`
  with a message naming the RFC requirement and the network failure —
  never `pytest.skip` to hide it. Skips are reserved for explicit
  preconditions (target not configured, draft RFC, opt-in marker).
- **Errors are typed.** One root `ConformanceError`; subclasses for the
  failure modes a test runner cares about (`TargetUnreachable`,
  `FixtureMissing`, `WireMismatch`). Do not raise bare `Exception`.

### Async

- Public CLI entry is `asyncio.run(...)`-driven. All HTTP I/O is async.
- Each test that performs HTTP receives an `httpx.AsyncClient` from a
  fixture. Tests do not construct clients themselves.
- No blocking calls inside `async def`. Crypto via `shadownet-py` is
  inline (Ed25519 sign/verify is microseconds — no thread pool).

### Naming

- Module names: short, lowercase. Test files are `test_<rfc>_<section>.py`
  (e.g. `tests/sca/test_0004_issuance.py`).
- Test function names mirror the RFC requirement they assert
  (`test_csr_aud_mismatch_returns_csr_invalid`). One MUST per test.

### Spec citations

- Every test module begins with a single-line `# RFC-XXXX §<section>`
  header. Multi-RFC modules list both.
- Every test docstring is one sentence, naming the requirement:
  `"""SCA MUST reject CSR whose aud is not its own DID (RFC-0004 §Issuance)."""`
- The RFC-keyed JSON reporter reads the marker `pytest.mark.rfc("0004",
  section="Issuance", requirement="csr_aud_mismatch")`. Every test
  carries this marker; lint enforces it.

### Configuration

- No `os.environ` reads outside `cli.py` and `config.py`. The parsed
  `Config` object is the single source of truth and is fixture-injected
  into tests.
- All timeouts and limits are constants in `config.py` with spec-defined
  defaults (`DEFAULT_HTTP_TIMEOUT = timedelta(seconds=10)`).

### Comments & docstrings

- Default to no comment. A comment exists when the *why* is non-obvious
  (cite the RFC + section).
- Test docstrings: one sentence, RFC-cited.
- Source docstrings: one sentence per public function/class.
- No narrative module-level docstrings, no banner comments, no emojis.

## Running locally

```sh
uv sync                       # install runtime + dev deps
uv run shadownet-conformance --help
uv run pytest                 # run the suite (no targets => skips network tests)
uv run ruff check .
uv run ruff format .
uv run mypy
```

To run against a local Go reference deployment:

```sh
uv run shadownet-conformance \
  --target sca=http://localhost:8443 \
  --target sns=http://localhost:8444 \
  --target sidecar=http://localhost:8340 \
  --report-junit=report.xml \
  --report-json=report.json
```

## Tests

- `pytest` + `pytest-asyncio` (mode = auto). One assert-cluster per test;
  one MUST per test.
- The suite must run **green with no targets configured** (every
  network-touching test skips with a clear reason). This is what makes
  the package importable in CI without infrastructure.
- Tests carry markers: `rfc(...)`, `class_(sca|sns|sidecar)`,
  `draft` (default-skipped), `network` (target-required, default
  auto-skip if target absent).
- No mocks for the system-under-test. The target is a real URL or the
  test is skipped. Fixtures may be byte-asserted; the wire may not be
  faked.

## Spec adherence

- Every wire-touching test cites the RFC + section in module header,
  function docstring, and `pytest.mark.rfc(...)` marker. The three must
  agree; lint enforces.
- For each MUST / MUST NOT in an RFC, there is a corresponding test.
  RFC SHOULDs are tested behind `@pytest.mark.should` (default-included
  but separately tallied in the report).
- JSON-Schema conformance is asserted directly against
  `../shadownet-specs/schemas/`. Path is configurable via env
  (`SHADOWNET_SPECS_PATH`); default is `../shadownet-specs`.
- When the spec changes, this suite either updates in the same change or
  its tests fail. Drift is the failure mode we are guarding against.

## Versioning & release

- Package version mirrors the protocol version it tests: `0.1.x`. A spec
  major bump (`0.2.0`) gates a coordinated bump.
- `_version.py` is the single source of truth. `pyproject.toml` reads it
  via `hatch` dynamic versioning.
- `CHANGELOG.md` per Keep-a-Changelog. Every PR that adds, removes, or
  re-classifies a test updates it (one bullet per RFC requirement
  affected).
- Release pipeline: GitHub Actions on tag push (`v*.*.*`) → lint + mypy +
  pytest + cross-SDK fixture verification → build & push multi-arch
  Docker image to `ghcr.io/shadownet-protocol/conformance:<tag>` and
  `:latest` → cut a GitHub Release. PyPI is intentionally not used at
  v0.1; consumers run via the Docker image, the GitHub Action that wraps
  it, or `uvx --from git+...@<tag>` for ad-hoc Python-native runs.

## Fixture regeneration (the safety net, in detail)

```sh
uv run shadownet-conformance-fixtures regen
```

The regen CLI:

1. Parses `fixtures/seeds.toml` (versioned seed manifest).
2. For each fixture entry, constructs the inputs (subject DID, issuer
   DID, claims, etc.) deterministically from the seeds.
3. Invokes **two** subprocesses:
   - `python -m shadownet._fixturegen ...` (using `shadownet-py`)
   - `shadownet fixturegen ...` (using `shadownet-go`'s CLI)
   Both are expected to emit the same canonical bytes for each artifact.
4. Diffs the two outputs byte-for-byte. On mismatch: prints both, exits
   non-zero, writes nothing.
5. On match: writes/overwrites the fixture file. The diff vs the
   previously-committed file is left for the developer to review and
   commit.

The regen CLI is part of the published wheel only behind the `[regen]`
extra (it pulls in `shadownet-py` at a pinned version and shells out to
the Go CLI; it is not part of the default install footprint).

CI runs the regen CLI in `--check` mode on every PR: any drift between
committed fixtures and freshly-regenerated ones fails the build.

## Things we do not do

- No mocks of the system-under-test in shipped tests. Mocking belongs in
  the SDKs' own unit tests.
- No `pytest.skip` to hide a real failure. Skips are explicit
  preconditions, period.
- No fixtures generated at test time without committing them first.
- No backwards-compatibility shims while the protocol is pre-v0.1.
- No emojis in code, comments, commits, or fixture file names.
- No banner comments, no narrative docstrings.
- No vendored deps. The lock file + `pip-audit` is the supply chain.

## Commits

`scope: imperative summary`, e.g. `sca: add csr_aud_mismatch test`,
`fixtures: regenerate L2 credential after seed bump`. One logical change
per commit; tests and fixtures land together. Reference the RFC section
when the change implements one.
