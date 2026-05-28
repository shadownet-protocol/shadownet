from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from shadownet.crypto.ed25519 import Ed25519KeyPair

# RFC-0002 §Permitted DID document fields — id, verificationMethod,
# authentication, assertionMethod, plus the optional orgs-only
# shadownet:delegatedIssuers list.

__all__ = ["DIDDocument", "VerificationMethod"]


class VerificationMethod(BaseModel):
    """A single verification method entry. v0.1 mandates Ed25519 only."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str
    type: Literal["Ed25519VerificationKey2020", "JsonWebKey2020"]
    controller: str
    public_key_jwk: dict[str, str] | None = Field(default=None, alias="publicKeyJwk")
    public_key_multibase: str | None = Field(default=None, alias="publicKeyMultibase")

    @field_validator("public_key_jwk")
    @classmethod
    def _check_jwk_is_ed25519(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is None:
            return value
        if value.get("kty") != "OKP" or value.get("crv") != "Ed25519":
            raise ValueError("verification method JWK must be Ed25519 (OKP)")
        return value

    def to_keypair(self) -> Ed25519KeyPair:
        if self.public_key_jwk is not None:
            return Ed25519KeyPair.from_jwk(self.public_key_jwk)
        if self.public_key_multibase is not None:
            from shadownet.crypto.multibase import (
                ED25519_PUB_MULTICODEC,
                decode_multibase_z,
                strip_multicodec,
            )

            raw = strip_multicodec(
                ED25519_PUB_MULTICODEC, decode_multibase_z(self.public_key_multibase)
            )
            return Ed25519KeyPair.from_public_bytes(raw)
        raise ValueError("verification method has no key material")


VerificationReference = Annotated[str | VerificationMethod, Field(union_mode="left_to_right")]


class DIDDocument(BaseModel):
    """Minimal DID document permitted by RFC-0002 §Permitted DID document fields."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str
    verification_method: list[VerificationMethod] = Field(
        default_factory=list, alias="verificationMethod"
    )
    authentication: list[VerificationReference] = Field(default_factory=list)
    assertion_method: list[VerificationReference] = Field(
        default_factory=list, alias="assertionMethod"
    )
    delegated_issuers: list[str] = Field(default_factory=list, alias="shadownet:delegatedIssuers")

    @model_validator(mode="after")
    def _scrub_delegated_issuers_on_did_key(self) -> DIDDocument:
        # RFC-0002: did:key documents MUST NOT carry shadownet:delegatedIssuers.
        # Verifiers MUST ignore the field on individuals.
        if self.delegated_issuers and not self.id.startswith("did:web:"):
            self.delegated_issuers = []
        return self

    def is_delegated_issuer(self, issuer_did: str) -> bool:
        """Return True iff issuer_did appears in this document's delegated-issuer list.

        Used by AffiliationCredential verifiers to confirm that an issuer DID
        is authorized to sign on behalf of this organization DID. Always
        returns False for did:key documents.
        """
        if not self.id.startswith("did:web:"):
            return False
        return issuer_did in self.delegated_issuers

    def find_key(self, key_id: str | None = None) -> Ed25519KeyPair:
        """Return the keypair for ``key_id``, or the first verification method if not specified.

        ``key_id`` may be a full DID URL (``did:web:x#key-1``), a bare fragment
        (``key-1`` or ``#key-1``), or ``None``. A fragment-less DID is treated as
        "no specific key" and resolves to the document's first verification method.
        """
        if not self.verification_method:
            raise ValueError(f"DID document {self.id} has no verification methods")
        if key_id is None or "#" not in key_id:
            return self.verification_method[0].to_keypair()
        fragment = key_id.split("#", 1)[1]
        for vm in self.verification_method:
            if vm.id == key_id or vm.id.split("#", 1)[-1] == fragment:
                return vm.to_keypair()
        raise ValueError(f"verification method {key_id!r} not found in {self.id}")
