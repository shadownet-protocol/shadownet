from __future__ import annotations

import time
from typing import TYPE_CHECKING

import httpx

from shadownet.logging import get_logger
from shadownet.sns.errors import (
    ShadownameInvalid,
    ShadownameNotFound,
    ShadownameTombstoned,
    SNSError,
)
from shadownet.sns.record import SNSRecord, parse_shadowname, verify_record

if TYPE_CHECKING:
    from collections.abc import Callable

    from shadownet.did.resolver import Resolver

# RFC-0005 §Resolution endpoint.

DEFAULT_NEGATIVE_TTL = 60

__all__ = ["DEFAULT_NEGATIVE_TTL", "SNSClient"]

_log = get_logger(__name__)


class SNSClient:
    """Async resolver for shadownames.

    The provider DID is derived from the shadowname's host: ``alice@x.example``
    is resolved by fetching ``https://x.example/.well-known/sns/v1/resolve``,
    and the signed record's issuer MUST equal ``did:web:x.example``.
    """

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        resolver: Resolver,
        clock: Callable[[], float] = time.monotonic,
        negative_ttl_seconds: int = DEFAULT_NEGATIVE_TTL,
    ) -> None:
        self._http = http
        self._resolver = resolver
        self._clock = clock
        self._negative_ttl = negative_ttl_seconds
        self._cache: dict[str, tuple[float, SNSRecord]] = {}
        self._negative_cache: dict[str, float] = {}

    async def resolve(self, shadowname: str) -> SNSRecord:
        local, provider = parse_shadowname(shadowname)
        canonical = f"{local}@{provider}"
        now = self._clock()

        cached = self._cache.get(canonical)
        if cached is not None and cached[0] > now:
            return cached[1]
        miss_until = self._negative_cache.get(canonical)
        if miss_until is not None and miss_until > now:
            raise ShadownameNotFound(canonical)

        url = f"https://{provider}/.well-known/sns/v1/resolve"
        params = {"name": canonical}
        try:
            response = await self._http.get(
                url,
                params=params,
                headers={"Accept": "application/jwt"},
            )
        except httpx.HTTPError as exc:
            raise SNSError(f"failed to fetch {url}: {exc}") from exc

        if response.status_code == 404:
            self._negative_cache[canonical] = now + self._negative_ttl
            raise ShadownameNotFound(canonical)
        if response.status_code == 410:
            self._negative_cache[canonical] = now + self._negative_ttl
            raise ShadownameTombstoned(canonical)
        if response.status_code != 200:
            raise SNSError(f"{url} returned HTTP {response.status_code}")

        token = response.text.strip()
        provider_did = f"did:web:{provider}"
        record = await verify_record(
            token,
            expected_provider_did=provider_did,
            resolver=self._resolver,
        )
        if record.shadowname != canonical:
            raise ShadownameInvalid(
                f"record.shadowname {record.shadowname!r} does not match {canonical!r}"
            )

        cache_control = response.headers.get("cache-control", "").lower()
        if "no-store" not in cache_control:
            self._cache[canonical] = (now + record.ttl, record)
            _log.debug("cached SNS record for %s for %ds", canonical, record.ttl)
        return record

    def invalidate(self, shadowname: str | None = None) -> None:
        if shadowname is None:
            self._cache.clear()
            self._negative_cache.clear()
            return
        local, provider = parse_shadowname(shadowname)
        canonical = f"{local}@{provider}"
        self._cache.pop(canonical, None)
        self._negative_cache.pop(canonical, None)
