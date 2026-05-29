"""JSON Canonicalization Scheme — RFC 8785.

Implementation follows <https://www.rfc-editor.org/rfc/rfc8785>; section
references in the code point at the normative paragraph.

Float canonicalization (§3.2.2.3 / ECMA-262 ToString) is not supported:
Shadownet wire artifacts only canonicalize integers (Unix-second timestamps)
and strings, and a partial float implementation would be a footgun for any
caller who later wanders into IEEE-754 territory expecting bit-exact
interop with cyberphone/json-canonicalization.
"""

from __future__ import annotations

from typing import Any

__all__ = ["JcsError", "canonicalize"]


class JcsError(ValueError):
    """A value could not be canonicalized per RFC 8785."""


def canonicalize(value: Any) -> bytes:
    return _encode(value).encode("utf-8")


def _encode(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return _encode_string(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        raise JcsError("float canonicalization is intentionally unsupported")
    if isinstance(value, dict):
        return _encode_object(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_encode(item) for item in value) + "]"
    raise JcsError(f"cannot canonicalize value of type {type(value).__name__}")


def _encode_object(obj: dict[Any, Any]) -> str:
    for key in obj:
        if not isinstance(key, str):
            raise JcsError(f"object keys must be strings, got {type(key).__name__}")
    # §3.2.3: sort by raw (unescaped) UTF-16 code-unit sequence, unsigned.
    parts = [_encode_string(k) + ":" + _encode(obj[k]) for k in sorted(obj, key=_utf16_codeunits)]
    return "{" + ",".join(parts) + "}"


def _utf16_codeunits(s: str) -> tuple[int, ...]:
    return tuple(s.encode("utf-16-be"))


# §3.2.2.2.
_SHORT_ESCAPES = {
    0x08: "\\b",
    0x09: "\\t",
    0x0A: "\\n",
    0x0C: "\\f",
    0x0D: "\\r",
}


def _encode_string(s: str) -> str:
    out = ['"']
    for ch in s:
        cp = ord(ch)
        if 0xD800 <= cp <= 0xDFFF:
            # §3.2.2.2: lone surrogates MUST cause termination.
            raise JcsError(f"lone surrogate U+{cp:04X} in input string")
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif cp in _SHORT_ESCAPES:
            out.append(_SHORT_ESCAPES[cp])
        elif cp < 0x20:
            out.append(f"\\u{cp:04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)
