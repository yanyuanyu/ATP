"""Authenticated local ATP mailbox; all domain transfers use ATP server delivery."""
import json
import os

import httpx

from atp.client import ATPClient


class Mailbox:
    def __init__(self, identity: str, server: str):
        self.identity, self.server = identity, server
        self.password = os.environ["ATP_FINANCE_PASSWORD"]
        self.client = ATPClient(identity, server, password=self.password)
        self.ready = False

    async def register(self):
        async with httpx.AsyncClient(timeout=10) as client:
            url = self.client._base_url + "/.well-known/atp/v1"
            response = await client.post(url + "/register", json={"agent_id": self.identity, "password": self.password})
            if response.status_code not in (201, 409):
                response.raise_for_status()
            # A pre-existing account with another password must fail visibly.
            response = await client.get(url + "/messages", auth=(self.identity, self.password), params={"limit": 1})
            response.raise_for_status()
        self.ready = True

    async def send(self, target: str, subject: str, body: dict, **correlation):
        result = await self.client.send(target, payload={"subject": subject, "body": json.dumps(body, ensure_ascii=False), **correlation})
        if result.get("status") != "accepted":
            raise RuntimeError("ATP拒绝消息提交: " + str(result.get("error", "unknown")))
        return result

    async def recv(self):
        if not self.ready:
            await self.register()
        return await self.client.recv()

    async def close(self):
        await self.client.close()
