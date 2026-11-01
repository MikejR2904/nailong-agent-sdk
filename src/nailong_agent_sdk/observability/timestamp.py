# Copyright (c) 2026 David Michael Indraputra

"""RFC 3161 trusted timestamps: ask an authority to attest when a digest existed, and check it."""

from __future__ import annotations

import base64
import functools
import hashlib
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from types import ModuleType
from typing import TYPE_CHECKING, Any, Protocol
from urllib import error as urlerror
from urllib import request as urlrequest

from pydantic import Field

from ..foundations.contracts import StrictModel
from ..foundations.errors import AgentSdkError

OID_SIGNED_DATA = "1.2.840.113549.1.7.2"
OID_TST_INFO = "1.2.840.113549.1.9.16.1.4"
OID_CONTENT_TYPE = "1.2.840.113549.1.9.3"
OID_MESSAGE_DIGEST = "1.2.840.113549.1.9.4"
OID_TIME_STAMPING = "1.3.6.1.5.5.7.3.8"
OID_SHA256 = "2.16.840.1.101.3.4.2.1"
HASH_OIDS = {
    "2.16.840.1.101.3.4.2.1": "sha256",
    "2.16.840.1.101.3.4.2.2": "sha384",
    "2.16.840.1.101.3.4.2.3": "sha512",
}
SIGNATURE_OIDS = {
    "1.2.840.113549.1.1.1": ("rsa", None),
    "1.2.840.113549.1.1.11": ("rsa", "sha256"),
    "1.2.840.113549.1.1.12": ("rsa", "sha384"),
    "1.2.840.113549.1.1.13": ("rsa", "sha512"),
    "1.2.840.10045.4.3.2": ("ecdsa", "sha256"),
    "1.2.840.10045.4.3.3": ("ecdsa", "sha384"),
    "1.2.840.10045.4.3.4": ("ecdsa", "sha512"),
}
GRANTED = (0, 1)
MAX_CHAIN_DEPTH = 6
MAX_RESPONSE_BYTES = 1_048_576
DEFAULT_MAX_SKEW_SECONDS = 600.0
TIMESTAMP_FIELD = "timestamp"


if TYPE_CHECKING:
    from cryptography.x509 import Certificate


class TimestampToken(StrictModel):
    authority: str = Field(min_length=1)
    digest_sha256: str = Field(min_length=64, max_length=64)
    token: str = Field(min_length=1)
    gen_time_utc: str


class TimestampAuthority(Protocol):
    @property
    def name(self) -> str: ...

    def stamp(self, digest: bytes) -> bytes: ...


@dataclass(frozen=True)
class VerifiedTimestamp:
    gen_time: datetime
    serial_number: int
    signer_subject: str


def digest_of(
    content: dict[str, Any], schema_message: Callable[[str, Any], bytes], schema: str
) -> bytes:
    stripped = {
        key: value for key, value in content.items() if key not in {"signature", TIMESTAMP_FIELD}
    }
    return hashlib.sha256(schema_message(schema, stripped)).digest()


def stamp_content(
    content: dict[str, Any],
    schema: str,
    authority: TimestampAuthority,
    schema_message: Callable[[str, Any], bytes],
    trusted_roots: Sequence[Certificate] | None = None,
) -> TimestampToken:
    digest = digest_of(content, schema_message, schema)
    der = authority.stamp(digest)
    try:
        verified = parse_token(der, digest, trusted_roots)
    except AgentSdkError as error:
        raise AgentSdkError(
            "TIMESTAMP_RESPONSE_INVALID",
            f'The timestamp authority "{authority.name}" answered with a token that does not '
            f"verify as a stamp of the digest it was asked about: {error.message}",
            {"authority": authority.name},
        ) from error
    return TimestampToken(
        authority=authority.name,
        digest_sha256=digest.hex(),
        token=base64.b64encode(der).decode("ascii"),
        gen_time_utc=verified.gen_time.isoformat(),
    )


