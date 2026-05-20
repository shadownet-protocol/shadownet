"""RFC-0009 Authorization Server + Resource Server primitives.

Two classes carry the policy:

- :class:`AuthorizationServer` mints and rotates tokens for a single
  tenant's MCP endpoint. It owns the issuer URL, the JWT signing key,
  the registered clients, the outstanding authorization codes, and the
  refresh-token rotation family.

- :class:`ResourceServer` validates inbound bearer tokens on every MCP
  request and enforces per-tool scope checks.

The FastAPI wiring lives in :mod:`shadownet.oauth.fastapi`; that module
imports only from here and from :mod:`fastapi`, so this file stays
import-clean for callers who want to drive the AS from a non-FastAPI
host (Starlette, Litestar, custom ASGI, an embedded test harness).

Token format: signed JWTs in EdDSA over the Sidecar's per-tenant signing
key. Storing tokens server-side is not required because the JWT carries
its own `iss`, `aud`, `exp`, `scope`, `sub`, and `jti`, which is what
the Resource Server needs to make an authorization decision without
round-tripping the AS. The downside is bounded revocation latency — see
``revoke_token`` for the introspection-style hook operators can wire in.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from shadownet.crypto.jwt import JWTError, decode_unverified_claims, sign_jwt, verify_jwt
from shadownet.logging import get_logger
from shadownet.oauth.errors import (
    InvalidClient,
    InvalidGrant,
    InvalidRedirectURI,
    InvalidRequest,
    InvalidScope,
    InvalidTarget,
    InvalidToken,
    UnauthorizedClient,
    UnsupportedGrantType,
)
from shadownet.oauth.models import (
    AuthorizationServerMetadata,
    ClientRegistrationRequest,
    ClientRegistrationResponse,
    ProtectedResourceMetadata,
    TokenResponse,
)
from shadownet.oauth.pkce import verify_s256
from shadownet.oauth.scopes import (
    DEFAULT_SCOPES,
    SCOPE_OFFLINE_ACCESS,
    parse_scope_string,
    scope_string,
)
from shadownet.oauth.store import (
    AuthorizationCode,
    AuthorizationCodeStore,
    ClientRegistration,
    ClientStore,
    InMemoryAuthorizationCodeStore,
    InMemoryClientStore,
    InMemoryRefreshTokenStore,
    RefreshTokenRecord,
    RefreshTokenStore,
)

if TYPE_CHECKING:
    from shadownet.crypto.ed25519 import Ed25519KeyPair

__all__ = [
    "AuthorizationConsent",
    "AuthorizationRequest",
    "AuthorizationServer",
    "AuthorizationServerSettings",
    "ResourceServer",
    "TokenClaims",
    "validate_redirect_uri",
]

_log = get_logger(__name__)

# RFC-0009 § Authorization Code flow — short-lived per OAuth 2.1 best
# practice. RFC-0009 RECOMMENDS ≤ 60s; we set 60.
DEFAULT_AUTHORIZATION_CODE_TTL_SECONDS = 60
DEFAULT_ACCESS_TOKEN_TTL_SECONDS = 60 * 60  # 1h per RFC-0009 open-question
DEFAULT_REFRESH_TOKEN_TTL_SECONDS = 60 * 60 * 24 * 30  # 30d
DEFAULT_CLOCK_SKEW_SECONDS = 60


def _now() -> int:
    return int(time.time())


def _new_token(prefix: str, length: int = 32) -> str:
    return f"{prefix}_{secrets.token_urlsafe(length)}"


@dataclass(frozen=True, slots=True)
class AuthorizationServerSettings:
    """Per-tenant Authorization Server configuration.

    The AS issues tokens for exactly one ``resource`` (one Sidecar MCP
    endpoint). Multi-tenant Sidecars stand up one
    :class:`AuthorizationServer` per tenant, each with its own issuer,
    signing key, and resource URL.
    """

    issuer: str
    resource: str
    signing_key: Ed25519KeyPair
    key_id: str
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str | None = None
    revocation_endpoint: str | None = None
    introspection_endpoint: str | None = None
    jwks_uri: str | None = None
    scopes_supported: tuple[str, ...] = DEFAULT_SCOPES
    allow_dynamic_client_registration: bool = True
    access_token_ttl_seconds: int = DEFAULT_ACCESS_TOKEN_TTL_SECONDS
    refresh_token_ttl_seconds: int = DEFAULT_REFRESH_TOKEN_TTL_SECONDS
    authorization_code_ttl_seconds: int = DEFAULT_AUTHORIZATION_CODE_TTL_SECONDS
    clock_skew_seconds: int = DEFAULT_CLOCK_SKEW_SECONDS


class AuthorizationRequest(BaseModel):
    """Parsed `/oauth/authorize` query parameters."""

    model_config = ConfigDict(extra="allow")

    response_type: str
    client_id: str
    redirect_uri: str
    code_challenge: str
    code_challenge_method: str
    resource: str
    scope: str | None = None
    state: str | None = None


@dataclass(frozen=True, slots=True)
class AuthorizationConsent:
    """Result of the human-facing consent step.

    The consent screen lives outside this module (operators integrate
    with their own login / session layer). When the resource owner
    approves, the integration calls
    :meth:`AuthorizationServer.issue_code` with the granted subject and
    scope set; this object captures that decision.
    """

    subject: str
    granted_scopes: frozenset[str]


class TokenClaims(BaseModel):
    """Decoded access-token claims, after signature + audience checks."""

    model_config = ConfigDict(extra="allow")

    iss: str
    aud: str
    sub: str
    client_id: str
    scope: str
    exp: int
    iat: int
    jti: str

    @property
    def scope_set(self) -> frozenset[str]:
        return parse_scope_string(self.scope)


def validate_redirect_uri(*, registered: tuple[str, ...], presented: str) -> bool:
    """Match ``presented`` against the registered allowlist per OAuth 2.1.

    RFC-0009 § Security considerations relaxes exact-port matching for
    ``http://localhost`` and ``http://127.0.0.1`` loopback URIs — the
    host MUST match but the port MAY differ, because host agents bind
    to an ephemeral free port at runtime. For every other URI the
    match MUST be byte-exact.
    """
    if presented in registered:
        return True
    from urllib.parse import urlparse

    try:
        presented_parsed = urlparse(presented)
    except ValueError:
        return False
    if presented_parsed.scheme != "http":
        return False
    if presented_parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        return False
    for entry in registered:
        try:
            entry_parsed = urlparse(entry)
        except ValueError:
            continue
        if (
            entry_parsed.scheme == "http"
            and entry_parsed.hostname == presented_parsed.hostname
            and entry_parsed.path == presented_parsed.path
        ):
            return True
    return False


class AuthorizationServer:
    """Per-tenant OAuth 2.1 Authorization Server.

    The class is stateless except for the stores it composes (which it
    does not own — operators may share stores across tenants when they
    want). Every method takes its inputs by keyword for callsite
    readability and returns rich :class:`OAuthError` subclasses rather
    than HTTP responses so callers can map errors to whichever transport
    they prefer.
    """

    def __init__(
        self,
        settings: AuthorizationServerSettings,
        *,
        clients: ClientStore | None = None,
        codes: AuthorizationCodeStore | None = None,
        refresh: RefreshTokenStore | None = None,
    ) -> None:
        self._settings = settings
        self._clients = clients or InMemoryClientStore()
        self._codes = codes or InMemoryAuthorizationCodeStore()
        self._refresh = refresh or InMemoryRefreshTokenStore()

    @property
    def settings(self) -> AuthorizationServerSettings:
        return self._settings

    def metadata(self) -> AuthorizationServerMetadata:
        """Build the RFC 8414 § 2 AS metadata document."""
        s = self._settings
        return AuthorizationServerMetadata(
            issuer=s.issuer,
            authorization_endpoint=s.authorization_endpoint,
            token_endpoint=s.token_endpoint,
            registration_endpoint=(
                s.registration_endpoint if s.allow_dynamic_client_registration else None
            ),
            revocation_endpoint=s.revocation_endpoint,
            introspection_endpoint=s.introspection_endpoint,
            jwks_uri=s.jwks_uri,
            scopes_supported=list(s.scopes_supported),
            response_types_supported=["code"],
            grant_types_supported=["authorization_code", "refresh_token"],
            code_challenge_methods_supported=["S256"],
            token_endpoint_auth_methods_supported=["none", "client_secret_post"],
        )

    async def register_client(
        self, request: ClientRegistrationRequest
    ) -> ClientRegistrationResponse:
        """RFC 7591 DCR — register a new client.

        Public clients ask for ``token_endpoint_auth_method=none``; the
        AS returns no ``client_secret``. Confidential clients get a
        generated secret. Redirect URIs are validated only for shape
        (``https://`` everywhere or loopback ``http://``) — operator
        trust policy is enforced by wrapping this method in a higher
        layer if needed.
        """
        if not self._settings.allow_dynamic_client_registration:
            raise InvalidRequest("dynamic client registration is disabled on this AS")

        for uri in request.redirect_uris:
            if not _is_acceptable_redirect_uri(uri):
                raise InvalidRequest(
                    f"redirect_uri must be https:// or loopback http://; got {uri!r}"
                )
        # Scope subsetting: RFC 7591 lets clients enumerate scopes
        # they intend to request; the AS narrows to what it advertises.
        requested = parse_scope_string(request.scope) if request.scope else frozenset()
        if requested:
            unknown = requested - set(self._settings.scopes_supported)
            if unknown:
                raise InvalidScope(f"requested unknown scopes: {sorted(unknown)}")
        client_id = _new_token("cid")
        client_secret: str | None = None
        if request.token_endpoint_auth_method != "none":  # noqa: S105
            client_secret = secrets.token_urlsafe(48)
        client = ClientRegistration(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uris=tuple(request.redirect_uris),
            grant_types=tuple(request.grant_types),
            response_types=tuple(request.response_types),
            token_endpoint_auth_method=request.token_endpoint_auth_method,
            client_name=request.client_name,
            scope=request.scope,
            client_id_issued_at=_now(),
        )
        await self._clients.register(client)
        _log.info(
            "registered oauth client client_id=%s name=%r public=%s",
            client_id,
            request.client_name,
            client_secret is None,
        )
        return ClientRegistrationResponse(
            client_id=client_id,
            client_id_issued_at=client.client_id_issued_at,
            client_secret=client_secret,
            redirect_uris=list(client.redirect_uris),
            client_name=client.client_name,
            grant_types=list(client.grant_types),
            response_types=list(client.response_types),
            token_endpoint_auth_method=client.token_endpoint_auth_method,
            scope=client.scope,
        )

    async def get_client(self, client_id: str) -> ClientRegistration:
        """Look up a registered client or raise :class:`InvalidClient`."""
        client = await self._clients.get(client_id)
        if client is None:
            raise InvalidClient(f"unknown client_id {client_id!r}")
        return client

    async def validate_authorization_request(
        self, request: AuthorizationRequest
    ) -> ClientRegistration:
        """Validate an `/oauth/authorize` request to the point of consent.

        Returns the resolved :class:`ClientRegistration` so the caller
        (the consent screen) can display the client's name and
        decisions. Raises an :class:`OAuthError` subclass when the
        request is malformed.

        Ordering matters: client and redirect_uri are validated *first*
        so that subsequent errors (PKCE, scope, resource) can be
        safely redirected back to the client per OAuth 2.1 § 4.1.2.1.
        Errors raised before the redirect is trusted are rendered
        directly to the user-agent.
        """
        # 1. Resolve the client. An unknown client_id is not safe to
        #    redirect anywhere — the value is attacker-controlled.
        client = await self.get_client(request.client_id)
        # 2. Validate the redirect URI against the client's allowlist.
        if not validate_redirect_uri(
            registered=client.redirect_uris, presented=request.redirect_uri
        ):
            raise InvalidRedirectURI(
                f"redirect_uri {request.redirect_uri!r} is not registered for this client"
            )
        # 3. From here on, the redirect URI is trusted, so we can
        #    redirect-with-error per OAuth 2.1 § 4.1.2.1.
        if request.response_type != "code":
            raise UnsupportedGrantType(
                f"response_type {request.response_type!r} not supported; only 'code' is permitted"
            )
        if request.code_challenge_method != "S256":
            raise InvalidRequest(
                "PKCE code_challenge_method must be 'S256'; plain is forbidden by OAuth 2.1"
            )
        if not request.code_challenge:
            raise InvalidRequest("PKCE code_challenge is required")
        if request.resource != self._settings.resource:
            # RFC 8707 § 2.2 — invalid_target when the requested
            # resource is not one this AS will issue tokens for.
            raise InvalidTarget(
                f"resource must equal {self._settings.resource!r}; got {request.resource!r}"
            )
        if "authorization_code" not in client.grant_types:
            raise UnauthorizedClient("client is not authorized for the authorization_code grant")
        requested = parse_scope_string(request.scope) if request.scope else frozenset()
        if requested:
            unknown = requested - set(self._settings.scopes_supported)
            if unknown:
                raise InvalidScope(f"requested unknown scopes: {sorted(unknown)}")
        return client

    async def issue_code(
        self,
        request: AuthorizationRequest,
        consent: AuthorizationConsent,
    ) -> AuthorizationCode:
        """Persist and return a single-use authorization code.

        Caller is the consent screen integration: it has already
        authenticated the resource owner and confirmed they approved
        the listed scopes. ``consent.granted_scopes`` MAY be a subset
        of what the client requested; the AS narrows requested scopes
        to that subset per OAuth 2.1 § 3.3.
        """
        # Re-validate so the call is safe regardless of whether
        # ``validate_authorization_request`` was called first.
        await self.validate_authorization_request(request)
        requested = parse_scope_string(request.scope) if request.scope else frozenset()
        granted = consent.granted_scopes if not requested else (requested & consent.granted_scopes)
        # RFC-0009 § Consent — issuing scope the user did not approve is
        # a protocol violation. ``granted`` is the intersection by
        # construction; the assert below is a belt-and-braces guard.
        assert granted <= consent.granted_scopes
        code = AuthorizationCode(
            code=_new_token("code"),
            client_id=request.client_id,
            redirect_uri=request.redirect_uri,
            code_challenge=request.code_challenge,
            code_challenge_method=request.code_challenge_method,
            scope=granted,
            resource=request.resource,
            subject=consent.subject,
            expires_at=_now() + self._settings.authorization_code_ttl_seconds,
            consented_at=_now(),
        )
        await self._codes.put(code)
        return code

    async def exchange_authorization_code(
        self,
        *,
        code: str,
        client_id: str,
        redirect_uri: str,
        code_verifier: str,
        resource: str,
        client_secret: str | None = None,
    ) -> TokenResponse:
        """OAuth 2.1 § 4.1.3 — exchange an auth code for tokens."""
        record = await self._codes.consume(code)
        if record is None:
            raise InvalidGrant("authorization code unknown, expired, or already redeemed")
        if record.client_id != client_id:
            raise InvalidGrant("authorization code was not issued to this client")
        if record.redirect_uri != redirect_uri:
            raise InvalidGrant("redirect_uri does not match the value used to obtain the code")
        if record.resource != resource:
            # RFC 8707 § 2 — resource MUST be presented identically on both
            # the authorization and token requests.
            raise InvalidTarget("resource parameter does not match the original authorization")
        if not verify_s256(verifier=code_verifier, challenge=record.code_challenge):
            raise InvalidGrant("PKCE verification failed")
        client = await self.get_client(client_id)
        self._authenticate_client(client, client_secret)
        return await self._mint_token_pair(
            client_id=client.client_id,
            subject=record.subject,
            scope=record.scope,
            resource=record.resource,
        )

    async def exchange_refresh_token(
        self,
        *,
        refresh_token: str,
        client_id: str,
        resource: str | None = None,
        requested_scope: frozenset[str] | None = None,
        client_secret: str | None = None,
    ) -> TokenResponse:
        """OAuth 2.1 § 4.3 / 6 — rotate a refresh token for a new pair.

        Rotation: every successful redemption consumes the presented
        refresh token and returns a freshly-minted one bound to the
        same family. Re-use of a consumed token revokes the whole
        family (RFC-0009 § Refresh tokens).
        """
        current = await self._refresh.get(refresh_token)
        if current is None:
            raise InvalidGrant("refresh token unknown")
        if current.client_id != client_id:
            raise InvalidGrant("refresh token was not issued to this client")
        client = await self.get_client(client_id)
        self._authenticate_client(client, client_secret)
        if resource is not None and resource != current.resource:
            raise InvalidTarget("resource does not match the originally-bound resource")
        if requested_scope is not None:
            extra = requested_scope - current.scope
            if extra:
                # OAuth 2.1 § 6 — scope of the new access token MUST NOT
                # exceed the scope of the refresh token.
                raise InvalidScope(
                    f"refresh request asked for scopes outside the granted set: {sorted(extra)}"
                )
            effective_scope = requested_scope
        else:
            effective_scope = current.scope

        new_refresh = RefreshTokenRecord(
            token=_new_token("rt"),
            family_id=current.family_id,
            client_id=client.client_id,
            subject=current.subject,
            resource=current.resource,
            scope=current.scope,
            expires_at=_now() + self._settings.refresh_token_ttl_seconds,
        )
        rotated = await self._refresh.rotate(presented=refresh_token, new_record=new_refresh)
        if rotated is None:
            # Either replay (family revoked by the store) or expired —
            # either way we MUST NOT issue a token.
            raise InvalidGrant("refresh token rotation failed (replay or expiry)")
        access = self._mint_access_token(
            client_id=client.client_id,
            subject=current.subject,
            scope=effective_scope,
            resource=current.resource,
        )
        return TokenResponse(
            access_token=access,
            expires_in=self._settings.access_token_ttl_seconds,
            refresh_token=new_refresh.token,
            scope=scope_string(effective_scope),
        )

    async def revoke(self, token: str, *, client_id: str | None = None) -> None:
        """RFC 7009 § 2 — revoke a refresh token (and its access tokens).

        Per RFC 7009 the endpoint also accepts access tokens; for JWT
        access tokens revocation is best-effort (the resource server
        SHOULD check an introspection / blocklist surface if it wants
        sub-token-lifetime revocation). Here we revoke the refresh
        family the token belongs to.
        """
        record = await self._refresh.get(token)
        if record is None:
            # RFC 7009 § 2.2 — revocation MUST NOT leak token validity;
            # return success even when the token was never registered.
            return
        if client_id is not None and record.client_id != client_id:
            # Same rule: do not leak — return success without acting.
            return
        await self._refresh.revoke_family(record.family_id)

    async def _mint_token_pair(
        self,
        *,
        client_id: str,
        subject: str,
        scope: frozenset[str],
        resource: str,
    ) -> TokenResponse:
        access = self._mint_access_token(
            client_id=client_id, subject=subject, scope=scope, resource=resource
        )
        refresh: str | None = None
        if SCOPE_OFFLINE_ACCESS in scope:
            record = RefreshTokenRecord(
                token=_new_token("rt"),
                family_id=_new_token("rf", length=16),
                client_id=client_id,
                subject=subject,
                resource=resource,
                scope=scope,
                expires_at=_now() + self._settings.refresh_token_ttl_seconds,
            )
            await self._refresh.put(record)
            refresh = record.token
        return TokenResponse(
            access_token=access,
            expires_in=self._settings.access_token_ttl_seconds,
            refresh_token=refresh,
            scope=scope_string(scope),
        )

    def _mint_access_token(
        self,
        *,
        client_id: str,
        subject: str,
        scope: frozenset[str],
        resource: str,
    ) -> str:
        now = _now()
        claims: dict[str, Any] = {
            "iss": self._settings.issuer,
            "aud": resource,
            "sub": subject,
            "client_id": client_id,
            "scope": scope_string(scope),
            "iat": now,
            "exp": now + self._settings.access_token_ttl_seconds,
            "jti": secrets.token_urlsafe(16),
        }
        return sign_jwt(
            claims,
            self._settings.signing_key,
            header_extras={"kid": self._settings.key_id, "typ": "at+jwt"},
        )

    def _authenticate_client(
        self, client: ClientRegistration, presented_secret: str | None
    ) -> None:
        if client.token_endpoint_auth_method == "none":  # noqa: S105
            # Public client: no secret expected. Reject any
            # accidentally-sent secret so misconfigured callers see the
            # mismatch immediately.
            if presented_secret:
                raise InvalidClient("public client must not send client_secret")
            return
        if not client.client_secret:
            raise InvalidClient("client registration is inconsistent (missing secret)")
        if not presented_secret:
            raise InvalidClient("client_secret is required for this client")
        if not _constant_time_eq(client.client_secret, presented_secret):
            raise InvalidClient("client_secret mismatch")


@dataclass(slots=True)
class _ResourceServerConfig:
    issuer: str
    resource: str
    signing_key: Ed25519KeyPair
    accepted_kids: frozenset[str] = field(default_factory=frozenset)
    clock_skew_seconds: int = DEFAULT_CLOCK_SKEW_SECONDS


class ResourceServer:
    """RFC-0009 § Token validation — the Resource Server side.

    A :class:`ResourceServer` validates every access token presented at
    the MCP endpoint and answers two questions:

    1. Is the token genuine, fresh, and bound to this resource?
       (signature + issuer + audience + expiry checks)
    2. Does it carry the scopes required by the operation about to run?
       (callers supply ``required_scopes`` to :meth:`validate`)

    The implementation supports a single signing key today; rotating
    keys is handled by widening ``accepted_kids`` and adding the new
    key to a sibling instance, then retiring the old once tokens have
    expired. The ``revocation_check`` hook lets operators plug in an
    online check (token blocklist, introspection) when sub-token-lifetime
    revocation is required.
    """

    def __init__(
        self,
        *,
        issuer: str,
        resource: str,
        signing_key: Ed25519KeyPair,
        accepted_kids: frozenset[str] = frozenset(),
        clock_skew_seconds: int = DEFAULT_CLOCK_SKEW_SECONDS,
    ) -> None:
        self._config = _ResourceServerConfig(
            issuer=issuer,
            resource=resource,
            signing_key=signing_key,
            accepted_kids=accepted_kids,
            clock_skew_seconds=clock_skew_seconds,
        )

    @classmethod
    def from_authorization_server(cls, server: AuthorizationServer) -> ResourceServer:
        """Convenience: build a co-located Resource Server from an AS."""
        s = server.settings
        return cls(
            issuer=s.issuer,
            resource=s.resource,
            signing_key=s.signing_key,
            accepted_kids=frozenset({s.key_id}),
            clock_skew_seconds=s.clock_skew_seconds,
        )

    def validate(
        self,
        token: str,
        *,
        required_scopes: frozenset[str] = frozenset(),
    ) -> TokenClaims:
        """Validate an access token and return its claims.

        Raises :class:`InvalidToken` for signature / issuer / audience /
        expiry failures and :class:`InvalidScope` for missing required
        scopes. Callers translate these to ``401 invalid_token`` /
        ``403 insufficient_scope`` HTTP responses.
        """
        if not token:
            raise InvalidToken("missing access token")
        try:
            # Header inspection runs first so we can reject tokens for
            # other key IDs without paying the verification cost.
            from shadownet.crypto.jwt import decode_header

            header = decode_header(token)
        except JWTError as exc:
            raise InvalidToken(str(exc)) from exc
        kid = header.get("kid")
        if self._config.accepted_kids and (kid not in self._config.accepted_kids):
            raise InvalidToken(f"unrecognized key id: {kid!r}")
        try:
            raw = verify_jwt(
                token,
                self._config.signing_key,
                audience=self._config.resource,
                issuer=self._config.issuer,
                leeway=self._config.clock_skew_seconds,
                required=["iss", "aud", "sub", "exp", "iat", "client_id", "scope", "jti"],
            )
        except JWTError as exc:
            raise InvalidToken(str(exc)) from exc
        claims = TokenClaims.model_validate(raw)
        if required_scopes and not required_scopes <= claims.scope_set:
            missing = required_scopes - claims.scope_set
            raise InvalidScope(f"required scopes missing: {sorted(missing)}")
        return claims

    @staticmethod
    def peek_kid(token: str) -> str | None:
        """Return the ``kid`` of the token's JOSE header without verifying.

        Useful when an operator wires multiple :class:`ResourceServer`
        instances together to support key rotation: the dispatcher
        inspects the header to pick which instance owns the key.
        """
        from shadownet.crypto.jwt import decode_header

        try:
            header = decode_header(token)
        except JWTError:
            return None
        kid = header.get("kid")
        return kid if isinstance(kid, str) else None


def build_protected_resource_metadata(
    *,
    resource: str,
    authorization_servers: list[str],
    scopes_supported: list[str],
    resource_documentation: str | None = None,
    jwks_uri: str | None = None,
) -> ProtectedResourceMetadata:
    """Compose the RFC 9728 PRM document for a Sidecar resource.

    Sidecars typically delegate to this helper from the FastAPI route
    serving ``/u/<shadowname>/.well-known/oauth-protected-resource``.
    """
    return ProtectedResourceMetadata(
        resource=resource,
        authorization_servers=authorization_servers,
        scopes_supported=scopes_supported,
        bearer_methods_supported=["header"],
        resource_documentation=resource_documentation,
        jwks_uri=jwks_uri,
    )


def _is_acceptable_redirect_uri(uri: str) -> bool:
    from urllib.parse import urlparse

    try:
        parsed = urlparse(uri)
    except ValueError:
        return False
    if parsed.scheme == "https":
        return bool(parsed.netloc)
    if parsed.scheme == "http":
        return parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    return False


def _constant_time_eq(a: str, b: str) -> bool:
    import hmac

    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def peek_unverified_subject(token: str) -> str | None:
    """Read the ``sub`` claim without verifying. Audit-log use only.

    NEVER use this for authorization — it strips the signature check.
    The intended use is correlating a 401 log line with the upstream
    request that issued the token.
    """
    try:
        claims = decode_unverified_claims(token)
    except JWTError:
        return None
    sub = claims.get("sub")
    return sub if isinstance(sub, str) else None
