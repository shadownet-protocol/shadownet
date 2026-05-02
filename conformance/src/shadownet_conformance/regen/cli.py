"""`shadownet-conformance-fixtures` — regenerate the canonical fixture set.

Usage:
    shadownet-conformance-fixtures regen [--check] [--only ID]

`--check` runs every emitter pair, byte-diffs the output against the committed
fixture, and exits non-zero on drift. Used by CI to catch SDK serialization
changes that drift fixtures away from canon.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import json
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.did.key import derive_did_key

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

from shadownet_conformance.errors import ConformanceError, FixtureDrift
from shadownet_conformance.logging import get_logger
from shadownet_conformance.regen import crosscheck
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
        help="Diff freshly-emitted bytes against committed fixtures; exit non-zero on drift.",
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
    go_emit = crosscheck.find_go_emit_binary(REPO_ROOT)

    targets = _select(manifest, only)
    if not targets:
        raise ConformanceError(f"no fixtures matched --only {only!r}")

    derived = _derive_state(targets, seeds)
    drifted: list[str] = []
    written: list[str] = []
    for entry in targets:
        spec = _resolve_spec(entry, seeds, derived)
        result = crosscheck.cross_check_emit(entry.kind, spec, go_emit)
        if not result.matched:
            _logger.error("CROSS-CHECK MISMATCH for %s", entry.id)
            _logger.error("  py_emit (%d bytes): %r", len(result.py_bytes), result.py_bytes[:200])
            _logger.error("  go-emit (%d bytes): %r", len(result.go_bytes), result.go_bytes[:200])
            raise ConformanceError(
                f"cross-check failed for {entry.id}: shadownet-py and shadownet-go "
                "produced different bytes for the same input. This is a wire-level "
                "interop concern — fix the SDK that drifted before regenerating."
            )

        out_path = FIXTURES_ROOT / entry.out
        if check_only:
            committed = out_path.read_bytes() if out_path.is_file() else b""
            if committed != result.bytes_:
                drifted.append(entry.id)
                _logger.error(
                    "DRIFT for %s: committed != freshly-emitted (out=%s)",
                    entry.id,
                    entry.out,
                )
        else:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(result.bytes_)
            written.append(entry.id)
            _logger.info("wrote %s -> %s (%d bytes)", entry.id, entry.out, len(result.bytes_))

    if check_only:
        if drifted:
            raise FixtureDrift(
                f"{len(drifted)} fixture(s) drifted from committed bytes: {', '.join(drifted)}. "
                "Run `shadownet-conformance-fixtures regen` and commit the result."
            )
        _logger.info("regen --check OK: %d fixtures verified", len(targets))
    else:
        _logger.info("regen OK: %d fixtures written", len(written))
    return 0


def _select(manifest: Manifest, only: list[str] | None) -> list[FixtureEntry]:
    if not only:
        return list(manifest.fixtures)
    wanted = set(only)
    return [e for e in manifest.fixtures if e.id in wanted]


def _derive_state(entries: Iterable[FixtureEntry], seeds: Mapping[str, bytes]) -> dict[str, Any]:
    """Pre-compute everything the manifest entries can reference.

    Returns a dict with two keys:

    - ``dids``: {seed_name: did:key DID derived from the seed}
    - ``read_fixture(rel_path) -> bytes``: callable that reads a previously-written
      fixture file from FIXTURES_ROOT.
    """
    dids: dict[str, str] = {}
    for name, seed in seeds.items():
        kp = Ed25519KeyPair.from_seed(seed)
        dids[name] = derive_did_key(bytes(kp.public_key.public_bytes_raw()))

    return {"dids": dids}


def _resolve_spec(
    entry: FixtureEntry,
    seeds: Mapping[str, bytes],
    derived: Mapping[str, Any],
) -> dict[str, Any]:
    """Materialize the manifest spec into the JSON the emitters expect.

    Resolves seed references (`subject_seed` -> `subject_seed_hex` + computed
    `subject_did`), reads referenced credential fixtures for VPs, and
    pre-computes the BitstringStatusList encodedList.
    """
    spec = dict(entry.spec)

    if entry.kind == "key":
        seed_name = spec.pop("seed")
        spec["seed_hex"] = seeds[seed_name].hex()
        return spec

    if entry.kind == "credential":
        issuer_seed = spec.pop("issuer_seed")
        spec["issuer_seed_hex"] = seeds[issuer_seed].hex()
        if "subject_seed" in spec:
            subject_seed = spec.pop("subject_seed")
            spec["subject"] = derived["dids"][subject_seed]
        else:
            spec["subject"] = spec.pop("subject_did")
        return spec

    if entry.kind == "freshness":
        issuer_seed = spec.pop("issuer_seed")
        spec["issuer_seed_hex"] = seeds[issuer_seed].hex()
        return spec

    if entry.kind == "presentation":
        holder_seed = spec.pop("holder_seed")
        audience_seed = spec.pop("audience_seed")
        spec["holder_seed_hex"] = seeds[holder_seed].hex()
        spec["holder_did"] = derived["dids"][holder_seed]
        spec["audience_did"] = derived["dids"][audience_seed]
        refs = spec.pop("credential_refs")
        spec["credentials"] = [_read_committed_jwt(r) for r in refs]
        return spec

    if entry.kind == "sns_record":
        provider_seed = spec.pop("provider_seed")
        subject_seed = spec.pop("subject_seed")
        spec["provider_seed_hex"] = seeds[provider_seed].hex()
        spec["subject_seed_hex"] = seeds[subject_seed].hex()
        spec["subject_did"] = derived["dids"][subject_seed]
        return spec

    if entry.kind == "status_list":
        issuer_seed = spec.pop("issuer_seed")
        spec["issuer_seed_hex"] = seeds[issuer_seed].hex()
        size = int(spec.pop("size"))
        set_indices = list(spec.pop("set_indices"))
        # list_id is informational; the wire artifact carries status_list_url.
        spec.pop("list_id", None)
        spec["encoded_list"] = _encode_bitstring(size, set_indices)
        return spec

    raise ConformanceError(f"unknown fixture kind: {entry.kind}")


def _read_committed_jwt(rel_path: str) -> str:
    full = FIXTURES_ROOT / rel_path
    if not full.is_file():
        raise ConformanceError(
            f"VP references {rel_path} which has not been emitted yet. "
            "Order your manifest so the referenced fixture comes first."
        )
    return full.read_bytes().decode("ascii").strip()


def _encode_bitstring(size: int, set_indices: list[int]) -> str:
    """Canonical bitstring encoder for BitstringStatusList #encodedList.

    gzip output is implementation-defined; the conformance suite picks the
    Python `gzip.compress(buf, mtime=0)` output as the canonical bytes for
    fixtures. Implementations are required to produce a *decompressed*
    bitstring with the same bits set, not byte-equal compressed payloads.
    """
    nbytes = (size + 7) // 8
    buf = bytearray(nbytes)
    for idx in set_indices:
        if idx < 0 or idx >= size:
            raise ConformanceError(f"set_index {idx} out of range for size {size}")
        buf[idx // 8] |= 1 << (7 - (idx % 8))
    compressed = gzip.compress(bytes(buf), mtime=0)
    return base64.urlsafe_b64encode(compressed).rstrip(b"=").decode("ascii")


# Imported here only to satisfy the JSON spec parser; emitters get them via stdin.
_ = json  # silence unused import in rare formatter passes


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
