"""In-process state stores for the RFC-0009 Authorization Server.

The reference Sidecar runs co-located with its AS and keeps client
registrations, authorization codes, and refresh-token families in
memory. Operators wanting a Postgres-backed AS (e.g. ``shadownet-cloud``)
swap in their own implementation of the :class:`ClientStore`,
:class:`AuthorizationCodeStore`, and :class:`RefreshTokenStore`
Protocols; nothing else in :mod:`shadownet.oauth.server` depends on the
storage shape.

The default in-memory stores are async-safe behind a single
:class:`asyncio.Lock` each so co-located deployments running under
``uvicorn`` workers see consistent state. They are not durable —
restarts wipe outstanding codes and refresh families. That is the
correct property for v0.1; access tokens are short-lived JWTs whose
validity outlives the AS's memory, so the only operational impact of a
restart is that in-flight authorization codes must be re-obtained.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass
from typing import Protocol

__all__ = [
    "AuthorizationCode",
    "AuthorizationCodeStore",
    "ClientRegistration",
    "ClientStore",
    "InMemoryAuthorizationCodeStore",
    "InMemoryClientStore",
    "InMemoryRefreshTokenStore",
    "RefreshTokenRecord",
    "RefreshTokenStore",
]


@dataclass(frozen=True, slots=True)
class ClientRegistration:
    """One registered OAuth client.

    Confidential clients carry a non-empty ``client_secret``; public
    clients (the host-agent default) carry ``None``. ``redirect_uris``
    is the exact-match allowlist enforced at authorization time per
    OAuth 2.1 § 7.12.
    """

    client_id: str
    client_secret: str | None
    redirect_uris: tuple[str, ...]
    grant_types: tuple[str, ...]
    response_types: tuple[str, ...]
    token_endpoint_auth_method: str
    client_name: str | None
    scope: str | None
    client_id_issued_at: int


@dataclass(frozen=True, slots=True)
class AuthorizationCode:
    """One outstanding authorization code awaiting redemption."""

    code: str
    client_id: str
    redirect_uri: str
    code_challenge: str
    code_challenge_method: str
    scope: frozenset[str]
    resource: str
    subject: str
    expires_at: int
    # The DID / tenant identifier the consent screen authenticated. The
    # AS stores it so the redeemed token's `sub` is bound to the user
    # who actually consented, not a value the client can lie about.
    consented_at: int = 0


@dataclass(slots=True)
class RefreshTokenRecord:
    """One refresh token in a rotation family.

    Refresh tokens are rotated on use per OAuth 2.1 best practice. The
    ``family_id`` groups every token issued from the same original
    consent. On replay (presentation of a rotated-out token) the
    entire family is revoked — see :meth:`RefreshTokenStore.consume`.
    """

    token: str
    family_id: str
    client_id: str
    subject: str
    resource: str
    scope: frozenset[str]
    expires_at: int
    consumed: bool = False
    revoked: bool = False


class ClientStore(Protocol):
    async def register(self, client: ClientRegistration) -> None: ...
    async def get(self, client_id: str) -> ClientRegistration | None: ...


class AuthorizationCodeStore(Protocol):
    async def put(self, code: AuthorizationCode) -> None: ...
    async def consume(self, code: str) -> AuthorizationCode | None:
        """Single-use redemption — returns the record once and never again."""


class RefreshTokenStore(Protocol):
    async def put(self, record: RefreshTokenRecord) -> None: ...
    async def get(self, token: str) -> RefreshTokenRecord | None: ...
    async def rotate(
        self, *, presented: str, new_record: RefreshTokenRecord
    ) -> RefreshTokenRecord | None:
        """Atomically consume ``presented`` and persist ``new_record``.

        Returns the consumed record on success. Returns ``None`` if the
        presented token was unknown, already consumed (replay), or
        already revoked. Implementations MUST revoke the entire family
        on replay per RFC-0009 § Refresh tokens.
        """

    async def revoke_family(self, family_id: str) -> None: ...
    async def revoke_token(self, token: str) -> None: ...


def _now() -> int:
    return int(time.time())


def _generate_token(length: int = 32) -> str:
    return secrets.token_urlsafe(length)


class InMemoryClientStore(ClientStore):
    """Async-safe in-memory :class:`ClientStore`."""

    def __init__(self) -> None:
        self._clients: dict[str, ClientRegistration] = {}
        self._lock = asyncio.Lock()

    async def register(self, client: ClientRegistration) -> None:
        async with self._lock:
            self._clients[client.client_id] = client

    async def get(self, client_id: str) -> ClientRegistration | None:
        async with self._lock:
            return self._clients.get(client_id)


class InMemoryAuthorizationCodeStore(AuthorizationCodeStore):
    """Async-safe in-memory :class:`AuthorizationCodeStore`."""

    def __init__(self) -> None:
        self._codes: dict[str, AuthorizationCode] = {}
        self._lock = asyncio.Lock()

    async def put(self, code: AuthorizationCode) -> None:
        async with self._lock:
            self._codes[code.code] = code

    async def consume(self, code: str) -> AuthorizationCode | None:
        async with self._lock:
            record = self._codes.pop(code, None)
            if record is None:
                return None
            if record.expires_at < _now():
                return None
            return record


class InMemoryRefreshTokenStore(RefreshTokenStore):
    """Async-safe in-memory :class:`RefreshTokenStore` with family revocation."""

    def __init__(self) -> None:
        self._records: dict[str, RefreshTokenRecord] = {}
        self._lock = asyncio.Lock()

    async def put(self, record: RefreshTokenRecord) -> None:
        async with self._lock:
            self._records[record.token] = record

    async def get(self, token: str) -> RefreshTokenRecord | None:
        async with self._lock:
            return self._records.get(token)

    async def rotate(
        self, *, presented: str, new_record: RefreshTokenRecord
    ) -> RefreshTokenRecord | None:
        async with self._lock:
            current = self._records.get(presented)
            if current is None:
                return None
            if current.revoked:
                return None
            if current.expires_at < _now():
                return None
            if current.consumed:
                # Replay of a rotated-out token — revoke the entire
                # family per RFC-0009 § Refresh tokens.
                self._revoke_family_unlocked(current.family_id)
                return None
            current.consumed = True
            self._records[new_record.token] = new_record
            return current

    async def revoke_family(self, family_id: str) -> None:
        async with self._lock:
            self._revoke_family_unlocked(family_id)

    async def revoke_token(self, token: str) -> None:
        async with self._lock:
            record = self._records.get(token)
            if record is not None:
                record.revoked = True

    def _revoke_family_unlocked(self, family_id: str) -> None:
        for record in self._records.values():
            if record.family_id == family_id:
                record.revoked = True


def new_in_memory_stores() -> tuple[ClientStore, AuthorizationCodeStore, RefreshTokenStore]:
    """Convenience: build a fresh trio of in-memory stores.

    Used by tests and by the FastAPI helper when no operator-supplied
    stores are provided.
    """
    return InMemoryClientStore(), InMemoryAuthorizationCodeStore(), InMemoryRefreshTokenStore()
