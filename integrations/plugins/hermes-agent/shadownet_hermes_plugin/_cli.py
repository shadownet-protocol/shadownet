"""``hermes shadownet`` CLI subcommand tree (status / doctor / sync / logout)."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from shadownet_hermes_plugin import _env, _mcp_config, _skills

if TYPE_CHECKING:
    import argparse

__all__ = ["do_doctor", "do_logout", "do_status", "do_sync", "handle", "setup"]


def setup(subparser: argparse.ArgumentParser) -> None:
    """Build the argparse tree for ``hermes shadownet ...``."""
    subs = subparser.add_subparsers(dest="shadownet_cmd")
    subs.add_parser("status", help="Show shadownet connection state")
    subs.add_parser("doctor", help="Run end-to-end self-check")
    subs.add_parser("sync", help="Re-write MCP config + re-materialize skills")
    subs.add_parser("logout", help="Disconnect this Hermes from shadownet")
    subparser.set_defaults(func=handle)


def handle(args: argparse.Namespace) -> None:
    """Dispatch to the per-subcommand implementation."""
    cmd = getattr(args, "shadownet_cmd", None)
    if cmd == "status":
        print(do_status())
    elif cmd == "doctor":
        report, ok = _doctor_report()
        print(report)
        raise SystemExit(0 if ok else 1)
    elif cmd == "sync":
        print(do_sync())
    elif cmd == "logout":
        print(do_logout())
    else:
        print("Usage: hermes shadownet {status|doctor|sync|logout}")


def _format_connect_url(value: str | None) -> str:
    """Redact a connect URL for display: show the endpoint, never the token."""
    if not value:
        return "absent"
    try:
        from shadownet.onboarding import parse_connect_uri

        parsed = parse_connect_uri(value)
    except Exception:  # noqa: BLE001 - any parse failure means we just say "present"
        return "present (unparseable)"
    token_state = "token present" if parsed.access_token else "no token"
    return f"{parsed.mcp_endpoint} ({token_state})"


def do_status() -> str:
    """Report current shadownet connection state."""
    lines = ["shadownet plugin status:"]
    env_url = _env.read_connect_url_from_env()
    runtime_url = os.environ.get("SHADOWNET_CONNECT_URL", "").strip() or None
    lines.append(f"  .env SHADOWNET_CONNECT_URL: {_format_connect_url(env_url)}")
    lines.append(f"  runtime SHADOWNET_CONNECT_URL: {_format_connect_url(runtime_url)}")
    mcp = _mcp_config.read_mcp_server_config()
    if mcp:
        url = mcp.get("url", "<missing>")
        lines.append(f"  config.yaml mcp_servers.shadownet: {url}")
    else:
        lines.append("  config.yaml mcp_servers.shadownet: absent")
    count = _skills.count_materialized_skills()
    total = len(_skills.SKILL_NAMES)
    lines.append(f"  materialized skills: {count}/{total} under {_skills.hermes_data_dir()}")
    return "\n".join(lines)


def _doctor_report() -> tuple[str, bool]:
    """Run each end-to-end check; return ``(report, overall_ok)``."""
    results: list[tuple[str, bool, str]] = []

    env_url = _env.read_connect_url_from_env()
    runtime_url = os.environ.get("SHADOWNET_CONNECT_URL", "").strip() or None
    has_url = bool(env_url or runtime_url)
    results.append(
        (
            "SHADOWNET_CONNECT_URL set",
            has_url,
            "found in .env or runtime env" if has_url else "missing — paste your connect URL",
        )
    )

    mcp = _mcp_config.read_mcp_server_config()
    results.append(
        (
            "config.yaml mcp_servers.shadownet present",
            mcp is not None,
            f"url={mcp['url']}" if mcp else "absent — run `hermes shadownet sync`",
        )
    )

    if mcp and has_url:
        reachable, why = _probe_mcp_endpoint(mcp.get("url", ""))
        results.append(("MCP endpoint reachable", reachable, why))

    materialized = _skills.count_materialized_skills()
    total = len(_skills.SKILL_NAMES)
    results.append(
        (
            "skills materialized",
            materialized == total,
            f"{materialized}/{total} present",
        )
    )

    lines = ["shadownet plugin doctor:"]
    for label, ok, detail in results:
        prefix = "OK  " if ok else "FAIL"
        lines.append(f"  [{prefix}] {label} — {detail}")
    overall = all(ok for _, ok, _ in results)
    lines.append("  ----")
    lines.append(f"  overall: {'OK' if overall else 'FAIL'}")
    return "\n".join(lines), overall


def do_doctor() -> str:
    """Run the self-check and return the multi-line report (see :func:`_doctor_report`)."""
    return _doctor_report()[0]


def _probe_mcp_endpoint(url: str) -> tuple[bool, str]:
    if not url:
        return False, "no URL configured"
    try:
        import httpx
    except ImportError:
        return False, "httpx not installed"
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(url, follow_redirects=False)
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
    # Any non-5xx response means the endpoint is alive; MCP servers
    # commonly return 4xx for a plain GET.
    if resp.status_code < 500:
        return True, f"HTTP {resp.status_code}"
    return False, f"HTTP {resp.status_code}"


def do_sync() -> str:
    """Re-write MCP config + re-materialize skills (idempotent)."""
    _mcp_config.ensure_mcp_server_in_config()
    _skills.materialize_skills_into_data_dir(_skills.skill_paths())
    return (
        "shadownet plugin sync complete. "
        "Restart Hermes for the agent's tool registry to pick up any changes."
    )


def do_logout() -> str:
    """Remove the connection state from this Hermes install."""
    removed_mcp = _mcp_config.remove_mcp_server_from_config()
    cleared_env = _env.strip_connect_url_from_env()
    actions: list[str] = []
    if removed_mcp:
        actions.append("removed config.yaml mcp_servers.shadownet")
    if cleared_env:
        actions.append("cleared SHADOWNET_CONNECT_URL from .env")
    # Only touch gateway.platforms.shadownet.enabled when there was real
    # state to disconnect — otherwise we'd materialize a phantom config.yaml
    # on a fresh, never-connected install.
    if actions:
        disabled = _mcp_config.set_platform_enabled("shadownet", False)
        if disabled:
            actions.append("set gateway.platforms.shadownet.enabled=false")
    env_file = _env.env_path()
    if not actions:
        return (
            "shadownet plugin: already disconnected (nothing to remove). "
            f"To reconnect: paste your shadow://connect?mcp=…&token=… URL into {env_file} "
            "and run `hermes shadownet sync`."
        )
    return (
        "Disconnected from shadownet:\n  - "
        + "\n  - ".join(actions)
        + "\nRestart Hermes (docker compose restart hermes) for changes to take effect.\n"
        f"To reconnect: paste your shadow://connect?mcp=…&token=… URL into {env_file}, "
        "run `hermes shadownet sync`, and restart."
    )
