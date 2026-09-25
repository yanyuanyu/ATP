"""Docker integration helper for SM4 payload protection over TLCP."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import time
from pathlib import Path

from atp.client import ATPClient


AUDIT_AGENT = "crypto-audit@hotel.test"
AUDIT_PASSWORD = "auditpass"
MARKER_PREFIX = "atp-sm4-tlcp-audit-"


async def send() -> int:
    marker = f"{MARKER_PREFIX}{time.time_ns()}"
    client = ATPClient(
        agent_id="travel@family.test",
        server="server-family.family.test:7443",
        password="travelpass",
        no_verify=True,
    )
    try:
        result = await client.send(
            to=AUDIT_AGENT,
            payload={"subject": "crypto-audit", "body": marker},
        )
    finally:
        await client.close()
    if result.get("status") != "accepted":
        raise RuntimeError(f"send failed: {result}")
    print(f"NONCE={result['nonce']}")
    print(f"MARKER={marker}")
    return 0


def inspect_stored(nonce: str, marker: str) -> int:
    database = Path.home() / ".atp" / "data" / "messages.db"
    deadline = time.monotonic() + 30
    raw = ""
    while time.monotonic() < deadline:
        with sqlite3.connect(database) as connection:
            row = connection.execute(
                "SELECT message_json FROM messages WHERE nonce = ?", (nonce,)
            ).fetchone()
        if row:
            raw = row[0]
            break
        time.sleep(1)
    if not raw:
        raise RuntimeError(f"remote message {nonce} did not arrive")
    message = json.loads(raw)
    payload = message.get("payload", {})
    if payload.get("_atp_encrypted") is not True:
        raise RuntimeError("remote database payload is not an encrypted envelope")
    if payload.get("algorithm") != "sm4-cbc-sm3" or payload.get("key_wrap") != "sm2":
        raise RuntimeError(f"unexpected encrypted envelope: {payload}")
    if marker in raw:
        raise RuntimeError("plaintext marker leaked into the remote ATP database")
    print("PASS: remote database contains SM4 ciphertext and an SM2-wrapped session key")
    return 0


async def receive(nonce: str, marker: str) -> int:
    client = ATPClient(
        agent_id=AUDIT_AGENT,
        server="server-hotel.hotel.test:7443",
        password=AUDIT_PASSWORD,
        no_verify=True,
    )
    try:
        messages = await client.recv(limit=100)
    finally:
        await client.close()
    message = next((item for item in messages if item.nonce == nonce), None)
    if message is None:
        raise RuntimeError(f"decrypted message {nonce} was not returned to recipient")
    if message.payload.get("body") != marker:
        raise RuntimeError(f"recipient plaintext mismatch: {message.payload}")
    print("PASS: recipient ATP server authenticated and decrypted the original payload")
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit("usage: check_sm4_tlcp.py send|inspect|receive [nonce marker]")
    action = sys.argv[1]
    if action == "send":
        return asyncio.run(send())
    if len(sys.argv) != 4:
        raise SystemExit(f"{action} requires nonce and marker")
    if action == "inspect":
        return inspect_stored(sys.argv[2], sys.argv[3])
    if action == "receive":
        return asyncio.run(receive(sys.argv[2], sys.argv[3]))
    raise SystemExit(f"unknown action: {action}")


if __name__ == "__main__":
    raise SystemExit(main())
