"""Long-running bill agent on payment.test for offline-queue demo.

Echoes back an acknowledgement to the sender so the demo can confirm
end-to-end delivery once the offline queue drains.
"""

import asyncio
import os
import sys

from atp.client import ATPClient


async def main() -> None:
    local_part = os.environ.get("ATP_LOCAL_PART", "bill")
    server = os.environ.get("ATP_SERVER", "server-payment.payment.test:7443")
    password = os.environ.get("ATP_PASSWORD", "billpass")
    agent_id = f"{local_part}@payment.test"

    client = ATPClient(
        agent_id=agent_id,
        server=server,
        password=password,
        no_verify=True,
    )
    print(f"[bill] started agent_id={agent_id} server={server}", flush=True)

    try:
        while True:
            try:
                messages = await client.recv(wait=True, timeout=10)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                print(f"[bill] recv error: {exc}", file=sys.stderr, flush=True)
                await asyncio.sleep(2)
                continue

            for msg in messages:
                body = msg.payload.get("body", "")
                print(
                    f"[bill] recv nonce={msg.nonce} from={msg.from_id} "
                    f"to={msg.to_id} body={body!r}",
                    flush=True,
                )
                try:
                    await client.send(to=msg.from_id, body=f"bill ack: {body}")
                except Exception as exc:
                    print(
                        f"[bill] send error: {exc} (nonce={msg.nonce})",
                        file=sys.stderr,
                        flush=True,
                    )
    except KeyboardInterrupt:
        print("[bill] interrupted, shutting down", flush=True)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
