"""Optional FastAPI helpers for serving the one-token install endpoints.

Lives behind the ``shadownet[fastapi]`` extra. Imports of ``fastapi`` and
``starlette`` are deferred to module-import time — installing the extra is
the caller's signal that they want this module loaded. Importing without
the extra raises :class:`ImportError`, not a Shadownet error, so the
misconfiguration is obvious.

Implements the server-side surfaces from RFC-0008 (Sidecar Onboarding
Surface): integration-bundle endpoint, ``shadownet://connect`` handoff
resolver, and content-negotiated ``<base>/connect/<host>`` install pages.
The long-poll MCP tool ``social_inbox_wait`` (RFC-0007) is registered
separately via :mod:`shadownet.mcp.register`.

Sidecar operators mount this router on their FastAPI app, supplying a
``bundle_builder`` callable that resolves a bearer token to an
:class:`IntegrationBundle` (or ``None`` to signal 401).
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any, Protocol

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
    "DEFAULT_HANDOFF_TTL_SECONDS",
    "DEFAULT_HOST_TEMPLATES",
    "RESERVED_HOST_SLUGS",
    "HostTemplate",
    "build_connect_router",
]

_log = get_logger(__name__)

# RFC-0008 § Connect URL scheme recommends 15-minute handoff TTL.
DEFAULT_HANDOFF_TTL_SECONDS = 15 * 60

# `raw` is the universal escape hatch slug, owned by the spec; operators
# MUST NOT register their own template under this name per
# examples/well-known-hosts.md.
RESERVED_HOST_SLUGS: frozenset[str] = frozenset({"raw"})


class HostTemplate(Protocol):
    """Renders an :class:`IntegrationBundle` into per-host install copy.

    Three content-negotiation paths per RFC-0008 § Content negotiation:
    HTML for browsers, plain text for ``curl``/``wget``, JSON for
    automation. Every JSON response MUST carry a top-level
    ``"shadownet:v": "0.1"`` marker — implementations using
    :func:`build_connect_router` get this enforced.
    """

    def render_text(self, bundle: IntegrationBundle) -> str:
        """Plain-text install snippet (for `Accept: text/plain` / `curl`)."""

    def render_html(self, bundle: IntegrationBundle) -> str:
        """Self-contained HTML install page (for browser flows)."""

    def render_json(self, bundle: IntegrationBundle) -> dict[str, Any]:
        """Host-specific JSON. Top-level keys per ``well-known-hosts.md``.

        Implementations MUST omit the ``shadownet:v`` field — the router
        injects it after this returns so the marker is uniform across hosts.
        """


class _HermesAgentTemplate:
    """`hermes-agent` snippet — Hermes plugin install incantation + env vars.

    Per ``examples/well-known-hosts.md`` the JSON form is
    ``{ "shadownet:v": "0.1", "configSchema": {...} }``; ``configSchema``
    is the shape the plugin's ``requires_env`` prompts expect.
    """

    def render_text(self, bundle: IntegrationBundle) -> str:
        return _HERMES_TEXT_TEMPLATE.format(
            base_url=_strip_user_path(bundle.mcp_endpoint),
            shadowname=bundle.shadowname,
        )

    def render_html(self, bundle: IntegrationBundle) -> str:
        return _HTML_WRAPPER.format(
            title=f"Install Shadownet for Hermes Agent - {bundle.shadowname}",
            heading=f"Hermes Agent install for {bundle.shadowname}",
            snippet=self.render_text(bundle),
        )

    def render_json(self, bundle: IntegrationBundle) -> dict[str, Any]:
        return {
            "configSchema": {
                "SHADOWNET_TOKEN": {
                    "prompt": "Shadownet account bearer token",
                    "required": True,
                },
                "SHADOWNET_SIDECAR_BASE_URL": {
                    "prompt": "Sidecar base URL",
                    "default": _strip_user_path(bundle.mcp_endpoint),
                },
            },
            "install_command": "hermes plugins install shadownet-protocol/shadownet --enable",
            "shadowname": bundle.shadowname,
        }


class _ClaudeCodeTemplate:
    """`claude-code` snippet — marketplace install + .mcp.json block.

    Per ``examples/well-known-hosts.md`` the JSON form is
    ``{ "shadownet:v": "0.1", "mcpServerConfig": {...} }`` — a single MCP
    server entry the host can drop into its settings file.
    """

    def render_text(self, bundle: IntegrationBundle) -> str:
        return _CLAUDE_CODE_TEXT_TEMPLATE.format(
            mcp_endpoint=bundle.mcp_endpoint,
            shadowname=bundle.shadowname,
        )

    def render_html(self, bundle: IntegrationBundle) -> str:
        return _HTML_WRAPPER.format(
            title=f"Install Shadownet for Claude Code - {bundle.shadowname}",
            heading=f"Claude Code install for {bundle.shadowname}",
            snippet=self.render_text(bundle),
        )

    def render_json(self, bundle: IntegrationBundle) -> dict[str, Any]:
        return {
            "mcpServerConfig": {
                "shadownet": {
                    "type": "http",
                    "url": bundle.mcp_endpoint,
                    "headers": {"Authorization": "Bearer ${SHADOWNET_TOKEN}"},
                }
            },
            "marketplace": "github:shadownet-protocol/shadownet",
            "shadowname": bundle.shadowname,
        }


class _CursorTemplate:
    """`cursor` snippet — same MCP server block shape as Claude Code."""

    def render_text(self, bundle: IntegrationBundle) -> str:
        return _CURSOR_TEXT_TEMPLATE.format(mcp_endpoint=bundle.mcp_endpoint)

    def render_html(self, bundle: IntegrationBundle) -> str:
        return _HTML_WRAPPER.format(
            title=f"Install Shadownet for Cursor - {bundle.shadowname}",
            heading=f"Cursor install for {bundle.shadowname}",
            snippet=self.render_text(bundle),
        )

    def render_json(self, bundle: IntegrationBundle) -> dict[str, Any]:
        return {
            "mcpServerConfig": {
                "shadownet": {
                    "url": bundle.mcp_endpoint,
                    "headers": {"Authorization": "Bearer <paste-token>"},
                }
            },
        }


class _RawBundleTemplate:
    """The universal escape hatch — returns the integration bundle JSON.

    Per RFC-0008 § Content negotiation, ``/connect/raw`` with
    ``Accept: application/json`` returns the canonical bundle (already
    carrying ``shadownet:v``).
    """

    def render_text(self, bundle: IntegrationBundle) -> str:
        return bundle.model_dump_json(by_alias=True, indent=2)

    def render_html(self, bundle: IntegrationBundle) -> str:
        return _HTML_WRAPPER.format(
            title=f"Shadownet integration bundle - {bundle.shadowname}",
            heading=f"Integration bundle for {bundle.shadowname}",
            snippet=self.render_text(bundle),
        )

    def render_json(self, bundle: IntegrationBundle) -> dict[str, Any]:
        # The bundle already carries `shadownet:v`; the router merges below
        # using setdefault so the bundle's value wins.
        return bundle.model_dump(by_alias=True, mode="json")


DEFAULT_HOST_TEMPLATES: dict[str, HostTemplate] = {
    "hermes-agent": _HermesAgentTemplate(),
    "claude-code": _ClaudeCodeTemplate(),
    "cursor": _CursorTemplate(),
    "raw": _RawBundleTemplate(),
}
"""Default per-host snippet renderers covering the well-known slugs that
have a stable JSON shape today (hermes-agent, claude-code, cursor, raw).

