"""Status list — RFC 0001 §6.4.

Per-epoch revocation bitstring fetched from
``https://<iss-domain>/.well-known/shadownet/status/<epoch>``. Wire format is a
gzip-compressed raw bitstring, base64url-encoded (no padding) as a single
ASCII string with ``Content-Type: text/plain``.

Bit indexing is big-endian within each byte: bit at index 0 is the most
significant bit of byte 0, matching the W3C BitstringStatusList convention
and the v0.1 Go reference (``core/pkg/vc/statuslist.go``). v0.2 carries no
W3C VC wrapper around the list; the body IS the encoded bitstring.

Fetch failures and malformed lists are hard-fail per §6.4 ("verifiers MUST
fail closed").
"""

from __future__ import annotations

import base64
import gzip
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import httpx

from shadownet.errors import ShadownetError

if TYPE_CHECKING:
    from shadownet.credential import VerifiedCredential

__all__ = [
    "MAX_STATUS_BODY_BYTES",
    "StatusList",
    "StatusListError",
    "acheck_revocation",
    "afetch_status_list",
    "build_status_list_url",
    "check_revocation",
    "decode_status_list",
    "encode_status_list",
    "fetch_status_list",
]


# §6.4 status list body is RECOMMENDED max-age 300. The body itself is small
# (one bit per credential); cap it at 8 MiB which addresses 64M credentials.
MAX_STATUS_BODY_BYTES: Final = 8 * 1024 * 1024
DEFAULT_FETCH_TIMEOUT: Final = 10.0


class StatusListError(ShadownetError):
    """Status list could not be fetched, decoded, or checked."""


