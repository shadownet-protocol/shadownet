"""End-to-end Shadownet credential flow using the Python SDK only.

Demonstrates: an SCA issues a credential to a holder, the holder mints a
Verifiable Presentation audienced at a peer verifier, the verifier evaluates
the chain end-to-end against a trust store. No network, no servers — pure
cryptographic primitives over did:key.

Run:
    uv run --with shadownet python birthday_credential.py
"""

from __future__ import annotations

import asyncio

from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.did.key import derive_did_key
from shadownet.did.resolver import Resolver
from shadownet.trust import TrustStore
from shadownet.vc.credential import issue_credential, new_credential, verify_credential
from shadownet.vc.presentation import mint_presentation, verify_presentation


async def main() -> None:
    print("Shadownet end-to-end credential flow (Python SDK)")
    print()

    # 1. Identities ------------------------------------------------------------
    # The SCA, the holder, and a peer verifier each get a fresh Ed25519 key
    # and a self-describing did:key. did:key needs no network because the
    # public key is recoverable from the DID itself.
    issuer_kp = Ed25519KeyPair.generate()
    issuer_did = derive_did_key(issuer_kp.public_bytes)

    holder_kp = Ed25519KeyPair.generate()
    holder_did = derive_did_key(holder_kp.public_bytes)

    verifier_kp = Ed25519KeyPair.generate()
    verifier_did = derive_did_key(verifier_kp.public_bytes)

    print(f"  SCA / issuer DID: {issuer_did}")
    print(f"  Holder DID:       {holder_did}")
    print(f"  Verifier DID:     {verifier_did}")

    # The same Resolver is used everywhere; bare Resolver() handles did:key
    # locally with no I/O. (Real deployments add WebDIDResolver(http_client)
    # for did:web issuers.)
    resolver = Resolver()

    # 2. Issue & verify the credential ----------------------------------------
    # The SCA mints a Verifiable Credential JWT attesting that the holder is
    # at level L2 ("verified human"). We round-trip through verify_credential
    # so the example fails loudly if anything is wrong (signature, expiry,
    # subject-type rules, …).
    credential = new_credential(
        issuer=issuer_did,
        subject=holder_did,
        level="urn:shadownet:level:L2",
        subject_type="person",
    )
    cred_jwt = issue_credential(
        issuer_key=issuer_kp,
        issuer_kid=issuer_did,
        credential=credential,
    )
    verified_cred = await verify_credential(cred_jwt, resolver=resolver)

    print()
    print(f"  Issued credential JWT ({len(cred_jwt)} chars).")
    print(
        f"  Verified credential: level={verified_cred.level}, "
        f"sub={verified_cred.sub}"
    )

    # 3. Mint a Verifiable Presentation ---------------------------------------
    # The holder bundles the credential into a VP and audiences it at the
    # verifier. The VP is signed by the holder's key — proving the holder
    # actually controls the subject DID inside the credential.
    vp_jwt = mint_presentation(
        holder_key=holder_kp,
        holder_did=holder_did,
        audience_did=verifier_did,
        credentials=[cred_jwt],
    )

    print()
    print(f"  Minted VP JWT ({len(vp_jwt)} chars).")

    # 4. Verifier checks the VP -----------------------------------------------
    # The verifier evaluates the VP against a TrustStore that pins the SCA's
    # DID at level L2. Credentials whose (issuer, level) aren't in the trust
    # store are silently dropped from `credentials` (still surfaced via
    # `presentation.credential_jwts`), so a real verifier can distinguish
    # "VP unverifiable" from "VP fine, just no recognised issuer."
    trust = TrustStore.from_pairs([(issuer_did, ["urn:shadownet:level:L2"])])
    verified_vp = await verify_presentation(
        vp_jwt,
        resolver=resolver,
        expected_audience=verifier_did,
        trust_store=trust,
    )

    print(f"  Verifier accepted {len(verified_vp.credentials)} credential(s).")
    for c in verified_vp.credentials:
        print(f"    - {c.iss} → {c.sub}  (level: {c.level})")

    print()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
