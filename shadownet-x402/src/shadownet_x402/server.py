"""The resource-server paywall: gate -> 402 challenge -> settle, as pure logic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from shadownet.credential import CredentialError, verify_credential
from shadownet.crypto.jwt import JWTError, decode_unverified_claims
from shadownet.status import StatusListError, check_revocation

from shadownet_x402.errors import (
    AmountMismatchError,
    BudgetError,
    GateError,
    ReplayError,
    SettlementError,
)
from shadownet_x402.gate import run_identity_gate
from shadownet_x402.requirements import build_payment_requirements
from shadownet_x402.settlement import enforce_agreed_equals_paid, parse_x_payment

if TYPE_CHECKING:
    from collections.abc import Callable

    from shadownet.credential import VerifiedCredential
    from shadownet.crypto.ed25519 import Ed25519KeyPair
    from shadownet.trust import TrustStore

    from shadownet_x402.budget import BudgetStore
    from shadownet_x402.config import Settings
    from shadownet_x402.gate import ShadowIdentity
    from shadownet_x402.nonce import NonceStore
    from shadownet_x402.requirements import PaymentRequirements
    from shadownet_x402.settlement import Facilitator, SettleOutcome


@dataclass(frozen=True, slots=True)
class Challenge:
    """The 402 response: pay these requirements, signing over this nonce."""

    requirements: PaymentRequirements
    nonce: str


@dataclass(frozen=True, slots=True)
class Settled:
    """The payment settled; serve the resource and return the receipt."""

    identity: ShadowIdentity
    outcome: SettleOutcome


@dataclass(frozen=True, slots=True)
class Refused:
    """The request was refused before settlement."""

    status: int
    reason: str


PaywallResult = Challenge | Settled | Refused


class Paywall:
    """Gate a resource by verified Shadow identity, then settle x402 payment."""

    def __init__(
        self,
        settings: Settings,
        *,
        nonce_store: NonceStore,
        budget_store: BudgetStore,
        facilitator: Facilitator,
        trust_store: TrustStore | None = None,
        resolve_subject_key: Callable[[str], Ed25519KeyPair] | None = None,
        resolve_issuer_key: Callable[[str], str] | None = None,
        check_issuer_authorized_for_org: Callable[[str, str], None] | None = None,
        check_revoked: Callable[[VerifiedCredential], None] | None = None,
    ) -> None:
        self._settings = settings
        self._nonce = nonce_store
        self._budget = budget_store
        self._facilitator = facilitator
        self._trust_store = trust_store
        self._resolve_subject_key = resolve_subject_key
        self._resolve_issuer_key = resolve_issuer_key
        self._check_issuer_authorized_for_org = check_issuer_authorized_for_org
        self._check_revoked = check_revoked

    def process(
        self,
        *,
        resource_url: str,
        credential: str | None = None,
        pop: str | None = None,
        x_payment: str | None = None,
    ) -> PaywallResult:
        if x_payment is None:
            return self._challenge(resource_url, credential)
        return self._settle(resource_url, credential, pop, x_payment)

    def _challenge(self, resource_url: str, credential: str | None) -> PaywallResult:
        if not credential:
            return Refused(401, "Shadow-Credential header required")
        try:
            verified = verify_credential(
                credential,
                resolve_issuer_key=self._resolve_issuer_key,
                check_issuer_authorized_for_org=self._check_issuer_authorized_for_org,
            )
        except CredentialError as exc:
            return Refused(403, f"credential rejected: {exc}")
        sub = verified.payload.sub
        if not self._budget.allowed(sub):
            return Refused(403, "identity revoked")
        try:
            self._revoked(verified)
        except StatusListError:
            return Refused(403, "credential revoked")
        nonce = self._nonce.issue(ttl=self._settings.nonce_ttl_seconds, identity_key=sub)
        return Challenge(
            build_payment_requirements(self._settings, resource_url=resource_url), nonce
        )

    def _settle(
        self, resource_url: str, credential: str | None, pop: str | None, x_payment: str
    ) -> PaywallResult:
        if not credential or not pop:
            return Refused(401, "Shadow-Credential and Shadow-PoP headers required")
        try:
            nonce = str(decode_unverified_claims(pop).get("nonce", ""))
        except JWTError:
            return Refused(400, "malformed proof-of-possession")
        try:
            identity = run_identity_gate(
                credential_jws=credential,
                pop_jws=pop,
                resource_url=resource_url,
                nonce=nonce,
                trust_store=self._trust_store,
                resolve_subject_key=self._resolve_subject_key,
                resolve_issuer_key=self._resolve_issuer_key,
                check_issuer_authorized_for_org=self._check_issuer_authorized_for_org,
                check_revoked=self._check_revoked,
            )
        except GateError as exc:
            return Refused(403, f"identity refused: {exc}")
        try:
            self._nonce.consume(nonce, identity_key=identity.sub)
        except ReplayError:
            return Refused(409, "payment nonce reused or expired")
        requirements = build_payment_requirements(self._settings, resource_url=resource_url)
        try:
            enforce_agreed_equals_paid(parse_x_payment(x_payment), requirements)
        except AmountMismatchError as exc:
            return Refused(402, str(exc))
        try:
            self._budget.reserve(identity.sub, requirements.amount)
        except BudgetError as exc:
            return Refused(402, str(exc))
        try:
            outcome = self._facilitator.settle(x_payment, requirements)
        except SettlementError as exc:
            return Refused(402, str(exc))
        if not outcome.success:
            return Refused(402, outcome.error_reason or "settlement failed")
        self._budget.record(identity.sub, requirements.amount)
        return Settled(identity, outcome)

    def _revoked(self, credential: VerifiedCredential) -> None:
        check = self._check_revoked if self._check_revoked is not None else check_revocation
        check(credential)
