from __future__ import annotations

from shadownet.did.document import DIDDocument


def test_did_document_parses_delegated_issuers_on_org() -> None:
    raw = {
        "id": "did:web:acme.example",
        "verificationMethod": [],
        "authentication": [],
        "assertionMethod": [],
        "shadownet:delegatedIssuers": [
            "did:web:sca.acme.example",
            "did:web:hr.acme.example",
        ],
    }
    doc = DIDDocument.model_validate(raw)
    assert doc.delegated_issuers == ["did:web:sca.acme.example", "did:web:hr.acme.example"]
    assert doc.is_delegated_issuer("did:web:sca.acme.example")
    assert not doc.is_delegated_issuer("did:web:other.example")


def test_did_document_drops_delegated_issuers_on_did_key() -> None:
    raw = {
        "id": "did:key:z6MkrJVnaZkeFzdQyMZu1cgjg7k1pZZ6pvBQ7XJPt4swbTQ2",
        "verificationMethod": [],
        "authentication": [],
        "assertionMethod": [],
        "shadownet:delegatedIssuers": ["did:web:should.not.appear"],
    }
    doc = DIDDocument.model_validate(raw)
    assert doc.delegated_issuers == []
    assert not doc.is_delegated_issuer("did:web:should.not.appear")


def test_did_document_round_trips_delegated_issuers_via_alias() -> None:
    doc = DIDDocument.model_validate(
        {
            "id": "did:web:acme.example",
            "verificationMethod": [],
            "authentication": [],
            "assertionMethod": [],
        }
    )
    assert doc.delegated_issuers == []
    dumped = doc.model_dump(by_alias=True, exclude_defaults=True)
    assert "shadownet:delegatedIssuers" not in dumped
