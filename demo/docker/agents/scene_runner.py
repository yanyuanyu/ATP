"""Phase 1 + 2 + 3-send of the travel scenario.

Run inside the ``scene-runner`` compose service. Assumes payment server
is already down (the scenario.sh orchestrator stops it before running
this scene). Writes JSONL trace events to stdout; everything else to
stderr.

Phase 1 — search (request/response):
    travel@family → search@hotel  "Paris 2 nights"
    search@hotel  → travel@family  "echo: Paris 2 nights"  (search agent is an echo)

Phase 2 — subscribe (event/subscription):
    travel@family → rates@hotel   "hotel=ParisGarden"
    rates@hotel   → travel@family  3 × price-change events ($180, $170, $190)

Phase 3 — book+pay (async + offline queue, payment offline):
    travel@family → bill@payment  "ParisGarden 2 nights $190"
    Message queues on family (payment unreachable). Exit without waiting
    for the ack — scene_ack.py picks that up after payment comes back.

Exit codes: 0 = all phases sent successfully; 1 = at least one send failed.
"""

import asyncio
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from atp.client import ATPClient  # noqa: E402

import trace  # noqa: E402


def register_travel(server_family: str, travel_pass: str) -> None:
    """Idempotent register travel@family.test — ignore 409 (already registered)."""
    reg = subprocess.run(
        ["atp", "agent", "register", "travel",
         "--server", server_family,
         "-p", travel_pass, "--no-verify"],
        capture_output=True, text=True,
    )
    if reg.returncode != 0:
        trace.log(f"[scene] register travel returned {reg.returncode}: {reg.stderr.strip()}")


async def phase_search(client: ATPClient) -> bool:
    trace.phase_start("search", narrative="travel searches for a hotel in Paris")
    body = "Paris 2 nights"
    result = await client.send(
        to="search@hotel.test",
        subject="search",
        body=body,
    )
    status = result.get("status")
    trace.send_event(
        phase="search",
        nonce=result.get("nonce", ""),
        from_id="travel@family.test",
        to_id="search@hotel.test",
        subject="search",
        body=body,
        status=str(status),
        narrative="travel asks search for Paris hotels",
    )
    if status != "accepted":
        trace.log(f"[scene] phase 1 send failed: {result}")
        trace.phase_end("search", narrative="phase 1 send failed")
        return False

    # Long-poll for the reply (search agent echoes back).
    messages = await client.recv(wait=True, timeout=20.0)
    if not messages:
        trace.log("[scene] phase 1 no reply within 20s")
        trace.phase_end("search", narrative="phase 1 no reply")
        return False
    msg = messages[0]
    reply_body = msg.payload.get("body", "")
    reply_subject = msg.payload.get("subject")
    trace.recv_event(
        phase="search",
        nonce=msg.nonce,
        from_id=msg.from_id,
        to_id=msg.to_id,
        subject=reply_subject,
        body=reply_body,
        narrative=f"search replied: {reply_body!r}",
    )
    trace.phase_end("search", narrative="phase 1 complete")
    return True


async def phase_subscribe(client: ATPClient) -> bool:
    trace.phase_start("subscribe", narrative="travel subscribes to ParisGarden price changes")
    body = "hotel=ParisGarden"
    result = await client.send(
        to="rates@hotel.test",
        subject="subscribe",
        body=body,
    )
    status = result.get("status")
    trace.send_event(
        phase="subscribe",
        nonce=result.get("nonce", ""),
        from_id="travel@family.test",
        to_id="rates@hotel.test",
        subject="subscribe",
        body=body,
        status=str(status),
        narrative="travel subscribes to ParisGarden rates",
    )
    if status != "accepted":
        trace.log(f"[scene] phase 2 subscribe failed: {result}")
        trace.phase_end("subscribe", narrative="phase 2 send failed")
        return False

    # Long-poll for 3 price-change events from rates@hotel.
    expected = 3
    received = 0
    deadline = asyncio.get_event_loop().time() + 30.0
    while received < expected and asyncio.get_event_loop().time() < deadline:
        remaining = max(1.0, deadline - asyncio.get_event_loop().time())
        messages = await client.recv(wait=True, timeout=min(10.0, remaining))
        for msg in messages:
            subject = msg.payload.get("subject")
            body = msg.payload.get("body", "")
            trace.recv_event(
                phase="subscribe",
                nonce=msg.nonce,
                from_id=msg.from_id,
                to_id=msg.to_id,
                subject=subject,
                body=body,
                narrative=f"price-change received: {body!r}",
            )
            received += 1
    trace.note("subscribe", f"received {received}/{expected} price-change events",
               received=received, expected=expected)
    trace.phase_end("subscribe", narrative=f"phase 2 complete ({received}/{expected} events)")
    return received >= expected


async def phase_book_send(client: ATPClient) -> bool:
    trace.phase_start("book", narrative="travel books ParisGarden and pays (payment offline)")
    body = "ParisGarden 2 nights $190"
    result = await client.send(
        to="bill@payment.test",
        subject="book+pay",
        body=body,
    )
    status = result.get("status")
    trace.send_event(
        phase="book",
        nonce=result.get("nonce", ""),
        from_id="travel@family.test",
        to_id="bill@payment.test",
        subject="book+pay",
        body=body,
        status=str(status),
        narrative="travel sends booking+payment; payment is offline, message queues",
    )
    ok = status == "accepted"
    trace.phase_end("book", narrative="phase 3 send complete (queued on family)" if ok else "phase 3 send failed")
    return ok


async def main() -> int:
    server_family = os.environ.get("ATP_SERVER_FAMILY", "server-family.family.test:7443")
    travel_pass = os.environ.get("ATP_TRAVEL_PASS", "travelpass")

    register_travel(server_family, travel_pass)

    client = ATPClient(
        agent_id="travel@family.test",
        server=server_family,
        password=travel_pass,
        no_verify=True,
    )

    results = {"search": False, "subscribe": False, "book": False}
    try:
        results["search"] = await phase_search(client)
        results["subscribe"] = await phase_subscribe(client)
        results["book"] = await phase_book_send(client)
    finally:
        await client.close()

    summary = ", ".join(f"{k}={'ok' if v else 'fail'}" for k, v in results.items())
    trace.note("setup", f"scene_runner complete: {summary}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
