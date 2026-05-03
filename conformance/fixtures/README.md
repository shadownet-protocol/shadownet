# fixtures/

Canonical wire artifacts the conformance suite asserts against. Every file
under this tree is **byte-stable** and reviewed: the test runner relies on
the bytes, not on regenerating-at-test-time.

## What is here

| Subtree | Contents | Wire artifact |
| --- | --- | --- |
| `keys/` | `{did, public_jwk, private_jwk, seed_hex}` JSON, one per seed. | Sorted-key JSON. |
| `credentials/` | Subject Credential JWTs (`vc+jwt`). | RFC-0003 §JWT shape. |
| `freshness/` | Freshness Proof JWTs. | RFC-0003 §Lifetimes-and-freshness. |
| `presentations/` | Verifiable Presentation JWTs (`vp+jwt`). | RFC-0003 §Presentation. |
| `sns_records/` | Signed SNS record JWTs. | RFC-0005 §Records. |
| `status_lists/` | BitstringStatusList VC JWTs. | RFC-0003 §Revocation, W3C VC §BitstringStatusList. |
| `malformed/` | Hand-curated negative inputs paired with their `expected_error_code`. | Out-of-spec inputs the suite POSTs to verify error responses. |

Plus the regen apparatus:

- `seeds.toml` — versioned 32-byte Ed25519 seed manifest. Clearly non-secret;
  changing one is a deliberate, reviewed event.
- `_regen/manifest.toml` — declarative list of fixture entries to emit.
- `_regen/go-emit/` — Go module containing the cross-check emitter binary.

## How regeneration works

```sh
uv sync --extra regen
cd fixtures/_regen/go-emit && go build -o go-emit .
cd ../../..
uv run shadownet-conformance-fixtures regen
```

For each entry in `_regen/manifest.toml` the regen CLI:

1. Resolves seed references and pre-computes derived values (DIDs from
   seeds, the BitstringStatusList encodedList).
2. Pipes the resolved spec to the Python emitter
   (`python -m shadownet_conformance.regen.py_emit <kind>`) **and** the Go
   emitter (`fixtures/_regen/go-emit/go-emit <kind>`).
3. Diffs the two outputs byte-for-byte.
4. On match: writes `fixtures/<out>`. On mismatch: prints both, errors,
   writes nothing.

Both emitters use their SDK's natural signing path (`shadownet-py` for
Python, `shadownet-go` for Go). The cross-check is what catches the two
SDKs disagreeing about JWS header serialization, JSON key order, or any
other wire detail that would otherwise let a bug ship into both
implementations *and* the canonical fixtures.

`uv run shadownet-conformance-fixtures regen --check` runs the same flow
but writes nothing — it only diffs the freshly-emitted bytes against the
committed fixture and exits non-zero on drift. CI runs this on every PR.

## Adding a fixture

1. Add an entry to `_regen/manifest.toml` describing the new fixture.
2. If the fixture introduces a new artifact kind, add an emitter to **both**
   `src/shadownet_conformance/regen/py_emit/__main__.py` and
   `fixtures/_regen/go-emit/main.go`. Both must produce byte-identical
   output for any input.
3. Run `uv run shadownet-conformance-fixtures regen` and commit the
   produced file together with the manifest change.
4. If the cross-check fails, fix whichever SDK has drifted before
   regenerating — committing a fixture that one SDK can't reproduce
   means we silently encoded a wire-level interop bug.

## What the gzip caveat means

The BitstringStatusList encoder produces gzip-compressed bytes. Deflate
output is implementation-defined: Python and Go produce semantically
equivalent (decompresses to the same bits) but byte-different compressed
payloads. The W3C BitstringStatusList spec only requires the
*decompressed* bitstring to match, not the compressed bytes.

So the regen CLI pre-computes the canonical `encodedList` (using Python's
`gzip.compress(buf, mtime=0)`) and passes it as opaque input to both
emitters. The cross-check then verifies the JWT signing of an opaque
string, which both emitters can produce identically. This trades one
narrow semantic check (verify decoded bits match) — added to the
predicate test suite — for a much simpler emitter contract.

## Malformed fixtures

`malformed/` does **not** participate in the regen cross-check pipeline.
These are hand-curated negative inputs with an associated
`expected_error_code` per RFC. The conformance suite POSTs them at the
target and asserts the documented error response.

Each malformed fixture is a JSON file:

```json
{
  "name": "csr_aud_mismatch",
  "rfc": "0004",
  "section": "Issuance",
  "endpoint": "POST /issuance",
  "request_body": { ... },
  "expected_status": 400,
  "expected_error": "csr_invalid",
  "notes": "CSR aud is not the SCA's own DID."
}
```

The structure is documented in `tests/sca/conftest.py` (added in Phase D
of the plan).
