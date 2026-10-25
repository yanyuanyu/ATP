"""Supplementary probes executed inside the family-domain ATP container.

No private key material is emitted. ACK loss is injected at the sender's
observation boundary after a real receiver commit, then the receiver restarts.
"""
import asyncio
import json
import socket
import sys
import time
sys.path.insert(0, '/agents')
from acceptance_probe import AcceptanceClient, PREFIX, PASSWORD, aad, keys
from atp.core.message import ATPMessage
from atp.core.signature import Signer
from atp.discovery.dns import DNSResolver
from atp.security.atk import ATKRecord
from atp.security.sm4 import encrypt_payload
from atp.security.transfer import make_transfer_proof


async def register(c, agent, domain):
    r=await c.post(f'https://server-{domain}.{domain}.test:7443{PREFIX}/register',json={'agent_id':agent,'password':PASSWORD})
    assert r.status_code in (201,409),r.text


async def receive(c, identity, nonce):
    domain=identity.split('@')[1].split('.')[0]
    deadline=time.monotonic()+40
    while time.monotonic()<deadline:
        r=await c.get(f'https://server-{domain}.{domain}.test:7443{PREFIX}/messages',auth=(identity,PASSWORD),params={'limit':100})
        r.raise_for_status()
        rows=[x for x in r.json()['messages'] if x['nonce']==nonce]
        if rows:return rows
        await asyncio.sleep(.2)
    raise AssertionError('Actual mailbox delivery timed out')


async def modes():
    agent='ch5-'+str(time.time_ns())
    async with AcceptanceClient(timeout=30) as c:
        for d in ('family','hotel','payment'):await register(c,agent,d)
        records=[]
        async def exchange(a,b,payload,kind='message',reply=None):
            m=ATPMessage.create(f'{agent}@{a}.test',f'{agent}@{b}.test',payload,message_type=kind,in_reply_to=reply,task_id=agent)
            start=time.monotonic()
            r=await c.post(f'https://server-{a}.{a}.test:7443{PREFIX}/message',auth=(m.from_id,PASSWORD),json=m.to_dict())
            assert r.status_code==202,r.text
            rows=await receive(c,m.to_id,m.nonce)
            assert len(rows)==1 and rows[0]['payload']==payload and rows[0]['type']==kind
            assert rows[0].get('in_reply_to')==reply
            records.append({'type':kind,'nonce':m.nonce,'from':m.from_id,'to':m.to_id,'in_reply_to':reply,'elapsed_seconds':time.monotonic()-start,'rows':len(rows),'passed':True})
            return m.nonce
        await exchange('family','hotel',{'subject':'financial-notification','order_id':agent})
        req=await exchange('family','payment',{'subject':'statement-query','order_id':agent},'request')
        await exchange('payment','family',{'subject':'statement-result','balance_fen':100000},'response',req)
        sub=await exchange('family','hotel',{'subject':'price-subscription','count':5},'request')
        for i in range(1,6):
            await exchange('hotel','family',{'subject':'price-event','event_index':i,'total_events':5,'price_fen':17000+i},'event',sub)
        payload={'body':'x'*(65536-11)}
        assert len(json.dumps(payload,separators=(',',':')).encode())==65536
        await exchange('family','payment',payload)
        return {'passed':True,'async_messages':1,'request_response_pairs':1,'subscription_events':5,'event_indices':list(range(1,6)),'queued_64kib':True,'deliveries':records}


async def ack_prepare():
    agent='ack-'+str(time.time_ns())
    payload={'order_id':agent,'amount_fen':34000,'currency':'CNY'}
    async with AcceptanceClient(timeout=30) as c:
        await register(c,agent,'hotel')
        private,_=keys();signer=Signer(private,'default','family.test')
        public=ATKRecord.parse(await DNSResolver().query_txt('default.atk._atp.hotel.test')).get_public_key()
        m=ATPMessage.create(f'{agent}@family.test',f'{agent}@hotel.test',payload)
        m.timestamp-=601
        m.payload=encrypt_payload(payload,recipient_public_key=public,aad=aad(m));signer.sign(m)
        proof=make_transfer_proof(m,signer)
        r=await send_attempt(c,m,proof)
        assert r.status_code==202,r.text
        rows=await receive(c,m.to_id,m.nonce)
        assert len(rows)==1 and rows[0]['payload']==payload
        return {'wire_message':m.to_dict(),'first_status':r.status_code,'first_result':r.json(),
                'payload':payload,'ack_discarded_by_test_sender':True,'first_receipt_count':len(rows)}


async def send_attempt(c,m,proof):
    return await c.post('http://tlcp-family:9080'+PREFIX+'/message',json=m.to_dict(),headers={
        'X-ATP-TLCP-Target':'server-hotel.hotel.test:7443','X-ATP-TLCP-Server-Name':'server-hotel.hotel.test',
        'X-ATP-Transfer-Proof':proof})


async def ack_retry(data):
    m=ATPMessage.from_dict(data['wire_message'])
    private,_=keys();signer=Signer(private,'default','family.test')
    deadline=time.monotonic()+40
    async with AcceptanceClient(timeout=10) as c:
        while True:
            try:
                proof=make_transfer_proof(m,signer)
                r=await send_attempt(c,m,proof)
                if r.status_code==202:break
            except Exception:
                pass
            assert time.monotonic()<deadline,'Receiver restart recovery timeout'
            await asyncio.sleep(.3)
        assert r.json()['status']=='already_accepted',r.text
        rows=await receive(c,m.to_id,m.nonce)
        assert len(rows)==1 and rows[0]['payload']==data['payload']
        replay=await send_attempt(c,m,proof)
        assert replay.status_code==400
        return {'passed':True,'nonce':m.nonce,'first_status':data['first_status'],'first_result':data['first_result'],
                'retry_status':r.status_code,'retry_result':r.json(),'receiver_restarted':True,
                'message_age_seconds':int(time.time())-m.timestamp,'receiver_mailbox_rows':len(rows),
                'decrypted_equal':True,'reused_proof_replay_status':replay.status_code,'ack_discarded_by_test_sender':True}


if __name__=='__main__':
    op=sys.argv[1]
    if op=='modes':result=asyncio.run(modes())
    elif op=='ack-prepare':result=asyncio.run(ack_prepare())
    elif op=='ack-retry':result=asyncio.run(ack_retry(json.load(sys.stdin)))
    else:raise ValueError(op)
    print(json.dumps(result,ensure_ascii=False))
