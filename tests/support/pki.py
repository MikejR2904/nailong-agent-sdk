import ipaddress
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

NOW = datetime.now(UTC)


def _name(common):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common)])


def _write_key(path: Path, key) -> None:
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )


def _write_cert(path: Path, certificate) -> None:
    path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))


def _issue(subject, issuer, key, signing_key, *, ca, usage, alt_names=()):
    builder = (
        x509.CertificateBuilder()
        .subject_name(_name(subject))
        .issuer_name(_name(issuer))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW - timedelta(days=1))
        .not_valid_after(NOW + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
    )
    if usage is not None:
        builder = builder.add_extension(x509.ExtendedKeyUsage([usage]), critical=False)
    if alt_names:
        builder = builder.add_extension(
            x509.SubjectAlternativeName(list(alt_names)), critical=False
        )
    return builder.sign(signing_key, hashes.SHA256())


def make_pki(directory: Path, name: str = "pki") -> dict[str, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = _issue(f"{name} CA", f"{name} CA", ca_key, ca_key, ca=True, usage=None)
    server_key = ec.generate_private_key(ec.SECP256R1())
    server = _issue(
        "localhost",
        f"{name} CA",
        server_key,
        ca_key,
        ca=False,
        usage=ExtendedKeyUsageOID.SERVER_AUTH,
        alt_names=[x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))],
    )
    client_key = ec.generate_private_key(ec.SECP256R1())
    client = _issue(
        "writer", f"{name} CA", client_key, ca_key, ca=False, usage=ExtendedKeyUsageOID.CLIENT_AUTH
    )
    paths = {
        "ca": directory / "ca.pem",
        "server_cert": directory / "server.pem",
        "server_key": directory / "server.key",
        "client_cert": directory / "client.pem",
        "client_key": directory / "client.key",
    }
    _write_cert(paths["ca"], ca)
    _write_cert(paths["server_cert"], server)
    _write_key(paths["server_key"], server_key)
    _write_cert(paths["client_cert"], client)
    _write_key(paths["client_key"], client_key)
    return paths
