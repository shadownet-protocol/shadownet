from __future__ import annotations

import time
from typing import Any

import httpx
import pytest
import respx

from shadownet.a2a import (
    A2A_VERSION,
    ENVELOPE_EXTENSION_URI,
    PROBLEM_JSON_MEDIA_TYPE,
    WIRE_ERROR_REGISTRY,
    A2AMessage,
    BuiltMessage,
    CredsRejectedError,
    CredsRequiredError,
    ParseError,
    PolicyError,
    ShadownetWireError,
    TransportRetryExhausted,
    UnknownRecipientError,
    acceptance_headers,
    build_acceptance_response,
    build_and_sign_message,
    build_outbound_message,
    extract_envelope_jws,
    problem_response,
    send_envelope,
    send_with_retries,
    wire_error_from_problem,
)
from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.envelope import EnvelopeBody, EnvelopePayload, verify_envelope


def _payload_template(msg_hash: str = "sha256:placeholder") -> EnvelopePayload:
    now = int(time.time())
    return EnvelopePayload(
        v="0.2",
        **{
            "from": "alice@sh4dow.org",
            "to": "bob@example.org",
            "msgHash": msg_hash,
        },
        iat=now,
        exp=now + 60,
        body=EnvelopeBody(text="hello", intent=None, data=None),
    )


class TestBuildOutboundMessage:
    def test_basic_shape(self) -> None:
        msg = build_outbound_message(body_text="hi")
        wire = msg.to_wire()
        assert wire["role"] == "ROLE_USER"
        assert wire["parts"] == [{"text": "hi"}]
        assert wire["extensions"] == [ENVELOPE_EXTENSION_URI]
        assert wire["metadata"] == {}
        assert wire["messageId"]

    def test_explicit_ids(self) -> None:
        msg = build_outbound_message(
            body_text="hi", context_id="ctx-1", message_id="msg-1", task_id="task-1"
        )
        wire = msg.to_wire()
        assert wire["contextId"] == "ctx-1"
        assert wire["taskId"] == "task-1"
        assert wire["messageId"] == "msg-1"

    def test_extra_metadata_preserved(self) -> None:
        msg = build_outbound_message(body_text="hi", extra_metadata={"hint": "value"})
        wire = msg.to_wire()
        assert wire["metadata"]["hint"] == "value"


class TestBuildAndSignMessage:
    def test_full_roundtrip(self) -> None:
        sender_key = Ed25519KeyPair.generate()
        outbound = build_outbound_message(body_text="hello", context_id="ctx-1")
        built = build_and_sign_message(outbound, _payload_template(), sender_key)
        # Envelope JWS lands in metadata under the Shadownet URI.
        assert built.message["metadata"][ENVELOPE_EXTENSION_URI] == built.envelope_jws
        # Receiver-side reverification round-trips the recipient correctly.
        verified = verify_envelope(
            built.envelope_jws, sender_key, expected_recipient="bob@example.org"
        )
        assert verified.recipient == "bob@example.org"
        # msgHash was actually computed from the message (not the placeholder).
        assert built.envelope_payload.msg_hash.startswith("sha256:")
        assert built.envelope_payload.msg_hash != "sha256:placeholder"


class TestExtractEnvelopeJws:
    def test_happy_path(self) -> None:
        outbound = build_outbound_message(body_text="hi")
        wire = outbound.to_wire()
        wire["metadata"][ENVELOPE_EXTENSION_URI] = "h.p.s"
        envelope, message = extract_envelope_jws({"message": wire})
        assert envelope == "h.p.s"
        assert message["role"] == "ROLE_USER"

    def test_missing_message(self) -> None:
        with pytest.raises(ParseError, match="missing 'message'"):
            extract_envelope_jws({})

    def test_missing_extension_declaration(self) -> None:
        body = {
            "message": {
                "messageId": "m1",
                "role": "ROLE_USER",
                "extensions": [],
                "metadata": {ENVELOPE_EXTENSION_URI: "h.p.s"},
            }
        }
        with pytest.raises(ParseError, match="extensions"):
            extract_envelope_jws(body)

    def test_missing_metadata(self) -> None:
        body = {
            "message": {
                "messageId": "m1",
                "role": "ROLE_USER",
                "extensions": [ENVELOPE_EXTENSION_URI],
                "metadata": {},
            }
        }
        with pytest.raises(ParseError, match="metadata"):
            extract_envelope_jws(body)


