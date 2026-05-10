from __future__ import annotations

import json
import re
import time
from typing import TYPE_CHECKING
from urllib.parse import quote, unquote

import httpx

if TYPE_CHECKING:
    from collections.abc import Callable

from shadownet.did.document import DIDDocument
from shadownet.did.errors import (
    DIDDocumentTooLarge,
    DIDNotResolvable,
    DIDSyntaxError,
)
from shadownet.logging import get_logger

# RFC-0002 §did:web — organizations; document at https://<host>/.well-known/did.json
# (or https://<host>/<path>/did.json with colon-encoded paths). Cap: 16 KiB.

__all__ = ["DEFAULT_CACHE_TTL", "MAX_DOCUMENT_BYTES", "WebDIDResolver", "parse_did_web"]

_DID_WEB_PREFIX = "did:web:"
MAX_DOCUMENT_BYTES = 16 * 1024
DEFAULT_CACHE_TTL = 3600  # seconds; 1 hour per RFC-0002 §Resolution

_log = get_logger(__name__)
_MAX_AGE_RE = re.compile(r"max-age\s*=\s*(\d+)", re.IGNORECASE)


def parse_did_web(did: str) -> str:
    """Translate a ``did:web`` DID into the URL of its DID document."""
    if not did.startswith(_DID_WEB_PREFIX):
        raise DIDSyntaxError(f"not a did:web DID: {did!r}")
    tail = did.removeprefix(_DID_WEB_PREFIX).split("#", 1)[0].split("?", 1)[0]
    if not tail:
        raise DIDSyntaxError("did:web requires a host")
    parts = [unquote(segment) for segment in tail.split(":")]
    host = parts[0]
    if "/" in host or not host:
        raise DIDSyntaxError(f"did:web host is malformed: {host!r}")
    if len(parts) == 1:
        return f"https://{quote(host, safe=':')}/.well-known/did.json"
    path = "/".join(quote(p, safe="") for p in parts[1:])
    return f"https://{quote(host, safe=':')}/{path}/did.json"


class WebDIDResolver:
    """Async resolver for ``did:web`` with an in-memory TTL cache."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        default_ttl: int = DEFAULT_CACHE_TTL,
        max_bytes: int = MAX_DOCUMENT_BYTES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = http
        self._default_ttl = default_ttl
        self._max_bytes = max_bytes
        self._clock = clock
        self._cache: dict[str, tuple[float, DIDDocument]] = {}

    async def resolve(self, did: str) -> DIDDocument:
        url = parse_did_web(did)
        now = self._clock()
        cached = self._cache.get(did)
        if cached is not None and cached[0] > now:
            return cached[1]
        try:
            response = await self._http.get(
                url, headers={"Accept": "application/did+json, application/json"}
            )
        except httpx.HTTPError as exc:
            raise DIDNotResolvable(f"failed to fetch {url}: {exc}") from exc
        if response.status_code != 200:
            raise DIDNotResolvable(f"{url} returned HTTP {response.status_code}")
        body = response.content
        if len(body) > self._max_bytes:
            raise DIDDocumentTooLarge(f"{url} exceeded {self._max_bytes} bytes")
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise DIDNotResolvable(f"{url} returned invalid JSON: {exc}") from exc
        document = DIDDocument.model_validate(data)
        if document.id != did:
            raise DIDNotResolvable(
                f"document id {document.id!r} does not match requested DID {did!r}"
            )
        ttl = _ttl_from_cache_control(response.headers.get("cache-control"), self._default_ttl)
        self._cache[did] = (now + ttl, document)
        _log.debug("resolved %s (cached for %ds)", did, ttl)
        return document

    def invalidate(self, did: str | None = None) -> None:
        if did is None:
            self._cache.clear()
        else:
            self._cache.pop(did, None)


def _ttl_from_cache_control(header: str | None, default: int) -> int:
    if not header:
        return default
    if "no-store" in header.lower() or "no-cache" in header.lower():
        return 0
    match = _MAX_AGE_RE.search(header)
    if not match:
        return default
    return max(0, int(match.group(1)))
