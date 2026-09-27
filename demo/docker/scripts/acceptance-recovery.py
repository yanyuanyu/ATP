"""Windows/WSL acceptance: restart and natural queued-message recovery.

No key material or hashes are printed; only equality results. No retry timers are edited.
"""
import hashlib
import os
import shlex
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
REPORT=ROOT/'traces/acceptance/recovery.json'

def docker(*args, check=True):
    prefix=shlex.split(os.environ.get('ATP_DOCKER_COMMAND', 'docker'))
    p=subprocess.run([*prefix,*args],capture_output=True)
    if check and p.returncode:
        raise RuntimeError(p.stdout.decode(errors='replace')+p.stderr.decode(errors='replace'))
    return p.returncode,p.stdout.decode('utf-8',errors='replace').strip()

def py(container, code):
    return docker('exec',container,'python','-c',code)[1]

def hashes():
    return {d:py('hack-server-'+d,"from pathlib import Path; import hashlib,json; p=Path('/root/.atp/keys'); print(json.dumps({f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in p.glob('*.sm2.*')}))") for d in ['family','hotel','payment']}

def ready():
    return all(docker('exec','hack-server-'+d,'curl','-fsS',f'https://server-{d}.{d}.test:7443/.well-known/atp/v1/health',check=False)[0]==0 for d in ['family','hotel','payment'])

def main():
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    if '--single' in sys.argv:
        rows=[]
        for domain in ['family','hotel','payment']:
            before=hashes(); t=time.monotonic()
            docker('restart','hack-server-'+domain)
            while time.monotonic()-t<30 and not ready(): time.sleep(.2)
            health=time.monotonic()-t
            target='payment' if domain=='payment' else 'hotel'
            transfer=json.loads(docker('exec','hack-server-family','python','/agents/acceptance_probe.py','transfer',target)[1])
            elapsed=time.monotonic()-t
            rows.append({'domain':domain,'health_seconds':health,'communication_seconds':elapsed,'under_30s':elapsed<=30 and transfer['passed'],'keys_unchanged':before==hashes(),'transfer':transfer})
            print(json.dumps(rows[-1]),flush=True)
        REPORT.with_name('restart-single.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
        return
    report={'restart':[]}
    for i in range(3):
        before=hashes(); t=time.monotonic()
        docker('restart','hack-server-family','hack-server-hotel','hack-server-payment')
        while time.monotonic()-t<30 and not ready(): time.sleep(.2)
        health=time.monotonic()-t
        transfers=[]
        for d in ['hotel','payment']:
            transfers.append(json.loads(docker('exec','hack-server-family','python','/agents/acceptance_probe.py','transfer',d)[1]))
        elapsed=time.monotonic()-t
        item={'round':i+1,'health_seconds':health,'communication_seconds':elapsed,'keys_unchanged':before==hashes(),'transfers':transfers,'under_30s':elapsed<=30}
        report['restart'].append(item); print(json.dumps(item),flush=True)
        REPORT.write_text(json.dumps(report,indent=2),encoding='utf-8')

    mailbox='recovery-'+str(time.time_ns())+'@hotel.test'
    py('hack-server-hotel',f"import httpx; r=httpx.post('https://server-hotel.hotel.test:7443/.well-known/atp/v1/register',json={{'agent_id':'{mailbox}','password':'acceptance-local-test'}}); assert r.status_code==201,r.text")
    docker('stop','hack-server-hotel')
    try:
        nonce=py('hack-server-family',f"import httpx; from atp.core.message import ATPMessage; httpx.post('https://server-family.family.test:7443/.well-known/atp/v1/register',json={{'agent_id':'acceptance','password':'acceptance-local-test'}}); m=ATPMessage.create('acceptance@family.test','{mailbox}',{{'body':'natural-offline-recovery'}}); r=httpx.post('https://server-family.family.test:7443/.well-known/atp/v1/message',json=m.to_dict(),auth=('acceptance@family.test','acceptance-local-test')); assert r.status_code==202,r.text; print(m.nonce)")
        queued=None
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            queued=json.loads(py('hack-server-family',f"import sqlite3,json; c=sqlite3.connect('/root/.atp/data/messages.db'); print(json.dumps(c.execute('SELECT status,retry_count,next_retry_at FROM messages WHERE nonce=?',('{nonce}',)).fetchone()))"))
            if queued and queued[1]>0: break
            time.sleep(.5)
        t=time.monotonic(); docker('start','hack-server-hotel')
        found=False
        while time.monotonic()-t<90:
            rc,out=docker('exec','hack-server-hotel','python','-c',f"import httpx,json; r=httpx.get('https://server-hotel.hotel.test:7443/.well-known/atp/v1/messages',params={{'limit':100}},auth=('{mailbox}','acceptance-local-test')); print(json.dumps(any(x['nonce']=='{nonce}' and x['payload'].get('body')=='natural-offline-recovery' for x in r.json().get('messages',[]))))",check=False)
            if rc==0 and out=='true': found=True;break
            time.sleep(.5)
        report['queued_recovery']={'delivered_and_decrypted':found,'seconds_from_start_command':time.monotonic()-t,'initial_queue_state':queued,'retry_timer_modified':False}
        print(json.dumps(report['queued_recovery']),flush=True)
    finally:
        docker('start','hack-server-hotel')
        REPORT.write_text(json.dumps(report,indent=2),encoding='utf-8')

if __name__=='__main__': main()
