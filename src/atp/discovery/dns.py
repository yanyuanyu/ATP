"""DNS-based discovery for ATP endpoints."""

from __future__ import annotations

from dataclasses import dataclass, field

import dns.asyncresolver
import dns.exception
import dns.rdatatype
import dns.resolver

from atp.core.errors import ATPErrorCode, DiscoveryError


@dataclass
class ServerInfo:
    """Information about an ATP server endpoint."""

    host: str  # "atp.example.com"
    port: int = 7443
    alpn: str = "atp/1"
    capabilities: list[str] = field(default_factory=lambda: ["message"])
    ip_addresses: list[str] = field(default_factory=list)


def parse_nameservers(value: str | None) -> list[str] | None:
    """Parse a comma-separated authoritative resolver override.

    Container runtimes commonly expose an embedded DNS stub in
    ``/etc/resolv.conf``. ATP deployments that need protocol-level DNS
    observability can point dnspython at their actual resolver instead.
    """
    if not value:
        return None
    nameservers = [item.strip() for item in value.split(",") if item.strip()]
    return nameservers or None


class BaseDNSResolver:
    """Abstract base for DNS resolution."""

    async def query_svcb(self, domain: str) -> ServerInfo | None:
        raise NotImplementedError

    async def query_txt(self, name: str) -> str | None:
        raise NotImplementedError

    async def resolve_ips(self, hostname: str) -> list[str]:
        """Resolve hostname to IP addresses. Override in subclasses."""
        return []


class DNSResolver(BaseDNSResolver):
    """Real DNS resolver using dnspython."""

    def __init__(self, nameservers: list[str] | None = None):
        self._resolver = dns.asyncresolver.Resolver()
        if nameservers:
            self._resolver.nameservers = nameservers

    async def query_svcb(self, domain: str) -> ServerInfo | None:
        """Query for SVCB records, falling back to SRV, then return ServerInfo."""
        # Try SVCB on _atp.{domain}, then _agent.{domain}
        for prefix in ("_atp", "_agent"):
            qname = f"{prefix}.{domain}"
            info = await self._try_svcb(qname)
            if info is not None:
                return info

        # SRV fallback on _atp._tcp.{domain}
        return await self._try_srv(f"_atp._tcp.{domain}")

    async def _try_svcb(self, qname: str) -> ServerInfo | None:
        """Attempt an SVCB query for *qname*."""
        try:
            answer = await self._resolver.resolve(qname, "SVCB")
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            return None
        except dns.resolver.NoNameservers as exc:
            raise DiscoveryError(
                ATPErrorCode.SERVER_ERROR,
                f"No nameservers available for {qname}",
            ) from exc
        except dns.exception.Timeout as exc:
            raise DiscoveryError(
                ATPErrorCode.SERVER_ERROR,
                f"DNS timeout querying {qname}",
            ) from exc
        except Exception:
            # SVCB record type may not be supported by resolver
            return None

        return await self._parse_svcb_answer(answer)

    async def _try_srv(self, qname: str) -> ServerInfo | None:
        """Attempt an SRV query as a fallback for SVCB."""
        try:
            answer = await self._resolver.resolve(qname, "SRV")
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            return None
        except dns.resolver.NoNameservers as exc:
            raise DiscoveryError(
                ATPErrorCode.SERVER_ERROR,
                f"No nameservers available for {qname}",
            ) from exc
        except dns.exception.Timeout as exc:
            raise DiscoveryError(
                ATPErrorCode.SERVER_ERROR,
                f"DNS timeout querying {qname}",
            ) from exc
        except Exception:
            return None

        for rdata in answer:
            target = str(rdata.target).rstrip(".")
            port = rdata.port or 7443
            ips = await self._resolve_ips(target)
            return ServerInfo(
                host=target,
                port=port,
                ip_addresses=ips,
            )
        return None

    async def _parse_svcb_answer(self, answer: dns.resolver.Answer) -> ServerInfo | None:
        """Extract ServerInfo fields from an SVCB answer set."""
        for rdata in answer:
            target = str(rdata.target).rstrip(".")
            if not target or target == ".":
                # AliasMode (priority 0) — skip for now
                continue

            port = 7443
            alpn = "atp/1"

            # Parse SvcParams if available
            params = getattr(rdata, "params", {})
            if params:
                # Port — key 3
                port_param = params.get(3)
                if port_param is not None:
                    port = int(getattr(port_param, "port", port))

                # ALPN — key 1
                alpn_param = params.get(1)
                if alpn_param is not None:
                    ids = getattr(alpn_param, "ids", [])
                    if ids:
                        alpn = ids[0].decode() if isinstance(ids[0], bytes) else str(ids[0])

            ips = await self._resolve_ips(target)
            return ServerInfo(
                host=target,
                port=port,
                alpn=alpn,
                ip_addresses=ips,
            )
        return None

    async def _resolve_ips(self, hostname: str) -> list[str]:
        """Resolve a hostname to a list of IP addresses (A then AAAA)."""
        ips: list[str] = []
        for rdtype in ("A", "AAAA"):
            try:
                answer = await self._resolver.resolve(hostname, rdtype)
                for rdata in answer:
                    ips.append(str(rdata))
            except Exception:
                pass
        return ips

    async def resolve_ips(self, hostname: str) -> list[str]:
        """Public API: resolve hostname to IP addresses."""
        return await self._resolve_ips(hostname)

    async def query_txt(self, name: str) -> str | None:
        """Query *name* for a TXT record and return its text content."""
        try:
            answer = await self._resolver.resolve(name, "TXT")
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            return None
        except dns.resolver.NoNameservers as exc:
            raise DiscoveryError(
                ATPErrorCode.SERVER_ERROR,
                f"No nameservers available for {name}",
            ) from exc
        except dns.exception.Timeout as exc:
            raise DiscoveryError(
                ATPErrorCode.SERVER_ERROR,
                f"DNS timeout querying {name}",
            ) from exc

        for rdata in answer:
            # TXT rdata.strings is a tuple of bytes
            parts = [
                s.decode() if isinstance(s, bytes) else s
                for s in rdata.strings
            ]
            return "".join(parts)
        return None
