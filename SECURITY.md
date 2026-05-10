# Security policy

Shadownet carries identity claims about real humans across agent-to-agent
boundaries. Bugs in the SDKs or reference servers can have real-world
consequences. We treat them accordingly.

## Supported versions

Security fixes target the latest released minor of each subtree:

| Subtree | Currently supported |
| --- | --- |
| `go/` (Go SDK + reference servers) | `v0.2.x` |
| `go/pgstore/` (Postgres backend) | `v0.2.x` |
| `py/` (Python SDK, PyPI `shadownet`) | `0.2.x` |

Older `v0.1.x` releases — published from the legacy
[`shadownet-go`](https://github.com/shadownet-protocol/shadownet-go) and
[`shadownet-py`](https://github.com/shadownet-protocol/shadownet-py) repos —
receive critical fixes only at maintainer discretion.

## Reporting a vulnerability

**Please do not open a public GitHub issue for security reports.**

Use one of the following private channels:

- **Preferred:** open a draft GitHub Security Advisory at
  [`/security/advisories/new`](https://github.com/shadownet-protocol/shadownet/security/advisories/new).
  This gives us a private collaboration space and a CVE pipeline.
- **Email:** `security@sh4dow.org`. PGP key on request.

Please include:

- A description of the issue, including which component is affected
  (Go SDK, pgstore, Python SDK, reference SCA / SNS server).
- A reproduction (test, script, sequence of API calls).
- The version(s) you tested against.
- Your assessment of impact.

## What to expect

| Stage | Target |
| --- | --- |
| Initial acknowledgement | within 3 business days |
| Triage and severity classification | within 7 calendar days |
| Critical fix shipped | within 14 days of triage |
| High fix shipped | within 30 days of triage |
| Medium / Low fix shipped | within 90 days of triage |

We coordinate disclosure with you: once a patch ships, we publish a security
advisory crediting the reporter (unless you ask to remain anonymous), assign a
CVE if appropriate, and note the fix in the relevant `CHANGELOG.md`.

We do not currently run a bug bounty.

## Scope

**In scope:**

- Code in this repository (`go/`, `py/`, top-level configs).
- Cryptographic correctness against the
  [v0.1 RFCs](https://github.com/shadownet-protocol/shadownet-specs/tree/main/rfcs).
- Issues that allow forging Verifiable Credentials, bypassing freshness or
  revocation checks, or impersonating peers in the A2A handshake.
- Issues that leak private keys, secrets, or unintended PII through logs,
  error messages, SNS records, or callback payloads.
- Vulnerabilities in the published container images for `sca-server` /
  `sns-server` / `sca-server-pg` / `sns-server-pg`.

**Out of scope:**

- Issues already in their public CVE pipeline upstream — we'll pick those up
  via Dependabot, `govulncheck`, and Codecov-style alerts.
- Issues against
  [`shadownet-cloud`](https://github.com/shadownet-protocol/shadownet-cloud) or
  [`hermes-social`](https://github.com/meghancampbel9/hermes-social) — please
  report those to those repos directly.
- Issues against the protocol spec itself — file with
  [`shadownet-protocol/shadownet-specs`](https://github.com/shadownet-protocol/shadownet-specs).
  We'll happily implement a spec-defined fix once it's accepted upstream.
- Theoretical attacks on `did:web` resolution that require pre-existing
  TLS-CA compromise.

If you're not sure whether something is in scope, send it anyway — we'd
rather triage and decline than miss a real issue.
