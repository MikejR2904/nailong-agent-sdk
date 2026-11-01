import hashlib
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from nailong_agent_sdk.observability.timestamp import (
    OID_CONTENT_TYPE,
    OID_MESSAGE_DIGEST,
    OID_SHA256,
    OID_SIGNED_DATA,
    OID_TST_INFO,
    _integer,
    _oid,
    _tlv,
    read_all,
    read_one,
)

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def name(common: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common)])


def certificate(subject, issuer, key, signing_key, *, ca, eku, valid_from, valid_to):
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(valid_from)
        .not_valid_after(valid_to)
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
    )
    if eku:
        builder = builder.add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.TIME_STAMPING]), critical=True
        )
    algorithm = None if isinstance(signing_key, ed25519.Ed25519PrivateKey) else hashes.SHA256()
    return builder.sign(signing_key, algorithm)


class FakeTsa:
    def __init__(self, *, kind="rsa", clock=lambda: NOW, eku=True, lifetime_days=365):
        self.clock = clock
        self.root_key = ec.generate_private_key(ec.SECP256R1())
        self.key = (
            rsa.generate_private_key(65537, 2048)
            if kind == "rsa"
            else ec.generate_private_key(ec.SECP256R1())
        )
        self.kind = kind
        start = NOW - timedelta(days=1)
        end = NOW + timedelta(days=lifetime_days)
        self.root = certificate(
            name("Test Root"),
            name("Test Root"),
            self.root_key,
            self.root_key,
            ca=True,
            eku=False,
            valid_from=start,
            valid_to=end,
        )
        self.leaf = certificate(
            name("Test TSA"),
            name("Test Root"),
            self.key,
            self.root_key,
            ca=False,
            eku=eku,
            valid_from=start,
            valid_to=end,
        )
        self.name = "fake-tsa"
        self.serial = 0

    def stamp(self, digest: bytes) -> bytes:
        return self.reply(digest, None)

    def respond(self, request: bytes) -> bytes:
        fields = read_one(request)[0].children()
        imprint = fields[1].children()[1].content
        nonce = next((int.from_bytes(n.content, "big") for n in fields[2:] if n.tag == 0x02), None)
        return self.reply(imprint, nonce)

    def reply(self, digest: bytes, nonce, *, moment=None) -> bytes:
        self.serial += 1
        when = (moment or self.clock()).astimezone(UTC).strftime("%Y%m%d%H%M%SZ")
        info = _tlv(
            0x30,
            _integer(1)
            + _oid("1.2.3.4")
            + _tlv(0x30, _tlv(0x30, _oid(OID_SHA256) + _tlv(0x05, b"")) + _tlv(0x04, digest))
            + _integer(self.serial)
            + _tlv(0x18, when.encode("ascii"))
            + (_integer(nonce) if nonce is not None else b""),
        )
        attributes = _tlv(
            0x31,
            _attr(OID_CONTENT_TYPE, _oid(OID_TST_INFO))
            + _attr(OID_MESSAGE_DIGEST, _tlv(0x04, hashlib.sha256(info).digest())),
        )
        signed_bytes = attributes
        if self.kind == "rsa":
            signature = self.key.sign(signed_bytes, padding.PKCS1v15(), hashes.SHA256())
            algorithm = _tlv(0x30, _oid("1.2.840.113549.1.1.11") + _tlv(0x05, b""))
        else:
            signature = self.key.sign(signed_bytes, ec.ECDSA(hashes.SHA256()))
            algorithm = _tlv(0x30, _oid("1.2.840.10045.4.3.2"))
        issuer = self.leaf.issuer.public_bytes()
        signer = _tlv(
            0x30,
            _integer(1)
            + _tlv(0x30, issuer + _integer(self.leaf.serial_number))
            + _tlv(0x30, _oid(OID_SHA256) + _tlv(0x05, b""))
            + b"\xa0"
            + attributes[1:]
            + algorithm
            + _tlv(0x04, signature),
        )
        certificates = _tlv(
            0xA0,
            self.leaf.public_bytes(serialization.Encoding.DER)
            + self.root.public_bytes(serialization.Encoding.DER),
        )
        signed = _tlv(
            0x30,
            _integer(3)
            + _tlv(0x31, _tlv(0x30, _oid(OID_SHA256) + _tlv(0x05, b"")))
            + _tlv(0x30, _oid(OID_TST_INFO) + _tlv(0xA0, _tlv(0x04, info)))
            + certificates
            + _tlv(0x31, signer),
        )
        token = _tlv(0x30, _oid(OID_SIGNED_DATA) + _tlv(0xA0, signed))
        return _tlv(0x30, _tlv(0x30, _integer(0)) + token)

    def serve(self):
        authority = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                return

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                reply = authority.respond(body)
                self.send_response(200)
                self.send_header("Content-Type", "application/timestamp-reply")
                self.send_header("Content-Length", str(len(reply)))
                self.end_headers()
                self.wfile.write(reply)

        server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server


def _attr(oid: str, value: bytes) -> bytes:
    return _tlv(0x30, _oid(oid) + _tlv(0x31, value))


__all__ = ["FakeTsa", "NOW", "read_all"]
