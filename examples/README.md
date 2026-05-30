# Examples

Runnable end-to-end demonstrations of the Shadownet protocol using the
Python SDK. Each example is self-contained — no network, no DNS, no
servers, no Docker — and runs in well under a second.

| Directory | Language | Status | What it does |
| --- | --- | --- | --- |
| [`birthday-credential-py/`](./birthday-credential-py/) | Python 3.12+ | v0.2 | Runs the full §8 envelope flow Alice → Bob with an `org_affiliation` credential. |

The Python example models the worked transaction in
[`shadownet-specs/rfcs/0001-shadownet.md` Appendix B](https://github.com/shadownet-protocol/shadownet-specs/blob/main/rfcs/0001-shadownet.md#appendix-b--example-transaction):
the provider signs Alice's AgentCard, the hub issues her an `org_affiliation`
credential, Alice mints an envelope JWS bound to her A2A message via
`msgHash`, and Bob's receiver runs the §8.6 validation pipeline + §9
classification. The Shadowname-mode DNS lookups and AgentCard fetch are
injected so the script needs zero networking.

The legacy `birthday-credential-go/` example demonstrated the v0.1
SCA / SNS / VC flow against the now-removed Go SDK; it has been deleted as
part of the v0.2 cut. The canonical client SDK is Python.

## Going further

- **Talk to a real reference Provider / Issuer.** The v0.2 reference Provider
  and Issuer servers in `core/` ship as standalone binaries (separate from
  the SDK). See [`core/README.md`](../core/README.md) for the operator path.
- **Cross-implementation interop.** The
  [`conformance/`](../conformance/) suite runs the same wire-level checks
  across every SDK — that's what CI on this repo invokes via
  `.github/workflows/conformance.yml`.