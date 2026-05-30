"""Receiver pipeline — RFC 0001 §8.6 + §9.

Ties together AgentCard resolution, envelope verification, credential checks,
the replay cache, and the §9 classification policy. Used by the HTTP receiver
server (shadownet.servers.receiver) and by sidecar / test rigs that want to
run the full validation flow programmatically.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

from shadownet.a2a import (
    CredsRejectedError,
    CredsRequiredError,
    ParseError,
    PolicyError,
    ReplayError,
    ShadownetWireError,
    SignatureError,
    UnknownRecipientError,
    extract_envelope_jws,
)
from shadownet.agentcard import (
    AgentCardError,
    FetchedAgentCard,
    fetch_and_verify_agent_card,
)
from shadownet.credential import (
    CredentialError,
    VerifiedCredential,
    verify_credential,
)
from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.envelope import (
    ENVELOPE_EXTENSION_URI,
    EnvelopeError,
    EnvelopePayload,
    compute_msg_hash,
    verify_envelope,
)
from shadownet.identifiers import (
    canonicalize_identifier,
    is_public_key_identifier,
    is_shadowname,
    is_subdomain_of,
    parse_public_key,
    split_shadowname,
)
from shadownet.provider import (
    ProviderRecord,
    ProviderResolutionError,
    lookup_provider_record,
)
from shadownet.status import StatusListError, check_revocation
from shadownet.trust import AcceptancePolicy, TrustStore, satisfies_policy

__all__ = [
    "AUTO_ADD_LOOKBACK_DEFAULT",
    "CREDENTIAL_CACHE_DEFAULT_TTL",
    "REPLAY_CACHE_RETENTION",
    "AcceptedDecision",
    "ContactGraph",
    "CredentialCache",
    "InMemoryContactGraph",
    "InMemoryCredentialCache",
    "InMemoryReplayCache",
    "ReceiverConfig",
    "ReceiverPipeline",
    "ReplayCache",
    "Route",
]


# §8.9: cache (from, messageId) for >= 10 minutes (2x the max envelope lifetime).
REPLAY_CACHE_RETENTION: Final = 10 * 60
# §9 RECOMMENDED 7-day auto-add lookback.
AUTO_ADD_LOOKBACK_DEFAULT: Final = 7 * 24 * 60 * 60
# §8.6 step 9: cache credential acceptance until exp - 60.
CREDENTIAL_CACHE_LEEWAY: Final = 60
# Cap how long we keep a successfully verified credential cached when its
# remaining lifetime is longer than necessary; matches §6.3 lifetime bound.
CREDENTIAL_CACHE_DEFAULT_TTL: Final = 30 * 24 * 60 * 60


Route = Literal["inbox", "stranger_review"]


@dataclass(frozen=True, slots=True)
class AcceptedDecision:
    """Outcome of a successful §8.6 + §9 run."""

    route: Route
    sender: str
    envelope: EnvelopePayload
    auto_added_contact: bool = False


@runtime_checkable
class ReplayCache(Protocol):
    def seen(self, sender: str, message_id: str) -> bool: ...
    def remember(self, sender: str, message_id: str, *, retention_seconds: int) -> None: ...


@runtime_checkable
class ContactGraph(Protocol):
    def is_contact(self, shadowname: str) -> bool: ...
    def has_recent_outbound(self, *, context_id: str, peer: str, lookback_seconds: int) -> bool: ...
    def add_contact(self, shadowname: str) -> None: ...


@runtime_checkable
class CredentialCache(Protocol):
    def for_sender(self, sender: str) -> list[VerifiedCredential]: ...
    def cache(self, sender: str, credential: VerifiedCredential, *, expires_at: int) -> None: ...


class InMemoryReplayCache:
    """RAM-backed replay cache for the receiver's ``(from, messageId)`` set."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], int] = {}

    def seen(self, sender: str, message_id: str) -> bool:
        self._prune(int(time.time()))
        return (sender, message_id) in self._entries

    def remember(self, sender: str, message_id: str, *, retention_seconds: int) -> None:
        self._entries[(sender, message_id)] = int(time.time()) + retention_seconds

    def _prune(self, now: int) -> None:
        expired = [k for k, v in self._entries.items() if v <= now]
        for key in expired:
            del self._entries[key]


