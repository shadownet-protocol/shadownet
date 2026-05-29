"""Provider DNS record discovery — RFC 0001 §4.2.

Resolves ``_shadownet.<domain>`` TXT to a typed ``ProviderRecord`` that the
AgentCard layer (§5) and the credential layer (§6.6 delegate path) consume.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

import dns.exception
import dns.rdtypes.ANY.TXT
import dns.resolver

from shadownet.errors import ShadownetError
from shadownet.identifiers import (
    InvalidIdentifierError,
    MultibasePublicKey,
    parse_public_key,
)

__all__ = [
    "ProviderRecord",
    "ProviderResolutionError",
    "lookup_provider_record",
    "parse_provider_txt",
]


SHADOWNET_TXT_PREFIX: Final = "_shadownet."
SUPPORTED_VERSION: Final = "0.2"


class ProviderResolutionError(ShadownetError):
    """Provider DNS record could not be resolved or parsed."""


@dataclass(frozen=True, slots=True)
class ProviderRecord:
    """Parsed provider DNS TXT record."""

    domain: str
    version: str
    endpoint: str
    provider_keys: tuple[MultibasePublicKey, ...]
    is_issuer: bool = False
    delegates: tuple[str, ...] = field(default_factory=tuple)


def lookup_provider_record(
    domain: str,
    *,
    resolver: dns.resolver.Resolver | None = None,
    lifetime: float = 5.0,
) -> ProviderRecord:
    name = SHADOWNET_TXT_PREFIX + domain.rstrip(".")
    r = resolver or dns.resolver.Resolver()
    try:
        answer = r.resolve(name, rdtype="TXT", lifetime=lifetime)
    except dns.resolver.NXDOMAIN as exc:
        raise ProviderResolutionError(f"no _shadownet TXT for {domain!r}") from exc
    except dns.resolver.NoAnswer as exc:
        raise ProviderResolutionError(f"no TXT answer for {name!r}") from exc
    except dns.exception.DNSException as exc:
        raise ProviderResolutionError(f"DNS error resolving {name!r}: {exc}") from exc

    records: list[ProviderRecord] = []
    for rdata in answer:
        # §3.3.14: TXT carries one-or-more <character-string>s. RFC 0001 §4.2
        # concatenates them in order to form the logical value.
        if not isinstance(rdata, dns.rdtypes.ANY.TXT.TXT):
            continue
        joined = b"".join(rdata.strings).decode("utf-8", errors="strict")
        try:
            records.append(parse_provider_txt(domain, joined))
        except ProviderResolutionError:
            continue

    if not records:
        raise ProviderResolutionError(f"no v={SUPPORTED_VERSION} TXT at {name!r}")
    if len(records) > 1:
        raise ProviderResolutionError(
            f"multiple v={SUPPORTED_VERSION} TXT records at {name!r}; expected one"
        )
    return records[0]


def parse_provider_txt(domain: str, value: str) -> ProviderRecord:
    fields = _parse_kv(value)

    version = _single(fields, "v")
    if version != SUPPORTED_VERSION:
        raise ProviderResolutionError(f"unsupported v={version!r}")

    endpoint = _single(fields, "ep")
    if not endpoint.startswith(
        ("https://", "http://localhost", "http://127.0.0.1", "http://[::1]")
    ):
        raise ProviderResolutionError(
            f"ep must be https:// (or loopback http://), got {endpoint!r}"
        )

    pk_values = tuple(fields.get("pk", ()))
    if not pk_values:
        raise ProviderResolutionError("missing required key 'pk'")
    try:
        for pk in pk_values:
            parse_public_key(pk)
    except InvalidIdentifierError as exc:
        raise ProviderResolutionError(f"invalid pk: {exc}") from exc

    is_issuer = _flag(fields, "iss")
    delegates = tuple(fields.get("delegate", ()))

    return ProviderRecord(
        domain=domain,
        version=version,
        endpoint=endpoint,
        provider_keys=pk_values,
        is_issuer=is_issuer,
        delegates=delegates,
    )


def _parse_kv(value: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for raw in value.split(";"):
        part = raw.strip()
        if not part:
            continue
        if "=" not in part:
            raise ProviderResolutionError(f"malformed key=value pair: {part!r}")
        key, _, val = part.partition("=")
        out.setdefault(key.strip().lower(), []).append(val.strip())
    return out


def _single(fields: dict[str, list[str]], key: str) -> str:
    values = fields.get(key)
    if not values:
        raise ProviderResolutionError(f"missing required key {key!r}")
    if len(values) > 1:
        raise ProviderResolutionError(f"key {key!r} appears more than once")
    return values[0]


def _flag(fields: dict[str, list[str]], key: str) -> bool:
    values = fields.get(key)
    if not values:
        return False
    return values[0].lower() == "true"
