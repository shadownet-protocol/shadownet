"""Read/write helpers for ``~/.hermes/config.yaml`` mcp_servers + platform blocks."""

from __future__ import annotations

import logging
import os
from typing import Any

from shadownet_hermes_plugin._skills import hermes_data_dir

__all__ = [
    "config_path",
    "ensure_mcp_server_in_config",
    "read_mcp_server_config",
    "remove_mcp_server_from_config",
    "set_platform_enabled",
]

_log = logging.getLogger(__name__)


def config_path() -> Any:
    """Path to ``<data>/config.yaml`` — the file Hermes reads at startup."""
    return hermes_data_dir() / "config.yaml"


def _load_yaml_module() -> Any | None:
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        _log.warning("shadownet plugin: pyyaml not installed; cannot read/write config.yaml")
        return None
    return yaml


def _load_config() -> tuple[Any, dict[str, Any] | None]:
    """Read ``config.yaml`` into a dict. Returns (yaml_module, cfg) or (yaml, None) on error."""
    yaml = _load_yaml_module()
    if yaml is None:
        return None, None
    path = config_path()
    try:
        if path.is_file():
            with path.open() as f:
                cfg = yaml.safe_load(f) or {}
        else:
            cfg = {}
    except (OSError, yaml.YAMLError) as e:
        _log.warning("shadownet plugin: failed to read %s (%s)", path, e)
        return yaml, None
    if not isinstance(cfg, dict):
        _log.warning("shadownet plugin: %s is not a YAML mapping", path)
        return yaml, None
    return yaml, cfg


def _write_config(yaml: Any, cfg: dict[str, Any]) -> bool:
    """Write ``cfg`` back to ``config.yaml``. Returns True on success."""
    path = config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as f:
            yaml.safe_dump(cfg, f, sort_keys=False)
    except OSError as e:
        _log.warning("shadownet plugin: failed to write %s (%s)", path, e)
        return False
    return True


def ensure_mcp_server_in_config() -> None:
    """Write ``mcp_servers.shadownet`` to ``config.yaml`` so the agent sees ``mcp_shadownet_*`` tools.

    Resolves the canonical ``mcp_endpoint`` from the sidecar's integration
    bundle (the dashboard and MCP endpoint may live on different hosts),
    then merges the entry into ``config.yaml``. Idempotent: skips the
    write if the desired block is already present and identical.

    Fault-tolerant: every failure path logs at WARNING and returns
    without raising. The plugin's other surfaces remain functional even
    if this fails.
    """
    connect_url = os.environ.get("SHADOWNET_CONNECT_URL", "").strip()
    if not connect_url:
        _log.debug(
            "shadownet plugin: SHADOWNET_CONNECT_URL not set; skipping "
            "mcp_servers.shadownet config write"
        )
        return

    try:
        from shadownet.connect.url import parse_connect_url
    except ImportError as e:
        _log.warning(
            "shadownet plugin: shadownet SDK not importable (%s); "
            "skipping MCP config write",
            e,
        )
        return

    try:
        parsed = parse_connect_url(connect_url)
    except Exception as e:  # noqa: BLE001 — any parse error is non-fatal
        _log.warning(
            "shadownet plugin: failed to parse SHADOWNET_CONNECT_URL (%s); "
            "skipping MCP config write",
            e,
        )
        return

    if not getattr(parsed, "is_inline", False):
        _log.debug(
            "shadownet plugin: connect URL not in inline form; "
            "MCP config write needs the bearer inline — skipping"
        )
        return

    base_url = parsed.base_url.rstrip("/")
    token = parsed.token
    if not base_url or not token:
        return

    try:
        import httpx
    except ImportError:
        _log.warning("shadownet plugin: httpx not installed; skipping MCP config write")
        return

    bundle_url = f"{base_url}/v1/account/me/integration-bundle"
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(
                bundle_url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
            )
            resp.raise_for_status()
            bundle = resp.json()
    except Exception as e:  # noqa: BLE001
        _log.warning(
            "shadownet plugin: failed to fetch integration bundle from %s (%s); "
            "skipping MCP config write — agent will not have mcp_shadownet_* tools "
            "until config.yaml is set manually",
            bundle_url,
            e,
        )
        return

    mcp_endpoint = bundle.get("mcp_endpoint")
    if not mcp_endpoint:
        _log.warning(
            "shadownet plugin: bundle response missing mcp_endpoint; skipping MCP config write"
        )
        return

    yaml, cfg = _load_config()
    if cfg is None or yaml is None:
        return

    desired = {
        "url": mcp_endpoint,
        "headers": {"Authorization": f"Bearer {token}"},
        "timeout": 120,
        "connect_timeout": 60,
    }

    mcp_servers = cfg.setdefault("mcp_servers", {})
    if not isinstance(mcp_servers, dict):
        _log.warning(
            "shadownet plugin: %s has non-mapping `mcp_servers`; skipping MCP config write",
            config_path(),
        )
        return

    existing = mcp_servers.get("shadownet")
    if existing == desired:
        _log.debug("shadownet plugin: mcp_servers.shadownet already up-to-date")
        return

    mcp_servers["shadownet"] = desired

    if not _write_config(yaml, cfg):
        return

    _log.info(
        "shadownet plugin: wrote mcp_servers.shadownet to %s (url=%s). "
        "Restart Hermes once for the agent's tool registry to expose "
        "mcp_shadownet_* tools.",
        config_path(),
        mcp_endpoint,
    )


def remove_mcp_server_from_config() -> bool:
    """Remove ``mcp_servers.shadownet`` from ``config.yaml`` if present.

    Returns True if the file changed, False if there was nothing to do
    (or if the read/write failed — caller may surface the warning logs).
    """
    yaml, cfg = _load_config()
    if cfg is None or yaml is None:
        return False
    mcp_servers = cfg.get("mcp_servers")
    if not isinstance(mcp_servers, dict) or "shadownet" not in mcp_servers:
        return False
    del mcp_servers["shadownet"]
    if not mcp_servers:
        del cfg["mcp_servers"]
    return _write_config(yaml, cfg)


def read_mcp_server_config() -> dict[str, Any] | None:
    """Return the current ``mcp_servers.shadownet`` block, or None if absent."""
    _, cfg = _load_config()
    if cfg is None:
        return None
    mcp_servers = cfg.get("mcp_servers")
    if not isinstance(mcp_servers, dict):
        return None
    entry = mcp_servers.get("shadownet")
    return entry if isinstance(entry, dict) else None


def set_platform_enabled(name: str, enabled: bool) -> bool:
    """Toggle ``gateway.platforms.<name>.enabled`` in ``config.yaml``.

    Returns True if the file changed. Creates the parent dict structure
    on the fly if it's missing.
    """
    yaml, cfg = _load_config()
    if cfg is None or yaml is None:
        return False
    gateway = cfg.setdefault("gateway", {})
    if not isinstance(gateway, dict):
        _log.warning("shadownet plugin: `gateway` is not a mapping; cannot toggle platform")
        return False
    platforms = gateway.setdefault("platforms", {})
    if not isinstance(platforms, dict):
        _log.warning("shadownet plugin: `gateway.platforms` is not a mapping; cannot toggle")
        return False
    platform = platforms.setdefault(name, {})
    if not isinstance(platform, dict):
        _log.warning(
            "shadownet plugin: `gateway.platforms.%s` is not a mapping; cannot toggle", name
        )
        return False
    if platform.get("enabled") == enabled:
        return False
    platform["enabled"] = enabled
    return _write_config(yaml, cfg)
