from __future__ import annotations

import json
import time

import httpx
import pytest

from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.crypto.jwt import sign_jwt
from shadownet.did.key import derive_did_key
from shadownet.sca.client import SCAClient
from shadownet.sca.errors import (
    InvalidLevel,
    SessionConsumed,
    SessionNotReady,
    SubjectBlocked,
    UnknownJti,
)
from shadownet.vc.credential import issue_credential, new_credential

SCA_DID = "did:web:sca.example"
SCA_BASE = "https://sca.example"


@pytest.fixture
def holder() -> tuple[Ed25519KeyPair, str]:
    kp = Ed25519KeyPair.generate()
    return kp, derive_did_key(kp.public_bytes)


def _policy_payload() -> bytes:
    return json.dumps(
        {
            "issuer": SCA_DID,
            "shadownet:v": "0.1",
            "levels": [
                {
                    "level": "urn:shadownet:level:L1",
                    "method": "urn:example:method:email-v1",
                    "rateLimit": "1 per email per 24h",
                    "credentialLifetimeDays": 90,
                }
            ],
            "freshnessWindowSeconds": 86400,
            "statusListBase": f"{SCA_BASE}/status/",
        }
    ).encode()


def _proof_session_payload() -> bytes:
    return json.dumps(
        {
            "shadownet:v": "0.1",
            "sessionId": "ses-01",
            "expiresAt": int(time.time()) + 3600,
            "method": "urn:example:method:email-v1",
            "next": {"kind": "email-link", "ttl": 600},
        }
    ).encode()


def _make_handler(routes):
    def handler(request: httpx.Request) -> httpx.Response:
        for path, builder in routes.items():
            if request.url.path == path:
                return builder(request)
        return httpx.Response(404, content=b'{"error":"not_found"}')

    return handler


def _client(http: httpx.AsyncClient, holder: tuple[Ed25519KeyPair, str]) -> SCAClient:
    kp, did = holder
    return SCAClient(http, sca_base_url=SCA_BASE, sca_did=SCA_DID, holder_key=kp, holder_did=did)


async def test_fetch_policy(holder) -> None:
    routes = {
        "/.well-known/sca/policy.json": lambda r: httpx.Response(200, content=_policy_payload()),
    }
    transport = httpx.MockTransport(_make_handler(routes))
    async with httpx.AsyncClient(transport=transport, base_url=SCA_BASE) as http:
        policy = await _client(http, holder).fetch_policy()
    assert policy.issuer == SCA_DID
    assert policy.method_for("urn:shadownet:level:L1") == "urn:example:method:email-v1"


async def test_start_proof(holder) -> None:
    routes = {"/proof/start": lambda r: httpx.Response(200, content=_proof_session_payload())}
    transport = httpx.MockTransport(_make_handler(routes))
    async with httpx.AsyncClient(transport=transport, base_url=SCA_BASE) as http:
        session = await _client(http, holder).start_proof(level="urn:shadownet:level:L1")
    assert session.session_id == "ses-01"
    assert session.next.kind == "email-link"


async def test_start_proof_rejects_invalid_level(holder) -> None:
    routes = {
        "/proof/start": lambda r: httpx.Response(
            400, content=json.dumps({"error": "invalid_level"}).encode()
        ),
    }
    transport = httpx.MockTransport(_make_handler(routes))
    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(InvalidLevel):
            await _client(http, holder).start_proof(level="urn:shadownet:level:LX")


async def test_subject_blocked(holder) -> None:
    routes = {
        "/proof/start": lambda r: httpx.Response(
            403, content=json.dumps({"error": "subject_blocked"}).encode()
        ),
    }
    transport = httpx.MockTransport(_make_handler(routes))
    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(SubjectBlocked):
            await _client(http, holder).start_proof(level="urn:shadownet:level:L1")


