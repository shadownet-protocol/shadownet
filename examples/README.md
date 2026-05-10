# Examples

Runnable end-to-end demonstrations of the Shadownet protocol using the
SDKs in this repo. Each example is self-contained — no network, no DNS,
no servers, no Docker — and runs in well under a second.

| Directory | Language | What it does |
| --- | --- | --- |
| [`birthday-credential-py/`](./birthday-credential-py/) | Python 3.12+ | Issues a credential, mints a Verifiable Presentation, verifies it end-to-end. |
| [`birthday-credential-go/`](./birthday-credential-go/) | Go 1.25+ | Same flow as the Python example, using the Go SDK. |

Both examples model the
[birthday-flow walkthrough](https://github.com/shadownet-protocol/shadownet-specs/blob/main/examples/birthday-flow.md)
in the spec repo: a Shadow Certificate Authority issues a Verifiable Credential
attesting that a holder is at level **L2** ("verified human"), the holder mints
a Verifiable Presentation audienced at a peer verifier, and the verifier
checks the chain end-to-end against a trust store.

The flows use `did:key` (deterministic from the public key, no network needed)
to keep each example a single self-contained process. Real deployments use
`did:web` for the SCA / SNS so multiple keys and rotation can be expressed via
the published DID document — see the relevant SDK READMEs.

## Why two examples?

The protocol is language-agnostic. A senior engineer landing on the repo
should be able to read either example, recognize what their language ships,
and start integrating. Both examples are intentionally line-comparable so the
mapping between the Python and Go APIs is obvious.

## Going further

- **Talk to a real reference SCA / SNS.** Boot the Go reference servers
  via [`go/deploy/docker-compose.yml`](../go/deploy/docker-compose.yml) and
  point an SCA client at `http://127.0.0.1:8443`. The
  [`go/README.md`](../go/README.md#as-an-operator--run-the-reference-servers)
  walkthrough has the operator path.
- **Cross-implementation interop.** The
  [`shadownet-conformance`](https://github.com/shadownet-protocol/shadownet-conformance)
  suite runs the same wire-level checks across every SDK — that's what CI
  on this repo invokes via `.github/workflows/conformance.yml`.
