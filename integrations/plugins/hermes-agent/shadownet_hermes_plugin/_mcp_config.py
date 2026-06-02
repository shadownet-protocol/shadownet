"""Read/write helpers for ``~/.hermes/config.yaml`` mcp_servers + platform blocks."""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
from typing import Any

from shadownet_hermes_plugin import _paths

__all__ = [
    "config_path",
    "ensure_mcp_server_in_config",
    "read_mcp_server_config",
    "remove_mcp_server_from_config",
    "set_platform_enabled",
]

_log = logging.getLogger(__name__)


def config_path() -> Any:
    """Path to ``config.yaml`` — the file Hermes reads at startup."""
    return _paths.config_path()


def _has_env_ref_token(existing: Any, desired: dict[str, Any]) -> bool:
    """True when an existing entry matches by url but carries a ``${ENV}`` token.

    Hermes lets operators rewrite the bearer header as a ``${VAR}`` template and
    preserves it across saves. We must not clobber that with the literal token,
    so when the url matches and the existing Authorization is a template we leave
    the entry untouched.
    """
    if not isinstance(existing, dict) or existing.get("url") != desired.get("url"):
        return False
    headers = existing.get("headers")
    auth = headers.get("Authorization", "") if isinstance(headers, dict) else ""
    return isinstance(auth, str) and "${" in auth


def _is_managed() -> bool:
    """True when Hermes owns/regenerates config (NixOS/systemd/Homebrew).

    Writing config.yaml in managed mode would be clobbered by the next
    package activation, so we skip with a warning. Mirrors the host's
    ``is_managed`` signal (``HERMES_MANAGED`` env or a ``.managed`` marker).
    """
    if os.environ.get("HERMES_MANAGED", "").strip():
        return True
    try:
        return (_paths.hermes_home() / ".managed").exists()
    except OSError:
        return False


def _load_yaml_module() -> Any | None:
    try:
        import yaml
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
            with path.open(encoding="utf-8") as f:
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
    """Atomically write ``cfg`` to ``config.yaml`` with 0600 perms.

    Skips the write in Hermes managed mode (the activation script owns the
    file). Writes a sibling temp file, fsyncs, restricts permissions, then
    ``os.replace`` so a crash mid-write can never truncate the user's config.
    Returns True on success.
    """
    path = config_path()
    if _is_managed():
        _log.warning(
            "shadownet plugin: Hermes is in managed mode; not writing %s "
            "(config is owned by the activation script)",
            path,
        )
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".config.", suffix=".yaml")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                yaml.safe_dump(cfg, f, sort_keys=False)
                f.flush()
                os.fsync(f.fileno())
            if os.name != "nt":
                os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise
    except OSError as e:
        _log.warning("shadownet plugin: failed to write %s (%s)", path, e)
        return False
    return True


def ensure_mcp_server_in_config() -> None:
    """Write ``mcp_servers.shadownet`` to ``config.yaml`` so the agent sees ``mcp_shadownet_*`` tools.

    The v0.2 ``shadow://connect?...`` URI carries the MCP endpoint and
    bearer token directly (RFC 0003 §3) — no separate ``integration-bundle``
    fetch needed. For inline URIs we use the embedded token verbatim; for
    handoff URIs we skip the write because handoff redemption is the host
    LLM's onboarding-time concern, not the plugin's runtime concern.

    Fault-tolerant: every failure path logs at WARNING and returns without
    raising. The plugin's other surfaces remain functional even if this
    fails.
    """
    connect_uri = os.environ.get("SHADOWNET_CONNECT_URL", "").strip()
    if not connect_uri:
        _log.debug(
            "shadownet plugin: SHADOWNET_CONNECT_URL not set; skipping "
            "mcp_servers.shadownet config write"
        )
        return

    try:
        from shadownet.onboarding import parse_connect_uri
    except ImportError as e:
        _log.warning(
            "shadownet plugin: shadownet SDK not importable (%s); skipping MCP config write",
            e,
        )
        return

    try:
        parsed = parse_connect_uri(connect_uri)
    except Exception as e:  # noqa: BLE001 — any parse error is non-fatal
        _log.warning(
            "shadownet plugin: failed to parse SHADOWNET_CONNECT_URL (%s); "
            "skipping MCP config write",
            e,
        )
        return

    if not parsed.is_inline:
        _log.debug(
            "shadownet plugin: connect URI is in handoff form; redemption is "
            "the host LLM's responsibility — skipping config write"
        )
        return

    mcp_endpoint = parsed.mcp_endpoint
    token = parsed.access_token
    if not mcp_endpoint or not token:
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
    if _has_env_ref_token(existing, desired):
        _log.debug(
            "shadownet plugin: preserving env-ref Authorization template in mcp_servers.shadownet"
        )
        return

    mcp_servers["shadownet"] = desired

    if not _write_config(yaml, cfg):
        return

    _log.info(
        "shadownet plugin: wrote mcp_servers.shadownet to %s (url=%s). "
        "Interactive sessions auto-reload mcp_servers within a few seconds; "
        "restart the gateway daemon once if running headless to expose "
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