@dataclass(frozen=True, slots=True)
class StatusList:
    """Bitstring of revocation bits indexed by credential.rev.idx."""

    bits: bytes
    size: int

    @classmethod
    def empty(cls, size: int) -> StatusList:
        if size <= 0:
            size = 8
        byte_count = (size + 7) // 8
        return cls(bits=b"\x00" * byte_count, size=byte_count * 8)

    def is_revoked(self, idx: int) -> bool:
        if idx < 0 or idx >= self.size:
            raise StatusListError(f"status index {idx} out of range (size {self.size})")
        byte = self.bits[idx // 8]
        return (byte >> (7 - idx % 8)) & 1 == 1

    def with_revoked(self, idx: int) -> StatusList:
        if idx < 0 or idx >= self.size:
            raise StatusListError(f"status index {idx} out of range (size {self.size})")
        buf = bytearray(self.bits)
        buf[idx // 8] |= 1 << (7 - idx % 8)
        return StatusList(bits=bytes(buf), size=self.size)


def encode_status_list(status_list: StatusList) -> str:
    compressed = bytearray(gzip.compress(status_list.bits, mtime=0))
    # Byte 9 of the gzip envelope (RFC 1952) is the OS field, written from
    # the host platform by Python's gzip module (3 on Linux, 19 on macOS,
    # ...). Normalize to 255 ("unknown") so the encoded bytes are
    # platform-independent — required for deterministic conformance fixtures.
    if len(compressed) > 9:
        compressed[9] = 0xFF
    return base64.urlsafe_b64encode(bytes(compressed)).rstrip(b"=").decode("ascii")


def decode_status_list(body: str) -> StatusList:
    cleaned = body.strip()
    if not cleaned:
        raise StatusListError("empty status list body")
    try:
        compressed = base64.urlsafe_b64decode(cleaned + "=" * (-len(cleaned) % 4))
    except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        raise StatusListError(f"status list base64url decode failed: {exc}") from exc
    try:
        raw = gzip.decompress(compressed)
    except OSError as exc:
        raise StatusListError(f"status list gunzip failed: {exc}") from exc
    if not raw:
        raise StatusListError("status list bitstring is empty")
    return StatusList(bits=raw, size=len(raw) * 8)


def build_status_list_url(issuer_domain: str, epoch: str) -> str:
    if not issuer_domain or not epoch:
        raise StatusListError("issuer_domain and epoch are both required")
    return f"https://{issuer_domain}/.well-known/shadownet/status/{epoch}"


def fetch_status_list(
    issuer_domain: str,
    epoch: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = DEFAULT_FETCH_TIMEOUT,
) -> tuple[StatusList, int | None]:
    """Return ``(StatusList, cache_max_age_seconds_or_None)``.

    Fail-closed per §6.4: any error short-circuits to :class:`StatusListError`,
    which the credential layer turns into a revocation-positive outcome.
    """
    url = build_status_list_url(issuer_domain, epoch)
    owned: httpx.Client | None = None
    try:
        c = client
        if c is None:
            c = owned = httpx.Client(timeout=timeout)
        response = c.get(url, headers={"Accept": "text/plain"})
    except httpx.HTTPError as exc:
        raise StatusListError(f"status list fetch failed for {url!r}: {exc}") from exc
    finally:
        if owned is not None:
            owned.close()
    return _interpret_status_response(response, url)


async def afetch_status_list(
    issuer_domain: str,
    epoch: str,
    *,
    client: httpx.AsyncClient | None = None,
    timeout: float = DEFAULT_FETCH_TIMEOUT,
) -> tuple[StatusList, int | None]:
    """Async sibling of :func:`fetch_status_list` using ``httpx.AsyncClient``."""
    url = build_status_list_url(issuer_domain, epoch)
    owned: httpx.AsyncClient | None = None
    try:
        c = client
        if c is None:
            c = owned = httpx.AsyncClient(timeout=timeout)
        response = await c.get(url, headers={"Accept": "text/plain"})
    except httpx.HTTPError as exc:
        raise StatusListError(f"status list fetch failed for {url!r}: {exc}") from exc
    finally:
        if owned is not None:
            await owned.aclose()
    return _interpret_status_response(response, url)


def _interpret_status_response(response: httpx.Response, url: str) -> tuple[StatusList, int | None]:
    if response.status_code != 200:
        raise StatusListError(f"status list {url!r} returned HTTP {response.status_code}")
    body = response.text
    if len(body.encode("ascii", errors="ignore")) > MAX_STATUS_BODY_BYTES:
        raise StatusListError(f"status list {url!r} exceeds size cap")
    status_list = decode_status_list(body)
    max_age = _parse_max_age(response.headers.get("Cache-Control"))
    return status_list, max_age


def check_revocation(
    credential: VerifiedCredential,
    *,
    fetch: object | None = None,
    client: httpx.Client | None = None,
) -> None:
    """Verify ``credential`` is not revoked. Raises ``StatusListError`` if it is,
    or if the status list cannot be fetched / parsed."""
    fetcher = fetch if fetch is not None else fetch_status_list
    if callable(fetcher):
        status_list, _ = fetcher(
            credential.payload.iss, credential.payload.rev.epoch, client=client
        )
    else:  # pragma: no cover — defensive guard
        raise StatusListError("fetch parameter must be callable")
    _raise_if_revoked(credential, status_list)


async def acheck_revocation(
    credential: VerifiedCredential,
    *,
    fetch: object | None = None,
    client: httpx.AsyncClient | None = None,
) -> None:
    """Async sibling of :func:`check_revocation`.

    ``fetch`` defaults to :func:`afetch_status_list` and is awaited. Pass a
    custom async callable to wire in cached or test fetchers.
    """
    fetcher = fetch if fetch is not None else afetch_status_list
    if callable(fetcher):
        status_list, _ = await fetcher(
            credential.payload.iss, credential.payload.rev.epoch, client=client
        )
    else:  # pragma: no cover — defensive guard
        raise StatusListError("fetch parameter must be callable")
    _raise_if_revoked(credential, status_list)


def _raise_if_revoked(credential: VerifiedCredential, status_list: StatusList) -> None:
    if status_list.is_revoked(credential.payload.rev.idx):
        raise StatusListError(
            f"credential revoked at epoch={credential.payload.rev.epoch!r} "
            f"idx={credential.payload.rev.idx}"
        )


def _parse_max_age(cache_control: str | None) -> int | None:
    if not cache_control:
        return None
    for part in cache_control.split(","):
        token = part.strip().lower()
        if token.startswith("max-age="):
            try:
                return int(token[len("max-age=") :])
            except ValueError:
                return None
    return None
