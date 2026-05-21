from __future__ import annotations

from shadownet.sns.record import PublicKeyJWK, SNSRecord
from shadownet.sns.renewal import due_at, is_due, renew_due


def _record(issued_at: int, ttl: int) -> SNSRecord:
    return SNSRecord(
        shadowname="alice@x.example",
        did="did:key:z6MkAlice",
        endpoint="https://shadow.example/u/alice/a2a",
        publicKey=PublicKeyJWK(kty="OKP", crv="Ed25519", x="aaaa"),
        subjectType="person",
        ttl=ttl,
        issuedAt=issued_at,
        **{"shadownet:v": "0.1"},
    )


def test_due_at_uses_min_grace_floor_for_short_ttl() -> None:
    # ttl=300, ttl//10=30, grace floor of 60s wins.
    record = _record(issued_at=1_000_000, ttl=300)
    assert due_at(record) == 1_000_000 + 300 - 60


def test_due_at_uses_ttl_tenth_for_long_ttl() -> None:
    # ttl=3600, ttl//10=360, beats the 60s floor.
    record = _record(issued_at=1_000_000, ttl=3600)
    assert due_at(record) == 1_000_000 + 3600 - 360


def test_is_due_before_threshold_returns_false() -> None:
    record = _record(issued_at=1_000_000, ttl=3600)
    assert is_due(record, now=1_000_000 + 3000) is False


def test_is_due_at_threshold_returns_true() -> None:
    record = _record(issued_at=1_000_000, ttl=3600)
    threshold = 1_000_000 + 3600 - 360
    assert is_due(record, now=threshold) is True


async def test_renew_due_only_calls_register_for_due_records() -> None:
    fresh = _record(issued_at=1_000_000, ttl=3600)  # due at 1_000_000 + 3240
    stale = _record(issued_at=1_000_000, ttl=300)  # due at 1_000_000 + 240

    calls: list[SNSRecord] = []

    async def register(record: SNSRecord) -> None:
        calls.append(record)

    renewed = await renew_due([fresh, stale], register=register, now=1_000_000 + 250)
    assert renewed == [stale]
    assert calls == [stale]


async def test_renew_due_empty_when_none_due() -> None:
    fresh = _record(issued_at=1_000_000, ttl=3600)
    calls: list[SNSRecord] = []

    async def register(record: SNSRecord) -> None:
        calls.append(record)

    renewed = await renew_due([fresh], register=register, now=1_000_000 + 60)
    assert renewed == []
    assert calls == []
