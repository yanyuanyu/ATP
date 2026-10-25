"""Financial business security and persistence tests; no model or external service."""
import copy
from concurrent.futures import ThreadPoolExecutor

import pytest

from atp.finance.ledger import BUYER, PAYEE, FinanceError, MerchantBook, PaymentLedger, payment_request
from atp.security.sm2 import SM2PrivateKey


@pytest.fixture
def system(tmp_path):
    key = SM2PrivateKey.generate()
    merchant = MerchantBook(tmp_path/"merchant.db", key)
    ledger = PaymentLedger(tmp_path/"ledger.db", key.public_key())
    order = {"order_id":"JM-test-01", "sku":"business-stay", "quantity":2}
    quotes = merchant.quote(order, BUYER, now=1000)
    request = payment_request(min(quotes, key=lambda q:q["quote"]["total_fen"]))
    return merchant, ledger, request, key


def balances(ledger):
    return {x["id"]:x["balance_fen"] for x in ledger.snapshot()["accounts"]}


def test_signed_quotes_and_simulated_payment(system):
    merchant,ledger,request,key=system
    assert [q["quote"]["unit_price_fen"] for q in merchant.quote({"order_id":"JM-test-01","sku":"business-stay","quantity":2},BUYER,now=1001)]==[18000,17000,19000]
    result=ledger.pay(request,BUYER,now=1001)
    assert result["approved"] and result["amount_fen"]==34000 and result["simulation"]
    assert balances(ledger)=={BUYER:966000,PAYEE:34000}


@pytest.mark.parametrize("field,value,code",[
    ("amount_fen",33999,"PAYMENT_MISMATCH"),("amount_fen",34000.0,"PAYMENT_MISMATCH"),
    ("amount_fen",True,"PAYMENT_MISMATCH"),("payee","attacker","PAYMENT_MISMATCH"),
    ("currency","USD","PAYMENT_MISMATCH"),("order_id","JM-other","PAYMENT_MISMATCH"),
    ("payer","other@family.test","PAYMENT_MISMATCH"),("authorized",False,"AUTHORIZATION_REQUIRED"),
    ("authorized","true","AUTHORIZATION_REQUIRED"),
])
def test_business_mismatch_does_not_change_ledger(system,field,value,code):
    _,ledger,request,_=system
    before=balances(ledger)
    request[field]=value
    result=ledger.pay(request,BUYER,now=1001)
    assert not result["approved"] and result["code"]==code
    assert balances(ledger)==before and ledger.snapshot()["payments"]==[]
    assert ledger.snapshot()["audit"][0]["code"]==code


@pytest.mark.parametrize("field,value",[("total_fen",1),("payee","attacker"),("currency","USD"),("quantity",1),("expires_at",999999)])
def test_modified_signed_quote_rejected(system,field,value):
    _,ledger,request,_=system
    request["signed_quote"]["quote"][field]=value
    assert ledger.pay(request,BUYER,now=1001)["code"]=="INVALID_QUOTE_SIGNATURE"
    assert not ledger.snapshot()["payments"]


def test_untrusted_merchant_key(system):
    _,ledger,request,_=system
    q=request["signed_quote"]["quote"]
    from atp.finance.ledger import canonical
    request["signed_quote"]["signature"]=SM2PrivateKey.generate().sign(canonical(q)).hex()
    assert ledger.pay(request,BUYER,now=1001)["code"]=="INVALID_QUOTE_SIGNATURE"


@pytest.mark.parametrize("now",[999,1900,2000])
def test_quote_validity_window(system,now):
    _,ledger,request,_=system
    assert ledger.pay(request,BUYER,now=now)["code"]=="QUOTE_EXPIRED"


def test_wrong_atp_sender(system):
    _,ledger,request,_=system
    assert ledger.pay(request,"attacker@family.test",now=1001)["code"]=="UNAUTHORIZED_PAYER"


def test_restart_and_concurrent_repeated_payment_only_one_posting(system):
    _,ledger,request,key=system
    def pay(_):
        return PaymentLedger(ledger.path,key.public_key()).pay(copy.deepcopy(request),BUYER,now=1001)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(pay,range(16)))
    assert all(r["approved"] for r in results)
    assert len({r["receipt_id"] for r in results})==1
    assert sum(not r["duplicate"] for r in results)==1
    assert balances(ledger)=={BUYER:966000,PAYEE:34000}
    assert len(ledger.snapshot()["payments"])==1
    # A lost receipt may be queried again after quote expiry: no second debit.
    assert pay(0)["duplicate"]
    assert ledger.pay(request,BUYER,now=9999)["duplicate"]


def test_same_order_different_valid_quote_conflicts(system):
    merchant,ledger,request,_=system
    ledger.pay(request,BUYER,now=1001)
    other=merchant.quote({"order_id":"JM-test-01","sku":"business-stay","quantity":2},BUYER)[0]
    assert ledger.pay(payment_request(other),BUYER,now=1002)["code"]=="ORDER_CONFLICT"
    assert len(ledger.snapshot()["payments"])==1


def test_insufficient_balance_and_limit(system,tmp_path):
    merchant,ledger,request,key=system
    poor=PaymentLedger(tmp_path/"poor.db",key.public_key(),initial_balance=100)
    assert poor.pay(request,BUYER,now=1001)["code"]=="INSUFFICIENT_FUNDS"
    assert balances(poor)[BUYER]==100
    expensive=merchant.quote({"order_id":"JM-expensive","sku":"business-stay","quantity":7},BUYER,now=1000)[2]
    # 7 * 190 = 1330 CNY is within limit; test a valid signed quote above it.
    expensive=copy.deepcopy(expensive)
    expensive["quote"]["unit_price_fen"]=40000
    expensive["quote"]["total_fen"]=280000
    from atp.finance.ledger import canonical
    expensive["signature"]=key.sign(canonical(expensive["quote"])).hex()
    assert ledger.pay(payment_request(expensive),BUYER,now=1001)["code"]=="LIMIT_EXCEEDED"


@pytest.mark.parametrize("quantity",[0,8,True,1.5,"2",None])
def test_invalid_order_quantity(system,quantity):
    merchant,_,_,_=system
    with pytest.raises(FinanceError):
        merchant.quote({"order_id":"JM-new","sku":"business-stay","quantity":quantity},BUYER)


def test_merchant_order_binding_and_persistence(system):
    merchant,_,request,key=system
    reopened=MerchantBook(merchant.path,key)
    quotes=reopened.quote({"order_id":"JM-test-01","sku":"business-stay","quantity":2},BUYER)
    assert request["signed_quote"] in quotes
    with pytest.raises(FinanceError,match="不能更改"):
        reopened.quote({"order_id":"JM-test-01","sku":"business-stay","quantity":3},BUYER)


@pytest.mark.parametrize("malformed",[None,[],{}, {"signed_quote":None}])
def test_malformed_payment_is_declined(system,malformed):
    _,ledger,_,_=system
    assert ledger.pay(malformed,BUYER,now=1001)["approved"] is False
