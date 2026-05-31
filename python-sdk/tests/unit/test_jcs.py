from __future__ import annotations

import pytest

from shadownet.jcs import JcsError, canonicalize


def test_literals() -> None:
    assert canonicalize(None) == b"null"
    assert canonicalize(True) == b"true"
    assert canonicalize(False) == b"false"


def test_integer_no_sign_on_positive() -> None:
    assert canonicalize(0) == b"0"
    assert canonicalize(1) == b"1"
    assert canonicalize(-1) == b"-1"
    assert canonicalize(1730000050) == b"1730000050"


def test_floats_rejected() -> None:
    with pytest.raises(JcsError):
        canonicalize(1.5)


def test_string_minimal_escapes() -> None:
    assert canonicalize("hello") == b'"hello"'
    assert canonicalize('he said "hi"') == b'"he said \\"hi\\""'
    assert canonicalize("a\\b") == b'"a\\\\b"'
    assert canonicalize("a\tb") == b'"a\\tb"'
    assert canonicalize("a\nb") == b'"a\\nb"'
    assert canonicalize("a\rb") == b'"a\\rb"'
    assert canonicalize("a\bb") == b'"a\\bb"'
    assert canonicalize("a\fb") == b'"a\\fb"'


def test_string_other_control_char_lowercase_hex() -> None:
    assert canonicalize("\x00\x01\x1f") == b'"\\u0000\\u0001\\u001f"'


def test_string_high_unicode_passthrough() -> None:
    # §3.2.2.2: U+0080+ MUST be serialized as-is.
    assert canonicalize("é") == '"é"'.encode()
    assert canonicalize("€") == '"€"'.encode()


def test_lone_surrogate_rejected() -> None:
    with pytest.raises(JcsError):
        canonicalize("\ud800")


def test_array_no_whitespace() -> None:
    assert canonicalize([1, 2, 3]) == b"[1,2,3]"
    assert canonicalize([]) == b"[]"
    assert canonicalize([["a"], "b"]) == b'[["a"],"b"]'


def test_object_keys_sorted_utf16() -> None:
    # §3.2.3: lexicographic by UTF-16 code units. ASCII order suffices here.
    assert canonicalize({"b": 2, "a": 1}) == b'{"a":1,"b":2}'


def test_object_keys_sorted_unicode() -> None:
    # "é" (U+00E9) sorts after ASCII letters by code unit.
    out = canonicalize({"é": 1, "z": 2})
    assert out.startswith(b'{"z":2,')


def test_object_recursive_sort() -> None:
    payload: dict[str, object] = {
        "outer": {"z": 1, "a": {"y": 2, "b": 3}},
        "first": "x",
    }
    assert canonicalize(payload) == b'{"first":"x","outer":{"a":{"b":3,"y":2},"z":1}}'


def test_object_non_string_key_rejected() -> None:
    with pytest.raises(JcsError):
        canonicalize({1: "v"})


def test_nested_envelope_like_shape() -> None:
    # Approximates the canonical-message shape we'll feed into msgHash (§8.4).
    msg = {
        "messageId": "01HZ7K3CWAB4D6N5XT0M2EXAMPLE",
        "role": "ROLE_USER",
        "parts": [{"text": "hi"}],
        "contextId": "01HZ7K2BV5R2K0DW3FCONTEXT0001",
        "metadata": {},
    }
    expected = (
        b'{"contextId":"01HZ7K2BV5R2K0DW3FCONTEXT0001",'
        b'"messageId":"01HZ7K3CWAB4D6N5XT0M2EXAMPLE",'
        b'"metadata":{},'
        b'"parts":[{"text":"hi"}],'
        b'"role":"ROLE_USER"}'
    )
    assert canonicalize(msg) == expected
