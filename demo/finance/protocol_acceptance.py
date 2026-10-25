"""Collect reproducible protocol/crypto/ATS evidence for the Jinmi stack."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).parent/"evidence"


def main():
    OUT.mkdir(exist_ok=True)
    for operation,name in (("network","protocol-network.json"),("benchmark","crypto-performance.json")):
        p=subprocess.run(["docker","exec","jinmi-server-family","python","/agents/acceptance_probe.py",operation],capture_output=True,check=True)
        data=json.loads(p.stdout)
        (OUT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
        print(operation+" passed",flush=True)
    env={**os.environ,"ATP_ACCEPTANCE_PREFIX":"jinmi","ATP_ACCEPTANCE_NETWORK":"atp-jinmi-ats-check",
         "ATP_ACCEPTANCE_CA":str(Path(__file__).parent/"runtime/certs/ca.crt"),
         "ATP_ACCEPTANCE_REPORT":str(OUT/"ats.json")}
    subprocess.run([sys.executable,str(ROOT/"demo/docker/scripts/acceptance-ats.py")],env=env,check=True)


if __name__=="__main__":
    main()
