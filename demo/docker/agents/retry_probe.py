"""Run inside server-family: actual TLCP old-message / lost-ACK acceptance."""
import asyncio
import json
import tempfile
import time
from pathlib import Path

from acceptance_probe import AcceptanceClient, PASSWORD, PREFIX, keys
from atp.client.transport import HTTPTransport
from atp.core.message import ATPMessage
from atp.core.signature import Signer
from atp.discovery.dns import DNSResolver
from atp.server.delivery import DeliveryManager
from atp.storage.messages import MessageStore, MessageStatus


async def main():
    agent = "retry-" + str(time.time_ns())
    recipient = agent + "@hotel.test"
    async with AcceptanceClient(timeout=30) as client:
        result = await client.post("https://server-hotel.hotel.test:7443" + PREFIX + "/register",
                                   json={"agent_id": recipient, "password": PASSWORD})
        assert result.status_code == 201, result.text
        transport = HTTPTransport(transport_mode="tlcp", tlcp_gateway_url="http://tlcp-family:9080")
        actual = transport.post_message
        attempts = []
        async def lost_ack(*args, **kwargs):
            response = await actual(*args, **kwargs)
            attempts.append(response)
            if len(attempts) == 1:
                assert response.success, response
                raise TimeoutError("Injected loss AFTER remote acceptance")
            return response
        transport.post_message = lost_ack
        with tempfile.TemporaryDirectory(prefix="atp-retry-") as directory:
            path = Path(directory) / "messages.db"
            store = MessageStore(path)
            message = ATPMessage.create("acceptance@family.test", recipient, {"amount": 340})
            message.timestamp -= 601
            store.enqueue(message)
            signer = Signer(keys()[0], "default", "family.test")
            def manager():
                return DeliveryManager(store, DNSResolver(), transport, signer, "family.test",
                                       payload_encryption=True)
            started = time.monotonic()
            await manager()._deliver_one(store.get_by_nonce(message.nonce))
            assert store.get_by_nonce(message.nonce).status == MessageStatus.FAILED
            store._conn.close()
            store = MessageStore(path)
            await manager()._deliver_one(store.get_by_nonce(message.nonce))
            assert store.get_by_nonce(message.nonce).status == MessageStatus.DELIVERED
            assert attempts[1].body["status"] == "already_accepted"
            response = await client.get("https://server-hotel.hotel.test:7443" + PREFIX + "/messages",
                                        auth=(recipient, PASSWORD))
            rows = response.json()["messages"]
            assert len(rows) == 1 and rows[0]["payload"] == {"amount": 340}
            assert rows[0]["nonce"] == message.nonce and rows[0]["timestamp"] == message.timestamp
            print(json.dumps({"passed": True, "age_seconds": 601,
                              "lost_ack_recovered": True, "sender_store_reopened": True,
                              "received_count": len(rows), "receipt": attempts[1].body["status"],
                              "elapsed_seconds": time.monotonic() - started}))
            store._conn.close()
        await transport.close()


if __name__ == "__main__":
    asyncio.run(main())
