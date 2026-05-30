from __future__ import annotations

import time
from typing import Any

import pytest
from pydantic import ValidationError

from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.crypto.jwt import decode_header
from shadownet.envelope import (
    ENVELOPE_EXTENSION_URI,
    ENVELOPE_TYP,
    MAX_LIFETIME_SECONDS,
    EnvelopeBody,
    EnvelopeError,
    EnvelopePayload,
    compute_msg_hash,
    mint_envelope,
    verify_envelope,
)


def _msg_with_envelope(envelope: str) -> dict[str, Any]:
    return {
        "messageId": "01HZ7K3CWAB4D6N5XT0M2EXAMPLE",
        "role": "ROLE_USER",
        "parts": [{"text": "Hi"}],
        "contextId": "01HZ7K2BV5R2K0DW3FCONTEXT0001",
        "metadata": {ENVELOPE_EXTENSION_URI: envelope},
    }


def _base_payload(sender_key: Ed25519KeyPair, msg_hash: str) -> EnvelopePayload:
    now = int(time.time())
    return EnvelopePayload(
        v="0.2",
        **{"from": "alice@sh4dow.org", "to": "bob@example.org", "msgHash": msg_hash},
        iat=now,
        exp=now + 60,
        body=EnvelopeBody(text="Hi", intent=None, data=None),
    )


class TestComputeMsgHash:
    def test_stable_across_envelope_change(self) -> None:
        # §8.4: the Shadownet metadata key must be stripped before hashing,
        # so changing the envelope JWS does not change msgHash.
        msg_a = _msg_with_envelope("envelope-version-A")
        msg_b = _msg_with_envelope("envelope-version-B")
        assert compute_msg_hash(msg_a) == compute_msg_hash(msg_b)

    def test_part_change_changes_hash(self) -> None:
        msg_a = _msg_with_envelope("e")
        msg_b = _msg_with_envelope("e")
        msg_b["parts"] = [{"text": "Bye"}]
        assert compute_msg_hash(msg_a) != compute_msg_hash(msg_b)

    def test_optional_fields_omitted_when_absent(self) -> None:
        msg = {
            "messageId": "m",
            "role": "ROLE_USER",
            "parts": [],
            "metadata": {ENVELOPE_EXTENSION_URI: "e"},
        }
        # No contextId, no taskId — should not affect hash by being absent vs null.
        h = compute_msg_hash(msg)
        assert h.startswith("sha256:")

    def test_other_metadata_keys_preserved(self) -> None:
        msg_a = _msg_with_envelope("e")
        msg_b = _msg_with_envelope("e")
        msg_b["metadata"]["other"] = "value"
        assert compute_msg_hash(msg_a) != compute_msg_hash(msg_b)


@pytest.fixture
def sender_key() -> Ed25519KeyPair:
    return Ed25519KeyPair.generate()


class TestMintEnvelope:
    def test_happy_path(self, sender_key: Ed25519KeyPair) -> None:
        msg_hash = compute_msg_hash(_msg_with_envelope("placeholder"))
        payload = _base_payload(sender_key, msg_hash)
        jws = mint_envelope(payload, sender_key)
        header = decode_header(jws)
        assert header["typ"] == ENVELOPE_TYP
        assert header["alg"] == "EdDSA"
        assert header["kid"] == "alice@sh4dow.org"

    def test_lifetime_over_300s_rejected(self, sender_key: Ed25519KeyPair) -> None:
        now = int(time.time())
        payload = EnvelopePayload(
            v="0.2",
            **{"from": "alice@sh4dow.org", "to": "bob@example.org", "msgHash": "sha256:abc"},
            iat=now,
            exp=now + MAX_LIFETIME_SECONDS + 1,
            body=EnvelopeBody(),
        )
        with pytest.raises(EnvelopeError, match="300"):
            mint_envelope(payload, sender_key)

    def test_exp_must_exceed_iat(self, sender_key: Ed25519KeyPair) -> None:
        now = int(time.time())
        payload = EnvelopePayload(
            v="0.2",
            **{"from": "alice@sh4dow.org", "to": "bob@example.org", "msgHash": "sha256:abc"},
            iat=now,
            exp=now,
            body=EnvelopeBody(),
        )
        with pytest.raises(EnvelopeError, match="exp"):
            mint_envelope(payload, sender_key)


class TestVerifyEnvelope:
    def test_happy_path(self, sender_key: Ed25519KeyPair) -> None:
        msg_hash = compute_msg_hash(_msg_with_envelope("placeholder"))
        payload = _base_payload(sender_key, msg_hash)
        jws = mint_envelope(payload, sender_key)
        out = verify_envelope(jws, sender_key, expected_recipient="bob@example.org")
        assert out.sender == "alice@sh4dow.org"
        assert out.recipient == "bob@example.org"

    def test_wrong_recipient_rejected(self, sender_key: Ed25519KeyPair) -> None:
        msg_hash = compute_msg_hash(_msg_with_envelope("placeholder"))
        payload = _base_payload(sender_key, msg_hash)
        jws = mint_envelope(payload, sender_key)
        with pytest.raises(EnvelopeError, match="does not match"):
            verify_envelope(jws, sender_key, expected_recipient="eve@example.org")

    def test_wrong_signature_rejected(self, sender_key: Ed25519KeyPair) -> None:
        msg_hash = compute_msg_hash(_msg_with_envelope("placeholder"))
        payload = _base_payload(sender_key, msg_hash)
        jws = mint_envelope(payload, sender_key)
        other = Ed25519KeyPair.generate()
        with pytest.raises(EnvelopeError, match="signature"):
            verify_envelope(jws, other, expected_recipient="bob@example.org")

    def test_expired_rejected(self, sender_key: Ed25519KeyPair) -> None:
        now = int(time.time())
        payload = EnvelopePayload(
            v="0.2",
            **{"from": "alice@sh4dow.org", "to": "bob@example.org", "msgHash": "sha256:abc"},
            iat=now - 500,
            exp=now - 200,
            body=EnvelopeBody(),
        )
        jws = mint_envelope(payload, sender_key)
        with pytest.raises(EnvelopeError, match="expired"):
            verify_envelope(jws, sender_key, expected_recipient="bob@example.org")

    def test_iat_in_future_rejected(self, sender_key: Ed25519KeyPair) -> None:
        now = int(time.time())
        payload = EnvelopePayload(
            v="0.2",
            **{"from": "alice@sh4dow.org", "to": "bob@example.org", "msgHash": "sha256:abc"},
            iat=now + 200,
            exp=now + 400,
            body=EnvelopeBody(),
        )
        jws = mint_envelope(payload, sender_key)
        with pytest.raises(EnvelopeError, match="future"):
            verify_envelope(jws, sender_key, expected_recipient="bob@example.org")


class TestEnvelopePayloadValidation:
    def test_version_must_be_0_2(self) -> None:
        with pytest.raises(ValidationError):
            EnvelopePayload(
                v="0.3",
                **{"from": "alice@sh4dow.org", "to": "bob@example.org", "msgHash": "sha256:abc"},
                iat=0,
                exp=1,
                body=EnvelopeBody(),
            )

    def test_msg_hash_format(self) -> None:
        with pytest.raises(ValidationError):
            EnvelopePayload(
                v="0.2",
                **{"from": "alice@sh4dow.org", "to": "bob@example.org", "msgHash": "md5:abc"},
                iat=0,
                exp=1,
                body=EnvelopeBody(),
            )
