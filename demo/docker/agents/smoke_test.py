"""One-shot smoke test for IETF 126 Hackathon Docker env."""

import asyncio
import os
import subprocess
import sys

from atp.client import ATPClient


async def main() -> int:
    server_family = os.environ.get("ATP_SERVER_FAMILY", "server-family.family.test:7443")
    travel_pass = os.environ.get("ATP_TRAVEL_PASS", "travelpass")

    reg = subprocess.run(
        [
            "atp", "agent", "register", "travel",
            "--server", server_family,
            "-p", travel_pass,
            "--no-verify",
        ],
        capture_output=True,
        text=True,
    )
    if reg.returncode != 0:
        print(
            f"[smoke] register travel returned {reg.returncode}: "
            f"{reg.stderr.strip()}",
            file=sys.stderr,
            flush=True,
        )

    client = ATPClient(
        agent_id="travel@family.test",
        server=server_family,
        password=travel_pass,
        no_verify=True,
    )

    received_count = 0
    try:
        result = await client.send(to="search@hotel.test", body="ping from travel")
        if result.get("status") != "accepted":
            print(f"[smoke] send failed: {result}", file=sys.stderr, flush=True)
            return 1
        print(
            f"[smoke] sent nonce={result.get('nonce')} "
            f"timestamp={result.get('timestamp')}",
            flush=True,
        )

        messages = await client.recv(wait=True, timeout=30.0)
        for msg in messages:
            body = msg.payload.get("body", "")
            print(
                f"[smoke] recv nonce={msg.nonce} from={msg.from_id} "
                f"to={msg.to_id} body={body!r}",
                flush=True,
            )
            received_count += 1
    finally:
        await client.close()

    if received_count >= 1:
        print(f"[smoke] ok ({received_count} reply/replies)", flush=True)
        return 0
    print("[smoke] no reply received within timeout", file=sys.stderr, flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
