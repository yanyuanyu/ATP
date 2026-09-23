"""Minimal AF_PACKET probe for the local ATP Docker demo.

The probe runs inside an ATP server container through ``docker exec`` and
emits low-volume JSONL evidence. It intentionally records only DNS traffic
from ATP servers and cross-domain TCP/7443 traffic; payloads are never stored.
TLS records are classified by their public record header only.
"""

from __future__ import annotations

import json
import socket
import struct
import sys
import time
from datetime import datetime, timezone


SERVER_IPS = {
    "172.28.1.1": "server-family",
    "172.28.2.1": "server-hotel",
    "172.28.3.1": "server-payment",
}
NODE_IPS = {**SERVER_IPS, "172.28.0.10": "dns"}
NODE_IPS.update({
    "172.28.2.101": "search@hotel.test",
    "172.28.2.103": "rates@hotel.test",
    "172.28.3.101": "bill@payment.test",
    "172.28.4.1": "travel@family.test",
    "172.28.1.105": "travel@family.test",
    "172.28.1.106": "travel@family.test",
})
QTYPE_NAMES = {1: "A", 16: "TXT", 28: "AAAA", 64: "SVCB", 65: "HTTPS"}


def iso_ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def emit(sensor: str, evidence: str, **fields: object) -> None:
    record = {"ts": iso_ts(), "sensor": sensor, "evidence": evidence, **fields}
    sys.stdout.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def parse_dns_question(payload: bytes) -> tuple[str, int] | None:
    if len(payload) < 17:
        return None
    flags = struct.unpack("!H", payload[2:4])[0]
    questions = struct.unpack("!H", payload[4:6])[0]
    if flags & 0x8000 or questions < 1:
        return None
    labels: list[str] = []
    offset = 12
    while offset < len(payload):
        length = payload[offset]
        offset += 1
        if length == 0:
            break
        if length & 0xC0 or offset + length > len(payload):
            return None
        labels.append(payload[offset:offset + length].decode("ascii", errors="replace"))
        offset += length
    if offset + 4 > len(payload):
        return None
    qtype = struct.unpack("!H", payload[offset:offset + 2])[0]
    return ".".join(labels).lower(), qtype


def dns_evidence(qname: str, qtype: int) -> str:
    if qname.startswith("ats._atp.") and qtype == 16:
        return "ats_lookup"
    if ".atk._atp." in qname and qtype == 16:
        return "atk_lookup"
    if qname.startswith("_atp.") and qtype in {64, 65}:
        return "svcb_lookup"
    return "dns_query"


def node_for(ip: str, peer: str) -> str:
    if ip in NODE_IPS:
        return NODE_IPS[ip]
    peer_server = SERVER_IPS.get(peer)
    if peer_server == "server-family":
        return "travel@family.test"
    if peer_server == "server-hotel":
        return "search@hotel.test"
    if peer_server == "server-payment":
        return "bill@payment.test"
    return ip


def parse_ipv4(packet: bytes) -> tuple[str, str, int, int] | None:
    if len(packet) < 34 or struct.unpack("!H", packet[12:14])[0] != 0x0800:
        return None
    ip_offset = 14
    ihl = (packet[ip_offset] & 0x0F) * 4
    if ihl < 20 or len(packet) < ip_offset + ihl:
        return None
    protocol = packet[ip_offset + 9]
    src = socket.inet_ntoa(packet[ip_offset + 12:ip_offset + 16])
    dst = socket.inet_ntoa(packet[ip_offset + 16:ip_offset + 20])
    return src, dst, protocol, ip_offset + ihl


def main() -> int:
    sensor = sys.argv[1] if len(sys.argv) > 1 else "server-unknown"
    duration = float(sys.argv[2]) if len(sys.argv) > 2 else 75.0
    sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.ntohs(0x0003))
    sock.bind(("eth0", 0))
    sock.settimeout(0.5)
    deadline = time.monotonic() + duration
    recently_emitted: dict[str, float] = {}

    def allowed(key: str, interval: float) -> bool:
        now = time.monotonic()
        if now - recently_emitted.get(key, 0.0) < interval:
            return False
        recently_emitted[key] = now
        return True

    emit(sensor, "probe_ready", interface="eth0", capture="AF_PACKET")
    try:
        while time.monotonic() < deadline:
            try:
                packet = sock.recv(65535)
            except TimeoutError:
                continue
            parsed = parse_ipv4(packet)
            if not parsed:
                continue
            src, dst, protocol, offset = parsed

            if protocol == socket.IPPROTO_UDP and len(packet) >= offset + 8:
                src_port, dst_port, udp_length = struct.unpack("!HHH", packet[offset:offset + 6])
                if dst_port != 53 or src not in SERVER_IPS or dst != "172.28.0.10":
                    continue
                question = parse_dns_question(packet[offset + 8:offset + max(8, udp_length)])
                if not question:
                    continue
                qname, qtype = question
                evidence = dns_evidence(qname, qtype)
                key = f"{evidence}:{src}:{qname}:{qtype}"
                if allowed(key, 0.35):
                    emit(
                        sensor,
                        evidence,
                        protocol="udp",
                        src=src,
                        dst=dst,
                        src_node=NODE_IPS.get(src, src),
                        dst_node="dns",
                        src_port=src_port,
                        dst_port=dst_port,
                        scope="cross_domain",
                        qname=qname,
                        qtype=QTYPE_NAMES.get(qtype, str(qtype)),
                        bytes=max(0, udp_length - 8),
                    )
                continue

            if protocol != socket.IPPROTO_TCP or len(packet) < offset + 20:
                continue
            src_port, dst_port = struct.unpack("!HH", packet[offset:offset + 4])
            if 7443 not in {src_port, dst_port} or not ({src, dst} & set(SERVER_IPS)):
                continue
            tcp_header_length = ((packet[offset + 12] >> 4) & 0x0F) * 4
            if tcp_header_length < 20 or len(packet) < offset + tcp_header_length:
                continue
            flags = packet[offset + 13]
            payload = packet[offset + tcp_header_length:]
            common = {
                "protocol": "tcp",
                "src": src,
                "dst": dst,
                "src_node": node_for(src, dst),
                "dst_node": node_for(dst, src),
                "src_port": src_port,
                "dst_port": dst_port,
                "scope": "cross_domain" if src in SERVER_IPS and dst in SERVER_IPS else "edge_agent",
            }
            flow = f"{src}:{src_port}>{dst}:{dst_port}"
            if flags & 0x02 and not flags & 0x10 and allowed(f"syn:{flow}", 0.8):
                emit(sensor, "tcp_connect", tcp_flags="SYN", bytes=0, **common)
            if flags & 0x04 and allowed(f"rst:{flow}", 0.8):
                emit(sensor, "tcp_reset", tcp_flags="RST", bytes=0, **common)
            if len(payload) < 5 or payload[1] != 0x03:
                continue
            content_type = payload[0]
            if content_type == 22:
                evidence = "tls_handshake"
                interval = 0.45
            elif content_type == 23:
                evidence = "tls_appdata"
                interval = 2.5 if common["scope"] == "edge_agent" else 0.65
            else:
                continue
            key = f"{evidence}:{flow}"
            if allowed(key, interval):
                emit(
                    sensor,
                    evidence,
                    tls_record_type=content_type,
                    tls_legacy_version=f"{payload[1]}.{payload[2]}",
                    bytes=len(payload),
                    **common,
                )
    finally:
        sock.close()
    emit(sensor, "probe_complete", interface="eth0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
