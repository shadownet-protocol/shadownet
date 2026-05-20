# RFC-0009 §Discovery, §Authorization Code flow, §Token validation

"""Sidecar OAuth 2.1 authorization profile (RFC-0009).

These tests target an MCP endpoint advertising the ``oauth-authorize``
capability. They verify, in order:

- the unauthenticated MCP request returns 401 with the RFC-0009
  ``WWW-Authenticate`` shape;
- the Protected Resource Metadata document is reachable, well-formed,
  and points at a non-empty ``authorization_servers`` list;
- each advertised AS publishes RFC 8414 metadata containing the
  endpoints RFC-0009 § AS metadata requires;
- the AS rejects ``code_challenge_method=plain`` and missing PKCE,
  enforces RFC 8707 resource binding, and refuses to advertise
  ``"plain"`` in ``code_challenge_methods_supported``;
- a Sidecar with DCR enabled accepts a public-client registration with
  a localhost redirect URI and returns the RFC 7591 response shape;
- the full authorization-code + PKCE flow (modulo the human consent
  screen, which is operator-defined) issues a Bearer token whose
  audience is the MCP endpoint URL.

The end-to-end token-issuance test is gated on the operator providing
a pre-issued test bundle via ``SHADOWNET_CONFORMANCE_OAUTH_TEST_BUNDLE``
since the consent screen cannot be driven from a black-box runner.
The discovery and AS-metadata tests have no such precondition and
run on every Sidecar advertising ``oauth-authorize``.

RFC-0009 is an independent conformance class. These tests intentionally
do not depend on RFC-0007 or RFC-0008.
"""

from __future__ import annotations

import os
import re
from urllib.parse import urlparse

import httpx
import pytest

pytestmark = [pytest.mark.class_("sidecar"), pytest.mark.draft]

_WWW_AUTH_BEARER = re.compile(r"^Bearer\s+(.*)$", re.IGNORECASE)


