from shadownet.did.document import DIDDocument, VerificationMethod
from shadownet.did.errors import (
    DIDDocumentTooLarge,
    DIDError,
    DIDMethodUnsupported,
    DIDNotResolvable,
    DIDSyntaxError,
)
from shadownet.did.key import (
    derive_did_key,
    did_key_document,
    parse_did_key,
)
from shadownet.did.resolver import Resolver
from shadownet.did.web import WebDIDResolver, parse_did_web

__all__ = [
    "DIDDocument",
    "DIDDocumentTooLarge",
    "DIDError",
    "DIDMethodUnsupported",
    "DIDNotResolvable",
    "DIDSyntaxError",
    "Resolver",
    "VerificationMethod",
    "WebDIDResolver",
    "derive_did_key",
    "did_key_document",
    "parse_did_key",
    "parse_did_web",
]
