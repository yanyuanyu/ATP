"""Event publisher for IETF 126 Hackathon — sends N events to a subscriber.

Demonstrates the event/subscription semantic: a producer emits a stream
of typed events to a subscriber agent on another domain, using the same
ATP message primitive. The subscriber (event_subscriber.py) long-polls
and prints each event as it arrives.

Exits after sending all events. A non-zero exit code indicates send failure.
"""

import asyncio
import os
import subprocess
import sys

from atp.client import ATPClient


async def main() -> int:
    server_family = os.environ.get("ATP_SERVER_FAMILY", "server-family.family.test:7443")
    news_pass = os.environ.get("ATP_NEWS_PASS", "newspass")
    target = os.environ.get("ATP_EVENT_TARGET", "feed@hotel.test")
    event_count = int(os.environ.get("ATP_EVENT_COUNT", "5"))
    interval = float(os.environ.get("ATP_EVENT_INTERVAL", "2.0"))

    reg = subprocess.run(
        [
            "atp", "agent", "register", "news",
            "--server", server_family,
            "-p", news_pass,
            "--no-verify",
        ],
        capture_output=True,
        text=True,
    )
    if reg.returncode != 0:
        print(
            f"[event-pub] register news returned {reg.returncode}: "
            f"{reg.stderr.strip()}",
            file=sys.stderr,
            flush=True,
        )

    client = ATPClient(
        agent_id="news@family.test",
        server=server_family,
        password=news_pass,
        no_verify=True,
    )

    sent = 0
    try:
        for i in range(1, event_count + 1):
            body = f"event-{i}: topic=demo payload={i}"
            result = await client.send(
                to=target,
                subject="event",
                body=body,
            )
            if result.get("status") != "accepted":
                print(
                    f"[event-pub] send #{i} failed: {result}",
                    file=sys.stderr,
                    flush=True,
                )
                return 1
            print(
                f"[event-pub] sent #{i} nonce={result.get('nonce')} "
                f"to={target} body={body!r}",
                flush=True,
            )
            sent += 1
            if i < event_count:
                await asyncio.sleep(interval)
    finally:
        await client.close()

    print(f"[event-pub] ok ({sent}/{event_count} events sent)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
