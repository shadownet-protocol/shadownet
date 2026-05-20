"""Optional FastAPI integration for the RFC-0009 Authorization Server.

Lives behind the ``shadownet[fastapi]`` extra. Sidecar operators mount
:func:`build_oauth_router` on their FastAPI app along with the
companion :func:`build_resource_metadata_router` (which serves the
RFC 9728 PRM document per tenant). Token validation is performed by
:func:`build_bearer_auth_dependency`, a FastAPI dependency callers
attach to every MCP route.

The four entry points compose:

- :func:`build_resource_metadata_router` — the Resource Server's PRM
  document.
- :func:`build_oauth_router` — `/oauth/authorize`, `/oauth/token`,
  `/oauth/register`, `/oauth/revoke`, plus the AS metadata document.
- :func:`build_bearer_auth_dependency` — wraps :class:`ResourceServer`
  for use as a FastAPI dependency on MCP routes.
- :func:`oauth_challenge_headers` — builds the ``WWW-Authenticate``
  header for 401 / 403 responses.

The consent flow is operator-owned. The router accepts a
``consent_handler`` callable which is invoked on the GET
``/oauth/authorize`` endpoint; the handler decides whether the request
is satisfied (e.g. the user has an active session and has approved the
client) and either returns an :class:`AuthorizationConsent` (the router
issues a code and redirects) or returns ``None`` (the router renders
the consent screen the handler wires up out-of-band, typically a
redirect to the operator's login UI).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlencode

try:
    from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
    from fastapi.responses import JSONResponse, RedirectResponse
    from fastapi.security.utils import get_authorization_scheme_param
except ImportError as exc:  # pragma: no cover - only triggers without the extra
    raise ImportError(
        "shadownet.oauth.fastapi requires `fastapi` — install the `[fastapi]` extra"
    ) from exc

from shadownet.logging import get_logger
from shadownet.oauth.errors import (
    InvalidClient,
    InvalidRedirectURI,
    InvalidRequest,
    InvalidScope,
    InvalidToken,
    OAuthError,
    UnsupportedGrantType,
)
from shadownet.oauth.models import (
    ClientRegistrationRequest,
    ClientRegistrationResponse,
    ProtectedResourceMetadata,
)
from shadownet.oauth.scopes import parse_scope_string
from shadownet.oauth.server import (
    AuthorizationConsent,
    AuthorizationRequest,
    AuthorizationServer,
    ResourceServer,
    TokenClaims,
    build_protected_resource_metadata,
)

__all__ = [
    "ConsentHandler",
    "ConsentResult",
    "build_bearer_auth_dependency",
    "build_oauth_router",
    "build_resource_metadata_router",
    "oauth_challenge_headers",
]

_log = get_logger(__name__)


class ConsentResult:
    """Return value from a :data:`ConsentHandler`.

    Three outcomes:

    - ``approved(subject, scopes)`` — the user is authenticated and has
      approved the listed scopes. The router issues an authorization
      code and redirects to ``redirect_uri``.
    - ``deny(error, description)`` — the user (or operator policy)
      declined. The router redirects to ``redirect_uri`` with an OAuth
      2.1 error response.
    - ``render(response)`` — the handler wants to display a consent or
      login UI itself. The router returns ``response`` to the browser
      verbatim.
    """

    __slots__ = ("consent", "deny_description", "deny_error", "rendered")

    def __init__(
        self,
        *,
        consent: AuthorizationConsent | None = None,
        deny_error: str | None = None,
        deny_description: str | None = None,
        rendered: Response | None = None,
    ) -> None:
        self.consent = consent
        self.deny_error = deny_error
        self.deny_description = deny_description
        self.rendered = rendered

    @classmethod
    def approved(cls, *, subject: str, scopes: frozenset[str]) -> ConsentResult:
        return cls(consent=AuthorizationConsent(subject=subject, granted_scopes=scopes))

    @classmethod
    def denied(
        cls, *, error: str = "access_denied", description: str | None = None
    ) -> ConsentResult:
        return cls(deny_error=error, deny_description=description)

    @classmethod
    def render(cls, response: Response) -> ConsentResult:
        return cls(rendered=response)


ConsentHandler = Callable[[Request, AuthorizationRequest], Awaitable[ConsentResult]]


def build_oauth_router(
    server: AuthorizationServer,
    *,
    consent_handler: ConsentHandler,
    path_prefix: str = "",
) -> APIRouter:
    """Mount RFC-0009 endpoints on a FastAPI router.

    The router exposes (relative to ``path_prefix``):

    - ``GET oauth/authorize`` -> authorization endpoint
    - ``POST oauth/token`` -> token endpoint
    - ``POST oauth/register`` -> dynamic client registration (when enabled)
    - ``POST oauth/revoke`` -> token revocation (when configured)

    AS metadata (RFC 8414 § 3.1) is served at the path-insertion
    location: when ``path_prefix`` is empty, metadata is at
    ``/.well-known/oauth-authorization-server``; when ``path_prefix``
    is ``/u/alice`` (matching the issuer URL's path component),
    metadata is at ``/.well-known/oauth-authorization-server/u/alice``.
    The path-appended ``/u/alice/.well-known/oauth-authorization-server``
    is also served for OIDC-style clients that probe in that order.

    ``consent_handler`` is the operator's hook for authenticating the
    resource owner and recording their approval. The handler is given
    the parsed :class:`AuthorizationRequest` and the raw FastAPI
    :class:`Request`; it returns a :class:`ConsentResult` (see that
    class for the three outcomes).
    """
    router = APIRouter()
    prefix = path_prefix.rstrip("/")
    settings = server.settings

    metadata_paths: list[str] = []
    # RFC 8414 § 3.1 — path-insertion form. The canonical location.
    metadata_paths.append(f"/.well-known/oauth-authorization-server{prefix}")
    if prefix:
        # OIDC-style path-append fallback for clients that probe in the
        # `<base>/<tenant>/.well-known/oauth-authorization-server` order.
        metadata_paths.append(f"{prefix}/.well-known/oauth-authorization-server")

    async def _metadata() -> Response:
        return JSONResponse(
            content=server.metadata().model_dump(by_alias=True, mode="json", exclude_none=True)
        )

    for path in dict.fromkeys(metadata_paths):  # dedupe while preserving order
        router.add_api_route(path, _metadata, methods=["GET"])

    @router.get(f"{prefix}/oauth/authorize")
    async def _authorize(request: Request) -> Response:
        try:
            parsed = _parse_authorize_query(request)
        except OAuthError as exc:
            return _authorize_error_response(request, exc, redirect=None, state=None)

        try:
            await server.validate_authorization_request(parsed)
        except OAuthError as exc:
            return _authorize_error_response(
                request,
                exc,
                redirect=parsed.redirect_uri if _redirect_is_safe(exc) else None,
                state=parsed.state,
            )

        result = await consent_handler(request, parsed)
        if result.rendered is not None:
            return result.rendered
        if result.deny_error is not None:
            return _redirect_with_error(
                parsed.redirect_uri,
                error=result.deny_error,
                description=result.deny_description,
                state=parsed.state,
            )
        if result.consent is None:
            raise RuntimeError(
                "consent handler returned an empty ConsentResult; this is a programmer error"
            )
        try:
            code = await server.issue_code(parsed, result.consent)
        except OAuthError as exc:
            return _authorize_error_response(
                request,
                exc,
                redirect=parsed.redirect_uri,
                state=parsed.state,
            )
        params = {"code": code.code}
        if parsed.state is not None:
            params["state"] = parsed.state
        target = _append_query(parsed.redirect_uri, params)
        return RedirectResponse(url=target, status_code=status.HTTP_302_FOUND)

    @router.post(f"{prefix}/oauth/token")
    async def _token(request: Request) -> Response:
        form = await request.form()
        grant_type_raw = form.get("grant_type") or ""
        # Starlette returns `str | UploadFile` for form items; coerce to
        # the string-or-empty case (file uploads aren't valid here).
        grant_type = grant_type_raw.strip() if isinstance(grant_type_raw, str) else ""
        # OAuth 2.1 § 5 — Cache-Control: no-store on every token response.
        try:
            if grant_type == "authorization_code":
                resp = await server.exchange_authorization_code(
                    code=_form_str(form, "code"),
                    client_id=_form_str(form, "client_id"),
                    redirect_uri=_form_str(form, "redirect_uri"),
                    code_verifier=_form_str(form, "code_verifier"),
                    resource=_form_str(form, "resource"),
                    client_secret=_form_optional(form, "client_secret"),
                )
            elif grant_type == "refresh_token":
                requested_scope_raw = _form_optional(form, "scope")
                requested_scope = (
                    parse_scope_string(requested_scope_raw)
                    if requested_scope_raw is not None
                    else None
                )
                resp = await server.exchange_refresh_token(
                    refresh_token=_form_str(form, "refresh_token"),
                    client_id=_form_str(form, "client_id"),
                    resource=_form_optional(form, "resource"),
                    requested_scope=requested_scope,
                    client_secret=_form_optional(form, "client_secret"),
                )
            elif not grant_type:
                raise InvalidRequest("grant_type is required")
            else:
                raise UnsupportedGrantType(f"unsupported grant_type {grant_type!r}")
        except OAuthError as exc:
            return _oauth_error_json(exc, default_status=_status_for_token_error(exc))
        return JSONResponse(
            content=resp.model_dump(by_alias=True, mode="json", exclude_none=True),
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )

    if settings.allow_dynamic_client_registration:

        @router.post(f"{prefix}/oauth/register")
        async def _register(request: ClientRegistrationRequest) -> Response:
            try:
                resp: ClientRegistrationResponse = await server.register_client(request)
            except OAuthError as exc:
                return _oauth_error_json(exc, default_status=status.HTTP_400_BAD_REQUEST)
            return JSONResponse(
                status_code=status.HTTP_201_CREATED,
                content=resp.model_dump(by_alias=True, mode="json", exclude_none=True),
            )

    if settings.revocation_endpoint is not None:

        @router.post(f"{prefix}/oauth/revoke")
        async def _revoke(request: Request) -> Response:
            form = await request.form()
            token = _form_optional(form, "token")
            if not token:
                # RFC 7009 § 2.1 — `token` is REQUIRED. Missing it is
                # invalid_request, not silent success.
                return _oauth_error_json(
                    InvalidRequest("token is required"),
                    default_status=status.HTTP_400_BAD_REQUEST,
                )
            await server.revoke(token, client_id=_form_optional(form, "client_id"))
            return Response(status_code=status.HTTP_200_OK)

    return router


def build_resource_metadata_router(
    *,
    resource: str,
    authorization_servers: list[str],
    scopes_supported: list[str],
    path: str = "/.well-known/oauth-protected-resource",
    resource_documentation: str | None = None,
    jwks_uri: str | None = None,
) -> APIRouter:
    """Mount the RFC 9728 Protected Resource Metadata endpoint.

    Single tenant: pass the tenant's resource URL and AS issuer. For
    multi-tenant Sidecars (path-based), mount one router per tenant
    under ``/u/<shadowname>``; that is what the reference Sidecar does.
    """
    router = APIRouter()

    document = build_protected_resource_metadata(
        resource=resource,
        authorization_servers=authorization_servers,
        scopes_supported=scopes_supported,
        resource_documentation=resource_documentation,
        jwks_uri=jwks_uri,
    )

    @router.get(path)
    async def _prm() -> Response:
        return JSONResponse(
            content=document.model_dump(by_alias=True, mode="json", exclude_none=True),
            # RFC 9728 § 3 — the document is cacheable. Operators MAY
            # override by serving the route themselves; the default
            # mirrors the reference deployment's setting.
            headers={"Cache-Control": "public, max-age=300"},
        )

    return router


def build_bearer_auth_dependency(
    resource_server: ResourceServer,
    *,
    resource_metadata_url: str,
    required_scopes: frozenset[str] = frozenset(),
) -> Callable[[Request], Awaitable[TokenClaims]]:
    """Build a FastAPI dependency that enforces RFC-0009 token validation.

    Usage::

        require_bearer = build_bearer_auth_dependency(rs, resource_metadata_url=...)

        @app.post("/u/{shadowname}/mcp", dependencies=[Depends(require_bearer)])
        async def mcp(...): ...

    The dependency returns the validated :class:`TokenClaims`. Routes
    that need per-call scope checks (e.g. an MCP tool gate) can call
    :meth:`ResourceServer.validate` again with the tool's required
    scopes, or build a route-specific dependency with non-empty
    ``required_scopes``.
    """

    async def _dep(request: Request) -> TokenClaims:
        authorization = request.headers.get("authorization")
        scheme, token = get_authorization_scheme_param(authorization)
        if not authorization or scheme.lower() != "bearer" or not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="missing_bearer_token",
                headers=oauth_challenge_headers(
                    resource_metadata_url=resource_metadata_url,
                    error=None,
                ),
            )
        try:
            claims = resource_server.validate(token, required_scopes=required_scopes)
        except InvalidScope as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=str(exc),
                headers=oauth_challenge_headers(
                    resource_metadata_url=resource_metadata_url,
                    error="insufficient_scope",
                    scope=" ".join(sorted(required_scopes)),
                ),
            ) from exc
        except InvalidToken as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(exc),
                headers=oauth_challenge_headers(
                    resource_metadata_url=resource_metadata_url,
                    error="invalid_token",
                    error_description=str(exc),
                ),
            ) from exc
        return claims

    return _dep


def oauth_challenge_headers(
    *,
    resource_metadata_url: str,
    error: str | None,
    error_description: str | None = None,
    scope: str | None = None,
) -> dict[str, str]:
    """Compose the ``WWW-Authenticate`` Bearer challenge for 401 / 403.

    RFC-0009 § Error responses mandates ``resource_metadata`` on every
    401 and 403 reply so a mid-session host can rediscover the
    authorization surface. ``realm="mcp"`` is required.
    """
    params: list[tuple[str, str]] = [("realm", "mcp")]
    if error is not None:
        params.append(("error", error))
    if error_description is not None:
        params.append(("error_description", error_description))
    if scope is not None:
        params.append(("scope", scope))
    params.append(("resource_metadata", resource_metadata_url))
    value = "Bearer " + ", ".join(f'{k}="{_escape_quoted(v)}"' for k, v in params)
    return {"WWW-Authenticate": value}


def _escape_quoted(value: str) -> str:
    # RFC 7235 quoted-string: backslash-escape `"` and `\`. The rest are
    # safe for the small set of values we ever emit (URLs, ASCII scope
    # names, OAuth error codes).
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _form_str(form: Any, name: str) -> str:
    value = form.get(name)
    if not isinstance(value, str) or not value:
        raise InvalidRequest(f"{name} is required")
    return value


def _form_optional(form: Any, name: str) -> str | None:
    value = form.get(name)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise InvalidRequest(f"{name} must be a string")
    return value


def _parse_authorize_query(request: Request) -> AuthorizationRequest:
    qp = request.query_params
    # Reject repeated params per OAuth 2.1 § 5.1 (no merging).
    seen: dict[str, int] = {}
    for key, _ in qp.multi_items():
        seen[key] = seen.get(key, 0) + 1
    for key, count in seen.items():
        if count > 1 and key in {
            "response_type",
            "client_id",
            "redirect_uri",
            "code_challenge",
            "code_challenge_method",
            "scope",
            "resource",
            "state",
        }:
            raise InvalidRequest(f"parameter {key!r} given more than once")
    missing = [
        name
        for name in (
            "response_type",
            "client_id",
            "redirect_uri",
            "code_challenge",
            "code_challenge_method",
            "resource",
        )
        if not qp.get(name)
    ]
    if missing:
        raise InvalidRequest(f"missing required parameters: {missing}")
    return AuthorizationRequest(
        response_type=qp["response_type"],
        client_id=qp["client_id"],
        redirect_uri=qp["redirect_uri"],
        code_challenge=qp["code_challenge"],
        code_challenge_method=qp["code_challenge_method"],
        resource=qp["resource"],
        scope=qp.get("scope"),
        state=qp.get("state"),
    )


def _redirect_is_safe(exc: OAuthError) -> bool:
    """Return whether ``exc`` permits redirecting back to redirect_uri.

    OAuth 2.1 § 4.1.2.1 — redirecting an error back to the client is
    only safe once the redirect URI has been validated against the
    client's allowlist. :meth:`AuthorizationServer.validate_authorization_request`
    runs that check first, so errors raised after that point are
    safe to redirect. The two unsafe cases are :class:`InvalidClient`
    (unknown client_id, so the request's redirect_uri may be attacker
    controlled) and :class:`InvalidRedirectURI` (the URI failed the
    allowlist check) — both render to the user-agent directly.
    """
    return not isinstance(exc, (InvalidClient, InvalidRedirectURI))


def _authorize_error_response(
    request: Request,
    exc: OAuthError,
    *,
    redirect: str | None,
    state: str | None,
) -> Response:
    description = exc.description or exc.code
    if redirect is not None:
        return _redirect_with_error(redirect, error=exc.code, description=description, state=state)
    payload: dict[str, str] = {"error": exc.code, "error_description": description}
    if state is not None:
        payload["state"] = state
    return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content=payload)


def _redirect_with_error(
    redirect_uri: str,
    *,
    error: str,
    description: str | None,
    state: str | None,
) -> Response:
    params: dict[str, str] = {"error": error}
    if description is not None:
        params["error_description"] = description
    if state is not None:
        params["state"] = state
    return RedirectResponse(
        url=_append_query(redirect_uri, params),
        status_code=status.HTTP_302_FOUND,
    )


def _append_query(base: str, params: dict[str, str]) -> str:
    from urllib.parse import quote, urlparse, urlunparse

    parsed = urlparse(base)
    existing = parsed.query
    # ``quote_via=quote`` (rather than the default ``quote_plus``) keeps
    # the encoding faithful to OAuth wire convention — ``+`` should not
    # silently become a space at the client.
    encoded = urlencode(params, quote_via=quote)
    new_query = f"{existing}&{encoded}" if existing else encoded
    return urlunparse(parsed._replace(query=new_query))


def _oauth_error_json(exc: OAuthError, *, default_status: int) -> Response:
    body: dict[str, str] = {"error": exc.code}
    if exc.description:
        body["error_description"] = exc.description
    return JSONResponse(status_code=default_status, content=body)


def _status_for_token_error(exc: OAuthError) -> int:
    # OAuth 2.1 § 5.2.3 — invalid_client returns 401.
    if isinstance(exc, InvalidClient):
        return status.HTTP_401_UNAUTHORIZED
    return status.HTTP_400_BAD_REQUEST


# ``Depends`` and ``Form`` are imported so this module is self-contained
# for downstream callers that re-export from here; mark them used to
# avoid a ruff F401 complaint.
_ = (Depends, Form, ProtectedResourceMetadata, urlencode)
