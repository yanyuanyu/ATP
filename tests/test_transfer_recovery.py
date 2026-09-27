"""Fault tests use actual SM2 signatures and the real ASGI receive path."""
import asyncio
import base64
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from starlette.applications import Starlette

from atp.client.transport import HTTPTransport
from atp.core.message import ATPMessage
from atp.core.signature import Signer
from atp.security.atk import ATKVerifier
from atp.security.ats import ATSResult, ATSVerifier
from atp.security.replay import ReplayGuard
from atp.security.sm2 import SM2PrivateKey
from atp.security.transfer import make_transfer_proof, parse_transfer_proof
from atp.server.config import RuntimeServerConfig
from atp.server.delivery import DeliveryManager
from atp.server.metrics import ServerMetrics
from atp.server.queue import MessageQueue
from atp.server.routes import get_routes
from atp.storage.messages import MessageStore, MessageStatus


@pytest.fixture
def environment(tmp_path):
    sender_key, recipient_key = SM2PrivateKey.generate(), SM2PrivateKey.generate()
    signer = Signer(sender_key, "default", "family.test")
    resolver = AsyncMock()
    resolver.query_svcb.return_value = SimpleNamespace(host="hotel.test", port=7443)
    async def txt(name):
        key = sender_key if name.endswith("family.test") else recipient_key
        return "v=atp1 k=sm2 p=" + base64.b64encode(key.public_key().raw_bytes()).decode()
    resolver.query_txt.side_effect = txt
    server = SimpleNamespace(
        config=RuntimeServerConfig(domain="hotel.test"),
        ats_verifier=SimpleNamespace(verify=AsyncMock(return_value=ATSResult(status="PASS"))),
        atk_verifier=ATKVerifier(resolver), replay_guard=ReplayGuard(),
        metrics=ServerMetrics(), queue=MessageQueue(MessageStore(tmp_path / "inbox.db")),
        encryption_private_key=recipient_key,
    )
    app = Starlette(routes=get_routes())
    app.state.server = server
    return SimpleNamespace(server=server, app=app, signer=signer, resolver=resolver,
                           sender=MessageStore(tmp_path / "outbox.db"), tmp=tmp_path)


@pytest.mark.asyncio
@pytest.mark.parametrize("recipient", ["pay@hotel.test", "PAY@HOTEL.TEST"])
async def test_lost_ack_restart_and_old_message_are_exactly_once(environment, recipient):
    e = environment
    wire = HTTPTransport()
    wire._client = httpx.AsyncClient(transport=httpx.ASGITransport(app=e.app))
    actual_post = wire.post_message
    attempts = []
    async def lose_first_ack(base, message, **kwargs):
        result = await actual_post(base, message, **kwargs)
        attempts.append((message.to_json(), kwargs["transfer_proof"], result))
        if len(attempts) == 1:
            assert result.success
            raise TimeoutError("Remote committed but ACK lost")
        return result
    wire.post_message = lose_first_ack
    message = ATPMessage.create("travel@family.test", recipient, {"amount": 340})
    message.timestamp -= 601
    e.sender.enqueue(message)
    def manager():
        return DeliveryManager(e.sender, e.resolver, wire, e.signer, "family.test",
                               payload_encryption=True)
    await manager()._deliver_one(e.sender.get_by_nonce(message.nonce))
    assert e.sender.get_by_nonce(message.nonce).status == MessageStatus.FAILED
    # Restart BOTH stores/guards; no in-memory receipt can make the test pass.
    e.sender._conn.close()
    e.sender = MessageStore(e.tmp / "outbox.db")
    e.server.queue._store._conn.close()
    e.server.queue = MessageQueue(MessageStore(e.tmp / "inbox.db"))
    e.server.replay_guard = ReplayGuard()
    await manager()._deliver_one(e.sender.get_by_nonce(message.nonce))
    assert e.sender.get_by_nonce(message.nonce).status == MessageStatus.DELIVERED
    assert attempts[0][0] == attempts[1][0]
    assert attempts[0][1] != attempts[1][1]
    assert attempts[1][2].body["status"] == "already_accepted"
    rows = e.server.queue._store.get_messages_for_agent(recipient.lower())
    assert len(rows) == 1
    stored = ATPMessage.from_json(rows[0].message_json)
    assert stored.nonce == message.nonce and stored.timestamp == message.timestamp
    # Normal replay remains rejected, and a captured transport attempt too.
    assert (await actual_post("https://hotel.test", stored)).status_code == 400
    assert (await actual_post("https://hotel.test", stored,
                             transfer_proof=attempts[1][1])).status_code == 400
    # Recipients still decrypt the original payment.
    response = await wire._client.get("https://hotel.test/.well-known/atp/v1/messages",
                                     params={"agent_id": recipient.lower()})
    assert response.json()["messages"][0]["payload"] == {"amount": 340}
    # Mailbox cleanup cannot erase the permanent idempotency receipt.
    e.server.queue._store._conn.execute("DELETE FROM messages")
    e.server.queue._store._conn.commit()
    retry = await actual_post("https://hotel.test", stored,
                              transfer_proof=make_transfer_proof(stored, e.signer))
    assert retry.body["status"] == "already_accepted"
    assert e.server.queue._store.get_messages_for_agent(recipient.lower()) == []
    await wire.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["proof_tamper", "body_tamper", "stale_proof",
                                  "wrong_signer", "nonce_conflict", "ats_denied"])
