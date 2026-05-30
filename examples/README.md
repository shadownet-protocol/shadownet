# Examples

Runnable end-to-end demonstrations of the Shadownet protocol using the
SDKs in this repo. Each example is self-contained — no network, no DNS,
no servers, no Docker — and runs in well under a second.

| Directory | Language | Status | What it does |
| --- | --- | --- | --- |
| [`birthday-credential-py/`](./birthday-credential-py/) | Python 3.12+ | v0.2 | Runs the full §8 envelope flow Alice → Bob with an `org_affiliation` credential. |
| [`birthday-credential-go/`](./birthday-credential-go/) | Go 1.25+ | v0.1 (pending v0.2 rewrite) | Same flow in Go. Will be rewritten once `core/` migrates to v0.2. |

The Python example models the worked transaction in
[`shadownet-specs/rfcs/0001-shadownet.md` Appendix B](https://github.com/shadownet-protocol/shadownet-specs/blob/main/rfcs/0001-shadownet.md#appendix-b--example-transaction):
the provider signs Alice's AgentCard, the hub issues her an `org_affiliation`
credential, Alice mints an envelope JWS bound to her A2A message via
`msgHash`, and Bob's receiver runs the §8.6 validation pipeline + §9
classification. The Shadowname-mode DNS lookups and AgentCard fetch are
injected so the script needs zero networking.

The Go example still demonstrates the v0.1 SCA / SNS / VC flow; it will be
brought to v0.2 parity once the Go SDK in `core/` finishes migrating.

## Why two examples?

The protocol is language-agnostic. A senior engineer landing on the repo
should be able to read either example, recognize what their language ships,
and start integrating. Both examples are intentionally line-comparable so the
mapping between the Python and Go APIs is obvious.

## Going further

- **Talk to a real reference Provider / Issuer.** The v0.2 reference Provider
  and Issuer servers in `core/` ship as standalone binaries (separate from
  the SDK). See [`core/README.md`](../core/README.md) for the operator path.
- **Cross-implementation interop.** The
  [`conformance/`](../conformance/) suite runs the same wire-level checks
  across every SDK — that's what CI on this repo invokes via
  `.github/workflows/conformance.yml`.