def timestamp_problem(
    token: TimestampToken,
    content: dict[str, Any],
    schema: str,
    schema_message: Callable[[str, Any], bytes],
    trusted_roots: Sequence[Certificate] | None,
    claimed_utc: str,
    max_skew_seconds: float = DEFAULT_MAX_SKEW_SECONDS,
) -> tuple[datetime | None, str | None]:
    digest = digest_of(content, schema_message, schema)
    if token.digest_sha256 != digest.hex():
        return None, (
            f"the timestamp stamps digest {token.digest_sha256[:12]} but the record's content "
            f"hashes to {digest.hex()[:12]}: the record changed after it was stamped"
        )
    try:
        der = base64.b64decode(token.token, validate=True)
        verified = parse_token(der, digest, trusted_roots)
    except (ValueError, AgentSdkError) as error:
        message = error.message if isinstance(error, AgentSdkError) else str(error)
        return None, f"the timestamp from {token.authority} is invalid: {message}"
    if trusted_roots is None:
        return None, None
    try:
        claimed = datetime.fromisoformat(claimed_utc)
    except ValueError:
        return None, f'the record claims the time "{claimed_utc}", which does not parse'
    if claimed.tzinfo is None:
        claimed = claimed.replace(tzinfo=UTC)
    skew = abs((verified.gen_time - claimed).total_seconds())
    if skew > max_skew_seconds:
        return None, (
            f"the record claims {claimed_utc} but the authority stamped "
            f"{verified.gen_time.isoformat()}, {skew:.0f}s apart (the limit is "
            f"{max_skew_seconds:g}s)"
        )
    return verified.gen_time, None


class Rfc3161Authority:
    """A time-stamping authority reached over HTTP with a DER TimeStampReq."""

    def __init__(self, url: str, *, timeout_seconds: float = 10.0, name: str | None = None) -> None:
        if not url.startswith(("http://", "https://")):
            raise ValueError(f'A timestamp authority URL must be http(s), got "{url}".')
        if timeout_seconds <= 0:
            raise ValueError(f"timeout_seconds must be positive, got {timeout_seconds}.")
        self._url = url
        self._timeout = timeout_seconds
        self._name = name or url

    @property
    def name(self) -> str:
        return self._name

    def stamp(self, digest: bytes) -> bytes:
        nonce = secrets.randbits(63) + 1
        body = build_request(digest, nonce)
        call = urlrequest.Request(
            self._url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/timestamp-query"},
        )
        try:
            with _OPENER.open(call, timeout=self._timeout) as response:
                reply = response.read(MAX_RESPONSE_BYTES + 1)
                if len(reply) > MAX_RESPONSE_BYTES:
                    raise AgentSdkError(
                        "TIMESTAMP_RESPONSE_TOO_LARGE",
                        f"The timestamp authority at {self._url} sent more than "
                        f"{MAX_RESPONSE_BYTES} bytes, which no token needs.",
                        {"url": self._url},
                    )
        except urlerror.HTTPError as failure:
            raise AgentSdkError(
                "TIMESTAMP_REQUEST_FAILED",
                f"The timestamp authority at {self._url} answered HTTP {failure.code}.",
                {"url": self._url, "status": failure.code},
            ) from failure
        except (urlerror.URLError, OSError) as failure:
            raise AgentSdkError(
                "TIMESTAMP_UNREACHABLE",
                f"The timestamp authority at {self._url} cannot be reached: "
                f"{type(failure).__name__}: {failure}",
                {"url": self._url},
            ) from failure
        _expect_nonce(reply, nonce, self._url)
        return reply


class _NoRedirect(urlrequest.HTTPRedirectHandler):
    def redirect_request(self, *arguments: Any, **keywords: Any) -> None:
        return None


_OPENER = urlrequest.build_opener(_NoRedirect)


