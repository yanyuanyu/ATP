"""Long-running rates broker on hotel.test for the travel scenario.

Handles ``subject="subscribe"`` requests by emitting 3 deterministic
price-change events back to the requester (cross-domain, hotel → family
via reverse server-to-server delivery). This is the event/subscription
semantic in the scenario narrative: travel subscribes to ParisGarden
price changes, rates pushes 3 events.

Deterministic: same input always produces the same 3 events with the
same prices ($180 → $170 → $190) at the same 1-second intervals. This
makes the trace reproducible across runs.
"""

import asyncio
import os
import sys

# /agents is mounted readonly in the container; trace.py lives next to this file.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from atp.client import ATPClient  # noqa: E402

import trace  # noqa: E402


# Fixed price ladder — same every run so the trace is reproducible.
PRICE_LADDER = [
    ("ParisGarden", 180),
    ("ParisGarden", 170),
    ("ParisGarden", 190),
]


async def main() -> None:
    local_part = os.environ.get("ATP_LOCAL_PART", "rates")
    server = os.environ.get("ATP_SERVER", "server-hotel.hotel.test:7443")
    password = os.environ.get("ATP_PASSWORD", "ratespass")
    agent_id = f"{local_part}@hotel.test"

    client = ATPClient(
        agent_id=agent_id,
        server=server,
        password=password,
        no_verify=True,
    )
    trace.log(f"[rates] started agent_id={agent_id} server={server}")

    try:
        while True:
            try:
                messages = await client.recv(wait=True, timeout=10)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                trace.log(f"[rates] recv error: {exc}")
                await asyncio.sleep(2)
                continue

            for msg in messages:
                subject = msg.payload.get("subject", "")
                body = msg.payload.get("body", "")
                trace.log(f"[rates] recv nonce={msg.nonce} from={msg.from_id} subject={subject!r} body={body!r}")

                if subject == "subscribe":
                    # Push 3 deterministic price-change events back to subscriber.
                    trace.emit(
                        phase="subscribe",
                        event="subscribe_received",
                        nonce=msg.nonce,
                        **{"from": msg.from_id, "to": msg.to_id, "subject": subject, "body": body},
                        narrative=f"rates received subscribe from {msg.from_id}",
                    )
                    for hotel, price in PRICE_LADDER:
                        event_body = f"{hotel} ${price}/night"
                        result = await client.send(
                            to=msg.from_id,
                            subject="price-change",
                            body=event_body,
                        )
                        trace.emit(
                            phase="subscribe",
                            event="price_push",
                            nonce=result.get("nonce"),
                            **{"from": agent_id, "to": msg.from_id, "subject": "price-change", "body": event_body, "status": result.get("status")},
                            narrative=f"rates pushed price-change {event_body}",
                        )
                        await asyncio.sleep(1.0)
                else:
                    # Unrecognized subject — reply with a neutral ack so the
                    # sender isn't left hanging.
                    try:
                        await client.send(to=msg.from_id, body=f"rates ack: {body}")
                    except Exception as exc:
                        trace.log(f"[rates] send error: {exc} (nonce={msg.nonce})")
    except KeyboardInterrupt:
        trace.log("[rates] interrupted, shutting down")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
