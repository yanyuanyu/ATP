"""Tests for atp.discovery (LocalResolver, CompositeResolver)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from atp.discovery.dns import BaseDNSResolver, ServerInfo, parse_nameservers
from atp.discovery.local import CompositeResolver, LocalResolver


# ── Fixtures ────────────────────────────────────────────────────────────────


def test_parse_nameservers() -> None:
    assert parse_nameservers(None) is None
    assert parse_nameservers("") is None
    assert parse_nameservers("172.28.1.10, 172.28.0.10") == [
        "172.28.1.10",
        "172.28.0.10",
    ]


@pytest.fixture
def peers_file(tmp_path):
    content = """\
["alice.local"]
host = "127.0.0.1"
port = 7443

["bob.local"]
host = "127.0.0.1"
port = 7444
"""
    p = tmp_path / "peers.toml"
    p.write_text(content)
    return str(p)


@pytest.fixture
def dns_override_file(tmp_path):
    content = """\
["ats._atp.alice.local"]
record = "v=atp1 allow=ip:127.0.0.1 deny=all"

["default.atk._atp.alice.local"]
record = "v=atp1 k=ed25519 p=MCowBQ..."
"""
    p = tmp_path / "dns_override.toml"
    p.write_text(content)
    return str(p)


@pytest.fixture
def local_resolver(peers_file, dns_override_file):
    return LocalResolver(peers_path=peers_file, dns_override_path=dns_override_file)


# ── LocalResolver: query_svcb ───────────────────────────────────────────────


class TestLocalResolverSVCB:

    async def test_known_peer(self, local_resolver):
        info = await local_resolver.query_svcb("alice.local")
        assert info is not None
        assert info.host == "127.0.0.1"
        assert info.port == 7443

    async def test_second_peer(self, local_resolver):
        info = await local_resolver.query_svcb("bob.local")
        assert info is not None
        assert info.host == "127.0.0.1"
        assert info.port == 7444

    async def test_unknown_peer(self, local_resolver):
        info = await local_resolver.query_svcb("unknown.local")
        assert info is None

    async def test_defaults(self, local_resolver):
        info = await local_resolver.query_svcb("alice.local")
        assert info is not None
        assert info.alpn == "atp/1"
        assert info.capabilities == ["message"]
        assert info.ip_addresses == []


# ── LocalResolver: query_txt ────────────────────────────────────────────────


class TestLocalResolverTXT:

    async def test_ats_record(self, local_resolver):
        txt = await local_resolver.query_txt("ats._atp.alice.local")
        assert txt == "v=atp1 allow=ip:127.0.0.1 deny=all"

    async def test_atk_record(self, local_resolver):
        txt = await local_resolver.query_txt("default.atk._atp.alice.local")
        assert txt == "v=atp1 k=ed25519 p=MCowBQ..."

    async def test_unknown_record(self, local_resolver):
        txt = await local_resolver.query_txt("unknown.record")
        assert txt is None


# ── LocalResolver: edge cases ───────────────────────────────────────────────


class TestLocalResolverEdgeCases:

    async def test_no_files(self):
        resolver = LocalResolver()
        assert await resolver.query_svcb("anything") is None
        assert await resolver.query_txt("anything") is None

    async def test_nonexistent_paths(self):
        resolver = LocalResolver(
            peers_path="/nonexistent/peers.toml",
            dns_override_path="/nonexistent/dns.toml",
        )
        assert await resolver.query_svcb("anything") is None
        assert await resolver.query_txt("anything") is None


# ── CompositeResolver ───────────────────────────────────────────────────────


class TestCompositeResolver:

    async def test_local_hit(self, local_resolver):
        """When local resolver has an entry, DNS should not be consulted."""
        mock_dns = AsyncMock(spec=BaseDNSResolver)
        composite = CompositeResolver(local=local_resolver, dns=mock_dns)

        info = await composite.query_svcb("alice.local")
        assert info is not None
        assert info.host == "127.0.0.1"
        mock_dns.query_svcb.assert_not_called()

    async def test_local_miss_falls_back_to_dns(self):
        """When local returns None, CompositeResolver falls back to DNS."""
        local = LocalResolver()  # empty — returns None for everything

        mock_dns = AsyncMock(spec=BaseDNSResolver)
        expected = ServerInfo(host="dns.example.com", port=8443)
        mock_dns.query_svcb.return_value = expected

        composite = CompositeResolver(local=local, dns=mock_dns)
        info = await composite.query_svcb("remote.example.com")

        assert info is expected
        mock_dns.query_svcb.assert_awaited_once_with("remote.example.com")

    async def test_txt_local_hit(self, local_resolver):
        mock_dns = AsyncMock(spec=BaseDNSResolver)
        composite = CompositeResolver(local=local_resolver, dns=mock_dns)

        txt = await composite.query_txt("ats._atp.alice.local")
        assert txt == "v=atp1 allow=ip:127.0.0.1 deny=all"
        mock_dns.query_txt.assert_not_called()

    async def test_txt_local_miss_falls_back_to_dns(self):
        local = LocalResolver()
        mock_dns = AsyncMock(spec=BaseDNSResolver)
        mock_dns.query_txt.return_value = "v=atp1 k=ed25519 p=AAAA"

        composite = CompositeResolver(local=local, dns=mock_dns)
        txt = await composite.query_txt("some.record")

        assert txt == "v=atp1 k=ed25519 p=AAAA"
        mock_dns.query_txt.assert_awaited_once_with("some.record")

    async def test_no_local_resolver(self):
        """When local is None, go straight to DNS."""
        mock_dns = AsyncMock(spec=BaseDNSResolver)
        mock_dns.query_svcb.return_value = ServerInfo(host="d.example.com")

        composite = CompositeResolver(local=None, dns=mock_dns)
        info = await composite.query_svcb("d.example.com")

        assert info is not None
        assert info.host == "d.example.com"
        mock_dns.query_svcb.assert_awaited_once()
