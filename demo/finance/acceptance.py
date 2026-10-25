"""Exercise real three-domain ATP/TLCP financial operations and recovery.

Requires manage.py start. Writes evidence only after checking structured
receipts and ledger changes. Stops/restarts only jinmi-* test services.
"""
import argparse
import json
from pathlib import Path
import subprocess
import time

import httpx

BASE="http://127.0.0.1:18088"
OUT=Path(__file__).parent/"evidence"


def state():
    r=httpx.get(BASE+"/api/finance/state",timeout=10,trust_env=False)
    r.raise_for_status()
    return r.json()


def action(action,**extra):
    r=httpx.post(BASE+"/api/finance/action",json={"action":action,**extra},timeout=15,trust_env=False)
    r.raise_for_status()
    return r.json()["result"]


def wait(predicate,timeout=60):
    start=time.monotonic()
    while time.monotonic()-start<timeout:
        s=state()
        if predicate(s): return s,time.monotonic()-start
        time.sleep(.3)
    raise AssertionError("Timed out waiting for financial evidence")


def find(s,oid):
    return next(o for o in s["orders"] if o["order_id"]==oid)


def new_order():
    oid=action("create",quantity=2)["order_id"]
    s,elapsed=wait(lambda s:find(s,oid)["status"]=="AWAITING_AUTHORIZATION")
    order=find(s,oid)
    assert sorted(q["quote"]["total_fen"] for q in order["quotes"])==[34000,36000,38000]
    return oid,elapsed


def statement():
    nonce=action("statement")["nonce"]
    return wait(lambda s:any(e.get("request_nonce")==nonce and e["kind"]=="received" for e in s["events"]))[0]["statement"]


def main(outage):
    OUT.mkdir(exist_ok=True)
    report={"started_at":time.time(),"transport":"real ATP three-domain Docker/TLCP","simulation":True,"tests":[]}
    initial=statement()
    oid,elapsed=new_order()
    report["tests"].append({"name":"three_signed_quote_events","passed":True,"seconds":elapsed,"order_id":oid})
    for variant in ("amount","payee","signature","unauthorized"):
        before=len([e for e in state()["events"] if e["kind"]=="test" and e.get("order_id")==oid])
        action("test",order_id=oid,variant=variant)
        s,_=wait(lambda s:len([e for e in s["events"] if e["kind"]=="test" and e.get("order_id")==oid])>before)
        event=next(e for e in s["events"] if e["kind"]=="test" and e.get("order_id")==oid)
        assert event["passed"],event
        report["tests"].append({"name":variant,"passed":True,"code":event["result"].get("code")})
    declined=statement()
    assert declined["accounts"]==initial["accounts"]
    assert len(declined["payments"])==len(initial["payments"])
    report["tests"].append({"name":"rejected_requests_leave_balances_unchanged","passed":True})
    action("pay",order_id=oid,authorized=True)
    s,elapsed=wait(lambda s:find(s,oid)["status"]=="PAID")
    receipt=find(s,oid)["receipt"]
    assert receipt["amount_fen"]==34000 and receipt["simulation"] is True
    paid=statement()
    assert len(paid["payments"])==len(initial["payments"])+1
    balances={a["id"]:a["balance_fen"] for a in paid["accounts"]}
    old={a["id"]:a["balance_fen"] for a in initial["accounts"]}
    assert balances["buyer@family.test"]==old["buyer@family.test"]-34000
    assert sum(balances.values())==sum(old.values())
    report["tests"].append({"name":"payment_and_double_entry_balance","passed":True,"seconds":elapsed,"receipt":receipt})
    # Browser retry is not a second operation.
    action("pay",order_id=oid,authorized=True)
    before=len([e for e in state()["events"] if e["kind"]=="test" and e.get("order_id")==oid])
    action("test",order_id=oid,variant="repeat")
    s,_=wait(lambda s:len([e for e in s["events"] if e["kind"]=="test" and e.get("order_id")==oid])>before)
    event=next(e for e in s["events"] if e["kind"]=="test" and e.get("order_id")==oid)
    assert event["passed"] and event["result"]["receipt_id"]==receipt["receipt_id"]
    duplicate=statement()
    assert duplicate["accounts"]==paid["accounts"] and len(duplicate["payments"])==len(paid["payments"])
    report["tests"].append({"name":"new_nonce_same_order_idempotency","passed":True})
    if outage:
        # Persisted ledger must survive payment-agent restart.
        subprocess.run(["docker","restart","jinmi-finance-payment"],check=True,capture_output=True)
        assert statement()["accounts"]==paid["accounts"]
        report["tests"].append({"name":"payment_worker_restart_persistence","passed":True})
        oid2,_=new_order()
        subprocess.run(["docker","stop","jinmi-server-payment"],check=True,capture_output=True)
        try:
            action("pay",order_id=oid2,authorized=True)
            time.sleep(2)
            assert find(state(),oid2)["status"]=="PAYMENT_PENDING"
        finally:
            start=time.monotonic()
            subprocess.run(["docker","start","jinmi-server-payment"],check=True,capture_output=True)
        s,_=wait(lambda s:find(s,oid2)["status"]=="PAID",timeout=90)
        elapsed=time.monotonic()-start
        final=statement()
        assert len([p for p in final["payments"] if p["order_id"]==oid2])==1
        report["tests"].append({"name":"payment_domain_offline_queue_recovery","passed":True,"seconds":elapsed,"order_id":oid2})
    report.update(passed=True,finished_at=time.time(),state=state())
    (OUT/"finance-acceptance.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"passed":True,"tests":len(report["tests"]),"evidence":str(OUT/"finance-acceptance.json")},ensure_ascii=False))


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--outage",action="store_true")
    main(parser.parse_args().outage)
