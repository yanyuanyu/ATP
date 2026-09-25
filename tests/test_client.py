"""Tests for the ATP client transport and client modules."""

import pytest

from atp.client.transport import HTTPTransport, TransportResult, parse_server_url


@pytest.mark.asyncio
async def test_transport_post_message():
    """Verify the class can be instantiated and method exists."""
    transport = HTTPTransport(no_verify=True)
    # Don't actually make HTTP calls in unit tests
    await transport.close()


def test_transport_result():
    r = TransportResult(success=True, status_code=202, body={"status": "accepted"})
    assert r.success
    assert r.status_code == 202


def test_transport_result_error():
    r = TransportResult(success=False, status_code=0, error="connection refused")
    assert not r.success
    assert r.status_code == 0
    assert r.error == "connection refused"


def test_transport_result_defaults():
    r = TransportResult(success=True, status_code=200)
    assert r.body == {}
    assert r.error is None


def test_http_transport_init():
    transport = HTTPTransport(no_verify=True, timeout=10.0)
    assert transport._verify is False
    assert transport._timeout == 10.0
    assert transport._client is None


def test_tlcp_transport_routes_via_local_gateway():
    transport = HTTPTransport(
        transport_mode="tlcp",
        tlcp_gateway_url="http://tlcp-family.family.test:9080/",
    )
    url, headers = transport._route_url(
        "https://server-hotel.hotel.test:7443",
        "/.well-known/atp/v1/message",
    )
    assert url == (
        "http://tlcp-family.family.test:9080/.well-known/atp/v1/message"
    )
    assert headers == {
        "X-ATP-TLCP-Target": "server-hotel.hotel.test:7443",
        "X-ATP-TLCP-Server-Name": "server-hotel.hotel.test",
    }


def test_tlcp_transport_requires_gateway():
    with pytest.raises(ValueError, match="tlcp_gateway_url"):
        HTTPTransport(transport_mode="tlcp")


def test_tlcp_transport_rejects_plaintext_remote_target():
    transport = HTTPTransport(
        transport_mode="tlcp",
        tlcp_gateway_url="http://tlcp-family.family.test:9080",
    )
    with pytest.raises(ValueError, match="HTTPS ATP server URLs"):
        transport._route_url("http://server-hotel:7443", "/message")


def test_http_transport_get_client():
    transport = HTTPTransport(no_verify=True)
    client = transport._get_client()
    assert client is not None
    # Calling again should return same client
    assert transport._get_client() is client


# ---------------------------------------------------------------------------
# parse_server_url tests
# ---------------------------------------------------------------------------

def test_parse_plain_domain():
    url, is_https = parse_server_url("example.com")
    assert url == "https://example.com:7443"
    assert is_https is True


def test_parse_domain_with_port():
    url, is_https = parse_server_url("example.com:8443")
    assert url == "https://example.com:8443"
    assert is_https is True


def test_parse_https_explicit():
    url, is_https = parse_server_url("https://example.com")
    assert url == "https://example.com:7443"
    assert is_https is True


def test_parse_https_with_port():
    url, is_https = parse_server_url("https://example.com:8443")
    assert url == "https://example.com:8443"
    assert is_https is True


def test_parse_http_explicit():
    url, is_https = parse_server_url("http://example.com")
    assert url == "http://example.com:7443"
    assert is_https is False


def test_parse_http_with_port():
    url, is_https = parse_server_url("http://example.com:8080")
    assert url == "http://example.com:8080"
    assert is_https is False


def test_parse_ip_address():
    url, is_https = parse_server_url("192.168.1.1")
    assert url == "https://192.168.1.1:7443"
    assert is_https is True


def test_parse_ip_with_port():
    url, is_https = parse_server_url("192.168.1.1:8443")
    assert url == "https://192.168.1.1:8443"
    assert is_https is True


def test_parse_localhost():
    url, is_https = parse_server_url("127.0.0.1")
    assert url == "https://127.0.0.1:7443"
    assert is_https is True


def test_parse_localhost_with_port():
    url, is_https = parse_server_url("127.0.0.1:7443")
    assert url == "https://127.0.0.1:7443"
    assert is_https is True