class InMemoryContactGraph:
    """Trivial contact graph with an outbound-context log for §9 auto-add."""

    def __init__(self) -> None:
        self._contacts: set[str] = set()
        self._outbound: dict[tuple[str, str], int] = {}

    def is_contact(self, shadowname: str) -> bool:
        return shadowname in self._contacts

    def add_contact(self, shadowname: str) -> None:
        self._contacts.add(shadowname)

    def remove_contact(self, shadowname: str) -> None:
        self._contacts.discard(shadowname)

    def record_outbound(self, *, context_id: str, peer: str) -> None:
        self._outbound[(context_id, peer)] = int(time.time())

    def has_recent_outbound(self, *, context_id: str, peer: str, lookback_seconds: int) -> bool:
        ts = self._outbound.get((context_id, peer))
        if ts is None:
            return False
        return ts >= int(time.time()) - lookback_seconds


class InMemoryCredentialCache:
    """RAM-backed per-sender credential cache. Entries expire on read."""

    def __init__(self) -> None:
        self._by_sender: dict[str, list[tuple[VerifiedCredential, int]]] = {}

    def for_sender(self, sender: str) -> list[VerifiedCredential]:
        now = int(time.time())
        entries = self._by_sender.get(sender, [])
        live = [(c, exp) for c, exp in entries if exp > now]
        self._by_sender[sender] = live
        return [c for c, _ in live]

    def cache(self, sender: str, credential: VerifiedCredential, *, expires_at: int) -> None:
        bucket = self._by_sender.setdefault(sender, [])
        bucket.append((credential, expires_at))


@dataclass(frozen=True, slots=True)
class ReceiverConfig:
    """Configuration the pipeline needs to make decisions.

    ``subject`` is the Shadowname served at this endpoint. ``policy`` and
    ``trust_store`` come from the receiver's own deployment. The
    ``same_provider_org`` flag enables the §9 shortcut and MUST be enabled
    only for single-tenant-org provider deployments.
    """

    subject: str
    trust_store: TrustStore
    policy: AcceptancePolicy = field(default_factory=AcceptancePolicy)
    same_provider_org: bool = False
    auto_add_lookback_seconds: int = AUTO_ADD_LOOKBACK_DEFAULT
    leeway_seconds: int = 60


