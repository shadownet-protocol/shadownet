# Security policy

Shadownet carries identity claims about real humans across agent-to-agent
boundaries; bugs in the SDKs or reference servers can have real-world
consequences. We treat them accordingly.

## Reporting a vulnerability

**Please do not open a public GitHub issue for security reports.**

Open a private report via GitHub Security Advisories:
[`/security/advisories/new`](https://github.com/shadownet-protocol/shadownet/security/advisories/new).
This gives the maintainers a private collaboration space and a CVE pipeline
without requiring any prior coordination.

Helpful detail to include:

- Which component is affected (Go SDK, `pgstore`, Python SDK, reference SCA
  or SNS server).
- A reproduction (test, script, sequence of API calls).
- The version(s) you tested against.
- Your assessment of impact.

## Supported versions

Security fixes target the latest released minor of each subtree:

| Subtree | Currently supported |
| --- | --- |
| `core/` (Go SDK + reference servers) | `v0.2.x` |
| `core/pgstore/` (Postgres backend) | `v0.2.x` |
| `python-sdk/` (Python SDK, PyPI `shadownet`) | `0.2.x` |
| `conformance/` (PyPI `shadownet-conformance`, image `ghcr.io/shadownet-protocol/conformance`) | `0.2.x` |
| `integrations/plugins/openclaw/` (npm `@shadownet/openclaw-plugin`) | latest only |

Older `v0.1.x` releases — published from the legacy
[`shadownet-go`](https://github.com/shadownet-protocol/shadownet-go),
[`shadownet-py`](https://github.com/shadownet-protocol/shadownet-py), and
[`shadownet-conformance`](https://github.com/shadownet-protocol/shadownet-conformance)
repos — receive critical fixes only at maintainer discretion.

## Scope

**In scope:**

- Code in this repository (`core/`, `python-sdk/`, `conformance/`, `integrations/`, top-level configs).
- Cryptographic correctness against the
  [v0.1 RFCs](https://github.com/shadownet-protocol/shadownet-specs/tree/main/rfcs).
- Issues that allow forging Verifiable Credentials, bypassing freshness or
  revocation checks, or impersonating peers in the A2A handshake.
- Issues that leak private keys, secrets, or unintended PII through logs,
  error messages, SNS records, or callback payloads.
- Vulnerabilities in the published container images for `sca-server` /
  `sns-server` / `sca-server-pg` / `sns-server-pg`.

**Out of scope:**

- Issues against [`hermes-social`](https://github.com/meghancampbel9/hermes-social)
  or any operator-side cloud / hosting deployment — report those to those
  repos directly.
- Issues against the protocol spec itself — file with
  [`shadownet-protocol/shadownet-specs`](https://github.com/shadownet-protocol/shadownet-specs).
- Theoretical attacks on `did:web` resolution that require pre-existing
  TLS-CA compromise.
- Issues already in their public CVE pipeline upstream — we pick those up
  via Dependabot and `govulncheck`.

If you're not sure whether something is in scope, send it anyway — we'd
rather triage and decline than miss a real issue.

## Disclosure

Once a fix lands, the advisory is published with credit to the reporter
(unless they ask to remain anonymous), a CVE is requested if appropriate, and
the fix is noted in the relevant `CHANGELOG.md`.
