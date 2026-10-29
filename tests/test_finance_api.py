import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from atp.finance import api
from atp.finance.hub import Hub
from atp.finance.ledger import BUYER, MERCHANT, CASHIER, MerchantBook, PaymentLedger
from atp.security.sm2 import SM2PrivateKey


@pytest.fixture
def hub(tmp_path):
    key=SM2PrivateKey.generate()
    mailbox=SimpleNamespace(send=AsyncMock(return_value={"status":"accepted","nonce":"nonce-1"}))
    h=Hub(tmp_path/"user",key.public_key(),mailbox)
    h.test_key=key
    return h


def message(sender,request,body,nonce="reply-1",task=None):
    return SimpleNamespace(from_id=sender,to_id=BUYER,nonce=nonce,
                           payload={"body":json.dumps(body),"in_reply_to":request,"task_id":task})


@pytest.mark.asyncio
async def test_quote_correlation_and_restart(hub,tmp_path):
    order=await hub.create(2)
    quote_nonce=hub.mailbox.send.await_args.kwargs["nonce"]
    book=MerchantBook(tmp_path/"merchant.db",hub.test_key)
    q=book.quote({k:order[k] for k in ("order_id","sku","quantity")},BUYER)
    # A signed, but wrong-sender or wrong-task response must not affect the order.
    await hub.receive(message(CASHIER,quote_nonce,q[0],task=order["order_id"]))
    await hub.receive(message(MERCHANT,quote_nonce,q[0],task="another-task"))
    assert hub.order(order["order_id"])["quotes"]==[]
    for i,envelope in enumerate(q):
        m=message(MERCHANT,quote_nonce,envelope,nonce=f"q-{i}",task=order["order_id"])
        await hub.receive(m)
        await hub.receive(m)
    assert hub.order(order["order_id"])["status"]=="AWAITING_AUTHORIZATION"
    reopened=Hub(hub.root,hub.public,hub.mailbox)
    assert len(reopened.order(order["order_id"])["quotes"])==3
    assert reopened.snapshot()["orders"]==hub.snapshot()["orders"]


@pytest.mark.asyncio
async def test_complete_hub_payment_and_browser_retry(hub,tmp_path):
    order=await hub.create(2)
    quote_nonce=hub.mailbox.send.await_args.kwargs["nonce"]
    book=MerchantBook(tmp_path/"merchant.db",hub.test_key)
    q=book.quote({k:order[k] for k in ("order_id","sku","quantity")},BUYER)
    for i,envelope in enumerate(q):
        await hub.receive(message(MERCHANT,quote_nonce,envelope,nonce=f"q-{i}",task=order["order_id"]))
    hub.mailbox.send.return_value={"status":"accepted","nonce":"nonce-pay"}
    await hub.pay(order["order_id"])
    sent=hub.mailbox.send.await_args.args[2]
    pay_nonce=hub.mailbox.send.await_args.kwargs["nonce"]
    ledger=PaymentLedger(tmp_path/"ledger.db",hub.public)
    receipt=ledger.pay(sent,BUYER)
    hub.mailbox.send.return_value={"status":"accepted","nonce":"nonce-statement"}
    bad=copy.deepcopy(receipt);bad["amount_fen"]+=1
    await hub.receive(message(CASHIER,pay_nonce,bad,task=order["order_id"]))
    assert hub.order(order["order_id"])["status"]=="PAYMENT_PENDING"
    await hub.receive(message(CASHIER,pay_nonce,receipt,nonce="good",task=order["order_id"]))
    assert hub.order(order["order_id"])["status"]=="PAID"
    count=hub.mailbox.send.await_count
    await hub.pay(order["order_id"])
    assert hub.mailbox.send.await_count==count


def test_api_requires_authorization_and_same_origin(hub,monkeypatch):
    monkeypatch.setattr(api,"hub",hub)
    with TestClient(Starlette(routes=api.routes)) as client:
        assert client.post('/api/finance/action',json={"action":"pay","authorized":False}).json()["code"]=="AUTHORIZATION_REQUIRED"
        assert client.post('/api/finance/action',json={"action":"create"},headers={"Origin":"https://unrelated.invalid"}).status_code==403
        assert client.post('/api/finance/action',json=[]).status_code==400
        assert client.post('/api/finance/action',content='not-json').status_code==400
        assert client.post('/api/finance/action',json={"action":"unknown"}).status_code==400
        assert client.get('/api/finance/state').headers["cache-control"]=='no-store'


@pytest.mark.asyncio
async def test_worker_durable_outbox(tmp_path):
    from atp.finance.worker import Worker
    key=SM2PrivateKey.generate()
    worker=Worker("merchant",tmp_path,key,key.public_key())
    m=SimpleNamespace(from_id=BUYER,nonce="inbound",payload={"subject":"finance.quote","task_id":"order-1",
                       "body":json.dumps({"order_id":"order-1","sku":"business-stay","quantity":2})})
    worker.process(m)
    failing=SimpleNamespace(send=AsyncMock(side_effect=ConnectionError()))
    with pytest.raises(ConnectionError):
        await worker.flush(failing)
    reopened=Worker("merchant",tmp_path,key,key.public_key())
    reopened.process(m)
    mailbox=SimpleNamespace(send=AsyncMock())
    await reopened.flush(mailbox)
    assert mailbox.send.await_count==3
    await reopened.flush(mailbox)
    assert mailbox.send.await_count==3