class ReceiverPipeline:
    """Runs RFC 0001 §8.6 validation and §9 classification for one Subject.

    Callers supply the storage primitives so the pipeline stays decoupled
    from any concrete database, queue, or HTTP framework.
    """

    def __init__(
        self,
        config: ReceiverConfig,
        *,
        replay_cache: ReplayCache,
        contact_graph: ContactGraph,
        credential_cache: CredentialCache,
        provider_lookup: Callable[[str], ProviderRecord] | None = None,
        agent_card_fetcher: Callable[[str, ProviderRecord], FetchedAgentCard] | None = None,
        revocation_check: Callable[[VerifiedCredential], None] | None = None,
        now: Callable[[], int] | None = None,
    ) -> None:
        self.config = config
        self._replay = replay_cache
        self._contacts = contact_graph
        self._credentials = credential_cache
        self._provider_lookup = provider_lookup or lookup_provider_record
        self._fetch_agent_card = agent_card_fetcher or _default_agent_card_fetcher
        self._revocation_check = revocation_check or _default_revocation_check
        self._now = now or (lambda: int(time.time()))

    def receive(self, request_body: dict[str, object]) -> AcceptedDecision:
        # §8.6 step 2.
        envelope_jws, message = extract_envelope_jws(request_body)

        # §8.6 step 4: parse and validate envelope claims via verify_envelope,
        # which needs the sender's pk. We need to resolve before verify, but
        # verify_envelope rejects if `to` mismatches — so do a soft pre-parse
        # to learn the sender. We sign the typical receive flow as:
        #   pre-parse → resolve sender → AgentCard → verify_envelope (sig + claims)
        try:
            from shadownet.crypto.jwt import (
                decode_header,
                decode_unverified_claims,
            )

            header = decode_header(envelope_jws)
            unverified = decode_unverified_claims(envelope_jws)
        except Exception as exc:
            raise ParseError(f"envelope JWS unparseable: {exc}") from exc

        sender_raw = unverified.get("from") if isinstance(unverified, dict) else None
        recipient_raw = unverified.get("to") if isinstance(unverified, dict) else None
        if not isinstance(sender_raw, str) or not isinstance(recipient_raw, str):
            raise ParseError("envelope missing 'from' or 'to'")
        if header.get("kid") != sender_raw:
            raise ParseError("envelope JWS kid does not match 'from' claim")
        try:
            sender = canonicalize_identifier(sender_raw)
            recipient = canonicalize_identifier(recipient_raw)
        except Exception as exc:
            raise ParseError(f"invalid identifier: {exc}") from exc

        if recipient != self.config.subject:
            # §8.8: `unknown_recipient` is distinct from `policy` and `creds_rejected`.
            raise UnknownRecipientError(f"envelope to={recipient!r} not served at this URL")

        # §8.6 step 5: resolve the sender's signing key. Path differs by mode.
        sender_provider_record: ProviderRecord | None = None
        if is_public_key_identifier(sender):
            # Direct mode (§5.3): the sender IS the verification key. No DNS,
            # no AgentCard fetch needed for envelope signature validation.
            sender_key = Ed25519KeyPair.from_public_bytes(parse_public_key(sender))
        else:
            # Shadowname mode (§5.2): DNS TXT then provider-signed AgentCard.
            try:
                _, sender_provider = split_shadowname(sender)
                sender_provider_record = self._provider_lookup(sender_provider)
            except ProviderResolutionError as exc:
                raise SignatureError(f"could not resolve sender provider: {exc}") from exc

            try:
                card = self._fetch_agent_card(sender, sender_provider_record)
            except AgentCardError as exc:
                raise SignatureError(f"sender AgentCard verification failed: {exc}") from exc

            sender_key = Ed25519KeyPair.from_public_bytes(parse_public_key(card.shadow_public_key))

        # §8.6 step 6 + 3 (typ/alg checks) + claim revalidation.
        try:
            envelope = verify_envelope(
                envelope_jws,
                sender_key,
                expected_recipient=self.config.subject,
                now=self._now(),
                leeway=self.config.leeway_seconds,
            )
        except EnvelopeError as exc:
            if "signature" in str(exc).lower():
                raise SignatureError(str(exc)) from exc
            raise ParseError(str(exc)) from exc

        # §8.6 step 7: recompute msgHash from the carried message.
        recomputed = compute_msg_hash(message)
        if recomputed != envelope.msg_hash:
            raise ParseError(
                f"msgHash mismatch: envelope claims {envelope.msg_hash!r}, computed {recomputed!r}"
            )

        # §8.6 step 8: replay cache.
        message_id = message.get("messageId")
        if not isinstance(message_id, str) or not message_id:
            raise ParseError("message.messageId missing")
        if self._replay.seen(sender, message_id):
            raise ReplayError(f"({sender!r}, {message_id!r}) replayed")
        self._replay.remember(sender, message_id, retention_seconds=REPLAY_CACHE_RETENTION)

        # §8.6 step 9: credentials.
        sender_credentials = self._validate_creds(envelope, sender)

        context_id = message.get("contextId")
        if context_id is not None and not isinstance(context_id, str):
            raise ParseError("message.contextId must be a string when present")

        # §8.6 step 10 / §9: classification.
        route, auto_added = self._classify(
            sender=sender,
            envelope=envelope,
            sender_provider_record=sender_provider_record,
            credentials=sender_credentials,
            context_id=context_id,
        )

        return AcceptedDecision(
            route=route,
            sender=sender,
            envelope=envelope,
            auto_added_contact=auto_added,
        )

    def _validate_creds(self, envelope: EnvelopePayload, sender: str) -> list[VerifiedCredential]:
        """§8.6 step 9: validate creds present on the envelope, or fall back
        to the per-sender cache. Absence of creds is not itself an error —
        the classification step (§9) decides whether the policy requires them.
        """
        if envelope.creds:
            verified: list[VerifiedCredential] = []
            for jws in envelope.creds:
                try:
                    cred = verify_credential(
                        jws,
                        now=self._now(),
                        leeway=self.config.leeway_seconds,
                        resolve_issuer_key=self._resolve_issuer_key,
                        check_issuer_authorized_for_org=self._check_issuer_authorized_for_org,
                    )
                except CredentialError as exc:
                    raise CredsRejectedError(f"credential rejected: {exc}") from exc
                try:
                    self._revocation_check(cred)
                except StatusListError as exc:
                    raise CredsRejectedError(f"credential revoked or unverifiable: {exc}") from exc
                expires_at = min(
                    cred.payload.exp - CREDENTIAL_CACHE_LEEWAY,
                    self._now() + CREDENTIAL_CACHE_DEFAULT_TTL,
                )
                self._credentials.cache(sender, cred, expires_at=expires_at)
                verified.append(cred)
            return verified
        return self._credentials.for_sender(sender)

    def _classify(
        self,
        *,
        sender: str,
        envelope: EnvelopePayload,
        sender_provider_record: ProviderRecord | None,
        credentials: Iterable[VerifiedCredential],
        context_id: str | None,
    ) -> tuple[Route, bool]:
        # §9 same-provider-domain shortcut. NOT valid for direct-mode senders
        # (which have no provider) and only meaningful when this Subject is
        # itself addressed by Shadowname.
        if (
            self.config.same_provider_org
            and sender_provider_record is not None
            and is_shadowname(self.config.subject)
        ):
            _, recipient_provider = split_shadowname(self.config.subject)
            if recipient_provider == sender_provider_record.domain:
                return "inbox", False

        # Existing contact path.
        if self._contacts.is_contact(sender):
            return "inbox", False

        # §9 auto-add-on-outbound-initiated (the contextId lives on the A2A
        # message, not the envelope JWS — see §8.2 threading).
        if context_id and self._contacts.has_recent_outbound(
            context_id=context_id,
            peer=sender,
            lookback_seconds=self.config.auto_add_lookback_seconds,
        ):
            self._contacts.add_contact(sender)
            return "inbox", True

        # Otherwise we need to satisfy the stranger policy.
        required = self.config.policy.required_kinds(is_contact=False)
        if not required:
            # Stranger policy explicitly rejects (empty list = reject outright per §7.2).
            raise PolicyError(f"stranger {sender!r} rejected by policy")
        creds = list(credentials)
        if not satisfies_policy(creds, self.config.trust_store, required_kinds=required):
            if creds:
                raise CredsRejectedError(
                    f"no presented credential satisfies stranger policy for {sender!r}"
                )
            raise CredsRequiredError(f"stranger {sender!r} presented no credentials")
        return "stranger_review", False

    def _resolve_issuer_key(self, issuer_domain: str) -> str:
        try:
            record = self._provider_lookup(issuer_domain)
        except ProviderResolutionError as exc:
            raise CredentialError(f"could not resolve issuer {issuer_domain!r}: {exc}") from exc
        if not record.provider_keys:
            raise CredentialError(f"issuer {issuer_domain!r} has no provider key")
        return record.provider_keys[0]

    def _check_issuer_authorized_for_org(self, issuer: str, org: str) -> None:
        if is_subdomain_of(issuer, org):
            return
        try:
            org_record = self._provider_lookup(org)
        except ProviderResolutionError as exc:
            raise CredentialError(
                f"could not verify issuer authorization for {org!r}: {exc}"
            ) from exc
        if issuer.lower() in (d.lower() for d in org_record.delegates):
            return
        raise CredentialError(f"issuer {issuer!r} is not authorized to attest for org {org!r}")


