"""Python fixture emitter, invoked as `python -m shadownet_conformance.regen.py_emit <kind>`.

Reads a JSON spec from stdin, writes the canonical fixture bytes to stdout.
The kind argument selects the emitter dispatch.

This module is intentionally a thin orchestrator over `shadownet-py`'s
natural signing primitives — the cross-check's value is precisely that both
SDKs use their natural paths. If shadownet-py changes its serialization in
a way that drifts from shadownet-go, the regen --check fails on CI.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any

from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.crypto.jwt import sign_jwt
from shadownet.did.key import derive_did_key

Emitter = Callable[[dict[str, Any]], bytes]
EMITTERS: dict[str, Emitter] = {}

CONTEXT_W3C_CRED_V2 = "https://www.w3.org/ns/credentials/v2"
CONTEXT_SHADOWNET_V1 = "https://sh4dow.org/contexts/v1"
TYPE_VC = "VerifiableCredential"
TYPE_SUBJECT_CRED = "ShadownetSubjectCredential"
TYPE_VP = "VerifiablePresentation"
TYPE_STATUS_LIST_CRED = "BitstringStatusListCredential"
TYPE_STATUS_LIST = "BitstringStatusList"


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


def _key_for(seed_hex: str) -> Ed25519KeyPair:
    return Ed25519KeyPair.from_seed(bytes.fromhex(seed_hex))


def _did_key_for(seed_hex: str) -> str:
    kp = _key_for(seed_hex)
    return derive_did_key(bytes(kp.public_key.public_bytes_raw()))


@_register("key")
def emit_key(spec: dict[str, Any]) -> bytes:
    seed_hex = spec["seed_hex"]
    kp = _key_for(seed_hex)
    public_jwk = kp.public_jwk()
    private_jwk = kp.private_jwk()
    did = derive_did_key(bytes(kp.public_key.public_bytes_raw()))
    payload = {
        "did": did,
        "public_jwk": public_jwk,
        "private_jwk": private_jwk,
        "seed_hex": seed_hex.lower(),
    }
    return _canonical_json_bytes(payload)


@_register("credential")
def emit_credential(spec: dict[str, Any]) -> bytes:
    issuer = spec["issuer"]
    issuer_kid = spec["issuer_kid"]
    subject = spec["subject"]
    level = spec["level"]
    subject_type = spec["subject_type"]
    iat = int(spec["iat"])
    exp = int(spec["exp"])
    jti = spec["jti"]
    issuer_seed_hex = spec["issuer_seed_hex"]

    vc: dict[str, Any] = {
        "@context": [CONTEXT_W3C_CRED_V2, CONTEXT_SHADOWNET_V1],
        "type": [TYPE_VC, TYPE_SUBJECT_CRED],
        "credentialSubject": {
            "id": subject,
            "level": level,
            "subjectType": subject_type,
        },
    }
    status = spec.get("status")
    if status is not None:
        vc["credentialStatus"] = {
            "type": "BitstringStatusListEntry",
            "statusListIndex": status["status_list_index"],
            "statusListCredential": status["status_list_credential"],
        }
    claims = {
        "iss": issuer,
        "sub": subject,
        "iat": iat,
        "exp": exp,
        "jti": jti,
        "shadownet:v": "0.1",
        "vc": vc,
    }
    token = sign_jwt(
        claims,
        _key_for(issuer_seed_hex),
        header_extras={"typ": "vc+jwt", "kid": issuer_kid},
    )
    return token.encode()


@_register("freshness")
def emit_freshness(spec: dict[str, Any]) -> bytes:
    claims = {
        "iss": spec["issuer"],
        "sub": spec["credential_jti"],
        "iat": int(spec["iat"]),
        "exp": int(spec["exp"]),
        "shadownet:freshness": "v1",
    }
    token = sign_jwt(
        claims,
        _key_for(spec["issuer_seed_hex"]),
        header_extras={"typ": "JWT", "kid": spec["issuer_kid"]},
    )
    return token.encode()


@_register("presentation")
def emit_presentation(spec: dict[str, Any]) -> bytes:
    holder_seed_hex = spec["holder_seed_hex"]
    holder_did = spec["holder_did"]
    audience_did = spec["audience_did"]
    nonce = spec["nonce"]
    iat = int(spec["iat"])
    exp = int(spec["exp"])
    credentials: list[str] = list(spec["credentials"])

    claims = {
        "iss": holder_did,
        "aud": audience_did,
        "iat": iat,
        "exp": exp,
        "nonce": nonce,
        "vp": {
            "@context": [CONTEXT_W3C_CRED_V2],
            "type": [TYPE_VP],
            "verifiableCredential": credentials,
        },
    }
    token = sign_jwt(
        claims,
        _key_for(holder_seed_hex),
        header_extras={"typ": "vp+jwt", "kid": f"{holder_did}#key-1"},
    )
    return token.encode()


@_register("sns_record")
def emit_sns_record(spec: dict[str, Any]) -> bytes:
    subject_seed_hex = spec["subject_seed_hex"]
    subject_did = spec["subject_did"]
    public_jwk = _key_for(subject_seed_hex).public_jwk()
    iat = int(spec["iat"])
    ttl = int(spec["ttl"])
    record = {
        "shadowname": spec["shadowname"],
        "did": subject_did,
        "endpoint": spec["endpoint"],
        "publicKey": public_jwk,
        "subjectType": spec["subject_type"],
        "ttl": ttl,
        "issuedAt": iat,
        "shadownet:v": "0.1",
    }
    claims = {
        "iss": spec["provider_did"],
        "sub": spec["shadowname"],
        "iat": iat,
        "exp": iat + ttl,
        "shadownet:v": "0.1",
        "record": record,
    }
    token = sign_jwt(
        claims,
        _key_for(spec["provider_seed_hex"]),
        header_extras={"typ": "JWT", "kid": spec["provider_kid"]},
    )
    return token.encode()


@_register("status_list")
def emit_status_list(spec: dict[str, Any]) -> bytes:
    # encoded_list is pre-computed by the regen CLI and passed in opaque.
    # Reason: gzip / deflate output is implementation-defined; Python and Go
    # zlib produce semantically-equivalent but byte-different compressed
    # payloads. The W3C BitstringStatusList spec only requires the
    # decompressed bits to match, not the compressed bytes — see the regen
    # CLI for the canonical encoder.
    encoded_list = spec["encoded_list"]
    iat = int(spec["iat"])
    exp = int(spec["exp"])
    claims = {
        "iss": spec["issuer"],
        "sub": spec["status_list_url"],
        "iat": iat,
        "exp": exp,
        "shadownet:v": "0.1",
        "vc": {
            "@context": [CONTEXT_W3C_CRED_V2],
            "type": [TYPE_VC, TYPE_STATUS_LIST_CRED],
            "credentialSubject": {
                "id": f"{spec['status_list_url']}#list",
                "type": TYPE_STATUS_LIST,
                "statusPurpose": spec["purpose"],
                "encodedList": encoded_list,
            },
        },
    }
    token = sign_jwt(
        claims,
        _key_for(spec["issuer_seed_hex"]),
        header_extras={"typ": "vc+jwt", "kid": spec["issuer_kid"]},
    )
    return token.encode()


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    """JSON serialize with sorted keys + 2-space indent, trailing newline.

    Used for non-JWT fixtures (keys). Sorted keys give deterministic output
    independent of dict construction order in the emitter.
    """
    return (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv))
