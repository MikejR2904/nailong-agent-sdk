# Copyright (c) 2026 David Michael Indraputra

"""RFC 8785 JSON Canonicalization for the bytes that evidence hashes and signatures cover."""

from __future__ import annotations

import json
import math
from decimal import Decimal
from typing import Any

from .hashing import canonical_hash, canonical_json, sha256_hex

CURRENT_SCHEMA_SUFFIX = "-v2"
_EXPONENT_FORM_ABOVE = 21
_EXPONENT_FORM_BELOW = -6


def jcs_json(value: Any) -> str:
    return "".join(_emit(value, "$"))


def jcs_bytes(value: Any) -> bytes:
    return jcs_json(value).encode("utf-8")


def jcs_hash(value: Any) -> str:
    return sha256_hex(jcs_bytes(value))


def digest_of_value(value: Any) -> str:
    try:
        return jcs_hash(value)
    except (TypeError, ValueError):
        return "legacy:" + canonical_hash(value)


def uses_jcs(schema_version: str) -> bool:
    return schema_version.endswith(CURRENT_SCHEMA_SUFFIX)


def schema_hash(schema_version: str, value: Any) -> str:
    return jcs_hash(value) if uses_jcs(schema_version) else canonical_hash(value)


def schema_message(schema_version: str, value: Any) -> bytes:
    text = jcs_json(value) if uses_jcs(schema_version) else canonical_json(value)
    return text.encode("utf-8")


def _emit(value: Any, path: str) -> Any:
    if value is None:
        yield "null"
    elif value is True:
        yield "true"
    elif value is False:
        yield "false"
    elif isinstance(value, str):
        yield _string(value, path)
    elif isinstance(value, int):
        yield str(int(value))
    elif isinstance(value, float):
        yield number_text(value, path)
    elif isinstance(value, list | tuple):
        yield "["
        for position, item in enumerate(value):
            if position:
                yield ","
            yield from _emit(item, f"{path}[{position}]")
        yield "]"
    elif isinstance(value, dict):
        yield "{"
        for position, key in enumerate(_sorted_keys(value, path)):
            if position:
                yield ","
            yield _string(key, f"{path}.{key}")
            yield ":"
            yield from _emit(value[key], f"{path}.{key}")
        yield "}"
    else:
        raise TypeError(
            f"{type(value).__name__} at {path} has no JSON form, so it cannot be canonicalized "
            "(RFC 8785 covers null, booleans, numbers, strings, arrays and objects)."
        )


def _sorted_keys(value: dict[Any, Any], path: str) -> list[str]:
    for key in value:
        if not isinstance(key, str):
            raise TypeError(
                f"The object at {path} has a {type(key).__name__} key {key!r}; JSON object keys "
                "are strings."
            )
    return sorted(value, key=lambda key: key.encode("utf-16-be", errors="surrogatepass"))


def _string(text: str, path: str) -> str:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError(
            f"The string at {path} holds a lone surrogate at index {error.start}, which has no "
            "UTF-8 form."
        ) from error
    return json.dumps(text, ensure_ascii=False)


def number_text(value: float, path: str = "$") -> str:
    if math.isnan(value) or math.isinf(value):
        raise ValueError(f"The number at {path} is {value!r}; JSON has no NaN or Infinity.")
    if value == 0:
        return "0"
    sign = "-" if value < 0 else ""
    parts = Decimal(repr(abs(value))).as_tuple()
    digits = "".join(str(item) for item in parts.digits).rstrip("0") or "0"
    exponent = int(parts.exponent) + len(parts.digits)
    point = exponent
    count = len(digits)
    if count <= point <= _EXPONENT_FORM_ABOVE:
        return sign + digits + "0" * (point - count)
    if 0 < point <= _EXPONENT_FORM_ABOVE:
        return sign + digits[:point] + "." + digits[point:]
    if _EXPONENT_FORM_BELOW < point <= 0:
        return sign + "0." + "0" * -point + digits
    power = point - 1
    mantissa = digits[0] + ("." + digits[1:] if count > 1 else "")
    return f"{sign}{mantissa}e{'+' if power >= 0 else '-'}{abs(power)}"
