# Response to SDK feedback

Thanks for the detailed report — every item was on-point. Here is the
disposition.

## Landed in `shadownet` (Unreleased; will ship as the next minor)

### 2. `a2a.problem_response()` agent-opacity leak — **fixed**

`ShadownetWireError.problem_body()` and `a2a.problem_response()` no
longer auto-fill `detail` from `str(self)`. The default body is now just
`type` + `title` + `status`, matching RFC 0001 §11. Two opt-in escape
hatches for trusted contexts:

- `detail="curated string"` — pass an explicit curated string.
- `include_detail=True` — forward the exception's message verbatim.

Public receivers should leave both off. The §11 leak-guard is now
covered by `test_a2a.py::test_default_sanitizes_exception_message`.

### 3. Keyed-issuer support in `ReceiverPipeline` — **fixed**

Both `ReceiverPipeline._resolve_issuer_key` /
`_check_issuer_authorized_for_org` and their async twins now branch on
`is_public_key_identifier` before calling `provider_lookup`, mirroring
`default_issuer_key_resolver`'s §6.6 rule-1 handling. A credential
issued by a keyed Hub (`iss` = `z6Mk...`) now validates without an
attempted DNS lookup of the multibase string. Guarded by
`test_keyed_hub_credential_skips_dns` in both `test_receiver.py` and
`test_async_surface.py`.

### 6. §8.10 retry helper — **added**

`shadownet.a2a` now exports `send_with_retries` (sync) and
`asend_with_retries` (async). Both:

- Take a `builder: Callable[[], BuiltMessage]` closure invoked per
  attempt to enforce per-attempt re-mint (fresh `iat` / `exp` /
  `messageId` per §8.10).
- Default to the spec's parameters (initial 30s, doubling, ±25% jitter,
  24h budget) with overrides for tests.
- Retry on `TransportError`; propagate `ShadownetWireError` immediately
  (the receiver gave a decisive answer).
- Raise `TransportRetryExhausted` when the cumulative wall time exceeds
  the budget.

Backoff parameters are exposed as `RETRY_INITIAL_DELAY` /
`RETRY_MAX_DELAY` / `RETRY_TOTAL_BUDGET` / `RETRY_JITTER`. The `sleep`
and `monotonic` injection points are there to make tests deterministic.

### Related cleanup: `TransportError` is now distinct from `ParseError`

Building the retry helper exposed that `send_envelope` /
`asend_envelope` previously raised `ParseError` for both "envelope
JWS unparseable" (don't retry) and "TCP didn't connect" (retry). That
conflation made retry classification impossible. Split into
`TransportError` (HTTP layer never reached a peer) and `ParseError`
(reached a peer, response was unparseable). Both already inherit from
`ShadownetError`; only `ParseError` keeps `ShadownetWireError` lineage
(it's a wire code).

If you were catching `ParseError, match="transport failed"` anywhere,
switch to `TransportError`. The retry helpers handle the
classification for you.

## Already shipped (predates your report)

### 4. Direct-mode TLS pin enforcement

`shadownet.tls.make_pinned_httpx_client(direct_address, ...)` returns
an `httpx.Client` whose transport verifies the peer cert's SHA-256
against the URI's `#sha256:` pin (or TOFU-records it on first use).
The async sibling `make_pinned_httpx_async_client` returns an
`httpx.AsyncClient` honoring the same policy via
`_PinnedAsyncTransport(httpx.AsyncHTTPTransport)`. The signing
identity is still authoritative (§11); pinning protects the channel
from MITM rewrites.

### 5. Async wrappers — full dual surface now ships

Every wire-layer function has an `a`-prefixed sibling that takes an
injected `httpx.AsyncClient` (or own one when `None`):

- `shadownet.provider.alookup_provider_record` (uses
  `dns.asyncresolver`).
- `shadownet.agentcard.afetch_agent_card_json` /
  `afetch_and_verify_agent_card` (+ direct-mode siblings).
- `shadownet.status.afetch_status_list` / `acheck_revocation`.
- `shadownet.csr.asubmit_csr`.
- `shadownet.a2a.asend_envelope`.
- `shadownet.onboarding.aredeem_handoff` /
  `arefresh_access_token`.
- `shadownet.credential.averify_credential` (awaitable
  `resolve_issuer_key` / `check_issuer_authorized_for_org`
  callbacks; defaults are `default_async_issuer_key_resolver` /
  `default_async_issuer_authorization_check`).
- `shadownet.receiver.AsyncReceiverPipeline` over async-shaped
  plug-points `AsyncReplayCache` / `AsyncContactGraph` /
  `AsyncCredentialCache`. RAM impls supplied:
  `AsyncInMemoryReplayCache` / `AsyncInMemoryContactGraph` /
  `AsyncInMemoryCredentialCache`.

You can now run the §8.6 / §9 receiver flow end-to-end from an async
host without thread-pool detours.

### 5 (cont.) — SQL-backed reference adapters: deliberately skipped

We opted not to ship a reference SQL adapter set. The right shape is
opinionated (SQLAlchemy vs asyncpg vs psycopg3; sync vs async session;
which migration tool) and a reference impl tends to ossify a choice
that downstream consumers immediately want to override. The async
Protocols are stable and small enough that wiring them to your own
`asyncpg` queries is a few dozen lines — and you've already done it.
Happy to revisit if other consumers ask.

## Worth doing, needs scoping — your call

### 1. Server-side onboarding (RFC 0003)

This is the largest single ask in the report (~500–800 LOC including
tests, by our estimate) and the right shape will be opinionated about
storage and rate-limit transport. We agree on the goal — nobody
reimplements the §4/§7/§8 state machine — but want to align with you on
shape before writing it. Two questions:

1. Token store abstraction. We'd ship a stateless protocol layer (mint,
   validate, family-revoke) over a `TokenStore` `Protocol` you wire to
   your own backing store (matching how `ReceiverPipeline` takes
   `ReplayCache` / `ContactGraph` / `CredentialCache`). Does the
   `Protocol` we settle on need to be sync, async, or both? Your
   cloud-sidecar context implies async-only; happy to land it that way
   if you confirm.
2. Rate limiting. We can keep it abstract too — `RateLimiter` `Protocol`
   with `check(ip) -> Allowed | Retry(after_seconds)` — or leave it
   entirely to the caller's middleware. Which would your sidecar prefer?

If you've already merged your server impl, we'd love to see it as a
starting point — we'll factor what's protocol-defined into the SDK and
leave storage / rate-limit to your wiring.

---

For everything in the "Landed" section: it's on the `Unreleased` branch
of `python-sdk/CHANGELOG.md`. Local gate (`uv run ruff check . && uv
run mypy src/shadownet && uv run pytest`) is green; 341 tests pass.
Will go out in the next minor.
