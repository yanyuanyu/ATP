"""Fault injection at the HTTP/business persistence boundary; no live services."""
import importlib.util
import copy
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from atp.client.client import ATPClient
from atp.core.message import ATPMessage
from atp.finance.hub import Hub
from atp.finance.ledger import BUYER, MERCHANT, CASHIER, MerchantBook, PaymentLedger, connect
from atp.finance.messaging import Mailbox
from atp.security.sm2 import SM2PrivateKey


def reply(sender, request_nonce, order_id, body):
    return ATPMessage.create(from_id=sender, to_id=BUYER, payload={
        'in_reply_to': request_nonce, 'task_id': order_id, 'body': json.dumps(body)})


@pytest.mark.asyncio
async def test_lost_submission_response_then_restart_and_late_reply(tmp_path):
    key = SM2PrivateKey.generate()
    mailbox = SimpleNamespace(send=AsyncMock(side_effect=ConnectionError('response lost')))
    hub = Hub(tmp_path/'user', key.public_key(), mailbox)
    order = await hub.create(2)
    nonce = mailbox.send.await_args.kwargs['nonce']
    assert order['status'] == 'QUOTING'
    hub = Hub(hub.root, key.public_key(), mailbox)
    quotes = MerchantBook(tmp_path/'merchant.db', key).quote(
        {k: order[k] for k in ('order_id', 'sku', 'quantity')}, BUYER)
    for q in quotes:
        await hub.receive(reply(MERCHANT, nonce, order['order_id'], q))
    assert hub.order(order['order_id'])['status'] == 'AWAITING_AUTHORIZATION'
    with connect(hub.db) as db:
        assert db.execute('SELECT done FROM operations').fetchone()[0] == 1


@pytest.mark.asyncio
async def test_payment_executed_but_response_lost_retries_without_second_debit(tmp_path):
    key = SM2PrivateKey.generate()
    mailbox = SimpleNamespace(send=AsyncMock())
    hub = Hub(tmp_path/'user', key.public_key(), mailbox)
    order = await hub.create(2)
    nonce = mailbox.send.await_args.kwargs['nonce']
    quotes = MerchantBook(tmp_path/'merchant.db', key).quote(
        {k: order[k] for k in ('order_id', 'sku', 'quantity')}, BUYER)
    for q in quotes:
        await hub.receive(reply(MERCHANT, nonce, order['order_id'], q))
    ledger = PaymentLedger(tmp_path/'ledger.db', key.public_key())
    receipts = []
    async def execute_then_lose_response(target, operation, body, **kwargs):
        if operation == 'finance.pay':
            receipts.append((kwargs['nonce'], ledger.pay(body, BUYER)))
            raise ConnectionError('HTTP response lost')
    mailbox.send.side_effect = execute_then_lose_response
    await hub.pay(order['order_id'])
    assert hub.order(order['order_id'])['status'] == 'PAYMENT_PENDING'
    before = ledger.snapshot()['accounts']
    hub = Hub(hub.root, key.public_key(), mailbox)
    with connect(hub.db) as db:
        db.execute('UPDATE operations SET retry_at=0 WHERE done=0')
    await hub.retry_pending()
    assert len(receipts) == 2 and receipts[0][0] != receipts[1][0]
    assert receipts[1][1]['duplicate'] is True
    assert ledger.snapshot()['accounts'] == before
    assert len(ledger.snapshot()['payments']) == 1
    await hub.receive(reply(CASHIER, receipts[1][0], order['order_id'], receipts[1][1]))
    assert hub.order(order['order_id'])['status'] == 'PAID'
    # A delayed rejection must not downgrade a completed operation.
    await hub.receive(reply(CASHIER, receipts[0][0], order['order_id'],
                            {'kind':'payment-result', 'simulation':True, 'order_id':order['order_id'], 'approved':False}))
    assert hub.order(order['order_id'])['status'] == 'PAID'


