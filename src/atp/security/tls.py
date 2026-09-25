import ssl
import datetime

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa


class TLSConfig:
    @staticmethod
    def create_server_context(cert_path: str, key_path: str) -> ssl.SSLContext:
        """Create server-side TLS 1.3 context."""
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_3
        ctx.load_cert_chain(cert_path, key_path)
        # ATPServer is currently served by Uvicorn's HTTP/1.1 implementation.
        # Advertising h2 (or a custom atp/1 protocol) makes capable clients
        # negotiate a protocol the server cannot actually speak.  In
        # particular, curl then waits for an HTTP/2 SETTINGS frame and aborts
        # an otherwise valid TLS 1.3 connection.
        ctx.set_alpn_protocols(["http/1.1"])
        return ctx

    @staticmethod
    def create_client_context(verify: bool = True) -> ssl.SSLContext:
        """Create client-side TLS context."""
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_3
        if verify:
            ctx.load_default_certs()
        else:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        ctx.set_alpn_protocols(["http/1.1"])
        return ctx

    @staticmethod
    def generate_self_signed_cert(
        cert_path: str, key_path: str, domain: str = "localhost"
    ) -> None:
        """Generate a self-signed certificate for development.

        Uses RSA 2048 key and X509 cert with CN=domain, valid for 365 days.
        """
        # Generate RSA 2048 key
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

        # Build X509 cert
        subject = issuer = x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, domain)]
        )
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + datetime.timedelta(days=365))
            .add_extension(
                x509.SubjectAlternativeName([x509.DNSName(domain)]),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )

        # Save cert as PEM
        with open(cert_path, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))

        # Save key as PEM (no encryption)
        with open(key_path, "wb") as f:
            f.write(
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.TraditionalOpenSSL,
                    serialization.NoEncryption(),
                )
            )
