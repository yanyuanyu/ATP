"""Reproducible acceptance probes; run inside server-family. No model calls."""
import asyncio
import copy
import importlib.metadata
import json
import os
import platform
import statistics
import sys
import time
from pathlib import Path

import httpx
from atp.core.message import ATPMessage
from atp.core.signature import Signer
from atp.discovery.dns import DNSResolver
from atp.security.atk import ATKRecord
from atp.security.sm4 import encrypt_payload, decrypt_payload, message_aad
from atp.storage.keys import KeyStorage

PREFIX = '/.well-known/atp/v1'
PASSWORD = 'acceptance-local-test'

def keys():
    return KeyStorage(Path('/root/.atp/keys')).load_key_pair('default', 'sm2')

def aad(m):
    return message_aad(from_id=m.from_id,to_id=m.to_id,timestamp=m.timestamp,nonce=m.nonce,message_type=m.type)

def benchmark():
    private, public = keys()
    result = {'environment': {'python': platform.python_version(), 'platform': platform.platform(), 'cpu_count': os.cpu_count(), 'gmssl': importlib.metadata.version('gmssl'), 'cpu': next((line.split(':',1)[1].strip() for line in Path('/proc/cpuinfo').read_text().splitlines() if line.startswith('model name')), '')}, 'samples': []}
    for size, count in [(1024,50),(65536,20)]:
        # UTF-8 serialized plaintext is exactly size bytes, including JSON.
        payload = {'body':'x'*(size-11)}
        assert len(json.dumps(payload,separators=(',',':')).encode()) == size
        m=ATPMessage.create('audit@family.test','audit@hotel.test',payload)
        enc=[]; dec=[]
        for i in range(count+2):
            t=time.perf_counter(); envelope=encrypt_payload(payload,recipient_public_key=public,aad=aad(m)); t1=time.perf_counter()
            plain=decrypt_payload(envelope,recipient_private_key=private,aad=aad(m)); t2=time.perf_counter()
            assert plain==payload
            if i>=2: enc.append((t1-t)*1000);dec.append((t2-t1)*1000)
        combined=[a+b for a,b in zip(enc,dec)]
        result['samples'].append({'plaintext_bytes':size,'n':count,'warmups':2,'encrypt_mean_ms':statistics.mean(enc),'decrypt_mean_ms':statistics.mean(dec),'roundtrip_mean_ms':statistics.mean(combined),'roundtrip_max_ms':max(combined),'roundtrip_p95_ms':sorted(combined)[int(.95*len(combined))-1],'limit_ms':100 if size==1024 else 2000,'all_equal':True})
    print(json.dumps(result))

