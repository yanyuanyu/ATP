"""One-shot offline-queue demo sender.

Sends a single ATP message from ``travel@family.test`` to
``bill@payment.test`` and exits immediately without waiting for a
reply. Used in conjunction with ``scripts/demo-offline-queue.sh``
which stops the payment server before this sender runs, so the
message sits in family's cross-domain retry queue until payment
comes back up.
"""

import asyncio
import os
import subprocess
import sys

from atp.client import ATPClient


async def main() -> int:
    server_family = os.environ.get("ATP_SERVER_FAMILY", "server-family.family.test:7443")
    travel_pass = os.environ.get("ATP_TRAVEL_PASS", "travelpass")
    target = os.environ.get("ATP_OFFLINE_TARGET", "bill@payment.test")
    body = os.environ.get("ATP_OFFLINE_BODY", "ping from travel while payment is down")

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
            f"[offline] register travel returned {reg.returncode}: "
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

    try:
        result = await client.send(to=target, body=body)
        if result.get("status") != "accepted":
            print(f"[offline] send failed: {result}", file=sys.stderr, flush=True)
            return 1
        print(
            f"[offline] sent nonce={result.get('nonce')} "
            f"timestamp={result.get('timestamp')} to={target} body={body!r}",
            flush=True,
        )
        print(
            f"[offline] message queued on family server; "
            f"family delivery manager will retry until payment is reachable.",
            flush=True,
        )
    finally:
        await client.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
