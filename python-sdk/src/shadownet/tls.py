"""TLS pinning for direct-mode connections — RFC 0001 §4.1, §5.3.

Direct-mode Shadows serve self-signed TLS certificates; senders MUST verify
the fingerprint either against a ``#sha256:`` pin carried in the
``shadow://`` URI (§3.2) or via Trust-On-First-Use (§5.3). This module
provides the pin store, the verification primitive, and an httpx transport
that wires both together so callers do not need to handle TLS internals.

The envelope JWS is the authoritative identity authenticator regardless of
TLS posture (RFC 0001 §11 "TLS in direct mode") — pinning protects the
channel from MITM rewrites, not the identity.
"""

from __future__ import annotations

import base64
import hashlib
import ssl
from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable

import httpx

from shadownet.errors import ShadownetError

if TYPE_CHECKING:
    from shadownet.addressing import DirectAddress

__all__ = [
    "DEFAULT_PINNED_TIMEOUT",
    "InMemoryTLSPinStore",
    "TLSPinMismatchError",
    "TLSPinStore",
    "compute_cert_fingerprint",
    "make_pinned_httpx_async_client",
    "make_pinned_httpx_client",
    "verify_tls_pin",
]


DEFAULT_PINNED_TIMEOUT: Final = 10.0


class TLSPinMismatchError(ShadownetError):
    """A direct-mode TLS certificate did not match the expected or recorded pin."""


@runtime_checkable
class TLSPinStore(Protocol):
    """Persistence interface for TOFU-recorded direct-mode TLS pins.

    Production sidecars plug in their own implementation (DB-backed, keychain,
    etc.) so pins survive restarts. The in-memory default is suitable for
    tests and short-lived processes.
    """

    def get(self, host_port: str) -> str | None: ...
    def record(self, host_port: str, fingerprint: str) -> None: ...


class InMemoryTLSPinStore:
    """RAM-backed TOFU pin store. Entries vanish on process restart."""

    def __init__(self) -> None:
        self._pins: dict[str, str] = {}

    def get(self, host_port: str) -> str | None:
        return self._pins.get(host_port)

    def record(self, host_port: str, fingerprint: str) -> None:
        self._pins[host_port] = fingerprint

    def __len__(self) -> int:
        return len(self._pins)


def compute_cert_fingerprint(der_bytes: bytes) -> str:
    """Return ``base64url(SHA-256(cert))`` with no padding — the wire form
    used in the ``shadow://`` URI's ``#sha256:`` fragment per RFC 0001 §3.2."""
    digest = hashlib.sha256(der_bytes).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def verify_tls_pin(
    peer_cert_der: bytes,
    *,
    expected_pin: str | None,
    tofu_store: TLSPinStore,
    host_port: str,
) -> str:
    """Validate the peer cert fingerprint and return the canonical form.

    Policy (RFC 0001 §5.3):
      1. If ``expected_pin`` is set (URI-supplied), MUST match.
      2. Otherwise, if the TOFU store has a recorded pin for ``host_port``,
         MUST match.
      3. Otherwise, record the current fingerprint as the TOFU pin and
         trust it (first-use).
    """
    actual = compute_cert_fingerprint(peer_cert_der)
    if expected_pin is not None:
        if actual != expected_pin:
            raise TLSPinMismatchError(
                f"peer cert fingerprint {actual!r} does not match URI pin "
                f"{expected_pin!r} for {host_port!r}"
            )
        return actual
    recorded = tofu_store.get(host_port)
    if recorded is not None:
        if actual != recorded:
            raise TLSPinMismatchError(
                f"peer cert fingerprint {actual!r} does not match recorded TOFU pin "
                f"{recorded!r} for {host_port!r}"
            )
        return actual
    tofu_store.record(host_port, actual)
    return actual


def _build_pinned_ssl_context() -> ssl.SSLContext:
    # CA validation is intentionally disabled; the cert is self-signed and we
    # do our own SHA-256 fingerprint check after the handshake. TLS 1.3 is
    # mandatory-to-implement per RFC 0001 §4.1.
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    return ctx


