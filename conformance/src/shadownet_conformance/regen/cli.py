"""``shadownet-conformance-fixtures`` — regenerate the canonical fixture set.

Usage::

    shadownet-conformance-fixtures regen [--check] [--only ID]

``--check`` runs every emitter, byte-diffs the output against the committed
fixture, and exits non-zero on drift. Used by CI to catch SDK serialization
changes that drift fixtures away from canon.

The v0.1 Go cross-check is removed: until ``core/`` and ``python-sdk`` both
implement the full v0.2 surface, the Python emitter is the single source of
truth. Cross-impl checks return as a v0.2.x follow-up using live runs
against ``core/``'s Provider+Issuer binaries.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.identifiers import encode_public_key

if TYPE_CHECKING:
    from collections.abc import Iterable

from shadownet_conformance.errors import ConformanceError
from shadownet_conformance.logging import get_logger
from shadownet_conformance.regen.manifest import (
    FixtureEntry,
    Manifest,
    load_manifest,
    load_seeds,
)

_logger = get_logger(__name__)

# Repo root is two parents up from this file (src/shadownet_conformance/regen/cli.py).
REPO_ROOT = Path(__file__).resolve().parents[3]
SEEDS_PATH = REPO_ROOT / "fixtures" / "seeds.toml"
MANIFEST_PATH = REPO_ROOT / "fixtures" / "_regen" / "manifest.toml"
FIXTURES_ROOT = REPO_ROOT / "fixtures"


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.subcommand == "regen":
            return _cmd_regen(check_only=args.check, only=args.only)
        parser.print_help(sys.stderr)
        return 2
    except ConformanceError as exc:
        print(f"shadownet-conformance-fixtures: {exc}", file=sys.stderr)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="shadownet-conformance-fixtures")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    regen = sub.add_parser(
        "regen",
        help="Regenerate fixtures from seeds.toml + manifest.toml.",
    )
    regen.add_argument(
        "--check",
        action="store_true",
        help=("Diff freshly-emitted bytes against committed fixtures; exit non-zero on drift."),
    )
    regen.add_argument(
        "--only",
        metavar="ID",
        action="append",
        help="Regenerate only the named fixture(s); may repeat.",
    )
    return parser


def _cmd_regen(*, check_only: bool, only: list[str] | None) -> int:
    seeds = load_seeds(SEEDS_PATH)
    manifest = load_manifest(MANIFEST_PATH)

    targets = _select(manifest, only)
    if not targets:
        raise ConformanceError(f"no fixtures matched --only {only!r}")

    encoded_keys = _encoded_keys(seeds)
    written_fixtures: dict[str, bytes] = {}
    drifted: list[str] = []
    written: list[str] = []
    for entry in targets:
        spec = _resolve_spec(entry, seeds, encoded_keys, written_fixtures)
        try:
            emitted = _run_py_emit(entry.kind, spec)
        except subprocess.CalledProcessError as exc:
            raise ConformanceError(f"py_emit failed for {entry.id}: exit {exc.returncode}") from exc

        out_path = FIXTURES_ROOT / entry.out
        if check_only:
            if not out_path.is_file():
                drifted.append(f"{entry.id}: committed fixture {out_path} missing")
                continue
            current = out_path.read_bytes()
            if current != emitted:
                drifted.append(
                    f"{entry.id}: {out_path} has drifted "
                    f"(committed {len(current)} bytes, regenerated {len(emitted)} bytes)"
                )
        else:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(emitted)
            written.append(str(out_path.relative_to(REPO_ROOT)))
        written_fixtures[entry.out] = emitted

    if drifted:
        for line in drifted:
            _logger.error("DRIFT: %s", line)
        raise ConformanceError(f"{len(drifted)} fixture(s) drifted; run without --check to update")

    if check_only:
        _logger.info("OK: %d fixtures up to date", len(targets))
    else:
        for line in written:
            _logger.info("wrote %s", line)
        _logger.info("regenerated %d fixtures", len(written))
    return 0


def _select(manifest: Manifest, only: list[str] | None) -> list[FixtureEntry]:
    if not only:
        return list(manifest.fixtures)
    wanted = set(only)
    return [e for e in manifest.fixtures if e.id in wanted]


def _encoded_keys(seeds: dict[str, bytes]) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, seed in seeds.items():
        kp = Ed25519KeyPair.from_seed(seed)
        out[name] = encode_public_key(kp.public_bytes)
    return out


def _resolve_spec(
    entry: FixtureEntry,
    seeds: dict[str, bytes],
    encoded_keys: dict[str, str],
    written_fixtures: dict[str, bytes],
) -> dict[str, Any]:
    """Materialize an emitter spec from a manifest entry.

    Translates seed names to hex strings, ``$pk:<seed>`` references to encoded
    public keys, and ``creds = ["credentials/..."]`` references to the loaded
    JWS strings of previously-emitted credential fixtures.
    """
    spec: dict[str, Any] = dict(entry.spec)
    if "seed" in spec:
        spec["seed_hex"] = seeds[spec.pop("seed")].hex()
    for key in ("issuer_seed", "subject_seed", "provider_seed", "sender_seed"):
        if key in spec:
            spec[f"{key}_hex"] = seeds[spec.pop(key)].hex()
    if "creds" in spec:
        creds_jws: list[str] = []
        for ref in spec.pop("creds"):
            data = written_fixtures.get(ref)
            if data is None:
                disk = FIXTURES_ROOT / ref
                if not disk.is_file():
                    raise ConformanceError(
                        f"credential ref {ref!r} not yet emitted; declare it earlier in manifest.toml"
                    )
                data = disk.read_bytes()
            creds_jws.append(data.decode("ascii").strip())
        spec["creds_jws"] = creds_jws
    spec["encoded_keys"] = encoded_keys
    return spec


def _run_py_emit(kind: str, spec: dict[str, Any]) -> bytes:
    """Invoke the Python emitter in a subprocess and return its stdout.

    The argv components are trusted: ``sys.executable`` is the current
    interpreter and ``kind`` is constrained to the FixtureKind enum at
    manifest-load time. Spec data flows in via stdin, not argv.
    """
    proc = subprocess.run(  # noqa: S603 — trusted argv per docstring
        [sys.executable, "-m", "shadownet_conformance.regen.py_emit", kind],
        input=json.dumps(spec).encode("utf-8"),
        capture_output=True,
        check=True,
    )
    return proc.stdout


__all__: list[str] = []


def _iter_targets(manifest: Manifest) -> Iterable[FixtureEntry]:
    return iter(manifest.fixtures)


if __name__ == "__main__":
    sys.exit(main())