async def test_poll_proof(holder) -> None:
    routes = {
        "/proof/status": lambda r: httpx.Response(
            200,
            content=json.dumps(
                {"shadownet:v": "0.1", "sessionId": "ses-01", "status": "ready"}
            ).encode(),
        ),
    }
    transport = httpx.MockTransport(_make_handler(routes))
    async with httpx.AsyncClient(transport=transport) as http:
        result = await _client(http, holder).poll_proof("ses-01")
    assert result.status == "ready"


async def test_request_issuance_returns_credential(holder) -> None:
    issuer_kp = Ed25519KeyPair.generate()
    _, holder_did = holder
    cred = new_credential(
        issuer=SCA_DID,
        subject=holder_did,
        level="urn:shadownet:level:L1",
        subject_type="person",
    )
    cred_jwt = issue_credential(issuer_key=issuer_kp, issuer_kid=SCA_DID, credential=cred)

    routes = {
        "/issuance": lambda r: httpx.Response(
            200,
            content=json.dumps({"shadownet:v": "0.1", "credential": cred_jwt}).encode(),
        ),
    }
    transport = httpx.MockTransport(_make_handler(routes))
    async with httpx.AsyncClient(transport=transport) as http:
        token, parsed = await _client(http, holder).request_issuance(
            session_id="ses-01",
            level="urn:shadownet:level:L1",
            subject_type="person",
        )
    assert token == cred_jwt
    assert parsed.iss == SCA_DID
    assert parsed.sub == holder_did


async def test_request_issuance_session_not_ready(holder) -> None:
    routes = {
        "/issuance": lambda r: httpx.Response(
            409, content=json.dumps({"error": "session_not_ready"}).encode()
        ),
    }
    transport = httpx.MockTransport(_make_handler(routes))
    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(SessionNotReady):
            await _client(http, holder).request_issuance(
                session_id="ses-01",
                level="urn:shadownet:level:L1",
                subject_type="person",
            )


async def test_request_issuance_session_consumed(holder) -> None:
    routes = {
        "/issuance": lambda r: httpx.Response(
            410, content=json.dumps({"error": "session_consumed"}).encode()
        ),
    }
    transport = httpx.MockTransport(_make_handler(routes))
    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(SessionConsumed):
            await _client(http, holder).request_issuance(
                session_id="ses-01",
                level="urn:shadownet:level:L1",
                subject_type="person",
            )


async def test_request_freshness(holder) -> None:
    issuer_kp = Ed25519KeyPair.generate()
    proof_jwt = sign_jwt(
        {
            "iss": SCA_DID,
            "sub": "urn:uuid:cred-1",
            "iat": int(time.time()),
            "exp": int(time.time()) + 86400,
            "shadownet:freshness": "v1",
        },
        issuer_kp,
    )
    routes = {
        "/freshness": lambda r: httpx.Response(
            200, content=json.dumps({"shadownet:v": "0.1", "freshnessProof": proof_jwt}).encode()
        ),
    }
    transport = httpx.MockTransport(_make_handler(routes))
    async with httpx.AsyncClient(transport=transport) as http:
        token, proof = await _client(http, holder).request_freshness(
            credential_jti="urn:uuid:cred-1"
        )
    assert token == proof_jwt
    assert proof.sub == "urn:uuid:cred-1"


async def test_request_freshness_unknown_jti(holder) -> None:
    routes = {
        "/freshness": lambda r: httpx.Response(
            404, content=json.dumps({"error": "unknown_jti"}).encode()
        ),
    }
    transport = httpx.MockTransport(_make_handler(routes))
    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(UnknownJti):
            await _client(http, holder).request_freshness(credential_jti="urn:uuid:none")


async def test_subject_auth_header_present(holder) -> None:
    """Confirm that authenticated requests carry a Bearer subject-auth JWT."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization", "")
        return httpx.Response(200, content=_proof_session_payload())

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        await _client(http, holder).start_proof(level="urn:shadownet:level:L1")
    assert captured["auth"].startswith("Bearer eyJ")
