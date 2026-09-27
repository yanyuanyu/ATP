"""Restart recovery must preserve messages and leave terminal states alone."""
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from atp.core.message import ATPMessage
from atp.server.delivery import DeliveryManager
from atp.storage.messages import MessageStore, MessageStatus


def test_startup_caps_legacy_timer_without_reviving_bounced_messages(tmp_path):
    store = MessageStore(tmp_path / "messages.db")
    message = ATPMessage.create("a@family.test", "b@hotel.test", {"body": "old"})
    store.enqueue(message)
    store.mark_retry(message.nonce, int(time.time()) + 86400)
    bounced = ATPMessage.create("a@family.test", "b@hotel.test", {"body": "terminal"})
    store.enqueue(bounced, MessageStatus.BOUNCED)
    store.recover_interrupted_deliveries()
    pending = store.get_by_nonce(message.nonce)
    assert pending.next_retry_at <= int(time.time()) + 10
    assert pending.retry_count == 1
    assert store.get_by_nonce(bounced.nonce).status == MessageStatus.BOUNCED


@pytest.mark.asyncio
async def test_restart_resumes_inflight_message_without_changing_identity(tmp_path):
    store = MessageStore(tmp_path / "messages.db")
    message = ATPMessage.create("a@family.test", "b@hotel.test", {"body": "recover"})
    store.enqueue(message, MessageStatus.DELIVERING)
    delivered = ATPMessage.create("a@family.test", "b@hotel.test", {"body": "done"})
    store.enqueue(delivered, MessageStatus.DELIVERED)
    manager = DeliveryManager(store, AsyncMock(), AsyncMock(), MagicMock(), "family.test")
    manager.transfer = AsyncMock(return_value=True)
    await manager.start()
    try:
        for _ in range(30):
            if store.get_by_nonce(message.nonce).status == MessageStatus.DELIVERED:
                break
            await asyncio.sleep(0.01)
        assert store.get_by_nonce(message.nonce).status == MessageStatus.DELIVERED
        manager.transfer.assert_awaited_once()
        recovered = manager.transfer.call_args.args[0]
        assert recovered.nonce == message.nonce
        assert recovered.timestamp == message.timestamp
        assert recovered.payload == message.payload
        assert store.get_by_nonce(delivered.nonce).status == MessageStatus.DELIVERED
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_failed_delivery_retries_original_message(tmp_path):
    store = MessageStore(tmp_path / "messages.db")
    message = ATPMessage.create("a@family.test", "b@hotel.test", {"body": "pending"})
    store.enqueue(message)
    manager = DeliveryManager(store, AsyncMock(), AsyncMock(), MagicMock(), "family.test")
    manager.transfer = AsyncMock(side_effect=[False, True])
    await manager._deliver_one(store.get_by_nonce(message.nonce))
    failed = store.get_by_nonce(message.nonce)
    assert failed.status == MessageStatus.FAILED
    assert failed.retry_count == 1
    assert 2 <= failed.next_retry_at - failed.updated_at <= 4
    await manager._deliver_one(failed)
    assert store.get_by_nonce(message.nonce).status == MessageStatus.DELIVERED
    assert [call.args[0].nonce for call in manager.transfer.call_args_list] == [message.nonce] * 2
