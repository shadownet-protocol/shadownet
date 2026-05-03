from __future__ import annotations

import argparse
import os
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Final, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator

if TYPE_CHECKING:
    from collections.abc import Iterable

from shadownet_conformance._version import __version__
from shadownet_conformance.errors import ConfigError

ENV_PREFIX: Final[str] = "SHADOWNET_CONFORMANCE_"
DEFAULT_SPECS_PATH: Final[Path] = Path("../shadownet-specs")
DEFAULT_PROOF_METHOD_URI: Final[str] = "instant-approval"
DEFAULT_HTTP_TIMEOUT_SECONDS: Final[float] = 10.0


class Role(StrEnum):
    """Conformance class a target URL implements."""

    SCA = "sca"
    SNS = "sns"
    SIDECAR = "sidecar"


class Config(BaseModel):
    """Parsed conformance run configuration. Single source of truth."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    targets: dict[Role, str] = Field(default_factory=dict)
    peer_targets: dict[Role, str] = Field(default_factory=dict)
    proof_method_uri: str = DEFAULT_PROOF_METHOD_URI
    sns_test_shadowname: str | None = None
    specs_path: Path = DEFAULT_SPECS_PATH
    http_timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS
    report_junit: Path | None = None
    report_json: Path | None = None
    gha_summary: Path | None = None
    include_draft: bool = False
    include_network: bool = True
    marker_expr: str | None = None
    peer_listen_host: str = "127.0.0.1"

    @field_validator("targets", "peer_targets")
    @classmethod
    def _no_blank_urls(cls, value: dict[Role, str]) -> dict[Role, str]:
        for role, url in value.items():
            if not url or not url.strip():
                raise ValueError(f"target {role.value!r} has empty URL")
        return value

    def target(self, role: Role) -> str | None:
        return self.targets.get(role)

    def peer_target(self, role: Role) -> str | None:
        return self.peer_targets.get(role)

    def has_round_trip(self, role: Role) -> bool:
        return role in self.targets and role in self.peer_targets

    @classmethod
    def from_argv(cls, argv: list[str] | None = None) -> tuple[Self, argparse.Namespace]:
        """Build a Config from CLI args, falling back to env for unset fields."""
        parser = build_parser()
        args = parser.parse_args(argv)
        return cls.from_namespace(args), args

    @classmethod
    def from_namespace(cls, args: argparse.Namespace) -> Self:
        targets = _parse_target_list(args.target or _env_list("TARGET"))
        peer_targets = _parse_target_list(args.peer_target or _env_list("PEER_TARGET"))
        return cls(
            targets=targets,
            peer_targets=peer_targets,
            proof_method_uri=args.proof_method
            or _env_str("PROOF_METHOD")
            or DEFAULT_PROOF_METHOD_URI,
            specs_path=Path(args.specs_path or _env_str("SPECS_PATH") or DEFAULT_SPECS_PATH),
            http_timeout_seconds=float(
                args.http_timeout
                if args.http_timeout is not None
                else _env_str("HTTP_TIMEOUT") or DEFAULT_HTTP_TIMEOUT_SECONDS
            ),
            report_junit=Path(args.report_junit) if args.report_junit else None,
            report_json=Path(args.report_json) if args.report_json else None,
            gha_summary=_resolve_gha_summary(args.gha_summary or _env_str("GHA_SUMMARY")),
            include_draft=args.include_draft or _env_bool("INCLUDE_DRAFT"),
            include_network=not args.no_network and _env_bool("INCLUDE_NETWORK", default=True),
            marker_expr=args.marker_expr or _env_str("MARKER_EXPR"),
            peer_listen_host=args.peer_listen_host or _env_str("PEER_LISTEN_HOST") or "127.0.0.1",
            sns_test_shadowname=args.sns_test_shadowname or _env_str("SNS_TEST_SHADOWNAME"),
        )


def build_parser() -> argparse.ArgumentParser:
    """Construct the `shadownet-conformance` argument parser."""
    parser = argparse.ArgumentParser(
        prog="shadownet-conformance",
        description=(
            "Wire-level interop test suite for the Shadownet protocol. "
            "Targets one or more SCA / SNS / Sidecar URLs and reports per-RFC pass/fail."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--target",
        action="append",
        metavar="ROLE=URL",
        help="Target URL for a conformance class. Repeat per role: "
        "--target sca=https://sca.example --target sns=https://sns.example",
    )
    parser.add_argument(
        "--peer-target",
        action="append",
        metavar="ROLE=URL",
        help="Second URL for the same role; enables round-trip tests.",
    )
    parser.add_argument(
        "--proof-method",
        metavar="URI",
        help=(
            f"Proof-method URI the SCA target exposes for instant-approval issuance "
            f"(default: {DEFAULT_PROOF_METHOD_URI})."
        ),
    )
    parser.add_argument(
        "--specs-path",
        metavar="DIR",
        help=f"Path to the shadownet-specs checkout (default: {DEFAULT_SPECS_PATH}).",
    )
    parser.add_argument(
        "--http-timeout",
        type=float,
        metavar="SECONDS",
        help=f"Per-request HTTP timeout (default: {DEFAULT_HTTP_TIMEOUT_SECONDS}s).",
    )
    parser.add_argument("--report-junit", metavar="PATH", help="Write JUnit XML report to PATH.")
    parser.add_argument(
        "--report-json", metavar="PATH", help="Write RFC-keyed JSON report to PATH."
    )
    parser.add_argument(
        "--gha-summary",
        metavar="PATH|auto",
        help=(
            "Write a GitHub Actions step-summary markdown to PATH. "
            "Use 'auto' (default in CI) to honor $GITHUB_STEP_SUMMARY."
        ),
    )
    parser.add_argument(
        "--include-draft",
        action="store_true",
        help="Include tests marked @pytest.mark.draft (skipped by default).",
    )
    parser.add_argument(
        "--no-network",
        action="store_true",
        help="Skip every test that requires a live target.",
    )
    parser.add_argument(
        "-m",
        "--marker-expr",
        dest="marker_expr",
        metavar="EXPR",
        help="Pytest marker expression (passed through as `-m EXPR`).",
    )
    parser.add_argument(
        "--peer-listen-host",
        metavar="HOST",
        help="Bind host for the in-process A2A test peer (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--sns-test-shadowname",
        metavar="LOCAL@PROVIDER",
        help=(
            "Shadowname the operator has pre-registered against the SNS target, "
            "used by the resolve happy-path test. If unset, the test is skipped."
        ),
    )
    return parser


def _parse_target_list(items: Iterable[str]) -> dict[Role, str]:
    out: dict[Role, str] = {}
    for raw in items:
        if "=" not in raw:
            raise ConfigError(f"--target must be of the form ROLE=URL, got: {raw!r}")
        role_str, url = raw.split("=", 1)
        try:
            role = Role(role_str.strip().lower())
        except ValueError as exc:
            valid = ", ".join(r.value for r in Role)
            raise ConfigError(
                f"unknown role {role_str!r} in {raw!r}; valid roles: {valid}"
            ) from exc
        if role in out:
            raise ConfigError(f"role {role.value!r} given more than once")
        out[role] = url.strip()
    return out


def _env_list(suffix: str) -> list[str]:
    raw = os.environ.get(f"{ENV_PREFIX}{suffix}", "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def _env_str(suffix: str) -> str | None:
    value = os.environ.get(f"{ENV_PREFIX}{suffix}")
    return value if value else None


def _env_bool(suffix: str, *, default: bool = False) -> bool:
    raw = os.environ.get(f"{ENV_PREFIX}{suffix}")
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _resolve_gha_summary(value: str | None) -> Path | None:
    if value is None:
        return None
    if value == "auto":
        env_path = os.environ.get("GITHUB_STEP_SUMMARY")
        return Path(env_path) if env_path else None
    return Path(value)
