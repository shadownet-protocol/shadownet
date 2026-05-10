from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from shadownet.a2a.fastapi import require_handshake
from shadownet.a2a.session import mint_session_token
from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.did.key import derive_did_key
from shadownet.did.resolver import Resolver
from shadownet.vc.credential import issue_credential, new_credential
from shadownet.vc.presentation import mint_presentation


def _build_app(callee_did: str):
    app = FastAPI()
    dep = Depends(require_handshake(expected_audience=callee_did, resolver=Resolver()))

    @app.get("/probe")
    async def probe(ctx=dep) -> dict[str, str]:
        return {"caller": ctx.caller_did}

    return app


def test_handshake_dependency_round_trip() -> None:
    issuer_kp = Ed25519KeyPair.generate()
    issuer_did = derive_did_key(issuer_kp.public_bytes)
    caller_kp = Ed25519KeyPair.generate()
    caller_did = derive_did_key(caller_kp.public_bytes)
    callee_did = derive_did_key(Ed25519KeyPair.generate().public_bytes)
    cred = new_credential(
        issuer=issuer_did,
        subject=caller_did,
        level="urn:shadownet:level:L1",
        subject_type="person",
    )
    cred_jwt = issue_credential(issuer_key=issuer_kp, issuer_kid=issuer_did, credential=cred)
    vp_jwt = mint_presentation(
        holder_key=caller_kp,
        holder_did=caller_did,
        audience_did=callee_did,
        credentials=[cred_jwt],
    )
    session_jwt = mint_session_token(
        holder_key=caller_kp, holder_did=caller_did, audience_did=callee_did
    )

    app = _build_app(callee_did)
    with TestClient(app) as client:
        response = client.get(
            "/probe",
            headers={
                "Authorization": f"Bearer {session_jwt}",
                "X-Shadownet-Presentation": vp_jwt,
            },
        )
    assert response.status_code == 200
    assert response.json() == {"caller": caller_did}


def test_missing_handshake_returns_401() -> None:
    callee_did = derive_did_key(Ed25519KeyPair.generate().public_bytes)
    app = _build_app(callee_did)
    with TestClient(app) as client:
        response = client.get("/probe")
    assert response.status_code == 401
    payload = response.json()["detail"]
    assert payload["error"] == "presentation_invalid"


def test_missing_vp_returns_presentation_required() -> None:
    caller_kp = Ed25519KeyPair.generate()
    caller_did = derive_did_key(caller_kp.public_bytes)
    callee_did = derive_did_key(Ed25519KeyPair.generate().public_bytes)
    session_jwt = mint_session_token(
        holder_key=caller_kp, holder_did=caller_did, audience_did=callee_did
    )
    app = _build_app(callee_did)
    with TestClient(app) as client:
        response = client.get(
            "/probe",
            headers={"Authorization": f"Bearer {session_jwt}"},
        )
    assert response.status_code == 401
    detail = response.json()["detail"]
    assert detail["error"] == "presentation_required"
    assert "nonce" in detail