def _default_agent_card_fetcher(
    shadowname: str, provider_record: ProviderRecord
) -> FetchedAgentCard:
    return fetch_and_verify_agent_card(shadowname, provider_record)


def _default_revocation_check(credential: VerifiedCredential) -> None:
    check_revocation(credential)


def header_includes_extension(a2a_extensions_header: str | None) -> bool:
    """§8.6 step 1 helper: A2A-Extensions includes urn:shadownet:0.2."""
    if not a2a_extensions_header:
        return False
    tokens = [t.strip() for t in a2a_extensions_header.split(",") if t.strip()]
    return ENVELOPE_EXTENSION_URI in tokens


def ensure_extension_declared(a2a_extensions_header: str | None) -> None:
    """Raises :class:`ParseError` if the A2A-Extensions header is missing the
    Shadownet URI. Per RFC 0001 §8.6 step 1 the A2A spec returns
    ``ExtensionSupportRequiredError`` here; this function maps it onto our
    own ``parse_error`` since Shadownet receivers MUST reject the request.
    """
    if not header_includes_extension(a2a_extensions_header):
        raise ParseError(f"A2A-Extensions header must include {ENVELOPE_EXTENSION_URI!r}")


# Re-export the wire error base so callers handling ``receive()`` only need
# a single import for the exception type to catch.
WireError = ShadownetWireError