@pytest.mark.asyncio
async def test_crash_before_network_send_keeps_order_and_pending_operation(tmp_path, monkeypatch):
    key = SM2PrivateKey.generate()
    hub = Hub(tmp_path, key.public_key(), SimpleNamespace(send=AsyncMock()))
    monkeypatch.setattr(hub, 'submit', AsyncMock(side_effect=RuntimeError('process stopped')))
    with pytest.raises(RuntimeError):
        await hub.create(2)
    reopened = Hub(tmp_path, key.public_key(), SimpleNamespace(send=AsyncMock()))
    await reopened.retry_pending()
    assert reopened.mailbox.send.await_count == 1
    assert reopened.snapshot()['orders'][0]['status'] == 'QUOTING'


def mailbox_for_test(tmp_path, monkeypatch):
    monkeypatch.setenv('ATP_FINANCE_PASSWORD', 'test-only')
    box = Mailbox(BUYER, 'https://family.test', tmp_path)
    box.ready = True
    return box


@pytest.mark.asyncio
async def test_inbox_survives_partial_processing_and_restart(tmp_path, monkeypatch):
    box = mailbox_for_test(tmp_path, monkeypatch)
    messages = [reply(MERCHANT, 'req', 'order', {'index': i}) for i in range(2)]
    async def fetch():
        box.client._last_recv_id = 2
        return messages
    box.client.recv = AsyncMock(side_effect=fetch)
    got = await box.recv()
    box.acknowledge(got[0])
    # Second message fails in business code: it is deliberately not acknowledged.
    assert [m.nonce for m in await box.recv()] == [messages[1].nonce]
    reopened = mailbox_for_test(tmp_path, monkeypatch)
    reopened.client.recv = AsyncMock(return_value=[])
    assert reopened.client._last_recv_id == 2
    pending = await reopened.recv()
    assert [m.nonce for m in pending] == [messages[1].nonce]
    reopened.client.recv.assert_not_awaited()
    reopened.acknowledge(pending[0])
    assert await reopened.recv() == []


@pytest.mark.asyncio
async def test_inbox_storage_failure_does_not_advance_cursor(tmp_path, monkeypatch):
    box = mailbox_for_test(tmp_path, monkeypatch)
    msg = reply(MERCHANT, 'req', 'order', {})
    async def fetch():
        box.client._last_recv_id = 8
        return [msg]
    box.client.recv = AsyncMock(side_effect=fetch)
    with connect(box.db) as db:
        db.execute("CREATE TRIGGER fail_inbox BEFORE INSERT ON inbox BEGIN SELECT RAISE(ABORT,'disk error'); END")
    with pytest.raises(sqlite3.IntegrityError):
        await box.recv()
    assert box.client._last_recv_id is None
    with connect(box.db) as db:
        assert db.execute('SELECT COUNT(*) FROM cursor').fetchone()[0] == 0
        db.execute('DROP TRIGGER fail_inbox')
    assert (await box.recv())[0].nonce == msg.nonce


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [401, 403, 500, 503])
@pytest.mark.parametrize('wait', [False, True])
async def test_receive_http_errors_are_not_empty_inbox(tmp_path, status, wait):
    client = ATPClient(BUYER, 'https://family.test', config_dir=tmp_path, password='test')
    client._transport._client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(status, json={'error':'failure'})))
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await client.recv(wait=wait)
        assert client._last_recv_id is None
    finally:
        await client.close()


