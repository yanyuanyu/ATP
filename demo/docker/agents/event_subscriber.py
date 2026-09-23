"""Event subscriber for IETF 126 Hackathon — long-polls and prints events.

Demonstrates the event/subscription semantic: this agent registers as
``feed@hotel.test`` and long-polls for incoming messages tagged with
``subject == "event"``. Exits after receiving ``ATP_EVENT_COUNT`` events
or on timeout — whichever comes first.

Exit code 0 = all expected events received; 1 = timeout before all events.
"""

import asyncio
import os
import subprocess
import sys

from atp.client import ATPClient


async def main() -> int:
    server_hotel = os.environ.get("ATP_SERVER", "server-hotel.hotel.test:7443")
    feed_pass = os.environ.get("ATP_PASSWORD", "feedpass")
    expected = int(os.environ.get("ATP_EVENT_COUNT", "5"))
    deadline = float(os.environ.get("ATP_DEADLINE", "60.0"))

    reg = subprocess.run(
        [
            "atp", "agent", "register", "feed",
            "--server", server_hotel,
            "-p", feed_pass,
            "--no-verify",
        ],
        capture_output=True,
        text=True,
    )
    if reg.returncode != 0:
        print(
            f"[event-sub] register feed returned {reg.returncode}: "
            f"{reg.stderr.strip()}",
            file=sys.stderr,
            flush=True,
        )

    client = ATPClient(
        agent_id="feed@hotel.test",
        server=server_hotel,
        password=feed_pass,
        no_verify=True,
    )

    received = 0
    try:
        loop = asyncio.get_event_loop()
        end = loop.time() + deadline
        while received < expected and loop.time() < end:
            remaining = max(1.0, end - loop.time())
            messages = await client.recv(wait=True, timeout=min(10.0, remaining))
            for msg in messages:
                subject = msg.payload.get("subject", "")
                body = msg.payload.get("body", "")
                print(
                    f"[event-sub] recv nonce={msg.nonce} from={msg.from_id} "
                    f"subject={subject!r} body={body!r}",
                    flush=True,
                )
                received += 1
    finally:
        await client.close()

    if received >= expected:
        print(f"[event-sub] ok ({received}/{expected} events received)", flush=True)
        return 0
    print(
        f"[event-sub] timeout ({received}/{expected} events received)",
        file=sys.stderr,
        flush=True,
    )
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
