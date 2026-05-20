from __future__ import annotations

import pytest

from shadownet.oauth.pkce import (
    VERIFIER_MAX_LENGTH,
    VERIFIER_MIN_LENGTH,
    generate_code_verifier,
    s256_challenge,
    verify_s256,
)


def test_generate_code_verifier_length_window():
    v = generate_code_verifier()
    assert VERIFIER_MIN_LENGTH <= len(v) <= VERIFIER_MAX_LENGTH


def test_generate_rejects_lengths_outside_window():
    with pytest.raises(ValueError):
        generate_code_verifier(length=10)
    with pytest.raises(ValueError):
        generate_code_verifier(length=200)


def test_s256_known_vector():
    # RFC 7636 § A.2 sample.
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    challenge = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    assert s256_challenge(verifier) == challenge
    assert verify_s256(verifier=verifier, challenge=challenge)


def test_verify_rejects_short_verifier():
    assert not verify_s256(verifier="too-short", challenge="x")


def test_verify_rejects_non_unreserved_chars():
    bad = "a" * VERIFIER_MIN_LENGTH + "!"  # bang is reserved
    assert not verify_s256(verifier=bad, challenge=s256_challenge(bad))


def test_verify_constant_time_failure():
    v = generate_code_verifier()
    real = s256_challenge(v)
    flipped = "x" + real[1:]
    assert not verify_s256(verifier=v, challenge=flipped)
