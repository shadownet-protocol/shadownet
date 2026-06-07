"""The identity gate: verify a presented credential and proof-of-possession."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from shadownet.credential import CredentialError, verify_credential
from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.identifiers import (
    InvalidIdentifierError,
    is_public_key_identifier,
    parse_public_key,
)
from shadownet.status import StatusListError, check_revocation
from shadownet.trust import satisfies_policy

from shadownet_x402.errors import GateError
from shadownet_x402.pop import verify_pop

if TYPE_CHECKING:
    from collections.abc import Callable

    from shadownet.credential import VerifiedCredential
    from shadownet.trust import TrustStore

DEFAULT_REQUIRED_KINDS: tuple[str, ...] = ("org_affiliation",)


@dataclass(frozen=True, slots=True)
class ShadowIdentity:
    """A caller whose credential and key control have been verified."""

    sub: str
    org: str
    credential: VerifiedCredential


def default_resolve_subject_key(sub: str) -> Ed25519KeyPair:
    """Resolve a direct-mode subject (a multibase key) to its verifier."""
    if not is_public_key_identifier(sub):
        raise GateError(
            f"cannot resolve a signing key for Shadowname {sub!r}; inject resolve_subject_key"
        )
    try:
        return Ed25519KeyPair.from_public_bytes(parse_public_key(sub))
    except InvalidIdentifierError as exc:
        raise GateError(f"invalid subject key {sub!r}: {exc}") from exc


def run_identity_gate(
    *,
    credential_jws: str,
    pop_jws: str,
    resource_url: str,
    nonce: str,
    trust_store: TrustStore | None = None,
    required_kinds: tuple[str, ...] = DEFAULT_REQUIRED_KINDS,
    now: int | None = None,
    leeway: int = 60,
    resolve_subject_key: Callable[[str], Ed25519KeyPair] | None = None,
    resolve_issuer_key: Callable[[str], str] | None = None,
    check_issuer_authorized_for_org: Callable[[str, str], None] | None = None,
    check_revoked: Callable[[VerifiedCredential], None] | None = None,
) -> ShadowIdentity:
    """Verify the credential (signature, lifetime, §6.6, revocation, policy) and the
    proof-of-possession, returning the gated identity or raising GateError."""
    try:
        credential = verify_credential(
            credential_jws,
            now=now,
            leeway=leeway,
            resolve_issuer_key=resolve_issuer_key,
            check_issuer_authorized_for_org=check_issuer_authorized_for_org,
        )
    except CredentialError as exc:
        raise GateError(f"credential rejected: {exc}") from exc

    if credential.payload.kind not in required_kinds:
        raise GateError(f"credential kind {credential.payload.kind!r} not accepted")

    revoke_check = check_revoked if check_revoked is not None else check_revocation
    try:
        revoke_check(credential)
    except StatusListError as exc:
        raise GateError("credential revoked") from exc

    if trust_store is not None and not satisfies_policy(
        [credential], trust_store, required_kinds=required_kinds
    ):
        raise GateError("credential not accepted by trust policy")

    verify_pop(
        pop_jws,
        expected_sub=credential.payload.sub,
        expected_audience=resource_url,
        expected_nonce=nonce,
        resolve_subject_key=resolve_subject_key or default_resolve_subject_key,
        leeway=leeway,
    )

    return ShadowIdentity(
        sub=credential.payload.sub,
        org=credential.payload.org,
        credential=credential,
    )