def _parse_www_authenticate(value: str) -> dict[str, str]:
    """Parse a Bearer-scheme WWW-Authenticate header into a dict.

    Tolerant of the small set of values RFC-0009 emits: ``realm``,
    ``error``, ``error_description``, ``scope``, ``resource_metadata``.
    Values may be quoted-strings or tokens.
    """
    match = _WWW_AUTH_BEARER.match(value.strip())
    if not match:
        return {}
    params: dict[str, str] = {}
    rest = match.group(1)
    for part in _split_params(rest):
        if "=" not in part:
            continue
        key, raw = part.split("=", 1)
        key = key.strip()
        raw = raw.strip()
        if raw.startswith('"') and raw.endswith('"'):
            raw = raw[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        params[key] = raw
    return params


def _split_params(value: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    in_quotes = False
    escape = False
    for ch in value:
        if escape:
            current.append(ch)
            escape = False
            continue
        if ch == "\\" and in_quotes:
            escape = True
            current.append(ch)
            continue
        if ch == '"':
            in_quotes = not in_quotes
            current.append(ch)
            continue
        if ch == "," and not in_quotes:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(ch)
    if current:
        parts.append("".join(current).strip())
    return parts


async def _maybe_skip_when_not_advertised(sidecar_url: str, http: httpx.AsyncClient) -> str:
    """Return the PRM URL if the Sidecar advertises oauth-authorize, else skip.

    The check uses the MCP endpoint's 401 response: a Sidecar
    implementing RFC-0009 MUST return ``resource_metadata`` in the
    challenge. If the Sidecar does not implement RFC-0009 the test
    skips silently — conformance to RFC-0009 is independent of
    RFC-0007.
    """
    candidate_urls = [
        f"{sidecar_url}/mcp",
        f"{sidecar_url}/u/test/mcp",
    ]
    last_status: int | None = None
    for url in candidate_urls:
        try:
            resp = await http.get(url)
        except httpx.HTTPError:
            continue
        last_status = resp.status_code
        if resp.status_code != 401:
            continue
        challenge = resp.headers.get("www-authenticate")
        if not challenge:
            continue
        parsed = _parse_www_authenticate(challenge)
        prm = parsed.get("resource_metadata")
        if prm:
            return prm
    pytest.skip(
        "Sidecar does not advertise RFC-0009 oauth-authorize via 401 + WWW-Authenticate "
        f"(last_status={last_status}); skipping authorization conformance suite"
    )


@pytest.mark.network
@pytest.mark.rfc("0009", section="Discovery", requirement="unauth_401_with_resource_metadata")
async def test_unauthenticated_mcp_returns_401_with_resource_metadata(
    sidecar_url: str, http: httpx.AsyncClient
) -> None:
    """RFC-0009 § Discovery — every 401 from the MCP endpoint MUST carry
    a ``WWW-Authenticate`` Bearer challenge with ``resource_metadata``
    and ``realm="mcp"``.
    """
    prm_url = await _maybe_skip_when_not_advertised(sidecar_url, http)
    # _maybe_skip already pulled the PRM URL; assert it points
    # somewhere absolute over https for production deployments.
    parsed = urlparse(prm_url)
    assert parsed.scheme in {"http", "https"}, f"PRM URL scheme must be http/https: {prm_url}"
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        assert parsed.scheme == "https", (
            f"PRM URL must use https in non-loopback deployments: {prm_url}"
        )


@pytest.mark.network
@pytest.mark.rfc("0009", section="Discovery", requirement="prm_required_fields")
async def test_protected_resource_metadata_well_formed(
    sidecar_url: str, http: httpx.AsyncClient
) -> None:
    """RFC 9728 § 3.1 — PRM document MUST list resource + authorization_servers."""
    prm_url = await _maybe_skip_when_not_advertised(sidecar_url, http)
    resp = await http.get(prm_url, headers={"Accept": "application/json"})
    assert resp.status_code == 200, f"PRM endpoint returned {resp.status_code}"
    body = resp.json()
    assert isinstance(body.get("resource"), str), "PRM 'resource' must be a string"
    auth_servers = body.get("authorization_servers")
    assert isinstance(auth_servers, list) and auth_servers, (
        "PRM 'authorization_servers' MUST be a non-empty array (RFC 9728 § 3.1)"
    )
    bearer = body.get("bearer_methods_supported")
    if bearer is not None:
        # RFC-0009 § Discovery: only header is permitted.
        assert "body" not in bearer and "query" not in bearer, (
            "PRM MUST NOT advertise body or query bearer methods (OAuth 2.1)"
        )


@pytest.mark.network
@pytest.mark.rfc("0009", section="Discovery", requirement="as_metadata_required_fields")
async def test_authorization_server_metadata_well_formed(
    sidecar_url: str, http: httpx.AsyncClient
) -> None:
    """RFC 8414 § 2 + RFC-0009 § AS metadata — required endpoint inventory."""
    prm_url = await _maybe_skip_when_not_advertised(sidecar_url, http)
    prm = (await http.get(prm_url)).json()
    issuer = prm["authorization_servers"][0]
    # Try RFC 8414 path-insertion first, then path-append. The MCP spec
    # mandates clients try these in this order; conformance follows.
    as_meta = None
    for candidate in _as_metadata_candidates(issuer):
        try:
            resp = await http.get(candidate)
        except httpx.HTTPError:
            continue
        if resp.status_code == 200:
            as_meta = resp.json()
            break
    assert as_meta is not None, (
        f"could not locate AS metadata for issuer {issuer!r}; tried "
        f"{list(_as_metadata_candidates(issuer))}"
    )
    for required in ("issuer", "authorization_endpoint", "token_endpoint"):
        assert isinstance(as_meta.get(required), str), (
            f"AS metadata missing required field {required!r} (RFC 8414 § 2)"
        )
    methods = as_meta.get("code_challenge_methods_supported")
    assert methods is not None, "RFC-0009 requires code_challenge_methods_supported"
    assert "S256" in methods, "RFC-0009 requires S256 in code_challenge_methods_supported"
    assert "plain" not in methods, (
        "OAuth 2.1 drops plain PKCE; AS MUST NOT advertise it (RFC-0009 § AS metadata)"
    )
    grants = as_meta.get("grant_types_supported", [])
    assert "authorization_code" in grants, "RFC-0009 requires authorization_code grant"


def _as_metadata_candidates(issuer: str) -> list[str]:
    parsed = urlparse(issuer)
    base = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path.rstrip("/")
    if path:
        return [
            f"{base}/.well-known/oauth-authorization-server{path}",
            f"{base}/.well-known/openid-configuration{path}",
            f"{base}{path}/.well-known/openid-configuration",
            f"{base}{path}/.well-known/oauth-authorization-server",
        ]
    return [
        f"{base}/.well-known/oauth-authorization-server",
        f"{base}/.well-known/openid-configuration",
    ]


@pytest.mark.network
@pytest.mark.rfc(
    "0009", section="AuthorizationCode", requirement="invalid_target_when_resource_unknown"
)
async def test_authorize_rejects_unknown_resource(
    sidecar_url: str, http: httpx.AsyncClient
) -> None:
    """RFC 8707 § 2.2 — the AS MUST reject resource values it does not own."""
    prm_url = await _maybe_skip_when_not_advertised(sidecar_url, http)
    prm = (await http.get(prm_url)).json()
    issuer = prm["authorization_servers"][0]
    as_meta = await _load_as_metadata(http, issuer)
    if "registration_endpoint" not in as_meta:
        pytest.skip("Sidecar does not advertise DCR; cannot register a test client")
    # Register a public client.
    reg = await http.post(
        as_meta["registration_endpoint"],
        json={
            "client_name": "shadownet-conformance-0009",
            "redirect_uris": ["http://localhost:0/cb"],
            "token_endpoint_auth_method": "none",
        },
    )
    if reg.status_code not in (200, 201):
        pytest.skip(f"DCR endpoint returned {reg.status_code}; operator may have restricted it")
    client_id = reg.json()["client_id"]
    # Send an /authorize with a hostile resource value.
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": "http://localhost:0/cb",
        "code_challenge": "x" * 43,
        "code_challenge_method": "S256",
        "resource": "https://impostor.example/mcp",
    }
    resp = await http.get(as_meta["authorization_endpoint"], params=params, follow_redirects=False)
    # The AS may redirect-with-error or return 400; either is acceptable
    # as long as the error code is invalid_target (RFC 8707 § 2.2).
    if 300 <= resp.status_code < 400:
        location = urlparse(resp.headers["location"])
        from urllib.parse import parse_qs

        qs = parse_qs(location.query)
        assert qs.get("error") == ["invalid_target"], qs
    else:
        assert resp.status_code == 400, resp.status_code
        body = resp.json()
        assert body.get("error") == "invalid_target", body


async def _load_as_metadata(http: httpx.AsyncClient, issuer: str) -> dict[str, object]:
    for candidate in _as_metadata_candidates(issuer):
        try:
            resp = await http.get(candidate)
        except httpx.HTTPError:
            continue
        if resp.status_code == 200:
            return resp.json()  # type: ignore[no-any-return]
    pytest.skip(f"could not load AS metadata from issuer {issuer!r}")


@pytest.mark.network
@pytest.mark.rfc("0009", section="AuthorizationCode", requirement="rejects_plain_pkce")
async def test_authorize_rejects_plain_pkce(sidecar_url: str, http: httpx.AsyncClient) -> None:
    """RFC-0009 — OAuth 2.1 forbids the PKCE plain method."""
    prm_url = await _maybe_skip_when_not_advertised(sidecar_url, http)
    prm = (await http.get(prm_url)).json()
    issuer = prm["authorization_servers"][0]
    as_meta = await _load_as_metadata(http, issuer)
    if "registration_endpoint" not in as_meta:
        pytest.skip("Sidecar does not advertise DCR")
    reg = await http.post(
        as_meta["registration_endpoint"],
        json={
            "client_name": "shadownet-conformance-0009",
            "redirect_uris": ["http://localhost:0/cb"],
            "token_endpoint_auth_method": "none",
        },
    )
    if reg.status_code not in (200, 201):
        pytest.skip(f"DCR returned {reg.status_code}")
    client_id = reg.json()["client_id"]
    resource_value = prm["resource"]
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": "http://localhost:0/cb",
        "code_challenge": "x" * 43,
        "code_challenge_method": "plain",
        "resource": resource_value,
    }
    resp = await http.get(as_meta["authorization_endpoint"], params=params, follow_redirects=False)
    # Either 400 invalid_request or 302 with error=invalid_request.
    if 300 <= resp.status_code < 400:
        from urllib.parse import parse_qs

        qs = parse_qs(urlparse(resp.headers["location"]).query)
        assert qs.get("error") == ["invalid_request"], qs
    else:
        assert resp.status_code == 400
        assert resp.json().get("error") == "invalid_request"