async def network():
    resolver=DNSResolver(); private,_=keys(); signer=Signer(private,'default','family.test')
    records={}; discovers={}; result={'discovery':discovers,'legal':[],'attacks':[]}
    async with httpx.AsyncClient(timeout=30) as client:
        for domain in ['family','hotel','payment']:
            d=domain+'.test'; info=await resolver.query_svcb(d)
            discovers[d]={'host':info.host,'port':info.port,'ips':info.ip_addresses}
            records[domain]=ATKRecord.parse(await resolver.query_txt('default.atk._atp.'+d)).get_public_key()
            response=await client.post(f'https://server-{domain}.{d}:7443{PREFIX}/register',json={'agent_id':'acceptance','password':PASSWORD})
            assert response.status_code in (201,409),response.text
        for domain in ['hotel','payment']:
            url=f'https://server-{domain}.{domain}.test:7443{PREFIX}'
            for size in [1024,65536]:
                for i in range(3):
                    payload={'body':'x'*(size-11)}
                    m=ATPMessage.create('acceptance@family.test',f'acceptance@{domain}.test',payload)
                    m.payload=encrypt_payload(payload,recipient_public_key=records[domain],aad=aad(m));signed=signer.sign(m).to_dict()
                    t=time.perf_counter()
                    response=await client.post('http://tlcp-family:9080'+PREFIX+'/message',json=signed,headers={'X-ATP-TLCP-Target':f'server-{domain}.{domain}.test:7443','X-ATP-TLCP-Server-Name':f'server-{domain}.{domain}.test'})
                    read=await client.get(url+'/messages',params={'limit':100},auth=(f'acceptance@{domain}.test',PASSWORD))
                    match=next((x for x in read.json().get('messages',[]) if x['nonce']==m.nonce),None)
                    result['legal'].append({'domain':domain,'bytes':size,'status':response.status_code,'transport':response.headers.get('x-atp-transport'),'cipher':response.headers.get('x-atp-tlcp-cipher'),'decrypted_equal':bool(match and match['payload']==payload),'elapsed_ms':(time.perf_counter()-t)*1000,'nonce':m.nonce})
                    response=await client.post(url+'/message',json=signed)
                    result['attacks'].append({'type':'replay','status':response.status_code,'error':response.json().get('error'),'rejected':response.status_code==400 and response.json().get('error')=='Replay detected'})
            for i in range(5):
                m=ATPMessage.create('acceptance@family.test',f'acceptance@{domain}.test',{'body':'original'})
                signed=signer.sign(m).to_dict()
                altered=copy.deepcopy(signed);altered['payload']['body']='tampered'
                response=await client.post(url+'/message',json=altered)
                result['attacks'].append({'type':'tamper','status':response.status_code,'rejected':response.status_code==403})
                forged=copy.deepcopy(signed);forged['from']='victim@family.test'
                response=await client.post(url+'/message',json=forged)
                result['attacks'].append({'type':'forged_signed_identity','status':response.status_code,'rejected':response.status_code==403})
        local=f'https://server-family.family.test:7443{PREFIX}/message'
        for i in range(5):
            m=ATPMessage.create('victim@family.test','acceptance@hotel.test',{'body':'test'}).to_dict()
            response=await client.post(local,json=m,auth=('acceptance@family.test',PASSWORD))
            result['attacks'].append({'type':'local_identity_mismatch','status':response.status_code,'rejected':response.status_code==403})
            m['from']='acceptance@family.test'
            response=await client.post(local,json=m,auth=('acceptance@family.test','wrong-password'))
            result['attacks'].append({'type':'unauthorized_credentials','status':response.status_code,'rejected':response.status_code==401})
            response=await client.post('http://tlcp-family:9080'+PREFIX+'/message',json=m,headers={'X-ATP-TLCP-Target':'attacker.invalid:7443','X-ATP-TLCP-Server-Name':'attacker.invalid'})
            result['attacks'].append({'type':'unauthorized_destination','status':response.status_code,'rejected':response.status_code==403})
    print(json.dumps(result))

async def transfer():
    """Submit through real delivery queue and poll recipient, no DB shortcuts."""
    domain=sys.argv[2] if len(sys.argv)>2 else 'hotel'
    size=int(sys.argv[3]) if len(sys.argv)>3 else 128
    marker='probe-'+str(time.time_ns())
    body=marker+'x'*(size-11-len(marker))
    async with httpx.AsyncClient(timeout=10) as c:
        for d in ['family',domain]:
            r=await c.post(f'https://server-{d}.{d}.test:7443{PREFIX}/register',json={'agent_id':'acceptance','password':PASSWORD})
            assert r.status_code in (201,409),r.text
        m=ATPMessage.create('acceptance@family.test',f'acceptance@{domain}.test',{'body':body})
        t=time.monotonic();r=await c.post(f'https://server-family.family.test:7443{PREFIX}/message',json=m.to_dict(),auth=('acceptance@family.test',PASSWORD))
        assert r.status_code==202,r.text
        while time.monotonic()-t<30:
            r=await c.get(f'https://server-{domain}.{domain}.test:7443{PREFIX}/messages',params={'limit':100},auth=(f'acceptance@{domain}.test',PASSWORD))
            found=next((x for x in r.json().get('messages',[]) if x['nonce']==m.nonce),None)
            if found:
                print(json.dumps({'passed':found['payload']=={'body':body},'elapsed_seconds':time.monotonic()-t,'nonce':m.nonce}));return
            await asyncio.sleep(.2)
        print(json.dumps({'passed':False,'elapsed_seconds':time.monotonic()-t,'nonce':m.nonce}))

if __name__=='__main__':
    if sys.argv[1]=='benchmark': benchmark()
    elif sys.argv[1]=='network': asyncio.run(network())
    elif sys.argv[1]=='transfer': asyncio.run(transfer())