async def test_retry_proof_cannot_bypass_security(environment, fault):
    e = environment
    message = ATPMessage.create("travel@family.test", "pay@hotel.test", {"amount": 340})
    e.signer.sign(message)
    proof = make_transfer_proof(message, e.signer)
    wire = HTTPTransport()
    wire._client = httpx.AsyncClient(transport=httpx.ASGITransport(app=e.app))
    if fault == "nonce_conflict":
        assert (await wire.post_message("https://hotel.test", message, transfer_proof=proof)).success
        message.payload["amount"] = 999
        e.signer.sign(message)
        proof = make_transfer_proof(message, e.signer)
    elif fault == "body_tamper":
        message.payload["amount"] = 999
    elif fault == "ats_denied":
        e.server.ats_verifier.verify.return_value = ATSResult(status="FAIL")
    else:
        decoded = parse_transfer_proof(proof, message)
        if fault == "proof_tamper":
            decoded.timestamp += 1
        elif fault == "stale_proof":
            decoded.timestamp -= 301
            e.signer.sign(decoded)
        else:
            Signer(SM2PrivateKey.generate(), "default", "family.test").sign(decoded)
        proof = base64.b64encode(decoded.to_json().encode()).decode()
    result = await wire.post_message("https://hotel.test", message, transfer_proof=proof)
    assert result.status_code in (400, 403, 409)
    rows = e.server.queue._store.get_messages_for_agent(message.to_id)
    assert len(rows) == (1 if fault == "nonce_conflict" else 0)
    await wire.close()


@pytest.mark.asyncio
async def test_twenty_slow_messages_do_not_block_healthy_domain(tmp_path):
    store = MessageStore(tmp_path / "queue.db")
    for i in range(20):
        store.enqueue(ATPMessage.create("a@family.test", "b@slow.test", {"i": i}))
    manager = DeliveryManager(store, AsyncMock(), AsyncMock(), MagicMock(), "family.test")
    slow_started = asyncio.Event()
    async def transfer(message):
        if message.to_id.endswith("slow.test"):
            slow_started.set()
            await asyncio.sleep(60)
        return True
    manager.transfer = transfer
    await manager.start()
    try:
        await asyncio.wait_for(slow_started.wait(), 2)
        healthy = ATPMessage.create("a@family.test", "b@healthy.test", {})
        store.enqueue(healthy)
        deadline = time.monotonic() + 2
        while store.get_by_nonce(healthy.nonce).status != MessageStatus.DELIVERED:
            assert time.monotonic() < deadline
            await asyncio.sleep(0.01)
    finally:
        await manager.stop()
    # Cancellation leaves no background send alive; startup can recover it.
    assert store.recover_interrupted_deliveries() == 1


@pytest.mark.asyncio
async def test_demo_ats_rejects_nat_and_only_allows_named_gateways():
    from pathlib import Path
    for zone in (Path(__file__).parents[1] / "demo/docker/bind/zones").glob("*.zone"):
        record = next(line.split('"')[1] for line in zone.read_text().splitlines()
                      if line.startswith("ats._atp"))
        resolver = AsyncMock()
        resolver.query_txt.return_value = record
        verifier = ATSVerifier(resolver)
        for address in ("172.28.1.2", "172.28.2.2", "172.28.3.2"):
            assert (await verifier.verify("family.test", address)).status == "PASS"
        for address in ("172.28.0.1", "172.28.1.253", "172.28.1.101"):
            assert (await verifier.verify("family.test", address)).status == "FAIL"


@pytest.mark.asyncio
async def test_four_busy_domains_do_not_starve_a_fifth(tmp_path):
    store = MessageStore(tmp_path / "fair.db")
    for i in range(20):
        store.enqueue(ATPMessage.create("a@family.test", f"b@slow{i % 4}.test", {"i": i}))
    manager = DeliveryManager(store, AsyncMock(), AsyncMock(), MagicMock(), "family.test")
    release = asyncio.Event()
    all_busy = asyncio.Event()
    order = []
    async def transfer(message):
        order.append(message.to_id)
        if len(order) == 4:
            all_busy.set()
        if message.to_id != "b@healthy.test":
            await release.wait()
            return False
        return True
    manager.transfer = transfer
    await manager.start()
    try:
        await asyncio.wait_for(all_busy.wait(), 2)
        healthy = ATPMessage.create("a@family.test", "b@healthy.test", {})
        store.enqueue(healthy)
        release.set()
        deadline = time.monotonic() + 2
        while store.get_by_nonce(healthy.nonce).status != MessageStatus.DELIVERED:
            assert time.monotonic() < deadline
            await asyncio.sleep(0.01)
        assert order[4] == "b@healthy.test"
    finally:
        await manager.stop()
