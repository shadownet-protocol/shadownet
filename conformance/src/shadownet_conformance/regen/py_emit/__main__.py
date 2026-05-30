"""Python fixture emitter — Shadownet v0.2.

Invoked as ``python -m shadownet_conformance.regen.py_emit <kind>``. Reads a
JSON spec from stdin, writes the canonical fixture bytes to stdout.

The emitter is a thin orchestrator over the v0.2 ``shadownet`` SDK's natural
signing primitives. The Go round-trip emitter (v0.1's interop oracle) is
removed at v0.2 until both implementations reach feature parity; until then
the Python emitter is the single source of truth and cross-impl checks
move to live runs against ``core/``'s Provider+Issuer binaries.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any

from shadownet.agentcard import (
    build_direct_signed_agent_card,
    build_signed_agent_card,
)
from shadownet.credential import (
    ORG_AFFILIATION,
    CredentialPayload,
    RevocationPointer,
    mint_credential,
)
from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.csr import CSR_TYP, CsrPayload, CsrRequest, mint_csr
from shadownet.envelope import EnvelopeBody, EnvelopePayload, mint_envelope
from shadownet.identifiers import encode_public_key
from shadownet.status import StatusList, encode_status_list

Emitter = Callable[[dict[str, Any]], bytes]
EMITTERS: dict[str, Emitter] = {}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m shadownet_conformance.regen.py_emit <kind>", file=sys.stderr)
        return 2
    kind = argv[1]
    spec = json.loads(sys.stdin.read())
    try:
        out = _dispatch(kind, spec)
    except KeyError as exc:
        print(f"py_emit: unknown kind {exc!s}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(out)
    return 0


def _dispatch(kind: str, spec: dict[str, Any]) -> bytes:
    if kind not in EMITTERS:
        raise KeyError(kind)
    return EMITTERS[kind](spec)


def _register(kind: str) -> Callable[[Emitter], Emitter]:
    def decorate(func: Emitter) -> Emitter:
        EMITTERS[kind] = func
        return func

    return decorate


def _key_from_seed(seed_hex: str) -> Ed25519KeyPair:
    return Ed25519KeyPair.from_seed(bytes.fromhex(seed_hex))


def _resolve_id(value: str, keys: dict[str, str]) -> str:
    """Resolve a ``$pk:<seed_name>`` reference to the encoded public key.

    The CLI substitutes seeds before invoking the emitter, but tests calling
    the emitter directly can use the same reference syntax for symmetry.
    """
    if value.startswith("$pk:"):
        return keys[value[len("$pk:") :]]
    return value


@_register("key")
def emit_key(spec: dict[str, Any]) -> bytes:
    seed_hex = spec["seed_hex"]
    kp = _key_from_seed(seed_hex)
    payload = {
        "public_key": encode_public_key(kp.public_bytes),
        "public_jwk": kp.public_jwk(),
        "private_jwk": kp.private_jwk(),
    }
    return _canonical_json(payload).encode("utf-8") + b"\n"


@_register("credential")
def emit_credential(spec: dict[str, Any]) -> bytes:
    issuer_key = _key_from_seed(spec["issuer_seed_hex"])
    keys = spec.get("encoded_keys", {})
    issuer = _resolve_id(spec["issuer"], keys)
    sub = _resolve_id(spec["sub"], keys)
    org = _resolve_id(spec["org"], keys)
    payload = CredentialPayload(
        iss=issuer,
        sub=sub,
        kind=ORG_AFFILIATION,
        org=org,
        iat=int(spec["iat"]),
        exp=int(spec["exp"]),
        rev=RevocationPointer(epoch=str(spec["epoch"]), idx=int(spec["idx"])),
    )
    jws = mint_credential(payload, issuer_key)
    return jws.encode("ascii") + b"\n"


@_register("csr")
def emit_csr(spec: dict[str, Any]) -> bytes:
    subject_key = _key_from_seed(spec["subject_seed_hex"])
    keys = spec.get("encoded_keys", {})
    payload = CsrPayload(
        iss=_resolve_id(spec["iss"], keys),
        aud=_resolve_id(spec["aud"], keys),
        iat=int(spec["iat"]),
        exp=int(spec["exp"]),
        req=CsrRequest(
            kind=str(spec["req_kind"]),
            org=_resolve_id(spec["req_org"], keys),
        ),
    )
    jws = mint_csr(payload, subject_key)
    assert jws.count(".") == 2
    assert CSR_TYP  # ensures import survives lint
    return jws.encode("ascii") + b"\n"


@_register("envelope")
def emit_envelope(spec: dict[str, Any]) -> bytes:
    sender_key = _key_from_seed(spec["sender_seed_hex"])
    keys = spec.get("encoded_keys", {})
    creds_jws = tuple(spec.get("creds_jws", ()))
    payload = EnvelopePayload(
        v="0.2",
        sender=_resolve_id(spec["from_id"], keys),
        recipient=_resolve_id(spec["to_id"], keys),
        msg_hash=str(spec.get("msg_hash") or "sha256:placeholder"),
        iat=int(spec["iat"]),
        exp=int(spec["exp"]),
        body=EnvelopeBody(text=str(spec["body_text"])),
        creds=creds_jws,
    )
    jws = mint_envelope(payload, sender_key)
    return jws.encode("ascii") + b"\n"


@_register("agentcard")
def emit_agentcard(spec: dict[str, Any]) -> bytes:
    mode = spec["mode"]
    if mode == "shadowname":
        card = build_signed_agent_card(
            name=str(spec["name"]),
            description=str(spec["description"]),
            version=str(spec["version"]),
            a2a_url=str(spec["a2a_url"]),
            shadow_public_key=encode_public_key(
                _key_from_seed(spec["subject_seed_hex"]).public_bytes
            ),
            provider_key=_key_from_seed(spec["provider_seed_hex"]),
            provider_domain=str(spec["provider_domain"]),
        )
    elif mode == "direct":
        card = build_direct_signed_agent_card(
            name=str(spec["name"]),
            description=str(spec["description"]),
            version=str(spec["version"]),
            a2a_url=str(spec["a2a_url"]),
            shadow_key=_key_from_seed(spec["subject_seed_hex"]),
        )
    else:
        raise ValueError(f"unknown agentcard mode: {mode!r}")
    return _canonical_json(card).encode("utf-8") + b"\n"


@_register("status_bitstring")
def emit_status_bitstring(spec: dict[str, Any]) -> bytes:
    size = int(spec["size_bits"])
    sl = StatusList.empty(size)
    for idx in spec.get("revoked_idx", ()):
        sl = sl.with_revoked(int(idx))
    encoded = encode_status_list(sl)
    return encoded.encode("ascii") + b"\n"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