class TestProblemResponse:
    def test_creates_well_formed_problem(self) -> None:
        status, body, headers = problem_response(CredsRejectedError("nope"))
        assert status == 403
        assert body["type"] == "urn:shadownet:error:creds_rejected"
        assert body["status"] == 403
        assert body["title"]
        assert headers["Content-Type"] == PROBLEM_JSON_MEDIA_TYPE
        assert headers["A2A-Extensions"] == ENVELOPE_EXTENSION_URI

    def test_detail_passthrough(self) -> None:
        _, body, _ = problem_response(PolicyError("rejected"), detail="explicit")
        assert body["detail"] == "explicit"

    def test_default_sanitizes_exception_message(self) -> None:
        """RFC 0001 §11: the default body MUST NOT leak the exception detail.

        Receivers commonly raise with messages that embed sender/messageId
        ("stranger {sender!r} rejected", "({sender!r}, {message_id!r}) replayed").
        Those signal stranger-vs-contact + replay-cache state, which §11
        forbids. The default response must drop the detail field entirely.
        """
        leaky = PolicyError("stranger 'alice@sh4dow.org' rejected by policy")
        _, body, _ = problem_response(leaky)
        assert "detail" not in body
        assert body["type"] == "urn:shadownet:error:policy"
        assert body["title"]
        assert body["status"] == 403

    def test_include_detail_opt_in_passes_through_str(self) -> None:
        leaky = PolicyError("stranger 'alice@sh4dow.org' rejected by policy")
        _, body, _ = problem_response(leaky, include_detail=True)
        assert "stranger" in body["detail"]


class TestWireErrorRoundtrip:
    @pytest.mark.parametrize(
        "cls",
        [
            ParseError,
            CredsRequiredError,
            CredsRejectedError,
            PolicyError,
            UnknownRecipientError,
        ],
    )
    def test_roundtrip(self, cls: type[ShadownetWireError]) -> None:
        original = cls("detail message")
        _, body, _ = problem_response(original)
        rebuilt = wire_error_from_problem(body)
        assert isinstance(rebuilt, cls)

    def test_unknown_code_falls_back_to_parse(self) -> None:
        rebuilt = wire_error_from_problem({"type": "urn:shadownet:error:made_up", "status": 400})
        assert isinstance(rebuilt, ParseError)

    def test_non_urn_type_falls_back_to_parse(self) -> None:
        rebuilt = wire_error_from_problem({"type": "about:blank"})
        assert isinstance(rebuilt, ParseError)

    def test_registry_covers_spec_codes(self) -> None:
        assert set(WIRE_ERROR_REGISTRY) == {
            "parse_error",
            "signature",
            "creds_required",
            "creds_rejected",
            "policy",
            "replay",
            "unknown_recipient",
            "rate_limited",
        }


class TestSendEnvelope:
    def _built(self) -> Any:
        sender_key = Ed25519KeyPair.generate()
        outbound = build_outbound_message(body_text="hi", context_id="ctx-1")
        return build_and_sign_message(outbound, _payload_template(), sender_key)

    @respx.mock
    def test_happy_path(self) -> None:
        built = self._built()
        respx.post("https://shadow.example.org/v1/a2a/bob/message:send").mock(
            return_value=httpx.Response(
                200,
                json={"message": build_acceptance_response(context_id="ctx-1")["message"]},
                headers=acceptance_headers(),
            )
        )
        result = send_envelope(built, "https://shadow.example.org/v1/a2a/bob")
        assert result.role == "ROLE_AGENT"
        assert result.context_id == "ctx-1"

    @respx.mock
    def test_problem_json_response_raises_typed_error(self) -> None:
        built = self._built()
        respx.post("https://shadow.example.org/v1/a2a/bob/message:send").mock(
            return_value=httpx.Response(
                403,
                json={
                    "type": "urn:shadownet:error:creds_rejected",
                    "title": "no",
                    "status": 403,
                },
                headers={"Content-Type": PROBLEM_JSON_MEDIA_TYPE},
            )
        )
        with pytest.raises(CredsRejectedError):
            send_envelope(built, "https://shadow.example.org/v1/a2a/bob")

    @respx.mock
    def test_non_problem_failure_is_parse_error(self) -> None:
        built = self._built()
        respx.post("https://shadow.example.org/v1/a2a/bob/message:send").mock(
            return_value=httpx.Response(500, text="oops")
        )
        with pytest.raises(ParseError):
            send_envelope(built, "https://shadow.example.org/v1/a2a/bob")

    @respx.mock
    def test_request_headers(self) -> None:
        built = self._built()
        route = respx.post("https://shadow.example.org/v1/a2a/bob/message:send").mock(
            return_value=httpx.Response(
                200,
                json={"message": build_acceptance_response(context_id="ctx-1")["message"]},
            )
        )
        send_envelope(built, "https://shadow.example.org/v1/a2a/bob")
        request = route.calls[0].request
        assert request.headers["a2a-version"] == A2A_VERSION
        assert request.headers["a2a-extensions"] == ENVELOPE_EXTENSION_URI


