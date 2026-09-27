"""Test an intact signed message from a source outside the allowed ATS subnet."""
import json
import os
import shlex
import subprocess
from pathlib import Path

def command(args, data=None):
    p=subprocess.run([*shlex.split(os.environ.get('ATP_DOCKER_COMMAND','docker')),*args],input=data,capture_output=True)
    if p.returncode: raise RuntimeError(p.stdout.decode(errors='replace')+p.stderr.decode(errors='replace'))
    return p.stdout.decode().strip()

rows=[]
network='atp-acceptance-ats'
command(['network','create','--internal','--subnet','172.29.250.0/24',network])
try:
    command(['network','connect','--ip','172.29.250.2',network,'hack-server-hotel'])
    for i in range(5):
        # Signing happens inside the legitimate server. The unauthorized network
        # origin reuses this intact test envelope; no private key leaves the server.
        raw=command(['exec','hack-server-family','python','-c',"from pathlib import Path; from atp.storage.keys import KeyStorage; from atp.core.signature import Signer; from atp.core.message import ATPMessage; k,_=KeyStorage(Path('/root/.atp/keys')).load_key_pair('default','sm2'); print(Signer(k,'default','family.test').sign(ATPMessage.create('acceptance@family.test','acceptance@hotel.test',{'body':'ATS-test'})).to_json())"])
        code="import sys,json,httpx; c=httpx.Client(transport=httpx.HTTPTransport(verify='/ca.crt')); r=c.post('https://server-hotel.hotel.test:7443/.well-known/atp/v1/message',json=json.load(sys.stdin)); print(json.dumps({'status':r.status_code,'error':r.json().get('error')}))"
        cert=os.environ.get('ATP_ACCEPTANCE_CA',str(Path(__file__).resolve().parents[1]/'certs/ca.crt'))
        result=json.loads(command(['run','--rm','-i','--network',network,'--add-host','server-hotel.hotel.test:172.29.250.2','--mount',f'type=bind,source={cert},target=/ca.crt,readonly','atp-hackthon:latest','python','-c',code],raw.encode()))
        result['rejected']=result['status']==403 and result['error']=='ATS validation failed'
        rows.append(result)
finally:
    command(['network','disconnect',network,'hack-server-hotel'])
    command(['network','rm',network])
report=Path(__file__).resolve().parents[1]/'traces/acceptance/ats.json'
report.parent.mkdir(parents=True,exist_ok=True)
report.write_text(json.dumps(rows,indent=2),encoding='utf-8')
print(json.dumps(rows))
assert len(rows)==5 and all(row['rejected'] for row in rows), 'ATS rejection test failed'
