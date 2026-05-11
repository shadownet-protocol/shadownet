"""Optional FastAPI helpers for serving the one-token install endpoints.

Lives behind the ``shadownet[fastapi]`` extra. Imports of ``fastapi`` and
``starlette`` are deferred to module-import time — installing the extra is
the caller's signal that they want this module loaded. Importing without
the extra raises :class:`ImportError`, not a Shadownet error, so the
misconfiguration is obvious.

RFC-0007 amendments A, B (handoff), and C are implemented here. Amendment D
is an MCP tool, registered separately via :mod:`shadownet.mcp.register`.

Sidecar operators mount this router on their FastAPI app, supplying a
``bundle_builder`` callable that resolves a bearer token to an
:class:`IntegrationBundle` (or ``None`` to signal 401).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

try:
    from fastapi import APIRouter, Header, HTTPException, Path, Request, Response
    from fastapi.responses import JSONResponse, PlainTextResponse
except ImportError as exc:  # pragma: no cover - only triggers without the extra
    raise ImportError(
        "shadownet.connect.fastapi requires `fastapi` — install the `[fastapi]` extra"
    ) from exc

from shadownet.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from shadownet.connect.bundle import IntegrationBundle

__all__ = [
    "DEFAULT_HOST_TEMPLATES",
    "HandoffPayload",
    "HostTemplate",
    "build_connect_router",
]

_log = get_logger(__name__)


class HostTemplate(Protocol):
    """Renders an :class:`IntegrationBundle` into per-host install copy.

    Implementations live next to the sidecar operator (or use the
    :data:`DEFAULT_HOST_TEMPLATES` we ship for the well-known hosts).
    """

    def render_text(self, bundle: IntegrationBundle) -> str:
        """Plain-text install snippet (for `Accept: text/plain` / `curl`)."""

    def render_html(self, bundle: IntegrationBundle) -> str:
        """Self-contained HTML install page (for browser flows)."""


class _HermesAgentTemplate:
    """Default `hermes-agent` host snippet — generic Hermes plugin install."""

    def render_text(self, bundle: IntegrationBundle) -> str:
        return _HERMES_TEXT_TEMPLATE.format(
            base_url=bundle.mcp_endpoint.rsplit("/u/", 1)[0],
            shadowname=bundle.shadowname,
        )

    def render_html(self, bundle: IntegrationBundle) -> str:
        return _HTML_WRAPPER.format(
            title=f"Install Shadownet for Hermes Agent — {bundle.shadowname}",
            heading=f"Hermes Agent install for {bundle.shadowname}",
            snippet=self.render_text(bundle),
        )


class _RawBundleTemplate:
    """The universal escape hatch — returns the bundle JSON unchanged.

    ``render_text`` returns the JSON; ``render_html`` wraps it in a viewer
    page. Sidecars typically short-circuit ``/connect/raw`` to skip HTML
    entirely and return JSON directly.
    """

    def render_text(self, bundle: IntegrationBundle) -> str:
        return bundle.model_dump_json(by_alias=True, indent=2)

    def render_html(self, bundle: IntegrationBundle) -> str:
        return _HTML_WRAPPER.format(
            title=f"Shadownet integration bundle — {bundle.shadowname}",
            heading=f"Integration bundle for {bundle.shadowname}",
            snippet=self.render_text(bundle),
        )


DEFAULT_HOST_TEMPLATES: dict[str, HostTemplate] = {
    "hermes-agent": _HermesAgentTemplate(),
    "raw": _RawBundleTemplate(),
}
"""Default per-host snippet renderers.