class _PinnedTransport(httpx.HTTPTransport):
    """httpx transport that grabs the peer cert post-handshake and pins.

    The handshake completes before any application data flows, but we only
    get access to the SSLObject after ``handle_request`` returns. We tolerate
    that ordering because Shadownet's direct-mode use cases are either GETs
    (AgentCard, status list — no sensitive data) or already-signed JWS POSTs
    (CSR submission), where MITM rewrites can be detected at the application
    layer. Production deployments that need pre-flight assurance can do a
    bare TLS connect to the same ``host:port`` before the first real request.
    """

    def __init__(
        self,
        *,
        host_port: str,
        expected_pin: str | None,
        tofu_store: TLSPinStore,
        timeout: float,
    ) -> None:
        super().__init__(verify=_build_pinned_ssl_context())
        self._host_port = host_port
        self._expected_pin = expected_pin
        self._tofu_store = tofu_store
        self._timeout = timeout

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = super().handle_request(request)
        try:
            network_stream = response.extensions.get("network_stream")
            if network_stream is None:
                raise TLSPinMismatchError(
                    "network_stream extension unavailable; cannot verify TLS pin"
                )
            ssl_object = network_stream.get_extra_info("ssl_object")
            if ssl_object is None:
                raise TLSPinMismatchError("SSL object unavailable; cannot verify TLS pin")
            peer_cert_der = ssl_object.getpeercert(binary_form=True)
            if not peer_cert_der:
                raise TLSPinMismatchError("peer certificate unavailable; cannot verify TLS pin")
            verify_tls_pin(
                peer_cert_der,
                expected_pin=self._expected_pin,
                tofu_store=self._tofu_store,
                host_port=self._host_port,
            )
        except TLSPinMismatchError:
            response.close()
            raise
        return response


def make_pinned_httpx_client(
    direct_address: DirectAddress,
    *,
    tofu_store: TLSPinStore | None = None,
    timeout: float = DEFAULT_PINNED_TIMEOUT,
) -> httpx.Client:
    """Build an httpx.Client that pins the TLS cert for a direct-mode Shadow.

    Pin verification policy (RFC 0001 §5.3):

      1. If ``direct_address.tls_pin_sha256`` (from the URI ``#sha256:``
         fragment) is set, MUST match.
      2. Otherwise, if ``tofu_store`` has a recorded pin for
         ``host:port``, MUST match.
      3. Otherwise, record the current fingerprint as the TOFU pin and
         trust it (first-use).

    The returned client should be used as a context manager (or explicitly
    closed) so the underlying transport tears down cleanly. Use the same
    ``tofu_store`` across calls to a given host to enforce TOFU; pass a fresh
    store only when you genuinely want first-use trust each time.
    """
    if tofu_store is None:
        tofu_store = InMemoryTLSPinStore()
    host_port = f"{direct_address.host}:{direct_address.port}"
    transport = _PinnedTransport(
        host_port=host_port,
        expected_pin=direct_address.tls_pin_sha256,
        tofu_store=tofu_store,
        timeout=timeout,
    )
    return httpx.Client(transport=transport, timeout=timeout)


class _PinnedAsyncTransport(httpx.AsyncHTTPTransport):
    """Async sibling of :class:`_PinnedTransport` — same pin policy."""

    def __init__(
        self,
        *,
        host_port: str,
        expected_pin: str | None,
        tofu_store: TLSPinStore,
        timeout: float,
    ) -> None:
        super().__init__(verify=_build_pinned_ssl_context())
        self._host_port = host_port
        self._expected_pin = expected_pin
        self._tofu_store = tofu_store
        self._timeout = timeout

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await super().handle_async_request(request)
        try:
            network_stream = response.extensions.get("network_stream")
            if network_stream is None:
                raise TLSPinMismatchError(
                    "network_stream extension unavailable; cannot verify TLS pin"
                )
            ssl_object = network_stream.get_extra_info("ssl_object")
            if ssl_object is None:
                raise TLSPinMismatchError("SSL object unavailable; cannot verify TLS pin")
            peer_cert_der = ssl_object.getpeercert(binary_form=True)
            if not peer_cert_der:
                raise TLSPinMismatchError("peer certificate unavailable; cannot verify TLS pin")
            verify_tls_pin(
                peer_cert_der,
                expected_pin=self._expected_pin,
                tofu_store=self._tofu_store,
                host_port=self._host_port,
            )
        except TLSPinMismatchError:
            await response.aclose()
            raise
        return response


def make_pinned_httpx_async_client(
    direct_address: DirectAddress,
    *,
    tofu_store: TLSPinStore | None = None,
    timeout: float = DEFAULT_PINNED_TIMEOUT,
) -> httpx.AsyncClient:
    """Async sibling of :func:`make_pinned_httpx_client`.

    Same pin verification policy. Returned ``httpx.AsyncClient`` should be
    used as an async context manager (or explicitly ``aclose``'d).
    """
    if tofu_store is None:
        tofu_store = InMemoryTLSPinStore()
    host_port = f"{direct_address.host}:{direct_address.port}"
    transport = _PinnedAsyncTransport(
        host_port=host_port,
        expected_pin=direct_address.tls_pin_sha256,
        tofu_store=tofu_store,
        timeout=timeout,
    )
    return httpx.AsyncClient(transport=transport, timeout=timeout)