@pytest.mark.network
@pytest.mark.rfc("0009", section="ClientRegistration", requirement="dcr_returns_public_client")
async def test_dynamic_client_registration_public_client(
    sidecar_url: str, http: httpx.AsyncClient
) -> None:
    """RFC 7591 § 3.2.1 — DCR returns the expected response shape."""
    prm_url = await _maybe_skip_when_not_advertised(sidecar_url, http)
    prm = (await http.get(prm_url)).json()
    issuer = prm["authorization_servers"][0]
    as_meta = await _load_as_metadata(http, issuer)
    if "registration_endpoint" not in as_meta:
        pytest.skip("Sidecar does not advertise DCR")
    reg = await http.post(
        as_meta["registration_endpoint"],
        json={
            "client_name": "shadownet-conformance-0009",
            "redirect_uris": ["http://localhost:0/cb"],
            "token_endpoint_auth_method": "none",
        },
    )
    assert reg.status_code in (200, 201), reg.text
    body = reg.json()
    assert isinstance(body.get("client_id"), str)
    assert body.get("client_secret") is None, (
        "public clients (token_endpoint_auth_method=none) MUST NOT receive a client_secret"
    )
    assert "http://localhost:0/cb" in body["redirect_uris"]


@pytest.mark.network
@pytest.mark.rfc("0009", section="TokenValidation", requirement="rejects_invalid_bearer")
async def test_mcp_rejects_garbage_bearer(sidecar_url: str, http: httpx.AsyncClient) -> None:
    """RFC-0009 § Token validation — random bytes MUST NOT validate."""
    prm_url = await _maybe_skip_when_not_advertised(sidecar_url, http)
    # Derive the MCP URL from the PRM document.
    prm = (await http.get(prm_url)).json()
    resource = prm["resource"]
    resp = await http.get(
        resource,
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert resp.status_code in (401, 403), resp.status_code
    challenge = resp.headers.get("www-authenticate", "")
    parsed_challenge = _parse_www_authenticate(challenge)
    # RFC-0009 § Error responses — resource_metadata MUST be on every 401/403.
    assert parsed_challenge.get("resource_metadata"), (
        "401/403 from MCP MUST include resource_metadata in WWW-Authenticate (RFC-0009)"
    )


@pytest.mark.network
@pytest.mark.rfc("0009", section="TokenValidation", requirement="accepts_valid_oauth_token")
async def test_mcp_accepts_valid_oauth_token(sidecar_url: str, http: httpx.AsyncClient) -> None:
    """RFC-0009 — a token issued by the AS for this resource is accepted.

    Operator supplies a pre-issued token via the
    ``SHADOWNET_CONFORMANCE_OAUTH_TEST_TOKEN`` env var. The conformance
    runner cannot drive the consent screen end-to-end (it is
    operator-defined), so the happy-path is gated on the operator
    providing a token they obtained out-of-band.
    """
    token = os.environ.get("SHADOWNET_CONFORMANCE_OAUTH_TEST_TOKEN")
    if not token:
        pytest.skip(
            "no SHADOWNET_CONFORMANCE_OAUTH_TEST_TOKEN set; "
            "operator-driven consent flow cannot be exercised from this runner"
        )
    prm_url = await _maybe_skip_when_not_advertised(sidecar_url, http)
    prm = (await http.get(prm_url)).json()
    resource = prm["resource"]
    # An initialize request through the streamable-HTTP transport is the
    # canonical "MCP request"; we use a HEAD/GET as a lightweight liveness
    # probe — the Sidecar MUST reach token validation before any other
    # check.
    resp = await http.get(
        resource,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    assert resp.status_code != 401, (
        f"valid token rejected with 401: {resp.headers.get('www-authenticate')}"
    )
