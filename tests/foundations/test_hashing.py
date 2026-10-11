import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import PurePosixPath

import pytest
from pydantic import BaseModel

from nailong_agent_sdk.foundations.hashing import (
    canonical_hash,
    canonical_json,
    estimate_tokens,
    model_canonical_json,
    sha256_hex,
    strict_canonical_json,
)


class Sample(BaseModel):
    a: int
    b: str


VALUES = {
    "empty": {},
    "scalars": {"i": 10**20, "f": 0.1, "t": True, "n": None, "s": "plain", "neg": -1.5e-07},
    "unicode": {
        "name": "r\u00e9sum\u00e9 \u6587\u4ef6 \U0001f680",
        "sep": "\u2028",
        "quote": 'a"b\\c',
    },
    "ordering": {"b": 1, "a": {"z": [3, 2, 1], "y": {}}, "c": []},
    "datetime": {"when": datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)},
    "pure_path": {"p": PurePosixPath("a/b")},
    "decimal": {"d": Decimal("1.50")},
    "tuple": {"t": (1, "a", None)},
    "model": {"m": Sample(a=1, b="x")},
    "nested_list": [{"k": 1}, {"k": 2}],
}

JSON_NATIVE = ("empty", "scalars", "unicode", "ordering", "tuple", "nested_list")

SCALARS_JSON = '{"f":0.1,"i":100000000000000000000,"n":null,"neg":-1.5e-07,"s":"plain","t":true}'

# Encodings, digests and estimates as written before the helpers were consolidated.
FROZEN = {
    "datetime": {
        "canonical": '{"when":"2026-01-02 03:04:05+00:00"}',
        "model": '{"when":"2026-01-02 03:04:05+00:00"}',
        "model_tokens": 9,
        "sha256": "01939be5ee4bb45d1ab4fe1bc836363b6763e2e21b60f659c7051840b70ced50",
        "strict": None,
        "tokens": 9,
    },
    "decimal": {
        "canonical": '{"d":"1.50"}',
        "model": '{"d":"1.50"}',
        "model_tokens": 3,
        "sha256": "7867fd0d035cc1bad62b1f8a55ffc76b8094f3ff8e2cec84251d58ce60bc106d",
        "strict": None,
        "tokens": 3,
    },
    "empty": {
        "canonical": "{}",
        "model": "{}",
        "model_tokens": 1,
        "sha256": "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a",
        "strict": "{}",
        "tokens": 1,
    },
    "model": {"model": '{"m":{"a":1,"b":"x"}}', "model_tokens": 5, "strict": None},
    "nested_list": {
        "canonical": '[{"k":1},{"k":2}]',
        "model": '[{"k":1},{"k":2}]',
        "model_tokens": 4,
        "sha256": "98fcf287e1991c1602a189793606501715f8ae194db5dcaaf6515ed29937c20d",
        "strict": '[{"k":1},{"k":2}]',
        "tokens": 4,
    },
    "ordering": {
        "canonical": '{"a":{"y":{},"z":[3,2,1]},"b":1,"c":[]}',
        "model": '{"a":{"y":{},"z":[3,2,1]},"b":1,"c":[]}',
        "model_tokens": 9,
        "sha256": "245cf8f5f5dbb1d381a08dea712a59f47227a0a4c0e357494f8a2cb8b3846ced",
        "strict": '{"a":{"y":{},"z":[3,2,1]},"b":1,"c":[]}',
        "tokens": 9,
    },
    "pure_path": {
        "canonical": '{"p":"a/b"}',
        "model": '{"p":"a/b"}',
        "model_tokens": 2,
        "sha256": "709ed742fd2545f6248d5262fc9674d5b13fa4b8c5c2d8b8cb8ee1d7966c2bcc",
        "strict": None,
        "tokens": 2,
    },
    "scalars": {
        "canonical": SCALARS_JSON,
        "model": SCALARS_JSON,
        "model_tokens": 20,
        "sha256": "9d67cadffc71020226503b63f325a4c94b852cce092c7bc0483d9cc7757b7ecc",
        "strict": SCALARS_JSON,
        "tokens": 20,
    },
    "tuple": {
        "canonical": '{"t":[1,"a",null]}',
        "model": '{"t":[1,"a",null]}',
        "model_tokens": 4,
        "sha256": "99a95a03bd8ee080bda2b8ff7d4f063d3e85b45d51f36a18653fa4fea91592b7",
        "strict": '{"t":[1,"a",null]}',
        "tokens": 4,
    },
    "unicode": {
        "canonical": '{"name":"r\\u00e9sum\\u00e9 \\u6587\\u4ef6 '
        '\\ud83d\\ude80","quote":"a\\"b\\\\c","sep":"\\u2028"}',
        "model": '{"name":"r\\u00e9sum\\u00e9 \\u6587\\u4ef6 '
        '\\ud83d\\ude80","quote":"a\\"b\\\\c","sep":"\\u2028"}',
        "model_tokens": 21,
        "sha256": "4475c1f47ea94751ea8ad986ffce63eb0ee7bb2fd98dcc115b169340ac49abe9",
        "strict": '{"name":"r\\u00e9sum\\u00e9 \\u6587\\u4ef6 '
        '\\ud83d\\ude80","quote":"a\\"b\\\\c","sep":"\\u2028"}',
        "tokens": 21,
    },
}


