from __future__ import annotations

import base64
import gzip
import time
from typing import TYPE_CHECKING

import httpx
from pydantic import BaseModel, ConfigDict

from shadownet.crypto.jwt import JWTError, decode_unverified_claims
from shadownet.logging import get_logger
from shadownet.vc.errors import Revoked, StatusListUnavailable

if TYPE_CHECKING:
    from collections.abc import Callable

# RFC-0003 §Revocation — BitstringStatusList VC; bit set ⇒ revoked.
# https://www.w3.org/TR/vc-bitstring-status-list/

DEFAULT_STATUS_LIST_TTL = 300  # 5 min, per RFC-0003

_log = get_logger(__name__)


class BitstringStatusList(BaseModel):
    """Parsed BitstringStatusList payload (the inner ``credentialSubject``)."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    encoded_list: str
    status_purpose: str = "revocation"

    @classmethod
    def from_credential(cls, credential_jwt: str) -> BitstringStatusList:
        try:
            claims = decode_unverified_claims(credential_jwt)
        except JWTError as exc:
            raise StatusListUnavailable(f"status list JWT invalid: {exc}") from exc
        try:
            subject = claims["vc"]["credentialSubject"]
        except (KeyError, TypeError) as exc:
            raise StatusListUnavailable(
                "status list credential lacks vc.credentialSubject"
            ) from exc
        encoded = subject.get("encodedList")
        if not isinstance(encoded, str):
            raise StatusListUnavailable("status list credential missing encodedList")
        return cls(encoded_list=encoded, status_purpose=subject.get("statusPurpose", "revocation"))

    def is_set(self, index: int) -> bool:
        bits = _decode_bitstring(self.encoded_list)
        if index < 0:
            raise ValueError("status list index must be non-negative")
        byte_index, bit_offset = divmod(index, 8)
        if byte_index >= len(bits):
            # Per the W3C spec, indices past the published list are treated as 0 (not revoked).
            return False
        return (bits[byte_index] >> (7 - bit_offset)) & 1 == 1


class StatusListClient:
    """Async fetcher with TTL cache + fail-closed checking per RFC-0003."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        default_ttl: int = DEFAULT_STATUS_LIST_TTL,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = http
        self._default_ttl = default_ttl
        self._clock = clock
        self._cache: dict[str, tuple[float, BitstringStatusList]] = {}

    async def fetch(self, url: str) -> BitstringStatusList:
        now = self._clock()
        cached = self._cache.get(url)
        if cached is not None and cached[0] > now:
            return cached[1]
        try:
            response = await self._http.get(url)
        except httpx.HTTPError as exc:
            raise StatusListUnavailable(f"failed to fetch {url}: {exc}") from exc
        if response.status_code != 200:
            raise StatusListUnavailable(f"{url} returned HTTP {response.status_code}")
        body = response.text.strip()
        try:
            sl = BitstringStatusList.from_credential(body)
        except StatusListUnavailable:
            raise
        except Exception as exc:
            raise StatusListUnavailable(f"could not parse status list at {url}: {exc}") from exc
        ttl = _ttl_from_cache_control(response.headers.get("cache-control"), self._default_ttl)
        self._cache[url] = (now + ttl, sl)
        _log.debug("status list %s cached for %ds", url, ttl)
        return sl

    async def check_not_revoked(self, url: str, index: int, *, fail_closed: bool) -> None:
        """Raise :class:`Revoked` if the bit is set, :class:`StatusListUnavailable` if fail_closed."""
        try:
            sl = await self.fetch(url)
        except StatusListUnavailable:
            if fail_closed:
                raise
            _log.warning("status list %s unavailable; allowing optimistic continuation", url)
            return
        if sl.is_set(index):
            raise Revoked(f"credential at index {index} is revoked")


def encode_bitstring(bits: bytes) -> str:
    """Encode a raw bitstring as gzip + base64url, the W3C status list format."""
    compressed = gzip.compress(bits)
    return base64.urlsafe_b64encode(compressed).rstrip(b"=").decode("ascii")


def _decode_bitstring(value: str) -> bytes:
    pad = "=" * (-len(value) % 4)
    try:
        compressed = base64.urlsafe_b64decode(value + pad)
        return gzip.decompress(compressed)
    except (ValueError, OSError) as exc:
        raise StatusListUnavailable(f"encodedList is not valid gzip+base64url: {exc}") from exc


def _ttl_from_cache_control(header: str | None, default: int) -> int:
    if not header:
        return default
    if "no-store" in header.lower() or "no-cache" in header.lower():
        return 0
    import re

    match = re.search(r"max-age\s*=\s*(\d+)", header, re.IGNORECASE)
    if not match:
        return default
    return max(0, int(match.group(1)))


__all__ = [
    "DEFAULT_STATUS_LIST_TTL",
    "BitstringStatusList",
    "StatusListClient",
    "encode_bitstring",
]
