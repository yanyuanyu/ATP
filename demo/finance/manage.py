"""Isolated Windows/Linux launcher. Never touches the original contest checkout.

Usage: python demo/finance/manage.py start|stop|status|build
"""
import argparse
import base64
import datetime as dt
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import time

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from atp.security.sm2 import SM2PrivateKey

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "demo/docker"
RUNTIME = ROOT / "demo/finance/runtime"
CONFIG = RUNTIME / "compose.json"


def mailbox_passwords(root):
    """Keep existing account credentials valid; isolate roles on new installs."""
    legacy = root / "mailbox-password"
    passwords = {}
    for role in ("buyer", "merchant", "payment"):
        path = root / ("mailbox-password-" + role)
        if not path.exists():
            # Existing ATP accounts retain their old password. Changing just the
            # client secret would lock them out; migration requires server update.
            path.write_text(legacy.read_text() if legacy.exists() else secrets.token_urlsafe(32))
        passwords[role] = path.read_text()
    if len(set(passwords.values())) != 3:
        print("注意：沿用旧部署共享口令以兼容已有账户；尚未完成角色凭据隔离。")
    return passwords


def run(*args, capture=False):
    p = subprocess.run([str(a) for a in args], cwd=ROOT, check=True, capture_output=capture)
    return p.stdout.decode("utf-8").strip() if capture else ""


def tls_certificates(target):
    target.mkdir(parents=True, exist_ok=True)
    if (target / "ca.crt").exists():
        return
    ca = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ATP Jinmi Local Demo CA")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(ca.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now-dt.timedelta(minutes=5))
            .not_valid_after(now+dt.timedelta(days=365)).add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
            .sign(ca, hashes.SHA256()))
    (target/"ca.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    for domain in ("family", "hotel", "payment"):
        key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"{domain}.test")])
        leaf = (x509.CertificateBuilder().subject_name(subject).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now-dt.timedelta(minutes=5))
                .not_valid_after(now+dt.timedelta(days=365))
                .add_extension(x509.SubjectAlternativeName([x509.DNSName(f"{domain}.test"), x509.DNSName(f"server-{domain}.{domain}.test")]), False)
                .sign(ca, hashes.SHA256()))
        (target/f"{domain}.test.crt").write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
        (target/f"{domain}.test.key").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))


def initialize():
    RUNTIME.mkdir(parents=True, exist_ok=True)
    keys = RUNTIME/"keys"
    keys.mkdir(exist_ok=True)
    if not (keys/"merchant.private").exists():
        if (keys/"merchant.public").exists():
            raise RuntimeError("Merchant key pair incomplete; restore its private key instead of rotating silently")
        key = SM2PrivateKey.generate()
        (keys/"merchant.private").write_text(key.private_key_hex)
        (keys/"merchant.public").write_text(key.public_key_hex)
    SM2PrivateKey((keys/"merchant.private").read_text(), (keys/"merchant.public").read_text())
    passwords = mailbox_passwords(RUNTIME)
    certs = RUNTIME/"certs"
    tls_certificates(certs)
    if not (certs/"tlcp-ca.crt").exists():
        # Generate as the same unprivileged UID used by the gateway, so its
        # mode-0600 private keys remain readable without making them public.
        run("docker", "run", "--rm", "-v", f"{certs}:/certs", "atp-tlcp-gateway:latest", "certgen", "/certs")
    bind = RUNTIME/"bind"
    if not bind.exists():
        shutil.copytree(BASE/"bind", bind)
        for file in (bind/"zones").glob("*.zone"):
            file.write_text(file.read_text().replace("172.28.", "172.32."), encoding="utf-8")
    config = json.loads(run("docker", "compose", "-f", BASE/"docker-compose.yml", "config", "--format", "json", capture=True))
    # Keep the working model demo, but do not require external model credentials
    # for financial business/security acceptance.
    services = config["services"]
    for name, service in services.items():
        service["container_name"] = "jinmi-"+name
        image = service.get("image")
        if image == "atp-hackthon:latest": service["image"] = "atp-jinmi-core:local"
        if image == "atp-hackthon-demo:latest": service["image"] = "atp-jinmi-demo:local"
        for volume in service.get("volumes", []):
            source = volume.get("source", "")
            if volume.get("type") == "bind":
                if "/certs" in source.replace("\\", "/"):
                    volume["source"] = str(certs / Path(source).name) if Path(source).suffix else str(certs)
                elif "/bind" in source.replace("\\", "/"):
                    tail = Path(source).relative_to(BASE/"bind")
                    volume["source"] = str(bind/tail)
    services["demo-controller"]["ports"][0]["published"] = "18088"
    env = services["demo-controller"]["environment"]
    env.update(ATP_FINANCE_ENABLED="1", ATP_FINANCE_DATA="/finance", ATP_MERCHANT_PUBLIC="/finance-keys/merchant.public",
               ATP_FINANCE_PASSWORD=passwords["buyer"], ATP_FINANCE_SERVER="server-family.family.test:7443")
    env["ATP_PACKET_IP_PREFIX"] = "172.32."
    services["demo-controller"]["volumes"].extend([
        {"type":"bind","source":str(keys/"merchant.public"),"target":"/finance-keys/merchant.public","read_only":True},
        {"type":"volume","source":"finance-user-data","target":"/finance"}])
    for role, domain, number in (("merchant","hotel",2),("payment","payment",3)):
        services["finance-"+role] = {
            "image":"atp-jinmi-core:local", "container_name":"jinmi-finance-"+role,
            "networks":{domain:{"ipv4_address":f"172.32.{number}.107"}}, "dns":[f"172.32.{number}.10"],
            "environment":{"ATP_FINANCE_ROLE":role,"ATP_FINANCE_SERVER":f"server-{domain}.{domain}.test:7443",
                           "ATP_FINANCE_PASSWORD":passwords[role],"ATP_FINANCE_DATA":"/finance",
                           "ATP_MERCHANT_PUBLIC":"/finance-keys/merchant.public", "ATP_MERCHANT_PRIVATE":"/finance-keys/merchant.private",
                           "SSL_CERT_FILE":"/etc/ssl/certs/ca-certificates.crt"},
            "volumes":[{"type":"bind","source":str(certs/"ca.crt"),"target":"/usr/local/share/ca-certificates/atp-ca.crt","read_only":True},
                       {"type":"bind","source":str(keys/"merchant.public"),"target":"/finance-keys/merchant.public","read_only":True},
                       {"type":"volume","source":"finance-"+role+"-data","target":"/finance"}],
            "command":["sh","-c","update-ca-certificates >/dev/null 2>&1; exec python -m atp.finance.worker"],
            "depends_on":{f"server-{domain}":{"condition":"service_started"}}}
        if role == "merchant":
            services["finance-"+role]["volumes"].append({"type":"bind","source":str(keys/"merchant.private"),"target":"/finance-keys/merchant.private","read_only":True})
    config["name"] = "atp-jinmi"
    for network in config["networks"].values():
        network.pop("name", None)
    for name in ("finance-user-data","finance-merchant-data","finance-payment-data"):
        config["volumes"][name] = {}
    for volume in config["volumes"].values():
        volume.pop("name", None)
    # The controller's existing Docker-backed controls must refer to this stack.
    controller = (BASE/"demo/controller.py").read_text(encoding="utf-8").replace('"hack-', '"jinmi-')
    (RUNTIME/"controller.py").write_text(controller, encoding="utf-8")
    services["demo-controller"]["volumes"].append({"type":"bind","source":str(RUNTIME/"controller.py"),"target":"/app/demo/controller.py","read_only":True})
    config = json.loads(json.dumps(config).replace("172.28.", "172.32."))
    CONFIG.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def compose(*args, capture=False):
    return run("docker", "compose", "-p", "atp-jinmi", "-f", CONFIG, *args, capture=capture)


