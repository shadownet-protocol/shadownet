from __future__ import annotations

import json
import time

import pytest

from shadownet.sca.callback import (
    CallbackReplayWindowError,
    CallbackSignatureError,
    build_callback_headers,
    sign_callback,
    verify_callback,
)


def _body(status: str = "ready", session_id: str = "ses-01") -> bytes:
    return json.dumps({"shadownet:v": "0.1", "sessionId": session_id, "status": status}).encode()


def test_round_trip() -> None:
    body = _body()
    headers = build_callback_headers(body, session_id="ses-01")
    event = verify_callback(headers, body, session_id="ses-01")
    assert event.session_id == "ses-01"
    assert event.status == "ready"


def test_signature_mismatch() -> None:
    body = _body()
    headers = build_callback_headers(body, session_id="ses-01")
    with pytest.raises(CallbackSignatureError):
        verify_callback(headers, body, session_id="ses-other")


def test_missing_headers() -> None:
    body = _body()
    with pytest.raises(CallbackSignatureError):
        verify_callback({}, body, session_id="ses-01")


def test_replay_window() -> None:
    body = _body()
    headers = build_callback_headers(body, session_id="ses-01", timestamp=int(time.time()) - 1000)
    with pytest.raises(CallbackReplayWindowError):
        verify_callback(headers, body, session_id="ses-01")


def test_signature_helper_returns_hex() -> None:
    body = b"x"
    sig = sign_callback(body, session_id="abc")
    assert all(c in "0123456789abcdef" for c in sig)
    assert len(sig) == 64
