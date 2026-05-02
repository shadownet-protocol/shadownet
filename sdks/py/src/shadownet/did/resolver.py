from __future__ import annotations

from typing import TYPE_CHECKING

from shadownet.did.errors import DIDMethodUnsupported, DIDSyntaxError
from shadownet.did.key import did_key_document

if TYPE_CHECKING:
    from shadownet.did.document import DIDDocument
    from shadownet.did.web import WebDIDResolver

__all__ = ["Resolver"]


class Resolver:
    """Dispatches DID resolution to the right method handler.

    ``did:key`` is resolved locally. ``did:web`` is delegated to the supplied
    :class:`WebDIDResolver` (which owns its own ``httpx.AsyncClient`` and cache).
    """

    def __init__(self, web: WebDIDResolver | None = None) -> None:
        self._web = web

    async def resolve(self, did: str) -> DIDDocument:
        if not did.startswith("did:"):
            raise DIDSyntaxError(f"not a DID: {did!r}")
        method = did.split(":", 2)[1] if did.count(":") >= 2 else ""
        if method == "key":
            return did_key_document(did)
        if method == "web":
            if self._web is None:
                raise DIDMethodUnsupported("did:web resolution requires a WebDIDResolver")
            return await self._web.resolve(did)
        raise DIDMethodUnsupported(f"unsupported DID method: {method!r}")
