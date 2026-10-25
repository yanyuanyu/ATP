"""Measure actual communication after ATP server restarts, preserving keys."""
import json
from pathlib import Path
import subprocess
import time

OUT=Path(__file__).parent/"evidence/recovery.json"


def docker(*args):
    return subprocess.run(["docker",*args],capture_output=True,check=True).stdout.decode().strip()


def identity_hashes():
    # Only equality is exported. No private material or key hashes in evidence.
    code="from pathlib import Path; import hashlib,json; print(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path('/root/.atp/keys').glob('*.sm2.*')},sort_keys=True))"
    return {d:docker('exec','jinmi-server-'+d,'python','-c',code) for d in ('family','hotel','payment')}


def transfer(domain):
    deadline=time.monotonic()+25
    while time.monotonic()<deadline:
        p=subprocess.run(['docker','exec','jinmi-server-family','python','/agents/acceptance_probe.py','transfer',domain],capture_output=True)
        if p.returncode==0:
            data=json.loads(p.stdout)
            if data['passed']: return data
        time.sleep(.2)
    raise AssertionError('Communication did not recover')


def main():
    report={'single':[],'group':[]}
    for group in [('family',),('hotel',),('payment',),('family','hotel','payment')]:
        before=identity_hashes()
        start=time.monotonic()
        docker('restart',*['jinmi-server-'+d for d in group])
        evidence=[transfer(d) for d in ('hotel','payment')]
        seconds=time.monotonic()-start
        keys_unchanged=before==identity_hashes()
        assert keys_unchanged and seconds<=30
        row={'domains':group,'communication_seconds':seconds,'keys_unchanged':keys_unchanged,'transfers':evidence,'passed':True}
        report['group' if len(group)>1 else 'single'].append(row)
        print(json.dumps({k:v for k,v in row.items() if k!='transfers'}),flush=True)
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__': main()