The bundled set is intentionally minimal — operators add more (Claude Code,
OpenClaw, Cursor, Continue, …) by passing their own dict to
:func:`build_connect_router`. ``raw`` is always present unless explicitly
overridden, since it's the universal fallback any plugin can call.
"""


class HandoffPayload(Protocol):
    """The body of a ``POST /v1/account/connect/handoff/{code}`` call."""

    client_nonce: str


def build_connect_router(
    *,
    bundle_builder: Callable[[str], Awaitable[IntegrationBundle | None]],
    host_templates: dict[str, HostTemplate] | None = None,
    handoff_resolver: Callable[[str, str], Awaitable[str | None]] | None = None,
) -> APIRouter:
    """Build a FastAPI router exposing the bundle + connect endpoints.

    Args:
        bundle_builder: callable the router invokes on every authenticated
            request. Receives the raw bearer token (without ``Bearer``
            prefix) and returns the tenant's :class:`IntegrationBundle`,
            or ``None`` if the token is invalid (router maps to 401).
        host_templates: per-host snippet renderers. Defaults to
            :data:`DEFAULT_HOST_TEMPLATES` (`hermes-agent` + `raw`).
            Pass an override dict to ship more hosts.
        handoff_resolver: callable for the RFC-0007 amendment B handoff
            flow. Receives the handoff short-code and the client's nonce;
            returns the resolved bearer token (or ``None`` if the code
            is invalid / expired / already consumed). When ``None``
            (default), the handoff endpoint returns 501 Not Implemented.

    The router mounts:

    - ``GET /v1/account/me/integration-bundle`` → JSON bundle
    - ``GET /v1/account/tenants/me/integration-bundle`` → deprecated alias
      for the same; logged at WARNING for operators to spot stale clients
    - ``GET /connect`` → HTML index of available hosts
    - ``GET /connect/{host}`` → templated install snippet
    - ``GET /connect/raw`` → bundle JSON (same as the first endpoint, but
      reachable via the connect-pages URL family for symmetry)
    - ``POST /v1/account/connect/handoff/{code}`` → handoff resolver
      (only if ``handoff_resolver`` was provided)
    """
    templates = dict(DEFAULT_HOST_TEMPLATES)
    if host_templates is not None:
        templates.update(host_templates)
    if "raw" not in templates:
        # Always keep raw as the universal escape hatch.
        templates["raw"] = _RawBundleTemplate()

    router = APIRouter()

    async def _resolve_bundle(authorization: str | None) -> IntegrationBundle:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(
                status_code=401,
                detail={"error": "missing_bearer_token", "shadownet:v": "0.1"},
            )
        token = authorization[len("Bearer ") :].strip()
        if not token:
            raise HTTPException(
                status_code=401,
                detail={"error": "missing_bearer_token", "shadownet:v": "0.1"},
            )
        bundle = await bundle_builder(token)
        if bundle is None:
            raise HTTPException(
                status_code=401,
                detail={"error": "invalid_token", "shadownet:v": "0.1"},
            )
        return bundle

    @router.get("/v1/account/me/integration-bundle")
    async def _bundle(authorization: str | None = Header(default=None)) -> Response:
        bundle = await _resolve_bundle(authorization)
        return JSONResponse(content=bundle.model_dump(by_alias=True, mode="json"))

    @router.get("/v1/account/tenants/me/integration-bundle", deprecated=True)
    async def _bundle_legacy(authorization: str | None = Header(default=None)) -> Response:
        _log.warning(
            "deprecated path /v1/account/tenants/me/integration-bundle hit; "
            "client should migrate to /v1/account/me/integration-bundle"
        )
        bundle = await _resolve_bundle(authorization)
        return JSONResponse(content=bundle.model_dump(by_alias=True, mode="json"))

    @router.get("/connect")
    async def _connect_index(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> Response:
        bundle = await _resolve_bundle(authorization)
        host_list = sorted(templates.keys())
        wants_html = "text/html" in (request.headers.get("accept") or "")
        if wants_html:
            items = "\n".join(f'  <li><a href="/connect/{h}">{h}</a></li>' for h in host_list)
            return _html_response(
                title=f"Shadownet connect — {bundle.shadowname}",
                heading="Available hosts",
                snippet=f"<ul>\n{items}\n</ul>",
            )
        return JSONResponse(content={"hosts": host_list, "shadowname": bundle.shadowname})

    @router.get("/connect/raw")
    async def _connect_raw(authorization: str | None = Header(default=None)) -> Response:
        bundle = await _resolve_bundle(authorization)
        return JSONResponse(content=bundle.model_dump(by_alias=True, mode="json"))

    @router.get("/connect/{host}")
    async def _connect_host(
        request: Request,
        host: str = Path(..., min_length=1, max_length=64),
        authorization: str | None = Header(default=None),
    ) -> Response:
        # Reject host slugs that contain path traversal characters before
        # they reach the registry lookup. FastAPI's path converter already
        # rejects `/`, but anything else permitted in a path segment
        # (including `.`, `..`) reaches us here.
        if host in {".", ".."} or host == "raw":
            # `raw` is handled by the dedicated route above; falling here
            # means the dedicated route did not match (shouldn't happen)
            # so reject.
            raise HTTPException(status_code=404, detail={"error": "unknown_host", "host": host})
        template = templates.get(host)
        if template is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "unknown_host",
                    "host": host,
                    "known_hosts": sorted(templates.keys()),
                },
                headers={"Link": '</connect>; rel="up"'},
            )
        bundle = await _resolve_bundle(authorization)
        accept = request.headers.get("accept") or ""
        if "text/html" in accept:
            return _html_response_raw(template.render_html(bundle))
        return PlainTextResponse(content=template.render_text(bundle))

    if handoff_resolver is not None:

        @router.post("/v1/account/connect/handoff/{code}")
        async def _handoff(
            request: Request,
            code: str = Path(..., min_length=8, max_length=128),
        ) -> Response:
            body = await request.json()
            client_nonce = body.get("client_nonce") if isinstance(body, dict) else None
            if not isinstance(client_nonce, str) or len(client_nonce) < 16:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "missing_or_short_client_nonce",
                        "shadownet:v": "0.1",
                    },
                )
            token = await handoff_resolver(code, client_nonce)
            if token is None:
                raise HTTPException(
                    status_code=404,
                    detail={"error": "handoff_invalid_or_expired", "shadownet:v": "0.1"},
                )
            return JSONResponse(
                content={
                    "shadownet:v": "0.1",
                    "token": token,
                    "expires_in": 600,
                }
            )

    return router


def _html_response(*, title: str, heading: str, snippet: str) -> Response:
    return _html_response_raw(_HTML_WRAPPER.format(title=title, heading=heading, snippet=snippet))


def _html_response_raw(html: str) -> Response:
    return Response(content=html, media_type="text/html; charset=utf-8")


_HERMES_TEXT_TEMPLATE = """\
# Hermes Agent - Shadownet install for {shadowname}
# (RFC-0007 amendments A-D)

# 1. Install the plugin (one-time):
hermes plugins install shadownet-protocol/shadownet --enable

# 2. Set your account credentials:
export SHADOWNET_TOKEN="<paste the token shown on your account page>"
export SHADOWNET_SIDECAR_BASE_URL="{base_url}"

# 3. Start (or restart) Hermes. The plugin will wire up MCP, skills,
#    and the long-poll inbox automatically.
"""

_HTML_WRAPPER = """\
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{title}</title>
  <style>
    body {{ font: 14px/1.5 system-ui, sans-serif; max-width: 720px; margin: 2rem auto; padding: 0 1rem; }}
    h1 {{ font-size: 1.25rem; }}
    pre {{ background: #f6f8fa; padding: 1rem; border-radius: 6px; overflow-x: auto; }}
    a {{ color: #0366d6; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
  </style>
</head>
<body>
  <h1>{heading}</h1>
  <pre>{snippet}</pre>
</body>
</html>
"""
