"""Export source, tests and measured evidence; omit local secrets and runtimes."""
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

ROOT=Path(__file__).resolve().parents[2]
OUTPUT=ROOT.parent/"ATP-金密杯源码与测试支撑材料.zip"


def main():
    files=subprocess.run(["git","ls-files","--cached","--others","--exclude-standard","-z"],cwd=ROOT,capture_output=True,check=True).stdout.decode('utf-8').split('\0')
    manifest=[]
    with zipfile.ZipFile(OUTPUT,'w',zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(set(filter(None,files))):
            relative=Path(name)
            if any(p in {'.git','.venv','node_modules','__pycache__','runtime','traces','certs','secrets'} for p in relative.parts): continue
            if relative.name.startswith('.env') and relative.name!='.env.example': continue
            if relative.suffix in {'.key','.pem','.pfx','.p12','.db','.sqlite','.sqlite3','.log','.pyc'}: continue
            file=ROOT/relative
            if not file.is_file(): continue
            data=file.read_bytes()
            archive.writestr('ATP/'+relative.as_posix(),data)
            manifest.append({'path':relative.as_posix(),'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
        archive.writestr('ATP/SOURCE_MANIFEST.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    with zipfile.ZipFile(OUTPUT) as archive:
        assert archive.testzip() is None
        assert not any('/runtime/' in n or '/.venv/' in n or n.endswith('/.env') or n.endswith('.key') for n in archive.namelist())
        assert 'ATP/src/atp/finance/ledger.py' in archive.namelist()
        assert 'ATP/demo/finance/evidence/python-tests.xml' in archive.namelist()
    print(json.dumps({'archive':str(OUTPUT),'files':len(manifest),'bytes':OUTPUT.stat().st_size},ensure_ascii=False))


if __name__=='__main__': main()
