# Birthday-credential example (Go)

End-to-end Shadownet credential flow using the Go SDK only. No network, no
servers, no Docker — pure cryptographic primitives over `did:key`. This
example mirrors the Python example next door:
[`../birthday-credential-py/`](../birthday-credential-py/).

## What it shows

1. Three Ed25519 keypairs are generated: SCA (issuer), holder, peer verifier.
   Each is given a `did:key` derived directly from its public key.
2. The SCA issues a Verifiable Credential to the holder at level **L2**
   ("verified human") and the example verifies it end-to-end.
3. The holder mints a Verifiable Presentation audienced at the verifier.
4. The verifier resolves the holder's DID, validates the VP signature, and
   confirms `aud` + `nonce` + lifetime constraints.

## Run it

```sh
# From this directory:
go run .
```

This module declares `replace github.com/shadownet-protocol/shadownet/core => ../../go`
so it resolves the SDK from the in-repo checkout. The `require` line in
`go.mod` is a placeholder; the replace directive is what binds it for
example builds.

If you want to consume the published SDK instead (after `core/v0.2.0` is
tagged), drop the replace directive and bump the require line to the
released version.

## Expected output

```
Shadownet end-to-end credential flow (Go SDK)

  SCA / issuer DID: did:key:z6Mk…
  Holder DID:       did:key:z6Mk…
  Verifier DID:     did:key:z6Mk…

  Issued credential JWT (≈900 chars).
  Verified credential: level=urn:shadownet:level:L2, sub=did:key:…

  Minted VP JWT (≈1100 chars).
  Verified presentation: holder=did:key:…  audience=did:key:…  credentials=1

Done.
```
