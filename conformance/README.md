# shadownet-conformance

Wire-level interop test suite for the [Shadownet](../shadownet-specs/) protocol.

## Status

v0.1 alpha. Tests the v0.1 RFCs at [`shadownet-specs/rfcs`](../shadownet-specs/rfcs/).
Coverage: SCA (RFC-0004), SNS (RFC-0005), Sidecar inbound handshake (RFC-0006),
predicate evaluation, JSON-Schema conformance, round-trip across two SCA / SNS
implementations. RFC-0007 MCP/webhook coverage lands once a Sidecar
implementation supports it (see [Sidecar coverage](#sidecar-coverage) below).

## What this repo is

The canonical "is your implementation correct?" check. A `pytest`-based runner that takes a target URL — an SCA, SNS, or Sidecar — and exercises every spec'd endpoint, error code, and edge case from the RFCs. It does not care which language the implementation is written in; it only speaks the wire.

It is what catches **Go and Python disagreeing about JWT canonicalization**, or **the Python verifier rejecting a credential the Go SCA happily issued**, or **a Sidecar accepting a replayed VP nonce**. None of an implementation's own internal tests can catch these — only an external runner with canonical fixtures can.

## What it tests

- **SCA** — every RFC-0004 endpoint (`/proof/start`, `/proof/status`, `/issuance`, `/freshness`, `/status/<list-id>`, `/.well-known/{did,sca/policy}.json`), including negative cases (expired sessions, mismatched CSR/session, revoked credentials).
- **SNS** — RFC-0005 resolution, signed records, key rotation, cross-provider rejection.
- **Sidecar** — RFC-0006 A2A handshake (session token + VP exchange), error envelope, async task semantics; RFC-0007 MCP tool surface and webhook contract.
- **Round-trip** — issue with impl A, verify with impl B; sign a record with A, resolve with B.
- **Predicate evaluation** — given the same `(predicate, presentation)` pair, all impls must return the same boolean.

## How it runs

```bash
uv sync
uv run shadownet-conformance \
    --target sca=https://sca.example \
    --target sns=https://sns.example \
    --target sidecar=https://shadow.example \
    --report-junit=conformance.xml \
    --report-json=conformance.json
```

Three reports are emitted (configurable independently):

- `--report-junit=PATH` — JUnit XML (CI-standard).
- `--report-json=PATH` — RFC-keyed JSON, one entry per `(rfc, section, requirement)`.
- `--gha-summary=PATH|auto` — GitHub Actions step-summary markdown.
  `auto` honors `$GITHUB_STEP_SUMMARY`.

For round-trip across two implementations of the same role:

```bash
uv run shadownet-conformance \
    --target sca=https://sca-go.example \
    --peer-target sca=https://sca-py.example \
    --target sns=https://sns-go.example \
    --peer-target sns=https://sns-py.example
```

For SCAs that expose an instant-approval test method under a non-default
URI, set `--proof-method <URI>`. For SNS resolve happy-path tests, set
`--sns-test-shadowname <local@provider>` to a name pre-registered against
the target.

Also published as a **GitHub Action** so any implementation's CI can
verify itself before merging:

```yaml
- uses: shadownet-protocol/conformance-action@v0.1
  with:
    sca:     http://localhost:8443
    sns:     http://localhost:8444
    sidecar: http://localhost:8340
```

### Smoke-testing locally against the Go reference

```sh
# In one terminal, boot the reference SCA + SNS:
( cd ../shadownet-go
  go build -o /tmp/sca-server ./cmd/sca-server
  go build -o /tmp/sns-server ./cmd/sns-server
  go build -o /tmp/shadownet ./cmd/shadownet
  /tmp/shadownet keygen -out /tmp/issuer.jwk
  /tmp/shadownet keygen -out /tmp/provider.jwk
  # Copy/edit deploy/sca-server.yaml and deploy/sns-server.yaml as needed.
  SHADOWNET_ALLOW_INSTANT_APPROVAL=1 /tmp/sca-server -config /path/to/sca.yaml
)

# In another terminal:
uv run shadownet-conformance \
    --target sca=http://127.0.0.1:8443 \
    --target sns=http://127.0.0.1:8444 \
    --proof-method instant-approval
```

The full automated version of this lives in `.github/workflows/self-test.yml`.

## Test vectors

Canonical fixtures live in `fixtures/` — deterministic Ed25519 keypairs (from fixed seeds), credential JWTs with byte-exact expected outputs, VPs, SNS records, status list credentials, predicate eval pairs, and malformed inputs paired with the expected error code. These let any implementation assert byte-for-byte equality, not just "looks roughly right."

The fixture set is the protocol's empirical contract. New normative behavior in a future RFC lands a fixture before it lands in code.

> ⚠️ **Test fixtures are public material, not production secrets.** Every
> private key in `fixtures/keys/` is derived from an obvious-pattern seed
> in `fixtures/seeds.toml` (`0x010101…`, `0x020202…`, …). Anyone with this
> repo can re-derive every "private" key. That is intentional — the whole
> point of canonical fixtures is byte-for-byte reproducibility.
>
> Operators of production deployments **MUST**:
>
> - Generate fresh keys via `shadownet keygen` (or any CSPRNG-backed Ed25519
>   generator). Never copy a fixture key into a production deployment.
> - Never list a fixture public key in a production trust store.
>
> The reference servers in `shadownet-go` enforce the first rule at startup:
> `sca-server` and `sns-server` refuse to boot if their signing key matches
> any fixture public key, unless `SHADOWNET_ALLOW_FIXTURE_KEYS=1` is set
> (used by this suite's CI self-test, never in production).

## Tooling

- **Package manager**: [`uv`](https://docs.astral.sh/uv/)
- **Python**: 3.12+
- **Test runner**: `pytest`
- **HTTP client**: `httpx` (async)
- **JWT / DID / VC primitives**: imported from
  [`shadownet`](https://pypi.org/project/shadownet/) on PyPI (the SDK is a
  runtime dependency; the safety against an SDK bug masking a wire bug is
  the cross-SDK fixture regen — see [`CLAUDE.md`](./CLAUDE.md) §Fixture
  discipline).

To run the **fixture regeneration** CLI you also need a Go toolchain
(Go 1.25+); the regen tool builds a small Go emitter that imports
[`shadownet-go`](https://pkg.go.dev/github.com/shadownet-protocol/shadownet-go)
from the public Go module proxy. Running the conformance suite itself
never needs Go.

## Distribution

| Audience | How they consume it |
| --- | --- |
| Implementer CI (any language) | `uses: shadownet-protocol/conformance-action@v0.1` (Docker action) |
| Local debugging | `docker run --rm --network host ghcr.io/shadownet-protocol/conformance:latest --target ...` |
| Python-native local run | `uvx --from git+https://github.com/shadownet-protocol/shadownet-conformance@v0.1.0 shadownet-conformance --target ...` |
| Hacking on the suite itself | `git clone && uv sync && uv run shadownet-conformance ...` |

PyPI is intentionally not used at v0.1 — the test runner is consumed via
the Docker image, the GitHub Action, or `uvx` directly from a tagged git
ref. This keeps the supply-chain surface small (one registry, one
artifact). PyPI may be added later if there's demand.

## Sidecar coverage

The Sidecar conformance suite at v0.1 covers:

- `GET /.well-known/agent-card.json` shape (RFC-0006 §Required A2A surface).
- A2A inbound handshake: missing auth, missing VP (→ `presentation_required`),
  valid handshake, wrong audience, malformed VP, expired session token,
  envelope-part absence (RFC-0006 §Handshake / §Errors).

The envelope schema tests (which run with no targets) cover both forms
RFC-0006 defines:

- **Free-form text** (the v0.1 default): `payload.text` + optional `hints`,
  no `interaction` URI.
- **Typed Interaction Profile**: `interaction` set to a profile URI; payload
  follows that profile's schema.

…plus the verifier obligations: missing `interaction` MUST validate, unknown
`interaction` MUST validate (envelope-layer rejection is forbidden by RFC-0006
§Verifier obligations).

RFC-0007 MCP tool surface and webhook dispatch tests will land once at
least one Sidecar implementation supports them. The plan keeps the
`--target sidecar=URL` flag uniform; new tests opt in without a
flag-design change.

Sidecar↔Sidecar round-trip is registered with a documented skip
(`sidecar round-trip skipped: only one v0.1 Sidecar impl exists`) so v0.2
can light it up by simply pointing `--peer-target sidecar=` at a second
implementation.

## Specifications

- Protocol: [`shadownet-specs/rfcs`](../shadownet-specs/rfcs/)
- Wire-level walkthrough: [`shadownet-specs/examples/birthday-flow.md`](../shadownet-specs/examples/birthday-flow.md)
- Development plan: [`shadownet-specs/DEVELOPMENT.md`](../shadownet-specs/DEVELOPMENT.md)

## License

MIT.
