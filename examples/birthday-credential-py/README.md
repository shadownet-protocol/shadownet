# Birthday-credential example (Python)

End-to-end Shadownet credential flow using the Python SDK only. No network,
no servers, no Docker — pure cryptographic primitives over `did:key`.

## What it shows

1. Three Ed25519 keypairs are generated: SCA (issuer), holder, peer verifier.
   Each is given a `did:key` derived directly from its public key.
2. The SCA issues a Verifiable Credential to the holder at level **L2**
   ("verified human") and the credential is verified end-to-end.
3. The holder mints a Verifiable Presentation audienced at the verifier and
   bundles the credential.
4. The verifier evaluates the VP against a `TrustStore` that pins the SCA's
   DID at level L2, and prints what survived the chain of checks.

This is the smallest possible end-to-end story — it exercises the
`shadownet.{crypto, did, vc, trust}` packages without needing the SCA / SNS
HTTP surfaces. For an operator-side walkthrough that boots the reference
servers, see [`go/README.md`](../../go/README.md).

## Run it

```sh
# From this directory:
uv run --with shadownet python birthday_credential.py

# Or, if you've already installed shadownet (pip install shadownet):
python birthday_credential.py
```

The script depends only on `shadownet>=0.2.0` — no extras.

## Expected output

```
Shadownet end-to-end credential flow (Python SDK)

  SCA / issuer DID: did:key:z6Mk…
  Holder DID:       did:key:z6Mk…
  Verifier DID:     did:key:z6Mk…

  Issued credential JWT (≈900 chars).
  Verified credential: level=urn:shadownet:level:L2, sub=did:key:…

  Minted VP JWT (≈1900 chars).
  Verifier accepted 1 credential(s).
    - did:key:… → did:key:…  (level: urn:shadownet:level:L2)

Done.
```