@pytest.mark.parametrize("name", [name for name in FROZEN if name != "model"])
def test_canonical_json_and_its_digest_match_what_earlier_versions_wrote(name):
    expected = FROZEN[name]
    assert canonical_json(VALUES[name]) == expected["canonical"]
    assert canonical_hash(VALUES[name]) == expected["sha256"]
    assert sha256_hex(expected["canonical"]) == expected["sha256"]
    assert estimate_tokens(VALUES[name]) == expected["tokens"]


def test_the_default_encoding_writes_a_model_as_its_text_form():
    value = VALUES["model"]
    expected = json.dumps({"m": str(value["m"])}, sort_keys=True, separators=(",", ":"))
    assert canonical_json(value) == expected
    assert canonical_hash(value) == sha256_hex(expected)


@pytest.mark.parametrize("name", list(FROZEN))
def test_the_model_aware_encoding_matches_what_earlier_versions_wrote(name):
    expected = FROZEN[name]
    assert model_canonical_json(VALUES[name]) == expected["model"]
    assert estimate_tokens(VALUES[name], model_canonical_json) == expected["model_tokens"]


@pytest.mark.parametrize("name", list(FROZEN))
def test_the_strict_encoding_refuses_values_json_cannot_hold(name):
    expected = FROZEN[name]["strict"]
    if expected is None:
        with pytest.raises(TypeError):
            strict_canonical_json(VALUES[name])
    else:
        assert strict_canonical_json(VALUES[name]) == expected


@pytest.mark.parametrize("name", JSON_NATIVE)
def test_the_three_encodings_agree_on_values_that_are_already_json(name):
    value = VALUES[name]
    assert canonical_json(value) == strict_canonical_json(value) == model_canonical_json(value)


def test_a_model_is_encoded_differently_by_the_default_and_the_model_aware_encodings():
    value = VALUES["model"]
    assert model_canonical_json(value) == '{"m":{"a":1,"b":"x"}}'
    assert canonical_json(value) != model_canonical_json(value)


def test_sha256_hex_hashes_text_as_utf8_and_bytes_as_given():
    assert sha256_hex("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert sha256_hex("abc") == sha256_hex(b"abc")
    accented = chr(0xE9)
    assert sha256_hex(accented) == sha256_hex(accented.encode("utf-8"))
    assert sha256_hex(accented) != sha256_hex(accented.encode("latin-1"))


def test_the_token_estimate_is_a_quarter_of_the_encoded_length_and_never_zero():
    assert estimate_tokens({}) == 1
    assert estimate_tokens({"k": "x" * 400}) == len('{"k":"' + "x" * 400 + '"}') // 4