Operators add more (OpenClaw, Continue, …) by passing their own dict to
:func:`build_connect_router`. The ``raw`` slug is reserved and cannot be
overridden — operator-supplied entries under the reserved name are
ignored.
"""


def build_connect_router(
    *,
    bundle_builder: Callable[[str], Awaitable[IntegrationBundle | None]],
    host_templates: dict[str, HostTemplate] | None = None,
    handoff_resolver: Callable[[str], Awaitable[str | None]] | None = None,
    handoff_ttl_seconds: int = DEFAULT_HANDOFF_TTL_SECONDS,
) -> APIRouter:
    """Build a FastAPI router exposing the RFC-0008 onboarding surface.

    Args:
        bundle_builder: callable the router invokes on every authenticated
            request. Receives the raw bearer token (without ``Bearer``
            prefix) and returns the tenant's :class:`IntegrationBundle`,
            or ``None`` if the token is invalid (router maps to 401).
        host_templates: per-host snippet renderers. The router starts from
            :data:`DEFAULT_HOST_TEMPLATES` and merges in this dict, with
            one exception: keys in :data:`RESERVED_HOST_SLUGS` (today:
            ``raw``) MUST NOT be overridden — operator entries under
            reserved names are ignored and a warning is logged.
        handoff_resolver: callable for the RFC-0008 handoff flow.
            Receives the handoff short-code and returns the resolved
            bearer token (or ``None`` if the code is invalid, expired,
            or already consumed). When ``None`` (default), the handoff
            endpoint is not mounted.
        handoff_ttl_seconds: TTL advertised in the handoff response's
            ``expires_in`` field. Defaults to 15 minutes per RFC-0008.

    The router mounts:

    - ``GET /v1/account/me/integration-bundle`` -> JSON bundle
    - ``GET /v1/account/tenants/me/integration-bundle`` -> deprecated alias
    - ``GET /connect`` -> HTML index / JSON list of available hosts
    - ``GET /connect/{host}`` -> templated install snippet
      (Accept: text/html, text/plain, or application/json)
    - ``GET /connect/raw`` -> canonical bundle JSON
    - ``POST /v1/account/connect/handoff/{code}`` -> handoff resolver
      (only if ``handoff_resolver`` was provided)
    """
    templates: dict[str, HostTemplate] = dict(DEFAULT_HOST_TEMPLATES)
    if host_templates is not None:
        for name, tmpl in host_templates.items():
            if name in RESERVED_HOST_SLUGS:
                _log.warning(
                    "ignoring operator-supplied template for reserved host slug %r "
                    "(RFC-0008 examples/well-known-hosts.md reserves this name)",
                    name,
                )
                continue
            templates[name] = tmpl

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
        accept = request.headers.get("accept") or ""
        if "text/html" in accept:
            items = "\n".join(f'  <li><a href="/connect/{h}">{h}</a></li>' for h in host_list)
            return _html_response(
                title=f"Shadownet connect - {bundle.shadowname}",
                heading="Available hosts",
                snippet=f"<ul>\n{items}\n</ul>",
            )
        return JSONResponse(
            content={
                "shadownet:v": "0.1",
                "hosts": host_list,
                "shadowname": bundle.shadowname,
            }
        )

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
        # Path-traversal guard before registry lookup. FastAPI's path
        # converter already rejects literal `/`; `.` and `..` and the
        # reserved `raw` slug land here.
        if host in {".", "..", "raw"}:
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
        if "application/json" in accept:
            payload = dict(template.render_json(bundle))
            # RFC-0008: every application/json response from a connect
            # route MUST carry top-level "shadownet:v": "0.1". Inject if
            # the template didn't already include it.
            payload.setdefault("shadownet:v", "0.1")
            return JSONResponse(content=payload)
        if "text/html" in accept:
            return _html_response_raw(template.render_html(bundle))
        return PlainTextResponse(content=template.render_text(bundle))

    if handoff_resolver is not None:

        @router.post("/v1/account/connect/handoff/{code}")
        async def _handoff(
            request: Request,
            code: str = Path(..., min_length=16, max_length=128),
        ) -> Response:
            # RFC-0008 § Connect URL scheme: v0.1 servers MUST IGNORE
            # client_nonce if present (the field is RESERVED for future
            # use). The request body MAY be empty; we parse leniently.
            with contextlib.suppress(ValueError):
                _ = await request.json()
            token = await handoff_resolver(code)
            if token is None:
                raise HTTPException(
                    status_code=404,
                    detail={"error": "handoff_invalid_or_expired", "shadownet:v": "0.1"},
                )
            return JSONResponse(
                content={
                    "shadownet:v": "0.1",
                    "token": token,
                    "expires_in": handoff_ttl_seconds,
                }
            )

    return router


def _html_response(*, title: str, heading: str, snippet: str) -> Response:
    return _html_response_raw(_HTML_WRAPPER.format(title=title, heading=heading, snippet=snippet))


def _html_response_raw(html: str) -> Response:
    return Response(content=html, media_type="text/html; charset=utf-8")


def _strip_user_path(mcp_endpoint: str) -> str:
    """Derive the sidecar base URL from a per-tenant MCP endpoint.

    ``https://app.sh4dow.org/u/alice/mcp`` -> ``https://app.sh4dow.org``.
    Used by snippet templates so the user gets the base URL pre-filled.
    """
    if "/u/" in mcp_endpoint:
        return mcp_endpoint.rsplit("/u/", 1)[0]
    return mcp_endpoint


_HERMES_TEXT_TEMPLATE = """\
# Hermes Agent - Shadownet install for {shadowname}

