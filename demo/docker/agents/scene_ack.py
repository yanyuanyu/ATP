"""Phase 3 ack receiver — long-polls travel@family for bill's ack.

Run inside the ``scene-ack`` compose service AFTER scenario.sh has
restarted payment + bill and forced the family delivery manager to
retry. Long-polls for up to 60s; exits 0 on ack received, 1 on timeout.

The ack message from bill@payment has body ``bill ack: <original booking body>``
and is delivered to travel@family.test's mailbox via cross-domain
payment → family transfer.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from atp.client import ATPClient  # noqa: E402

import trace  # noqa: E402


async def main() -> int:
    server_family = os.environ.get("ATP_SERVER_FAMILY", "server-family.family.test:7443")
    travel_pass = os.environ.get("ATP_TRAVEL_PASS", "travelpass")
    deadline_s = float(os.environ.get("ATP_ACK_DEADLINE", "60"))

    client = ATPClient(
        agent_id="travel@family.test",
        server=server_family,
        password=travel_pass,
        no_verify=True,
    )

    trace.phase_start("ack", narrative="travel waits for bill's booking ack (cross-domain payment→family)")

    received = 0
    try:
        loop = asyncio.get_event_loop()
        end = loop.time() + deadline_s
        while loop.time() < end:
            remaining = max(1.0, end - loop.time())
            messages = await client.recv(wait=True, timeout=min(10.0, remaining))
            for msg in messages:
                subject = msg.payload.get("subject")
                body = msg.payload.get("body", "")
                # A fresh ATPClient starts with no receive cursor, so it also
                # sees the earlier search and rate messages from this run.
                # Only the payment-domain reply is the Phase 3 acknowledgement.
                if msg.from_id == "bill@payment.test" and "bill ack" in body:
                    trace.recv_event(
                        phase="ack",
                        nonce=msg.nonce,
                        from_id=msg.from_id,
                        to_id=msg.to_id,
                        subject=subject,
                        body=body,
                        narrative=f"ack received from {msg.from_id}: {body!r}",
                    )
                    received += 1
                    break
            if received:
                break
    finally:
        await client.close()

    if received >= 1:
        trace.phase_end("ack", narrative=f"phase 3 complete ({received} ack received)")
        trace.note("setup", "scenario complete: travel got bill's ack")
        return 0
    trace.phase_end("ack", narrative=f"phase 3 timeout ({received} ack received)")
    trace.note("setup", f"scenario timeout: {received} ack received")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
