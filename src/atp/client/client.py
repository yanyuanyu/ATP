"""ATP client — build, sign, send, and receive messages."""

import asyncio
from pathlib import Path

from atp.client.transport import HTTPTransport, parse_server_url
from atp.core.message import ATPMessage
from atp.storage.config import ConfigStorage


class ATPClient:
    """ATP client for sending and receiving messages.

    Requires a full agent_id in 'local@domain' format. Short-form IDs
    (without '@') are only accepted by the server's registration endpoint.
    For send/recv, the client needs the full ID to set the message 'from'
    field correctly.
    """

    def __init__(
        self,
        agent_id: str,
        server: str,
        config_dir: Path | None = None,
        no_verify: bool = False,
        password: str | None = None,
    ):
        if "@" not in agent_id:
            raise ValueError(
                f"agent_id must be in 'local@domain' format, got: {agent_id!r}. "
                f"Use the full ID returned by 'atp agent register'."
            )
        self._server = server
        self._base_url, self._is_https = parse_server_url(server)
        self._agent_id = agent_id
        self._password = password
        self._config_storage = ConfigStorage(config_dir)
        self._config = self._config_storage.load()
        self._no_verify = no_verify
        self._transport = HTTPTransport(no_verify=no_verify)
        self._last_recv_id: int | None = None

    async def send(
        self,
        to: str,
        payload: dict | None = None,
        body: str | None = None,
        subject: str | None = None,
    ) -> dict:
        """Build and send a message (unsigned).

        The message is submitted unsigned to the local ATP Server.
        The server signs it with the configured domain-level key before
        forwarding to the remote server. This ensures ATK signing is
        a domain-level operation, not per-agent.
        """
        if payload is None:
            payload = {}
            if body:
                payload["body"] = body
            if subject:
                payload["subject"] = subject

        msg = ATPMessage.create(from_id=self._agent_id, to_id=to, payload=payload)

        # No client-side signing — Server A signs with domain key on transfer.

        # Send
        auth = (self._agent_id, self._password) if self._password else None
        result = await self._transport.post_message(self._base_url, msg, auth=auth)

        if result.success:
            return {"status": "accepted", "nonce": msg.nonce, "timestamp": msg.timestamp}
        else:
            return {
                "status": "error",
                "error": result.error or f"HTTP {result.status_code}",
                "nonce": msg.nonce,
            }

    async def recv(
        self,
        limit: int = 50,
        wait: bool = False,
        timeout: float = 30.0,
    ) -> list[ATPMessage]:
        """Receive messages from server. Requires Credential authentication.

        Uses cursor-based pagination via after_id to avoid re-fetching
        the same messages on repeated calls or in --wait mode.
        """
        url = f"{self._base_url}/.well-known/atp/v1/messages"
        params: dict[str, str] = {"limit": str(limit)}

        # Build auth headers
        headers = {}
        if self._password:
            import base64 as b64
            cred = b64.b64encode(f"{self._agent_id}:{self._password}".encode()).decode()
            headers["Authorization"] = f"Basic {cred}"
        else:
            params["agent_id"] = self._agent_id

        # Use stored cursor to avoid re-fetching
        if self._last_recv_id is not None:
            params["after_id"] = str(self._last_recv_id)

        client = self._transport._get_client()

        if wait:
            loop = asyncio.get_event_loop()
            deadline = loop.time() + timeout
            while loop.time() < deadline:
                resp = await client.get(url, params=params, headers=headers)
                data = resp.json()
                messages = data.get("messages", [])
                if messages:
                    last_id = data.get("last_id")
                    if last_id is not None:
                        self._last_recv_id = last_id
                        params["after_id"] = str(last_id)
                    return [ATPMessage.from_dict(m) for m in messages]
                await asyncio.sleep(2)
            return []
        else:
            resp = await client.get(url, params=params, headers=headers)
            data = resp.json()
            messages = data.get("messages", [])
            last_id = data.get("last_id")
            if last_id is not None:
                self._last_recv_id = last_id
            return [ATPMessage.from_dict(m) for m in messages]

    async def close(self) -> None:
        await self._transport.close()
