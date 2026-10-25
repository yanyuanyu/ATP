"""Run and archive the complete chapter-five acceptance suite."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT.parent/'第五章测试材料'
RAW=OUT/'00_原始验收数据'
LOG=OUT/'00_执行日志'
STEPS=[]


def save(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')


def execute(name,args,*,cwd=ROOT,env=None):
    start=time.monotonic()
    p=subprocess.run([str(x) for x in args],cwd=cwd,env=env,capture_output=True)
    (LOG/(name+'.log')).write_bytes(p.stdout+b'\n'+p.stderr)
    row={'name':name,'exit_code':p.returncode,'seconds':round(time.monotonic()-start,3),'passed':p.returncode==0}
    STEPS.append(row);save(RAW/'execution.json',STEPS)
    print(name, 'PASS' if row['passed'] else 'FAIL',row['seconds'],flush=True)
    if p.returncode:raise RuntimeError(name+': '+p.stderr.decode('utf-8',errors='replace')[-1800:])
    return p.stdout


def probe(op,data=None):
    code=(ROOT/'demo/finance/chapter5_probe.py').read_text(encoding='utf-8')
    p=subprocess.run(['docker','exec','-i','jinmi-server-family','python','-c',code,op],
                     input=json.dumps(data).encode() if data else b'',capture_output=True)
    (LOG/('probe-'+op+'.log')).write_bytes(p.stdout+b'\n'+p.stderr)
    if p.returncode:raise RuntimeError(p.stderr.decode('utf-8',errors='replace'))
    return json.loads(p.stdout)


def isolation():
    containers=[];checks=[]
    for i,d in enumerate(('family','hotel','payment'),1):
        spec=json.loads(subprocess.check_output(['docker','inspect','jinmi-server-'+d]))[0]
        networks={k:v['IPAddress'] for k,v in spec['NetworkSettings']['Networks'].items()}
        assert len(networks)==1 and list(networks.values())==[f'172.32.{i}.1']
        containers.append({'service':'jinmi-server-'+d,'networks':networks,'image_id':spec['Image']})
        targets=[(x,f'172.32.{j}.1') for j,x in enumerate(('family','hotel','payment'),1)]
        code="import socket,json; rows=[]\nfor name,ip in "+repr(targets)+":\n s=socket.socket();s.settimeout(.7)\n try:s.connect((ip,7443));ok=True\n except OSError:ok=False\n finally:s.close()\n rows.append({'target':name,'ip':ip,'connected':ok})\nprint(json.dumps(rows))"
        rows=json.loads(subprocess.check_output(['docker','exec','jinmi-server-'+d,'python','-c',code]))
        for row in rows:
            row.update(source=d,expected_connected=row['target']==d)
            row['passed']=row['connected']==row['expected_connected'];assert row['passed']
            checks.append(row)
    return {'passed':True,'server_networks':containers,'tcp_checks':checks,
            'cross_domain_direct_connections_blocked':sum(not r['connected'] for r in checks)}


def main():
    RAW.mkdir(parents=True,exist_ok=True);LOG.mkdir(parents=True,exist_ok=True)
    env={**os.environ,'PYTHONPATH':str(ROOT/'src'),'PYTHONIOENCODING':'utf-8','ATP_PYTHON':sys.executable}
    execute('python-full',[sys.executable,'-m','pytest','-q','--basetemp=.pytest-tmp-chapter5','--junitxml='+str(RAW/'python-tests.xml')],env=env)
    execute('finance-targeted',[sys.executable,'-m','pytest','-q','tests/test_finance.py','tests/test_finance_api.py','--basetemp=.pytest-tmp-chapter5-finance','--junitxml='+str(RAW/'finance-unit.xml')],env=env)
    execute('transfer-recovery-targeted',[sys.executable,'-m','pytest','-q','tests/test_transfer_recovery.py','tests/test_recovery.py','--basetemp=.pytest-tmp-chapter5-transfer','--junitxml='+str(RAW/'transfer-recovery.xml')],env=env)
    execute('node-runtime',['node','--test','demo/docker/agents/pi/guards.test.mjs','demo/docker/agents/pi/runtime.test.mjs'],env=env)
    execute('go-gateway',['docker','run','--rm','--mount',f'type=bind,source={ROOT / "demo/docker/tlcp-gateway"},target=/src,readonly','--mount','type=volume,source=atp-go-mod-cache,target=/go/pkg/mod','-w','/src','docker.1ms.run/library/golang:1.24-bookworm','go','test','-v','./...'])
    execute('frontend-build',['npm.cmd','run','build'],cwd=ROOT/'demo/docker/demo/web')
    save(OUT/'01_测试环境'/'network-isolation.json',isolation())
    print('network-isolation PASS',flush=True)
    execute('protocol-crypto-ats',[sys.executable,'demo/finance/protocol_acceptance.py'],env=env)
    save(RAW/'message-modes.json',probe('modes'));print('message-modes PASS',flush=True)
    pending=probe('ack-prepare')
    execute('ack-receiver-restart',['docker','restart','jinmi-server-hotel'])
    save(RAW/'ack-loss-recovery.json',probe('ack-retry',pending));print('ack-loss-recovery PASS',flush=True)
    execute('restart-recovery',[sys.executable,'demo/finance/recovery_acceptance.py','--group-rounds','3'],env=env)
    execute('finance-end-to-end',[sys.executable,'demo/finance/acceptance.py','--outage'],env=env)
    browser_env={**env,'NODE_PATH':r'C:\Users\13789\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules'}
    execute('browser-financial',['node','demo/finance/browser-test.cjs'],env=browser_env)
    for p in (ROOT/'demo/finance/evidence').iterdir():
        if p.is_file() and p.name not in ('python-tests.xml','finance-unit.xml','provenance.json','测试结果与报告对应.md','original-chat-smoke.json'):
            shutil.copy2(p,RAW/p.name)
    version={}
    for name,args in {'python':[sys.executable,'--version'],'node':['node','--version'],'npm':['npm.cmd','--version'],
                      'docker':['docker','version','--format','{{json .}}']}.items():
        version[name]=subprocess.check_output(args).decode('utf-8',errors='replace').strip()
    host=json.loads(subprocess.check_output(['powershell','-NoProfile','-Command',
        "[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); $cpu=Get-CimInstance Win32_Processor | Select-Object -First 1; $os=Get-CimInstance Win32_OperatingSystem; [pscustomobject]@{cpu=$cpu.Name;cores=$cpu.NumberOfCores;logical_processors=$cpu.NumberOfLogicalProcessors;memory_gib=[math]::Round($os.TotalVisibleMemorySize/1MB,2);os=$os.Caption;build=$os.BuildNumber} | ConvertTo-Json"]).decode('utf-8-sig'))
    save(OUT/'01_测试环境'/'environment.json',{'host':host,'versions':version,
        'git_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT).decode().strip(),
        'branch':subprocess.check_output(['git','branch','--show-current'],cwd=ROOT).decode().strip(),
        'source_dirty':bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT).strip())})
    save(RAW/'acceptance-complete.json',{'passed':True,'steps':STEPS,'network_isolation':True,'message_modes':True,'ack_loss':True})
    print('Complete acceptance PASS',flush=True)


if __name__=='__main__':main()