def build():
    if subprocess.run(["docker","image","inspect","atp-hackthon:latest"],capture_output=True).returncode:
        run("docker","build","-f",BASE/"Dockerfile","-t","atp-hackthon:latest",ROOT)
    if subprocess.run(["docker","image","inspect","atp-hackthon-demo:latest"],capture_output=True).returncode:
        run("docker","build","-f",BASE/"demo/Dockerfile","-t","atp-hackthon-demo:latest",BASE)
    if subprocess.run(["docker","image","inspect","atp-tlcp-gateway:latest"],capture_output=True).returncode:
        run("docker","build","-t","atp-tlcp-gateway:latest",BASE/"tlcp-gateway")
    for target in ("core","controller"):
        tag = "core" if target == "core" else "demo"
        run("docker","build","-f",ROOT/"demo/finance/Dockerfile","--target",target,"-t",f"atp-jinmi-{tag}:local", ROOT)


def start():
    initialize()
    compose("up","-d")
    for domain in ("family","hotel","payment"):
        deadline=time.monotonic()+90
        while True:
            p=subprocess.run(["docker","exec",f"jinmi-server-{domain}","curl","-fsS",f"https://server-{domain}.{domain}.test:7443/.well-known/atp/v1/health"],capture_output=True)
            if p.returncode==0: break
            if time.monotonic()>deadline: raise RuntimeError("ATP health timeout: "+domain)
            time.sleep(1)
        output=compose("exec","-T",f"server-{domain}","atp","keys","show","--selector","default","--algorithm","sm2","--public",capture=True)
        public=next(line.split()[-1] for line in output.splitlines() if "Public key" in line)
        zone=RUNTIME/f"bind/zones/{domain}.test.zone"
        lines=zone.read_text().splitlines()
        zone.write_text("\n".join(f'default.atk._atp IN TXT "v=atp1 k=sm2 p={public}"' if line.startswith("default.atk._atp") else line for line in lines)+"\n",encoding="utf-8")
    compose("restart","dns")
    # Return only after the restarted DNS serves discovery and signing keys.
    # This also makes an immediately following acceptance run reliable.
    dns_probe = (
        "import dns.resolver; "
        "[(dns.resolver.resolve('_atp.'+d+'.test','SVCB'), "
        "dns.resolver.resolve('default.atk._atp.'+d+'.test','TXT')) "
        "for d in ('family','hotel','payment')]"
    )
    deadline = time.monotonic() + 60
    while True:
        probe = subprocess.run(
            ["docker", "exec", "jinmi-server-family", "python", "-c", dns_probe],
            capture_output=True, timeout=20,
        )
        if probe.returncode == 0:
            break
        if time.monotonic() > deadline:
            raise RuntimeError("DNS discovery and signing-key records did not become ready")
        time.sleep(1)
    print("\n金融演示：http://127.0.0.1:18088/\n停止服务保留订单、账本和身份密钥。")


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("action",choices=["build","start","stop","status"])
    action=parser.parse_args().action
    if action=="build": build()
    elif action=="start": start()
    elif action=="stop": compose("stop")
    else: compose("ps")
