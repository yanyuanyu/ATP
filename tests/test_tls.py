import os
import ssl
import tempfile

import pytest

from atp.security.tls import TLSConfig


class TestTLSConfig:
    @staticmethod
    def _complete_memory_bio_handshake(server_context, client_context):
        """Complete a TLS handshake without opening a network socket."""
        server_in = ssl.MemoryBIO()
        server_out = ssl.MemoryBIO()
        client_in = ssl.MemoryBIO()
        client_out = ssl.MemoryBIO()
        server = server_context.wrap_bio(server_in, server_out, server_side=True)
        client = client_context.wrap_bio(
            client_in,
            client_out,
            server_side=False,
            server_hostname="localhost",
        )

        server_done = client_done = False
        while not (server_done and client_done):
            if not client_done:
                try:
                    client.do_handshake()
                    client_done = True
                except ssl.SSLWantReadError:
                    pass
            server_in.write(client_out.read())

            if not server_done:
                try:
                    server.do_handshake()
                    server_done = True
                except ssl.SSLWantReadError:
                    pass
            client_in.write(server_out.read())

        return server, client

    def test_generate_self_signed_cert_creates_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = os.path.join(tmpdir, "cert.pem")
            key_path = os.path.join(tmpdir, "key.pem")

            TLSConfig.generate_self_signed_cert(cert_path, key_path, domain="localhost")

            assert os.path.exists(cert_path)
            assert os.path.exists(key_path)
            assert os.path.getsize(cert_path) > 0
            assert os.path.getsize(key_path) > 0

    def test_create_server_context_with_generated_cert(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = os.path.join(tmpdir, "cert.pem")
            key_path = os.path.join(tmpdir, "key.pem")
            TLSConfig.generate_self_signed_cert(cert_path, key_path)

            ctx = TLSConfig.create_server_context(cert_path, key_path)
            assert isinstance(ctx, ssl.SSLContext)

    def test_create_client_context_verify_true(self):
        ctx = TLSConfig.create_client_context(verify=True)
        assert isinstance(ctx, ssl.SSLContext)

    def test_create_client_context_verify_false(self):
        ctx = TLSConfig.create_client_context(verify=False)
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.check_hostname is False
        assert ctx.verify_mode == ssl.CERT_NONE

    def test_minimum_tls_version_is_1_3(self):
        ctx = TLSConfig.create_client_context(verify=False)
        assert ctx.minimum_version == ssl.TLSVersion.TLSv1_3

    def test_server_context_minimum_tls_version(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = os.path.join(tmpdir, "cert.pem")
            key_path = os.path.join(tmpdir, "key.pem")
            TLSConfig.generate_self_signed_cert(cert_path, key_path)

            ctx = TLSConfig.create_server_context(cert_path, key_path)
            assert ctx.minimum_version == ssl.TLSVersion.TLSv1_3

    def test_contexts_negotiate_only_supported_http_protocol(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = os.path.join(tmpdir, "cert.pem")
            key_path = os.path.join(tmpdir, "key.pem")
            TLSConfig.generate_self_signed_cert(
                cert_path,
                key_path,
                domain="localhost",
            )

            server, client = self._complete_memory_bio_handshake(
                TLSConfig.create_server_context(cert_path, key_path),
                TLSConfig.create_client_context(verify=False),
            )

            assert server.version() == "TLSv1.3"
            assert client.version() == "TLSv1.3"
            assert server.selected_alpn_protocol() == "http/1.1"
            assert client.selected_alpn_protocol() == "http/1.1"
