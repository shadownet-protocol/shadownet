from __future__ import annotations

import time

import pytest

from shadownet.a2a.errors import (
    LevelInsufficientError,
    PresentationInvalidError,
    PresentationRequiredError,
)
from shadownet.a2a.server import verify_handshake
from shadownet.a2a.session import mint_session_token
from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.did.key import derive_did_key
from shadownet.did.resolver import Resolver
from shadownet.sca.predicate import LevelLeaf
from shadownet.vc.credential import issue_credential, new_credential
from shadownet.vc.presentation import mint_presentation


@pytest.fixture
def parties():
    issuer_kp = Ed25519KeyPair.generate()
    issuer_did = derive_did_key(issuer_kp.public_bytes)
    caller_kp = Ed25519KeyPair.generate()
    caller_did = derive_did_key(caller_kp.public_bytes)
    callee_did = derive_did_key(Ed25519KeyPair.generate().public_bytes)
    return {
        "issuer": (issuer_kp, issuer_did),
        "caller": (caller_kp, caller_did),
        "callee_did": callee_did,
    }


def _credential_jwt(parties, level: str = "urn:shadownet:level:L2") -> str:
    issuer_kp, issuer_did = parties["issuer"]
    _, caller_did = parties["caller"]
    cred = new_credential(
        issuer=issuer_did,
        subject=caller_did,
        level=level,
        subject_type="person",
        issued_at=int(time.time()),
    )
    return issue_credential(issuer_key=issuer_kp, issuer_kid=issuer_did, credential=cred)


async def test_handshake_happy_path(parties) -> None:
    caller_kp, caller_did = parties["caller"]
    callee_did = parties["callee_did"]
    cred_jwt = _credential_jwt(parties)
    vp_jwt = mint_presentation(
        holder_key=caller_kp,
        holder_did=caller_did,
        audience_did=callee_did,
        credentials=[cred_jwt],
    )
    session_jwt = mint_session_token(
        holder_key=caller_kp, holder_did=caller_did, audience_did=callee_did
    )
    headers = {
        "Authorization": f"Bearer {session_jwt}",
        "X-Shadownet-Presentation": vp_jwt,
    }
    ctx = await verify_handshake(
        headers,
        expected_audience=callee_did,
        resolver=Resolver(),
    )
    assert ctx.caller_did == caller_did
    assert ctx.presentation is not None
    assert len(ctx.presentation.credentials) == 1


async def test_handshake_missing_authorization(parties) -> None:
    with pytest.raises(PresentationInvalidError):
        await verify_handshake(
            {},
            expected_audience=parties["callee_did"],
            resolver=Resolver(),
        )


async def test_handshake_no_vp_no_cache_raises_required(parties) -> None:
    caller_kp, caller_did = parties["caller"]
    callee_did = parties["callee_did"]
    session_jwt = mint_session_token(
        holder_key=caller_kp, holder_did=caller_did, audience_did=callee_did
    )
    with pytest.raises(PresentationRequiredError) as ei:
        await verify_handshake(
            {"Authorization": f"Bearer {session_jwt}"},
            expected_audience=callee_did,
            resolver=Resolver(),
        )
    assert ei.value.nonce  # the challenge nonce is populated


async def test_handshake_uses_cached_vp(parties) -> None:
    """If the caller's VP is in the cache, the handshake succeeds without one in headers."""
    from shadownet.vc.presentation import VerifiablePresentation, VerifiedPresentation

    caller_kp, caller_did = parties["caller"]
    callee_did = parties["callee_did"]
    session_jwt = mint_session_token(
        holder_key=caller_kp, holder_did=caller_did, audience_did=callee_did
    )

    # Build a placeholder cached presentation — the cache only needs to contain
    # the caller's DID as a key; the value is not re-verified here.
    fake_vp = VerifiedPresentation(
        holder_did=caller_did,
        credentials=(),
        freshness_proofs=(),
        presentation=VerifiablePresentation.model_validate(
            {
                "iss": caller_did,
                "aud": callee_did,
                "iat": 0,
                "exp": 9999999999,
                "vp": {
                    "@context": ["https://www.w3.org/ns/credentials/v2"],
                    "type": ["VerifiablePresentation"],
                    "verifiableCredential": ["x"],
                },
            }
        ),
    )

    ctx = await verify_handshake(
        {"Authorization": f"Bearer {session_jwt}"},
        expected_audience=callee_did,
        resolver=Resolver(),
        cached_presentations={caller_did: fake_vp},
    )
    assert ctx.caller_did == caller_did
    assert ctx.presentation is None


async def test_handshake_predicate_unsatisfied(parties) -> None:
    caller_kp, caller_did = parties["caller"]
    callee_did = parties["callee_did"]
    cred_jwt = _credential_jwt(parties, level="urn:shadownet:level:L2")
    vp_jwt = mint_presentation(
        holder_key=caller_kp,
        holder_did=caller_did,
        audience_did=callee_did,
        credentials=[cred_jwt],
    )
    session_jwt = mint_session_token(
        holder_key=caller_kp, holder_did=caller_did, audience_did=callee_did
    )
    with pytest.raises(LevelInsufficientError):
        await verify_handshake(
            {
                "Authorization": f"Bearer {session_jwt}",
                "X-Shadownet-Presentation": vp_jwt,
            },
            expected_audience=callee_did,
            resolver=Resolver(),
            required_predicate=LevelLeaf(level="urn:shadownet:level:L3"),
        )


async def test_handshake_holder_mismatch_raises(parties) -> None:
    """A VP whose holder does not match the session-token issuer is rejected."""
    caller_kp, caller_did = parties["caller"]
    callee_did = parties["callee_did"]
    other_kp = Ed25519KeyPair.generate()
    other_did = derive_did_key(other_kp.public_bytes)
    issuer_kp, issuer_did = parties["issuer"]
    cred = new_credential(
        issuer=issuer_did,
        subject=other_did,
        level="urn:shadownet:level:L1",
        subject_type="person",
    )
    cred_jwt = issue_credential(issuer_key=issuer_kp, issuer_kid=issuer_did, credential=cred)
    other_vp = mint_presentation(
        holder_key=other_kp,
        holder_did=other_did,
        audience_did=callee_did,
        credentials=[cred_jwt],
    )
    session_jwt = mint_session_token(
        holder_key=caller_kp, holder_did=caller_did, audience_did=callee_did
    )
    with pytest.raises(PresentationInvalidError):
        await verify_handshake(
            {
                "Authorization": f"Bearer {session_jwt}",
                "X-Shadownet-Presentation": other_vp,
            },
            expected_audience=callee_did,
            resolver=Resolver(),
        )


async def test_presentation_required_error_response_shape() -> None:
    err = PresentationRequiredError(nonce="n123")
    status, body = err.to_response()
    assert status == 401
    assert body["error"] == "presentation_required"
    assert body["nonce"] == "n123"
    assert body["shadownet:v"] == "0.1"
