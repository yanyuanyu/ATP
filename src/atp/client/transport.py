"""HTTP transport layer for ATP client."""

import base64
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

from atp.core.message import ATPMessage


DEFAULT_PORT = 7443


def parse_server_url(server: str) -> tuple[str, bool]:
    """Parse server string into (base_url, is_https).

    'example.com' -> ('https://example.com:7443', True)
    'example.com:8443' -> ('https://example.com:8443', True)
    'https://example.com' -> ('https://example.com:7443', True)
    'https://example.com:8443' -> ('https://example.com:8443', True)
    'http://example.com' -> ('http://example.com:7443', False)
    'http://example.com:8080' -> ('http://example.com:8080', False)
    '127.0.0.1' -> ('https://127.0.0.1:7443', True)
    '127.0.0.1:7443' -> ('https://127.0.0.1:7443', True)
    """
    # If no scheme, add https://
    if not server.startswith("http://") and not server.startswith("https://"):
        # Could be "host", "host:port", "1.2.3.4", "1.2.3.4:port"
        server = f"https://{server}"

    parsed = urlparse(server)
    scheme = parsed.scheme  # 'http' or 'https'
    is_https = scheme == "https"
    hostname = parsed.hostname or "localhost"
    port = parsed.port or DEFAULT_PORT

    base_url = f"{scheme}://{hostname}:{port}"
    return base_url, is_https


@dataclass
class TransportResult:
    success: bool
    status_code: int
    body: dict = field(default_factory=dict)
    error: str | None = None


class HTTPTransport:
    def __init__(
        self,
        no_verify: bool = False,
        timeout: float = 30.0,
        transport_mode: str = "tls",
        tlcp_gateway_url: str = "",
    ):
        if transport_mode not in {"tls", "tlcp"}:
            raise ValueError("transport_mode must be 'tls' or 'tlcp'")
        if transport_mode == "tlcp" and not tlcp_gateway_url:
            raise ValueError("tlcp_gateway_url is required in TLCP mode")
        self._verify = not no_verify
        self._timeout = timeout
        self._transport_mode = transport_mode
        self._tlcp_gateway_url = tlcp_gateway_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    def _route_url(self, base_url: str, path: str) -> tuple[str, dict[str, str]]:
        """Return the actual URL and Docker-internal relay headers.

        Normal TLS mode connects directly to the discovered ATP server. TLCP
        mode sends the request to a local gateway, which validates the peer
        against its allow-list and establishes the SM2/SM3/SM4 connection.
        """
        if self._transport_mode == "tls":
            return f"{base_url}{path}", {}

        parsed = urlparse(base_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("TLCP relay targets must be HTTPS ATP server URLs")
        target_port = parsed.port or DEFAULT_PORT
        return (
            f"{self._tlcp_gateway_url}{path}",
            {
                "X-ATP-TLCP-Target": f"{parsed.hostname}:{target_port}",
                "X-ATP-TLCP-Server-Name": parsed.hostname,
            },
        )

    def _get_client(self, verify: bool | None = None) -> httpx.AsyncClient:
        effective_verify = verify if verify is not None else self._verify
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(verify=effective_verify, timeout=self._timeout)
        return self._client

    async def post_message(
        self, base_url: str, message: ATPMessage, auth: tuple[str, str] | None = None
    ) -> TransportResult:
        """POST to {base_url}/.well-known/atp/v1/message

        Content-Type: application/atp+json
        Body: message.to_json()

        auth: optional (agent_id, password) tuple for Basic Auth.
        """
        url, relay_headers = self._route_url(
            base_url, "/.well-known/atp/v1/message"
        )
        headers = {"Content-Type": "application/atp+json"}
        headers.update(relay_headers)
        if auth:
            credentials = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
            headers["Authorization"] = f"Basic {credentials}"
        try:
            client = self._get_client()
            resp = await client.post(
                url,
                content=message.to_json(),
                headers=headers,
            )
            body = (
                resp.json()
                if resp.headers.get("content-type", "").startswith("application/json")
                else {}
            )
            return TransportResult(
                success=(resp.status_code == 202),
                status_code=resp.status_code,
                body=body,
            )
        except Exception as e:
            return TransportResult(success=False, status_code=0, error=str(e))

    async def post_register(
        self, base_url: str, agent_id: str, password: str
    ) -> TransportResult:
        """POST to {base_url}/.well-known/atp/v1/register"""
        url, relay_headers = self._route_url(
            base_url, "/.well-known/atp/v1/register"
        )
        try:
            client = self._get_client()
            resp = await client.post(
                url,
                json={"agent_id": agent_id, "password": password},
                headers=relay_headers,
            )
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            return TransportResult(
                success=(resp.status_code == 201),
                status_code=resp.status_code,
                body=body,
            )
        except Exception as e:
            return TransportResult(success=False, status_code=0, error=str(e))

    async def get_capabilities(self, base_url: str) -> dict:
        """GET capabilities from the ATP server."""
        url, relay_headers = self._route_url(
            base_url, "/.well-known/atp/v1/capabilities"
        )
        client = self._get_client()
        resp = await client.get(url, headers=relay_headers)
        return resp.json()

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