# 1. Install the plugin (one-time):
hermes plugins install shadownet-protocol/shadownet --enable

# 2. Set your account credentials:
export SHADOWNET_TOKEN="<paste the token shown on your account page>"
export SHADOWNET_SIDECAR_BASE_URL="{base_url}"

# 3. Start (or restart) Hermes. The plugin wires up MCP, skills, and
#    the long-poll inbox automatically.
"""

_CLAUDE_CODE_TEXT_TEMPLATE = """\
# Claude Code - Shadownet install for {shadowname}

# 1. Add the marketplace and install the plugin:
/plugin marketplace add github:shadownet-protocol/shadownet
/plugin install shadownet@shadownet-protocol

# 2. Add this MCP server entry to your settings (.mcp.json snippet):
# {{
#   "mcpServers": {{
#     "shadownet": {{
#       "type": "http",
#       "url": "{mcp_endpoint}",
#       "headers": {{ "Authorization": "Bearer ${{SHADOWNET_TOKEN}}" }}
#     }}
#   }}
# }}

# 3. Export SHADOWNET_TOKEN in your shell, then restart Claude Code.
"""

_CURSOR_TEXT_TEMPLATE = """\
# Cursor - paste this MCP server entry in Cursor settings:
# {{
#   "shadownet": {{
#     "url": "{mcp_endpoint}",
#     "headers": {{ "Authorization": "Bearer <paste-token>" }}
#   }}
# }}
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