class TestAcceptanceResponse:
    def test_minimal_shape(self) -> None:
        body = build_acceptance_response(context_id="ctx-1")
        msg = body["message"]
        assert msg["role"] == "ROLE_AGENT"
        assert msg["parts"] == [{"text": "accepted"}]
        assert msg["contextId"] == "ctx-1"

    def test_custom_text(self) -> None:
        body = build_acceptance_response(context_id="ctx-1", text="thanks")
        assert body["message"]["parts"] == [{"text": "thanks"}]


class TestA2AMessageWire:
    def test_to_wire_drops_empty_extensions_but_keeps_parts(self) -> None:
        msg = A2AMessage(message_id="m", role="ROLE_USER", parts=[])
        wire = msg.to_wire()
        assert wire["parts"] == []
        assert "extensions" not in wire

    def test_wire_alias_round_trip(self) -> None:
        # RFC 0001 §2: wire keys are camelCase; Python stays snake_case.
        wire = {
            "messageId": "m1",
            "role": "ROLE_USER",
            "parts": [{"text": "hi"}],
            "contextId": "ctx-1",
        }
        parsed = A2AMessage.model_validate(wire)
        assert parsed.message_id == "m1"
        assert parsed.context_id == "ctx-1"
        assert parsed.to_wire()["contextId"] == "ctx-1"
        assert parsed.to_wire()["messageId"] == "m1"


class TestSendWithRetries:
    """RFC 0001 §8.10: retry-with-remint helper.

    Uses ``httpx.MockTransport`` so the side-effect sequence is controllable
    — respx wraps Exception side-effects which makes "fail twice then succeed"
    sequences awkward.
    """

    def test_succeeds_after_transient_transport_errors(self) -> None:
        alice_key = Ed25519KeyPair.generate()
        agent_url = "https://shadow.sh4dow.org/v1/a2a/bob"
        message_ids: set[str] = set()
        call_count = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise httpx.ConnectError("nope")
            return httpx.Response(
                200,
                json={
                    "message": {
                        "messageId": "ack",
                        "role": "ROLE_AGENT",
                        "parts": [{"text": "ok"}],
                    }
                },
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))

        def builder() -> BuiltMessage:
            built = build_and_sign_message(
                build_outbound_message(body_text="hi"),
                _payload_template(),
                alice_key,
            )
            message_ids.add(built.message["messageId"])
            return built

        sleeps: list[float] = []
        result = send_with_retries(
            builder,
            agent_url,
            client=client,
            initial_delay=0.0,
            max_delay=0.0,
            jitter=0.0,
            sleep=sleeps.append,
        )
        assert result.message_id == "ack"
        assert call_count == 3
        # §8.10: each retry MUST re-mint with a fresh messageId.
        assert len(message_ids) == 3
        assert len(sleeps) == 2  # two waits between three attempts

    def test_protocol_errors_are_not_retried(self) -> None:
        alice_key = Ed25519KeyPair.generate()
        call_count = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(
                403,
                json={
                    "type": "urn:shadownet:error:policy",
                    "title": "rejected",
                    "status": 403,
                },
                headers={"Content-Type": PROBLEM_JSON_MEDIA_TYPE},
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))

        def builder() -> BuiltMessage:
            return build_and_sign_message(
                build_outbound_message(body_text="hi"),
                _payload_template(),
                alice_key,
            )

        def fake_sleep(_: float) -> None:
            raise AssertionError("sleep called — retry path entered for a protocol error")

        with pytest.raises(PolicyError):
            send_with_retries(
                builder,
                "https://shadow.sh4dow.org/v1/a2a/bob",
                client=client,
                initial_delay=0.0,
                sleep=fake_sleep,
            )
        assert call_count == 1  # one attempt only

    def test_budget_exhaustion_raises(self) -> None:
        alice_key = Ed25519KeyPair.generate()

        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down")

        client = httpx.Client(transport=httpx.MockTransport(handler))

        def builder() -> BuiltMessage:
            return build_and_sign_message(
                build_outbound_message(body_text="hi"),
                _payload_template(),
                alice_key,
            )

        # Synthetic monotonic clock advances 100s per check; budget 150s.
        ticks = iter([0.0, 100.0, 200.0])

        with pytest.raises(TransportRetryExhausted, match="budget exhausted"):
            send_with_retries(
                builder,
                "https://shadow.sh4dow.org/v1/a2a/bob",
                client=client,
                initial_delay=0.0,
                total_budget=150.0,
                jitter=0.0,
                sleep=lambda _t: None,
                monotonic=lambda: next(ticks),
            )
