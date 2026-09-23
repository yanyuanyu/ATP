"""JSONL bridge between the TypeScript Pi runtime and the Python ATP SDK.

The Pi process owns the Agent loop and typed tools.  This adapter owns the
network client so models never construct ATP envelopes, URLs, signatures, or
CLI commands.  Requests and responses are newline-delimited JSON on stdio;
diagnostics are written to stderr only.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from typing import Any

from atp.client import ATPClient


def _write(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _register(local_part: str, server: str, password: str) -> None:
    result = subprocess.run(
        [
            "atp",
            "agent",
            "register",
            local_part,
            "--server",
            server,
            "-p",
            password,
            "--no-verify",
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    if result.returncode != 0:
        print(
            f"[pi-adapter] register returned {result.returncode}: {result.stderr.strip()}",
            file=sys.stderr,
            flush=True,
        )


def _serialize_message(message: Any) -> dict[str, Any]:
    payload = dict(message.payload or {})
    return {
        "nonce": message.nonce,
        "from": message.from_id,
        "to": message.to_id,
        "subject": payload.get("subject", ""),
        "body": payload.get("body", ""),
        "task_id": payload.get("task_id"),
        "context_id": payload.get("context_id"),
        "in_reply_to": payload.get("in_reply_to"),
    }


async def main() -> None:
    agent_id = os.environ.get("ATP_AGENT_ID", "travel@family.test")
    local_part, _, domain = agent_id.partition("@")
    if not local_part or not domain:
        raise RuntimeError(f"invalid ATP_AGENT_ID: {agent_id!r}")
    server = os.environ.get("ATP_SERVER", f"server-{domain.split('.')[0]}.{domain}:7443")
    password = os.environ.get("ATP_PASSWORD", "travelpass")

    await asyncio.to_thread(_register, local_part, server, password)
    client = ATPClient(agent_id=agent_id, server=server, password=password, no_verify=True)
    seen_nonces: set[str] = set()
    _write({"type": "adapter_ready", "agent_id": agent_id, "server": server})

    loop = asyncio.get_running_loop()
    try:
        while True:
            raw = await loop.run_in_executor(None, sys.stdin.readline)
            if not raw:
                break
            request: Any = None
            try:
                request = json.loads(raw)
                request_id = str(request["id"])
                method = request.get("method")
                params = request.get("params") or {}
                if method == "send":
                    payload = {
                        key: params[key]
                        for key in ("subject", "body", "task_id", "context_id", "in_reply_to")
                        if params.get(key) is not None
                    }
                    result = await client.send(
                        to=str(params["to"]),
                        payload=payload,
                    )
                    _write({"id": request_id, "ok": True, "result": result})
                elif method == "recv":
                    timeout = max(1, min(20, int(params.get("timeout", 8))))
                    messages = await client.recv(wait=True, timeout=timeout)
                    unseen = []
                    for message in messages:
                        if message.nonce in seen_nonces:
                            continue
                        seen_nonces.add(message.nonce)
                        unseen.append(_serialize_message(message))
                    _write({"id": request_id, "ok": True, "result": unseen})
                elif method == "ping":
                    _write({"id": request_id, "ok": True, "result": {"agent_id": agent_id}})
                else:
                    raise ValueError(f"unsupported adapter method: {method!r}")
            except Exception as exc:  # Keep the bridge alive after an individual tool failure.
                _write({
                    "id": str(request.get("id", "unknown")) if isinstance(request, dict) else "unknown",
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                })
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