def _expect_nonce(reply: bytes, nonce: int, url: str) -> None:
    try:
        info = _tst_info(reply)
    except AgentSdkError:
        return
    if info.nonce is not None and info.nonce != nonce:
        raise AgentSdkError(
            "TIMESTAMP_NONCE_MISMATCH",
            f"The timestamp authority at {url} answered with nonce {info.nonce} but the request "
            f"carried {nonce}: the reply belongs to another request.",
            {"url": url},
        )
    if info.nonce is None:
        raise AgentSdkError(
            "TIMESTAMP_NONCE_MISSING",
            f"The timestamp authority at {url} omitted the nonce the request carried, so the "
            "reply cannot be tied to this request.",
            {"url": url},
        )


def _tlv(tag: int, content: bytes) -> bytes:
    size = len(content)
    if size < 0x80:
        header = bytes([tag, size])
    else:
        length = size.to_bytes((size.bit_length() + 7) // 8, "big")
        header = bytes([tag, 0x80 | len(length)]) + length
    return header + content


def _integer(value: int) -> bytes:
    raw = value.to_bytes((value.bit_length() + 8) // 8, "big")
    return _tlv(0x02, raw)


def _oid(dotted: str) -> bytes:
    parts = [int(item) for item in dotted.split(".")]
    body = bytearray([parts[0] * 40 + parts[1]])
    for number in parts[2:]:
        chunk = [number & 0x7F]
        number >>= 7
        while number:
            chunk.append(0x80 | (number & 0x7F))
            number >>= 7
        body.extend(reversed(chunk))
    return _tlv(0x06, bytes(body))


def build_request(digest: bytes, nonce: int) -> bytes:
    imprint = _tlv(0x30, _tlv(0x30, _oid(OID_SHA256) + _tlv(0x05, b"")) + _tlv(0x04, digest))
    return _tlv(0x30, _integer(1) + imprint + _integer(nonce) + _tlv(0x01, b"\xff"))


@dataclass(frozen=True)
class _Node:
    tag: int
    content: bytes
    raw: bytes

    def children(self) -> list[_Node]:
        return read_all(self.content)


def read_one(data: bytes, offset: int = 0) -> tuple[_Node, int]:
    if offset + 2 > len(data):
        raise AgentSdkError(
            "TIMESTAMP_DER_INVALID", f"DER ends at byte {offset} where a tag and length belong."
        )
    tag = data[offset]
    first = data[offset + 1]
    position = offset + 2
    if first < 0x80:
        size = first
    else:
        count = first & 0x7F
        if count == 0 or count > 4 or position + count > len(data):
            raise AgentSdkError(
                "TIMESTAMP_DER_INVALID", f"DER has an unusable length at byte {offset + 1}."
            )
        size = int.from_bytes(data[position : position + count], "big")
        position += count
    end = position + size
    if end > len(data):
        raise AgentSdkError(
            "TIMESTAMP_DER_INVALID",
            f"DER element at byte {offset} claims {size} bytes but only "
            f"{len(data) - position} remain.",
        )
    return _Node(tag, data[position:end], data[offset:end]), end


def read_all(data: bytes) -> list[_Node]:
    nodes: list[_Node] = []
    offset = 0
    while offset < len(data):
        node, offset = read_one(data, offset)
        nodes.append(node)
    return nodes


def _decode_oid(node: _Node) -> str:
    if node.tag != 0x06 or not node.content:
        raise AgentSdkError("TIMESTAMP_DER_INVALID", "An object identifier was expected.")
    first = node.content[0]
    numbers = [first // 40, first % 40]
    value = 0
    for byte in node.content[1:]:
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            numbers.append(value)
            value = 0
    return ".".join(str(item) for item in numbers)


def _decode_int(node: _Node) -> int:
    if node.tag != 0x02:
        raise AgentSdkError("TIMESTAMP_DER_INVALID", "An INTEGER was expected.")
    return int.from_bytes(node.content, "big", signed=True)


@dataclass(frozen=True)
class _TstInfo:
    imprint_hash: str
    imprint: bytes
    serial: int
    gen_time: datetime
    nonce: int | None


def _tst_info(response: bytes) -> _TstInfo:
    return _signed_data(response)[1]


@functools.cache
def _x509() -> ModuleType:
    from cryptography import x509

    return x509


def _signed_data(response: bytes) -> tuple[list[_Node], _TstInfo, bytes]:
    top, _ = read_one(response)
    parts = top.children()
    if not parts or parts[0].tag != 0x30:
        raise AgentSdkError("TIMESTAMP_DER_INVALID", "A TimeStampResp starts with a status.")
    status = _decode_int(parts[0].children()[0])
    if status not in GRANTED:
        raise AgentSdkError(
            "TIMESTAMP_REFUSED",
            f"The authority refused the request with PKIStatus {status} (0 and 1 mean granted).",
            {"status": status},
        )
    if len(parts) < 2:
        raise AgentSdkError("TIMESTAMP_DER_INVALID", "A granted reply carries no token.")
    content_info = parts[1].children()
    if _decode_oid(content_info[0]) != OID_SIGNED_DATA:
        raise AgentSdkError("TIMESTAMP_DER_INVALID", "The token is not CMS SignedData.")
    signed = read_one(content_info[1].content)[0].children()
    encap = signed[2].children()
    if _decode_oid(encap[0]) != OID_TST_INFO:
        raise AgentSdkError("TIMESTAMP_DER_INVALID", "The token does not wrap a TSTInfo.")
    econtent = read_one(encap[1].content)[0].content
    tst = read_one(econtent)[0].children()
    algorithm = tst[2].children()[0].children()
    imprint_hash = HASH_OIDS.get(_decode_oid(algorithm[0]))
    if imprint_hash is None:
        raise AgentSdkError(
            "TIMESTAMP_DER_INVALID",
            f"The message imprint uses hash {_decode_oid(algorithm[0])}, which is not SHA-2.",
        )
    imprint = tst[2].children()[1].content
    serial = _decode_int(tst[3])
    stamped = tst[4].content.decode("ascii")
    gen_time = datetime.strptime(stamped.split(".")[0].rstrip("Z"), "%Y%m%d%H%M%S").replace(
        tzinfo=UTC
    )
    if "." in stamped:
        fraction = stamped.split(".")[1].rstrip("Z")
        gen_time = gen_time.replace(microsecond=int((fraction + "000000")[:6]))
    nonce = next((_decode_int(node) for node in tst[5:] if node.tag == 0x02), None)
    return signed, _TstInfo(imprint_hash, imprint, serial, gen_time, nonce), econtent


def parse_token(
    response: bytes, digest: bytes, trusted_roots: Sequence[Certificate] | None = None
) -> VerifiedTimestamp:
    signed, info, econtent = _signed_data(response)
    if info.imprint != digest:
        raise AgentSdkError(
            "TIMESTAMP_IMPRINT_MISMATCH",
            f"The token stamps {info.imprint.hex()[:12]} but the digest is {digest.hex()[:12]}.",
        )
    subject = ""
    if trusted_roots is not None:
        subject = _check_signature(signed, econtent, info, trusted_roots)
    return VerifiedTimestamp(info.gen_time, info.serial, subject)


def _check_signature(
    signed: list[_Node], econtent: bytes, info: _TstInfo, roots: Sequence[Certificate]
) -> str:
    x509 = _x509()
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

    certificates = [
        x509.load_der_x509_certificate(node.raw)
        for block in signed
        if block.tag == 0xA0 and block is not signed[2]
        for node in block.children()
    ]
    signer_infos = signed[-1].children()
    if len(signer_infos) != 1:
        raise AgentSdkError(
            "TIMESTAMP_SIGNATURE_INVALID",
            f"The token carries {len(signer_infos)} signers; RFC 3161 allows exactly one.",
        )
    fields = signer_infos[0].children()
    signer_id, digest_algorithm = fields[1], fields[2]
    position = 3
    attributes = None
    if fields[position].tag == 0xA0:
        attributes = fields[position]
        position += 1
    signature_algorithm = _decode_oid(fields[position].children()[0])
    signature = fields[position + 1].content
    certificate = _match_certificate(certificates, signer_id)
    if certificate is None:
        raise AgentSdkError(
            "TIMESTAMP_SIGNATURE_INVALID",
            "The signer's certificate is not in the token, so the signature cannot be checked.",
        )
    digest_name = HASH_OIDS.get(_decode_oid(digest_algorithm.children()[0]))
    if digest_name is None or attributes is None:
        raise AgentSdkError(
            "TIMESTAMP_SIGNATURE_INVALID",
            "The signer uses no signed attributes or an unsupported digest, so the content "
            "cannot be tied to the signature.",
        )
    seen = {_decode_oid(a.children()[0]): a.children()[1] for a in attributes.children()}
    wanted = hashlib.new(digest_name, econtent).digest()
    claimed = (
        read_one(seen[OID_MESSAGE_DIGEST].content)[0].content if OID_MESSAGE_DIGEST in seen else b""
    )
    if claimed != wanted:
        raise AgentSdkError(
            "TIMESTAMP_SIGNATURE_INVALID",
            "The messageDigest attribute is not the hash of the TSTInfo, so the token was altered.",
        )
    signed_bytes = b"\x31" + attributes.raw[1:]
    kind, named = SIGNATURE_OIDS.get(signature_algorithm, (None, None))
    algorithm = getattr(hashes, (named or digest_name).upper())()
    key = certificate.public_key()
    try:
        if kind == "rsa" and isinstance(key, rsa.RSAPublicKey):
            key.verify(signature, signed_bytes, padding.PKCS1v15(), algorithm)
        elif kind == "ecdsa" and isinstance(key, ec.EllipticCurvePublicKey):
            key.verify(signature, signed_bytes, ec.ECDSA(algorithm))
        else:
            raise AgentSdkError(
                "TIMESTAMP_SIGNATURE_INVALID",
                f"Signature algorithm {signature_algorithm} with a {type(key).__name__} key is "
                "not supported (RSA PKCS#1 v1.5 and ECDSA are).",
            )
    except InvalidSignature as error:
        raise AgentSdkError(
            "TIMESTAMP_SIGNATURE_INVALID", "The token's signature does not verify."
        ) from error
    _check_certificate(certificate, certificates, roots, info.gen_time)
    return certificate.subject.rfc4514_string()


def _match_certificate(certificates: list[Certificate], signer_id: _Node) -> Certificate | None:
    x509 = _x509()
    if signer_id.tag == 0x30:
        issuer, serial = signer_id.children()
        number = _decode_int(serial)
        for certificate in certificates:
            if (
                certificate.serial_number == number
                and certificate.issuer.public_bytes() == issuer.raw
            ):
                return certificate
        return None
    wanted = signer_id.content
    for certificate in certificates:
        try:
            extension = certificate.extensions.get_extension_for_class(x509.SubjectKeyIdentifier)
        except x509.ExtensionNotFound:
            continue
        if extension.value.digest == wanted:
            return certificate
    return None


def _check_certificate(
    certificate: Certificate,
    embedded: list[Certificate],
    roots: Sequence[Certificate],
    moment: datetime,
) -> None:
    try:
        _walk_chain(certificate, embedded, roots, moment)
    except (TypeError, ValueError) as error:
        raise AgentSdkError(
            "TIMESTAMP_CERTIFICATE_INVALID",
            f"The certificate chain could not be evaluated: {type(error).__name__}: {error}",
        ) from error


def _der(certificate: Certificate) -> bytes:
    from cryptography.hazmat.primitives import serialization

    return bytes(certificate.public_bytes(serialization.Encoding.DER))


def _is_root(certificate: Certificate, roots: Sequence[Certificate]) -> bool:
    wanted = _der(certificate)
    return any(_der(root) == wanted for root in roots)


def _weak_signature(certificate: Certificate) -> str | None:
    from cryptography.hazmat.primitives import hashes

    algorithm = certificate.signature_hash_algorithm
    if isinstance(algorithm, hashes.MD5 | hashes.SHA1):
        return algorithm.name
    return None


def _issuer_problem(issuer: Certificate, intermediates_below: int) -> str | None:
    x509 = _x509()
    try:
        constraints = issuer.extensions.get_extension_for_class(x509.BasicConstraints).value
    except x509.ExtensionNotFound:
        return "it carries no basicConstraints, so it is not a certificate authority"
    if not constraints.ca:
        return "its basicConstraints say it is not a certificate authority"
    if constraints.path_length is not None and intermediates_below > constraints.path_length:
        return (
            f"its path length limit is {constraints.path_length} but {intermediates_below} "
            "intermediate certificate(s) lie below it"
        )
    try:
        usage = issuer.extensions.get_extension_for_class(x509.KeyUsage).value
    except x509.ExtensionNotFound:
        return None
    return None if usage.key_cert_sign else "its key usage does not allow signing certificates"


def _walk_chain(
    certificate: Certificate,
    embedded: list[Certificate],
    roots: Sequence[Certificate],
    moment: datetime,
) -> None:
    x509 = _x509()
    try:
        usage = certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    except x509.ExtensionNotFound:
        usage = []
    if x509.ObjectIdentifier(OID_TIME_STAMPING) not in usage:
        raise AgentSdkError(
            "TIMESTAMP_CERTIFICATE_INVALID",
            "The signer's certificate does not carry the time-stamping extended key usage.",
        )
    current, intermediates = certificate, 0
    pool = [*embedded, *roots]
    for _ in range(MAX_CHAIN_DEPTH + 1):
        label = current.subject.rfc4514_string()
        if not current.not_valid_before_utc <= moment <= current.not_valid_after_utc:
            raise AgentSdkError(
                "TIMESTAMP_CERTIFICATE_INVALID",
                f"Certificate {label} was not valid at {moment.isoformat()} (valid "
                f"{current.not_valid_before_utc.isoformat()} to "
                f"{current.not_valid_after_utc.isoformat()}).",
            )
        if _is_root(current, roots):
            return
        weak = _weak_signature(current)
        if weak is not None:
            raise AgentSdkError(
                "TIMESTAMP_CERTIFICATE_INVALID",
                f"Certificate {label} is signed with {weak}, which is not accepted.",
            )
        issuer = _find_issuer(current, pool)
        if issuer is None:
            raise AgentSdkError(
                "TIMESTAMP_CERTIFICATE_INVALID",
                f"No trusted root issues certificate {label}; {len(roots)} root(s) were supplied.",
            )
        trusted = _is_root(issuer, roots)
        if not trusted:
            problem = _issuer_problem(issuer, intermediates)
            if problem is not None:
                raise AgentSdkError(
                    "TIMESTAMP_CERTIFICATE_INVALID",
                    f"Certificate {label} is issued by {issuer.subject.rfc4514_string()}, but "
                    f"{problem}.",
                )
            intermediates += 1
        current = issuer
    raise AgentSdkError(
        "TIMESTAMP_CERTIFICATE_INVALID",
        f"The certificate chain is deeper than {MAX_CHAIN_DEPTH} links without reaching a root.",
    )


def _find_issuer(certificate: Certificate, pool: Sequence[Certificate]) -> Certificate | None:
    for candidate in pool:
        if candidate is certificate:
            continue
        try:
            certificate.verify_directly_issued_by(candidate)
        except Exception:
            continue
        return candidate
    return None
