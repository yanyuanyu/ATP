"""File-based (local) discovery for development and testing."""

from __future__ import annotations

import os
import tomllib

from atp.discovery.dns import BaseDNSResolver, DNSResolver, ServerInfo


class LocalResolver(BaseDNSResolver):
    """File-based resolver for development/testing.

    peers.toml format::

        [alice.local]
        host = "127.0.0.1"
        port = 7443

        [bob.local]
        host = "127.0.0.1"
        port = 7444

    dns_override.toml format::

        ["ats._atp.alice.local"]
        record = "v=atp1 allow=ip:127.0.0.1 deny=all"

        ["default.atk._atp.alice.local"]
        record = "v=atp1 k=sm2 p=<SM2_PUBLIC_KEY_BASE64>"
    """

    def __init__(
        self,
        peers_path: str | None = None,
        dns_override_path: str | None = None,
    ):
        self._peers: dict = {}
        self._dns_overrides: dict = {}

        if peers_path and os.path.isfile(peers_path):
            with open(peers_path, "rb") as f:
                self._peers = tomllib.load(f)

        if dns_override_path and os.path.isfile(dns_override_path):
            with open(dns_override_path, "rb") as f:
                self._dns_overrides = tomllib.load(f)

    async def query_svcb(self, domain: str) -> ServerInfo | None:
        """Look up *domain* in the peers data and return ServerInfo or None."""
        entry = self._peers.get(domain)
        if entry is None:
            return None

        return ServerInfo(
            host=entry["host"],
            port=entry.get("port", 7443),
            alpn=entry.get("alpn", "atp/1"),
            capabilities=entry.get("capabilities", ["message"]),
            ip_addresses=entry.get("ip_addresses", []),
        )

    async def query_txt(self, name: str) -> str | None:
        """Look up *name* in the dns_override data and return the record text."""
        entry = self._dns_overrides.get(name)
        if entry is None:
            return None
        return entry.get("record")


class CompositeResolver(BaseDNSResolver):
    """Chains LocalResolver -> DNSResolver.  Local wins; DNS is fallback."""

    def __init__(self, local: LocalResolver | None, dns: DNSResolver):
        self._local = local
        self._dns = dns

    async def query_svcb(self, domain: str) -> ServerInfo | None:
        """Try local first, then dns."""
        if self._local is not None:
            result = await self._local.query_svcb(domain)
            if result is not None:
                return result
        return await self._dns.query_svcb(domain)

    async def query_txt(self, name: str) -> str | None:
        """Try local first, then dns."""
        if self._local is not None:
            result = await self._local.query_txt(name)
            if result is not None:
                return result
        return await self._dns.query_txt(name)

    async def resolve_ips(self, hostname: str) -> list[str]:
        """Try local peers first (use ip_addresses), then DNS."""
        if self._local is not None:
            info = await self._local.query_svcb(hostname)
            if info is not None and info.ip_addresses:
                return info.ip_addresses
        return await self._dns.resolve_ips(hostname)
