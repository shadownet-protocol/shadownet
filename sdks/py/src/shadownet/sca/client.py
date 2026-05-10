from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from shadownet.crypto.jwt import JWTError, decode_unverified_claims
from shadownet.sca.csr import build_csr, build_subject_auth
from shadownet.sca.errors import SCAError, SCAHTTPError, code_to_error
from shadownet.sca.policy import SCAPolicy
from shadownet.vc.credential import SubjectCredential, decode_credential
from shadownet.vc.freshness import FreshnessProof

if TYPE_CHECKING:
    import httpx

    from shadownet.crypto.ed25519 import Ed25519KeyPair

# RFC-0004 — async client for the on-protocol SCA endpoints.

__all__ = ["NextStep", "ProofSession", "ProofStatus", "ProofStatusResponse", "SCAClient"]


ProofStatus = Literal["pending", "ready", "failed", "expired"]


class NextStep(BaseModel):
    model_config = ConfigDict(extra="allow")

    kind: Literal["redirect", "embed", "email-link", "in-person"]
    url: str | None = None
    ttl: int | None = Field(default=None, ge=1)


class ProofSession(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    shadownet_v: Literal["0.1"] = Field(alias="shadownet:v")
    session_id: str = Field(alias="sessionId")
    expires_at: int = Field(alias="expiresAt", ge=0)
    # RFC-0004 §Policy document: `method` is an operator-defined URI; not URN-only.
    method: str = Field(min_length=1)
    next: NextStep


class ProofStatusResponse(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    shadownet_v: Literal["0.1"] = Field(alias="shadownet:v")
    session_id: str = Field(alias="sessionId")
    status: ProofStatus


class SCAClient:
    """Async client for an SCA's RFC-0004 endpoints.

    The client is responsible for minting the subject-auth JWT for every
    authenticated request. Construction is cheap; one client per (subject DID,
    SCA) pair is the natural unit, but it can be shared across calls.
    """

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        sca_base_url: str,
        sca_did: str,
        holder_key: Ed25519KeyPair,
        holder_did: str,
    ) -> None:
        self._http = http
        self._base = sca_base_url.rstrip("/")
        self._sca_did = sca_did
        self._holder_key = holder_key
        self._holder_did = holder_did

    # ----- public surface -----

    async def fetch_policy(self) -> SCAPolicy:
        response = await self._http.get(f"{self._base}/.well-known/sca/policy.json")
        self._raise_for_status(response)
        return SCAPolicy.model_validate(response.json())

    async def start_proof(
        self,
        *,
        level: str,
        callback_url: str | None = None,
    ) -> ProofSession:
        body: dict[str, object] = {
            "shadownet:v": "0.1",
            "subject": self._holder_did,
            "level": level,
        }
        if callback_url is not None:
            body["callbackUrl"] = callback_url
        response = await self._post("/proof/start", body)
        return ProofSession.model_validate(response.json())

    async def poll_proof(self, session_id: str) -> ProofStatusResponse:
        response = await self._post(
            "/proof/status",
            {"shadownet:v": "0.1", "sessionId": session_id},
        )
        return ProofStatusResponse.model_validate(response.json())

    async def request_issuance(
        self,
        *,
        session_id: str,
        level: str,
        subject_type: Literal["person", "organization"],
    ) -> tuple[str, SubjectCredential]:
        """Build a CSR, exchange it for a credential, return ``(jwt, parsed)``.

        The credential's signature is *not* verified here — the caller must do
        so via :func:`shadownet.vc.verify_credential` (we do not have the SCA's
        DID document at this layer; it's the caller's choice of resolver).
        """
        csr = build_csr(
            holder_key=self._holder_key,
            holder_did=self._holder_did,
            sca_did=self._sca_did,
            level=level,
            subject_type=subject_type,
        )
        response = await self._post(
            "/issuance",
            {"shadownet:v": "0.1", "csr": csr, "sessionId": session_id},
        )
        payload = response.json()
        token = payload["credential"]
        return token, decode_credential(token)

    async def request_freshness(self, *, credential_jti: str) -> tuple[str, FreshnessProof]:
        response = await self._post(
            "/freshness",
            {"shadownet:v": "0.1", "credentialJti": credential_jti},
        )
        token = response.json()["freshnessProof"]
        try:
            claims = decode_unverified_claims(token)
        except JWTError as exc:
            raise SCAError(f"freshness proof JWT invalid: {exc}") from exc
        return token, FreshnessProof.model_validate(claims)

    # ----- internals -----

    async def _post(self, path: str, body: dict[str, object]) -> httpx.Response:
        auth = build_subject_auth(
            holder_key=self._holder_key,
            holder_did=self._holder_did,
            sca_did=self._sca_did,
        )
        response = await self._http.post(
            f"{self._base}{path}",
            json=body,
            headers={"Authorization": f"Bearer {auth}"},
        )
        self._raise_for_status(response)
        return response

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if 200 <= response.status_code < 300:
            return
        code: str | None = None
        detail: str | None = None
        try:
            payload = response.json()
            if isinstance(payload, dict):
                code = payload.get("error") or payload.get("code")
                detail = payload.get("detail") or payload.get("message")
        except ValueError:
            pass
        if code:
            raise code_to_error(code, detail)
        raise SCAHTTPError(f"HTTP {response.status_code}: {response.text[:200]}")