def test_role_credentials_and_legacy_compatibility(tmp_path):
    path = Path(__file__).resolve().parents[1]/'demo/finance/manage.py'
    spec = importlib.util.spec_from_file_location('finance_manage', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fresh = tmp_path/'fresh'; fresh.mkdir()
    passwords = module.mailbox_passwords(fresh)
    assert len(set(passwords.values())) == 3
    assert module.mailbox_passwords(fresh) == passwords
    legacy = tmp_path/'legacy'; legacy.mkdir()
    (legacy/'mailbox-password').write_text('existing-account-secret')
    assert set(module.mailbox_passwords(legacy).values()) == {'existing-account-secret'}


@pytest.mark.asyncio
@pytest.mark.parametrize('case', [
    'missing', 'null', 'string', 'one', 'zero', 'list', 'object',
    'rejection-no-code', 'rejection-empty-code', 'rejection-wrong-code-type',
])
async def test_malformed_payment_reply_keeps_operation_pending_until_valid_reply(tmp_path, case):
    key = SM2PrivateKey.generate()
    mailbox = SimpleNamespace(send=AsyncMock())
    hub = Hub(tmp_path/'user', key.public_key(), mailbox)
    order = await hub.create(2)
    quote_nonce = mailbox.send.await_args.kwargs['nonce']
    quotes = MerchantBook(tmp_path/'merchant.db', key).quote(
        {k: order[k] for k in ('order_id', 'sku', 'quantity')}, BUYER)
    for q in quotes:
        await hub.receive(reply(MERCHANT, quote_nonce, order['order_id'], q))
    await hub.pay(order['order_id'])
    request = mailbox.send.await_args.args[2]
    payment_nonce = mailbox.send.await_args.kwargs['nonce']
    ledger = PaymentLedger(tmp_path/'ledger.db', key.public_key())
    valid = ledger.pay(request, BUYER)
    malformed = copy.deepcopy(valid)
    if case == 'missing':
        malformed.pop('approved')
    elif case.startswith('rejection-'):
        malformed['approved'] = False
        if case == 'rejection-empty-code':
            malformed['code'] = ' '
        elif case == 'rejection-wrong-code-type':
            malformed['code'] = 123
    else:
        malformed['approved'] = {'null': None, 'string': 'true', 'one': 1,
                                 'zero': 0, 'list': [], 'object': {}}[case]
    await hub.receive(reply(CASHIER, payment_nonce, order['order_id'], malformed))
    assert hub.order(order['order_id'])['status'] == 'PAYMENT_PENDING'
    with connect(hub.db) as db:
        assert db.execute('SELECT done FROM operations WHERE operation=?', ('finance.pay',)).fetchone()[0] == 0
    assert hub.snapshot()['events'][0]['kind'] == 'invalid_reply'
    await hub.receive(reply(CASHIER, payment_nonce, order['order_id'], valid))
    assert hub.order(order['order_id'])['status'] == 'PAID'
    with connect(hub.db) as db:
        assert db.execute('SELECT done FROM operations WHERE operation=?', ('finance.pay',)).fetchone()[0] == 1
    assert len(ledger.snapshot()['payments']) == 1


@pytest.mark.asyncio
async def test_valid_payment_rejection_finishes_operation(tmp_path):
    key = SM2PrivateKey.generate()
    mailbox = SimpleNamespace(send=AsyncMock())
    hub = Hub(tmp_path/'user', key.public_key(), mailbox)
    order = await hub.create(2)
    nonce = mailbox.send.await_args.kwargs['nonce']
    quotes = MerchantBook(tmp_path/'merchant.db', key).quote(
        {k: order[k] for k in ('order_id', 'sku', 'quantity')}, BUYER)
    for q in quotes:
        await hub.receive(reply(MERCHANT, nonce, order['order_id'], q))
    await hub.pay(order['order_id'])
    request = mailbox.send.await_args.args[2]
    nonce = mailbox.send.await_args.kwargs['nonce']
    ledger = PaymentLedger(tmp_path/'ledger.db', key.public_key(), initial_balance=0)
    rejected = ledger.pay(request, BUYER)
    assert rejected['code'] == 'INSUFFICIENT_FUNDS'
    await hub.receive(reply(CASHIER, nonce, order['order_id'], rejected))
    assert hub.order(order['order_id'])['status'] == 'REJECTED'
    with connect(hub.db) as db:
        assert db.execute('SELECT done FROM operations WHERE operation=?', ('finance.pay',)).fetchone()[0] == 1
    assert ledger.snapshot()['payments'] == []
