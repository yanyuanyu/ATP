"""Shared JSONL trace writer for the travel scenario.

Each ATP event (send, recv, queue transition, phase boundary) is one JSON
line on stdout. scripts/scenario.sh appends agent stdout to a single
``traces/<run_id>.jsonl`` file so the whole scenario produces one
replayable trace.

Schema (all fields optional except ts/run_id/phase/event):

    {"ts": "2026-07-14T13:42:00.123Z",
     "run_id": "run-20260714-134200",
     "phase": "search" | "subscribe" | "book" | "ack" | "setup",
     "event": "phase_start" | "phase_end" | "send" | "recv" | "queue_state" | "container" | "note",
     "nonce": "msg-abc123",
     "from": "travel@family.test",
     "to": "search@hotel.test",
     "subject": "search",
     "body": "Paris 2 nights",
     "status": "accepted" | "queued" | "delivered" | "failed",
     "retry_count": 0,
     "narrative": "travel asks for Paris hotels"}

All non-trace output (register stderr, agent info logs) goes to stderr
so scenario.sh can capture stdout cleanly.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any


def _iso_ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def emit(
    *,
    phase: str,
    event: str,
    nonce: str | None = None,
    **fields: Any,
) -> None:
    """Emit one trace event to stdout as a single JSON line.

    Reads ``ATP_RUN_ID`` from the environment; defaults to ``run-unknown``.
    Reads ``ATP_TRACE_PHASE`` as a fallback for ``phase`` if not given.
    """
    run_id = os.environ.get("ATP_RUN_ID", "run-unknown")
    record: dict[str, Any] = {
        "ts": _iso_ts(),
        "run_id": run_id,
        "phase": phase,
        "event": event,
    }
    if nonce is not None:
        record["nonce"] = nonce
    for k, v in fields.items():
        if v is not None:
            record[k] = v
    # One JSON object per line, no trailing whitespace, flush immediately.
    sys.stdout.write(json.dumps(record, ensure_ascii=False, sort_keys=False) + "\n")
    sys.stdout.flush()


def log(msg: str) -> None:
    """Human-readable log line to stderr — does not pollute the trace."""
    print(msg, file=sys.stderr, flush=True)


def phase_start(phase: str, narrative: str) -> None:
    emit(phase=phase, event="phase_start", narrative=narrative)


def phase_end(phase: str, narrative: str | None = None) -> None:
    emit(phase=phase, event="phase_end", narrative=narrative)


def send_event(
    *,
    phase: str,
    nonce: str,
    from_id: str,
    to_id: str,
    subject: str | None,
    body: str,
    status: str,
    narrative: str | None = None,
) -> None:
    emit(
        phase=phase,
        event="send",
        nonce=nonce,
        **{"from": from_id, "to": to_id, "subject": subject, "body": body, "status": status, "narrative": narrative},
    )


def recv_event(
    *,
    phase: str,
    nonce: str,
    from_id: str,
    to_id: str,
    subject: str | None,
    body: str,
    narrative: str | None = None,
) -> None:
    emit(
        phase=phase,
        event="recv",
        nonce=nonce,
        **{"from": from_id, "to": to_id, "subject": subject, "body": body, "narrative": narrative},
    )


def note(phase: str, message: str, **fields: Any) -> None:
    emit(phase=phase, event="note", narrative=message, **fields)
