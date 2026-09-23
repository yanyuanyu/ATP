"""ATP Hackathon local Demo Controller.

The controller serves the visualization, collects ordered JSONL events,
streams them over SSE, exposes Docker-backed controls, and runs an LLM travel
agent without requiring a terminal.  The model chooses ATP tool calls while
the controller supplies the local payment outage/recovery conditions.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import docker
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.routing import Route
from starlette.staticfiles import StaticFiles


TRACES_DIR = Path(os.environ.get("ATP_TRACES_DIR", "/app/traces"))
AGENTS_DIR = Path(os.environ.get("ATP_AGENTS_DIR", "/agents"))
PACKETS_DIR = TRACES_DIR / "packets"
PACKET_PROBE_SOURCE = Path(__file__).with_name("packet_probe.py")
TRACES_DIR.mkdir(parents=True, exist_ok=True)
PACKETS_DIR.mkdir(parents=True, exist_ok=True)

CONTAINERS = {
    "dns": "hack-dns",
    "server-family": "hack-server-family",
    "server-hotel": "hack-server-hotel",
    "server-payment": "hack-server-payment",
    "agent-search": "hack-agent-search",
    "agent-rates": "hack-agent-rates",
    "agent-bill": "hack-agent-bill",
    "demo-controller": "hack-demo-controller",
}

DB_PATH = "/root/.atp/data/messages.db"
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

_docker_client: docker.DockerClient | None = None
_trace_lock = asyncio.Lock()
_packet_lock = asyncio.Lock()
_scenario_task: asyncio.Task[None] | None = None
_packet_tasks: dict[tuple[str, str], asyncio.Task[None]] = {}
_packet_dedupe: dict[str, float] = {}
_packet_capture_state: dict[str, Any] = {
    "run_id": None,
    "status": "idle",
    "count": 0,
    "sensors": {},
    "signals": [],
    "started_at": None,
    "last_packet_at": None,
}
_chat_task: asyncio.Task[None] | None = None
_chat_state: dict[str, Any] = {
    "session_id": str(uuid.uuid4()),
    "status": "idle",
    "run_id": None,
    "active_request_id": None,
    "messages": [],
    "activities": [],
    "agent_audit": [],
    "runtime": {
        "name": "Pi",
        "version": "0.80.6",
        "model": os.environ.get("LLM_API_MODEL", "qwen3.7-plus"),
        "status": "stopped",
    },
    "error": None,
    "updated_at": None,
}
_scenario_state: dict[str, Any] = {
    "status": "idle",
    "run_id": None,
    "step": "idle",
    "step_index": 0,
    "step_total": 7,
    "message": "Ready",
    "started_at": None,
    "ended_at": None,
    "error": None,
    "agent_model": None,
    "agent_provider": None,
}


def _docker() -> docker.DockerClient:
    global _docker_client
    if _docker_client is None:
        _docker_client = docker.from_env()
    return _docker_client


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _safe_run_id(run_id: str) -> bool:
    return bool(RUN_ID_RE.fullmatch(run_id))


def _trace_path(run_id: str) -> Path:
    return TRACES_DIR / f"{run_id}.jsonl"


def _packet_trace_path(run_id: str) -> Path:
    return PACKETS_DIR / f"{run_id}.jsonl"


def _next_run_id() -> str:
    base = datetime.now().strftime("run-%Y%m%d-%H%M%S")
    run_id = base
    suffix = 1
    while _trace_path(run_id).exists() or _packet_trace_path(run_id).exists():
        suffix += 1
        run_id = f"{base}-{suffix}"
    return run_id


def _read_packet_signals(run_id: str) -> list[dict[str, Any]]:
    path = _packet_trace_path(run_id)
    if not path.exists():
        return []
    signals: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                signal = json.loads(line)
            except json.JSONDecodeError:
                continue
            signal.setdefault("pseq", line_number)
            signals.append(signal)
    return signals


def _packet_summary(signals: list[dict[str, Any]]) -> dict[str, Any]:
    evidence_types = sorted({str(item.get("evidence")) for item in signals if item.get("evidence")})
    cross_domain = [item for item in signals if item.get("scope") == "cross_domain"]
    cross_types = sorted({str(item.get("evidence")) for item in cross_domain if item.get("evidence")})
    edge_types = sorted({
        str(item.get("evidence")) for item in signals
        if item.get("scope") == "edge_agent" and item.get("evidence")
    })
    queries = list(dict.fromkeys(
        str(item["qname"]) for item in signals if item.get("qname")
    ))[-12:]
    return {
        "types_seen": evidence_types,
        "cross_domain_types_seen": cross_types,
        "edge_types_seen": edge_types,
        "queries": queries,
        "agent_edge_observed": any(item in edge_types for item in ("tcp_connect", "tls_handshake", "tls_appdata")),
        "dns_observed": any(item in cross_types for item in ("dns_query", "svcb_lookup")),
        "svcb_observed": "svcb_lookup" in cross_types,
        "tcp_observed": "tcp_connect" in cross_types,
        "tls_observed": any(item in cross_types for item in ("tls_handshake", "tls_appdata")),
        "ats_lookup_observed": "ats_lookup" in cross_types,
        "atk_lookup_observed": "atk_lookup" in cross_types,
        "encrypted_data_observed": "tls_appdata" in cross_types,
    }


async def _packet_payload(run_id: str) -> dict[str, Any]:
    async with _packet_lock:
        if _packet_capture_state.get("run_id") == run_id:
            signals = list(_packet_capture_state.get("signals", []))
            sensors = dict(_packet_capture_state.get("sensors", {}))
            status = str(_packet_capture_state.get("status", "idle"))
            started_at = _packet_capture_state.get("started_at")
            last_packet_at = _packet_capture_state.get("last_packet_at")
        else:
            signals = await asyncio.to_thread(_read_packet_signals, run_id)
            sensors = {}
            status = "replay" if signals else "unavailable"
            started_at = signals[0].get("ts") if signals else None
            last_packet_at = signals[-1].get("ts") if signals else None
    return {
        "run_id": run_id,
        "status": status,
        "source": "AF_PACKET",
        "primary_evidence": True,
        "count": len(signals),
        "sensors": sensors,
        "started_at": started_at,
        "last_packet_at": last_packet_at,
        "summary": _packet_summary(signals),
        "signals": signals[-500:],
    }


async def _set_packet_sensor_state(run_id: str, service: str, status: str, error: str | None = None) -> None:
    async with _packet_lock:
        if _packet_capture_state.get("run_id") != run_id:
            return
        sensor = {"status": status}
        if error:
            sensor["error"] = error
        _packet_capture_state.setdefault("sensors", {})[service] = sensor
        statuses = {item.get("status") for item in _packet_capture_state["sensors"].values()}
        if statuses & {"starting", "capturing"}:
            _packet_capture_state["status"] = "capturing"
        elif statuses and statuses <= {"complete", "unavailable"}:
            _packet_capture_state["status"] = "complete"


async def _record_packet_signal(run_id: str, signal: dict[str, Any]) -> None:
    global _packet_dedupe
    evidence = str(signal.get("evidence", "packet"))
    dedupe_key = "|".join(str(signal.get(field, "")) for field in (
        "evidence", "src", "dst", "src_port", "dst_port", "qname", "qtype"
    ))
    now = time.monotonic()
    dedupe_window = 0.18 if evidence not in {"probe_ready", "probe_complete"} else 0.0
    if dedupe_window and now - _packet_dedupe.get(dedupe_key, 0.0) < dedupe_window:
        return
    _packet_dedupe[dedupe_key] = now
    if len(_packet_dedupe) > 600:
        _packet_dedupe = {key: value for key, value in _packet_dedupe.items() if now - value < 90}

    async with _packet_lock:
        if _packet_capture_state.get("run_id") != run_id:
            return
        record = dict(signal)
        record["run_id"] = run_id
        record["pseq"] = int(_packet_capture_state.get("count", 0)) + 1
        _packet_capture_state["count"] = record["pseq"]
        _packet_capture_state["last_packet_at"] = record.get("ts", _ts())
        _packet_capture_state.setdefault("signals", []).append(record)
        path = _packet_trace_path(run_id)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _capture_packets_sync(service: str, run_id: str, loop: asyncio.AbstractEventLoop, duration: int) -> None:
    def submit(coro) -> None:
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        future.result(timeout=8)

    container = _container(service)
    if container is None:
        submit(_set_packet_sensor_state(run_id, service, "unavailable", "container not found"))
        return
    try:
        container.reload()
        if container.status != "running":
            submit(_set_packet_sensor_state(run_id, service, "unavailable", f"container {container.status}"))
            return
        source = PACKET_PROBE_SOURCE.read_text(encoding="utf-8")
        result = container.exec_run(
            ["python3", "-u", "-c", source, service, str(duration)],
            stream=True,
            demux=False,
        )
        submit(_set_packet_sensor_state(run_id, service, "capturing"))
        pending = ""
        for chunk in result.output:
            if not chunk:
                continue
            pending += chunk.decode("utf-8", errors="replace") if isinstance(chunk, bytes) else str(chunk)
            while "\n" in pending:
                line, pending = pending.split("\n", 1)
                if not line.strip():
                    continue
                try:
                    signal = json.loads(line)
                except json.JSONDecodeError:
                    continue
                submit(_record_packet_signal(run_id, signal))
        submit(_set_packet_sensor_state(run_id, service, "complete"))
    except Exception as exc:
        submit(_set_packet_sensor_state(run_id, service, "unavailable", str(exc)))


async def _start_packet_probe(run_id: str, service: str, duration: int = 75) -> None:
    key = (run_id, service)
    existing = _packet_tasks.get(key)
    if existing and not existing.done():
        return
    await _set_packet_sensor_state(run_id, service, "starting")
    loop = asyncio.get_running_loop()
    _packet_tasks[key] = asyncio.create_task(
        asyncio.to_thread(_capture_packets_sync, service, run_id, loop, duration),
        name=f"packet-probe-{run_id}-{service}",
    )


async def _prepare_packet_capture(run_id: str) -> None:
    global _packet_dedupe
    _packet_dedupe = {}
    path = _packet_trace_path(run_id)
    path.write_text("", encoding="utf-8")
    async with _packet_lock:
        _packet_capture_state.clear()
        _packet_capture_state.update({
            "run_id": run_id,
            "status": "starting",
            "count": 0,
            "sensors": {},
            "signals": [],
            "started_at": _ts(),
            "last_packet_at": None,
        })
    await asyncio.gather(
        _start_packet_probe(run_id, "server-family"),
        _start_packet_probe(run_id, "server-hotel"),
    )
    await asyncio.sleep(0.5)


def _container(name: str):
    container_name = CONTAINERS.get(name)
    if not container_name:
        return None
    try:
        return _docker().containers.get(container_name)
    except Exception:
        return None


def _exec_in(service: str, cmd: list[str]) -> tuple[int, str]:
    container = _container(service)
    if container is None:
        return 1, f"container for service '{service}' not found"
    try:
        result = container.exec_run(cmd, demux=False)
    except Exception as exc:
        return 1, str(exc)
    output = result.output.decode("utf-8", errors="replace") if isinstance(result.output, bytes) else str(result.output)
    return result.exit_code, output


def _container_action(services: tuple[str, ...], action: str) -> tuple[list[str], list[dict[str, str]]]:
    changed: list[str] = []
    errors: list[dict[str, str]] = []
    for service in services:
        container = _container(service)
        if container is None:
            errors.append({"service": service, "error": "container not found"})
            continue
        try:
            container.reload()
            if action == "start" and container.status != "running":
                container.start()
            elif action == "stop" and container.status == "running":
                container.stop(timeout=2)
            changed.append(service)
        except Exception as exc:
            errors.append({"service": service, "error": str(exc)})
    return changed, errors


def _soft_reset_sync() -> list[dict[str, Any]]:
    results = []
    for service in ("server-family", "server-hotel", "server-payment"):
        rc, output = _exec_in(service, [
            "python3", "-c",
            "import sqlite3; conn = sqlite3.connect('" + DB_PATH + "'); "
            "conn.execute('DELETE FROM messages'); conn.commit(); "
            "print(conn.execute('SELECT COUNT(*) FROM messages').fetchone()[0])",
        ])
        results.append({"service": service, "rc": rc, "rows_remaining": output.strip()})
    return results


def _force_retry_sync() -> tuple[int, str]:
    return _exec_in("server-family", [
        "python3", "-c",
        "import sqlite3; conn = sqlite3.connect('" + DB_PATH + "'); "
        "cur = conn.execute(\"UPDATE messages SET next_retry_at=0 "
        "WHERE status='failed' AND next_retry_at IS NOT NULL\"); conn.commit(); "
        "print(f'reset {cur.rowcount} failed message(s)')",
    ])


def _queue_state_sync() -> dict[str, list[dict[str, Any]]]:
    state: dict[str, list[dict[str, Any]]] = {}
    for service in ("server-family", "server-hotel", "server-payment"):
        rc, output = _exec_in(service, [
            "python3", "-c",
            "import sqlite3, json; conn = sqlite3.connect('" + DB_PATH + "'); "
            "rows = list(conn.execute('SELECT nonce, status, retry_count, next_retry_at, from_id, to_id, error FROM messages ORDER BY id')); "
            "print(json.dumps([{'nonce': r[0], 'status': r[1], 'retry_count': r[2], "
            "'next_retry_at': r[3], 'from': r[4], 'to': r[5], 'error': r[6] or ''} for r in rows]))",
        ])
        if rc != 0:
            state[service] = []
            continue
        try:
            state[service] = json.loads(output.strip())
        except json.JSONDecodeError:
            state[service] = []
    return state


def _container_states_sync() -> dict[str, str]:
    states: dict[str, str] = {}
    for service in CONTAINERS:
        container = _container(service)
        if container is None:
            states[service] = "missing"
            continue
        try:
            container.reload()
            states[service] = container.status
        except Exception:
            states[service] = "unknown"
    return states


def _read_events(run_id: str) -> list[dict[str, Any]]:
    path = _trace_path(run_id)
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            event.setdefault("seq", line_number)
            events.append(event)
    return events


async def _append_event(run_id: str, event: dict[str, Any]) -> dict[str, Any]:
    """Append one event with a stable monotonic sequence number."""
    async with _trace_lock:
        path = _trace_path(run_id)
        line_count = 0
        if path.exists():
            with path.open(encoding="utf-8") as handle:
                line_count = sum(1 for _ in handle)
        record = dict(event)
        record["run_id"] = run_id
        record.setdefault("ts", _ts())
        record["seq"] = line_count + 1
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record


async def _emit(run_id: str, phase: str, event: str, **fields: Any) -> dict[str, Any]:
    return await _append_event(run_id, {"phase": phase, "event": event, **fields})


def _set_scenario_state(**changes: Any) -> None:
    _scenario_state.update(changes)


def _llm_runtime() -> dict[str, Any]:
    base = os.environ.get("LLM_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
    path = os.environ.get("LLM_API_PATH", "/chat/completions")
    key_file = os.environ.get("LLM_API_KEY_FILE")
    configured = bool(os.environ.get("LLM_API_KEY") or os.environ.get("DASHSCOPE_API_KEY"))
    if not configured and key_file:
        configured = Path(key_file).is_file() and bool(Path(key_file).read_text(encoding="utf-8").strip())
    return {
        "base": base,
        "path": path,
        "model": os.environ.get("LLM_API_MODEL", "qwen3.7-plus"),
        "provider": urlsplit(base).netloc or "openai-compatible",
        "configured": configured,
    }


def _chat_message(message_id: str) -> dict[str, Any] | None:
    for message in _chat_state["messages"]:
        if message.get("id") == message_id:
            return message
    return None


def _append_agent_audit(
    run_id: str,
    agent_id: str,
    role: str,
    kind: str,
    title: str,
    data: Any = None,
    *,
    request_id: str | None = None,
    ts: str | None = None,
) -> None:
    """Keep application-level Pi audit records separate from packet evidence."""
    record = {
        "id": str(uuid.uuid4()),
        "run_id": run_id,
        "agent_id": agent_id,
        "role": role,
        "kind": kind,
        "title": title,
        "data": data,
        "request_id": request_id,
        "ts": ts or _ts(),
    }
    _chat_state["agent_audit"].append(record)
    # A persistent conversation needs a bounded history. The rendered view is
    # for a live demo, not a general-purpose message archive.
    _chat_state["agent_audit"] = _chat_state["agent_audit"][-320:]
    _chat_state["updated_at"] = _ts()


def _service_agent_log_events_sync(service: str, since: float) -> list[dict[str, Any]]:
    container = _container(service)
    if container is None:
        return []
    try:
        raw = container.logs(stdout=True, stderr=False, timestamps=False, since=max(0, int(since) - 1))
    except Exception:
        return []
    events: list[dict[str, Any]] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("role") in {"search", "rates", "bill"} and event.get("agent_id"):
            events.append(event)
    return events


def _record_service_audit_event(run_id: str, event: dict[str, Any]) -> None:
    role = str(event.get("role") or "service")
    agent_id = str(event.get("agent_id") or role)
    request_id = str(event.get("request_id") or "") or None
    ts = str(event.get("ts") or _ts())
    if event.get("type") == "agent_event":
        event_name = event.get("event")
        if event_name == "atp_input":
            _append_agent_audit(run_id, agent_id, role, "atp_input", "ATP input", event.get("message") or {}, request_id=request_id, ts=ts)
        elif event_name == "tool_start":
            _append_agent_audit(run_id, agent_id, role, "tool_input", str(event.get("tool") or "tool"), event.get("args") or {}, request_id=request_id, ts=ts)
        elif event_name == "tool_end":
            _append_agent_audit(run_id, agent_id, role, "tool_output", str(event.get("tool") or "tool"), {
                "output": event.get("output") or {},
                "is_error": bool(event.get("is_error")),
            }, request_id=request_id, ts=ts)
    elif event.get("type") == "prompt_result":
        _append_agent_audit(run_id, agent_id, role, "response", "Agent response", {
            "status": event.get("status"),
            "text": event.get("text") or event.get("error") or "",
        }, request_id=request_id, ts=ts)


async def _watch_service_agent_audit(run_id: str, since: float, stop: asyncio.Event) -> None:
    """Tail structured stdout from the three independent Pi service sessions."""
    seen: set[str] = set()

    async def sweep() -> None:
        result_sets = await asyncio.gather(*(
            asyncio.to_thread(_service_agent_log_events_sync, service, since)
            for service in ("agent-search", "agent-rates", "agent-bill")
        ))
        for events in result_sets:
            for event in events:
                key = "|".join(str(event.get(field, "")) for field in ("ts", "agent_id", "type", "event", "request_id", "tool"))
                if not key or key in seen:
                    continue
                seen.add(key)
                _record_service_audit_event(run_id, event)

    while not stop.is_set():
        await sweep()
        try:
            await asyncio.wait_for(stop.wait(), timeout=0.65)
        except asyncio.TimeoutError:
            pass
    await sweep()


class PiTravelRuntime:
    """Persistent Pi Agent process used by the browser conversation."""

    def __init__(self) -> None:
        self.process: asyncio.subprocess.Process | None = None
        self.ready = asyncio.Event()
        self.start_lock = asyncio.Lock()
        self.pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self.stdout_task: asyncio.Task[None] | None = None
        self.stderr_task: asyncio.Task[None] | None = None
        self.stderr_tail: list[str] = []

    async def ensure_started(self) -> None:
        async with self.start_lock:
            if self.process and self.process.returncode is None and self.ready.is_set():
                return
            await self.close()
            runtime_path = Path(os.environ.get("ATP_PI_RUNTIME", "/opt/atp-pi/runtime.mjs"))
            if not runtime_path.is_file():
                raise RuntimeError(f"Pi runtime not found: {runtime_path}")
            env = dict(os.environ)
            env.update({
                "PI_AGENT_ROLE": "travel",
                "ATP_AGENT_ID": "travel@family.test",
                "ATP_SERVER": os.environ.get("ATP_SERVER_FAMILY", "server-family.family.test:7443"),
                "ATP_PASSWORD": os.environ.get("ATP_TRAVEL_PASS", "travelpass"),
                "ATP_PI_ADAPTER": os.environ.get("ATP_PI_ADAPTER", "/agents/pi_adapter.py"),
            })
            self.ready.clear()
            self.stderr_tail = []
            self.process = await asyncio.create_subprocess_exec(
                "node",
                str(runtime_path),
                "travel",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            self.stdout_task = asyncio.create_task(self._read_stdout(), name="pi-travel-stdout")
            self.stderr_task = asyncio.create_task(self._read_stderr(), name="pi-travel-stderr")
            try:
                await asyncio.wait_for(self.ready.wait(), timeout=35)
            except asyncio.TimeoutError as exc:
                tail = "\n".join(self.stderr_tail[-12:])
                await self.close()
                raise RuntimeError(f"Pi Travel Agent did not become ready: {tail}") from exc

    async def _read_stdout(self) -> None:
        assert self.process and self.process.stdout
        while True:
            raw = await self.process.stdout.readline()
            if not raw:
                break
            try:
                event = json.loads(raw.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            event_type = event.get("type")
            if event_type == "runtime_ready":
                self.ready.set()
                _chat_state["runtime"].update({
                    "status": "ready",
                    "model": event.get("model"),
                    "version": event.get("pi_version", "0.80.6"),
                })
            request_id = str(event.get("request_id") or "")
            if event_type == "agent_event" and request_id:
                if event.get("event") == "tool_start":
                    _chat_state["activities"].append({
                        "id": str(uuid.uuid4()),
                        "request_id": request_id,
                        "tool": event.get("tool"),
                        "status": "running",
                        "args": event.get("args") or {},
                        "started_at": event.get("ts", _ts()),
                        "ended_at": None,
                    })
                    if _chat_state.get("run_id"):
                        _append_agent_audit(
                            str(_chat_state["run_id"]),
                            "travel@family.test",
                            "travel",
                            "tool_input",
                            str(event.get("tool") or "tool"),
                            event.get("args") or {},
                            request_id=request_id,
                            ts=event.get("ts"),
                        )
                elif event.get("event") == "tool_end":
                    for activity in reversed(_chat_state["activities"]):
                        if activity.get("request_id") == request_id and activity.get("tool") == event.get("tool") and activity.get("status") == "running":
                            activity["status"] = "failed" if event.get("is_error") else "completed"
                            activity["ended_at"] = event.get("ts", _ts())
                            break
                    if _chat_state.get("run_id"):
                        _append_agent_audit(
                            str(_chat_state["run_id"]),
                            "travel@family.test",
                            "travel",
                            "tool_output",
                            str(event.get("tool") or "tool"),
                            {
                                "output": event.get("output") or {},
                                "is_error": bool(event.get("is_error")),
                            },
                            request_id=request_id,
                            ts=event.get("ts"),
                        )
                # Pi may produce text in intermediate tool-selection turns. Keep
                # those deltas out of the user-facing conversation and publish
                # only the final prompt_result; typed tool activity remains live.
                if event.get("event") != "text_delta":
                    _chat_state["updated_at"] = _ts()
            if event_type in {"prompt_result", "runtime_error", "runtime_fatal"} and request_id:
                future = self.pending.pop(request_id, None)
                if future and not future.done():
                    if event_type == "prompt_result" and event.get("status") == "completed":
                        future.set_result(event)
                    else:
                        future.set_exception(RuntimeError(str(event.get("error") or "Pi Agent failed")))
        error = RuntimeError("Pi Travel Agent process closed")
        for future in self.pending.values():
            if not future.done():
                future.set_exception(error)
        self.pending.clear()
        self.ready.clear()
        _chat_state["runtime"]["status"] = "stopped"

    async def _read_stderr(self) -> None:
        assert self.process and self.process.stderr
        while True:
            raw = await self.process.stderr.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").strip()
            if line:
                self.stderr_tail.append(line)
                self.stderr_tail = self.stderr_tail[-40:]

    async def prompt(self, request_id: str, prompt: str) -> dict[str, Any]:
        await self.ensure_started()
        assert self.process and self.process.stdin
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self.pending[request_id] = future
        self.process.stdin.write((json.dumps({
            "type": "prompt",
            "id": request_id,
            "prompt": prompt,
        }, ensure_ascii=False) + "\n").encode())
        await self.process.stdin.drain()
        try:
            return await asyncio.wait_for(future, timeout=180)
        finally:
            self.pending.pop(request_id, None)

    async def reset(self) -> None:
        await self.ensure_started()
        assert self.process and self.process.stdin
        self.process.stdin.write((json.dumps({"type": "reset", "id": str(uuid.uuid4())}) + "\n").encode())
        await self.process.stdin.drain()

    async def close(self) -> None:
        process = self.process
        self.process = None
        self.ready.clear()
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        for task in (self.stdout_task, self.stderr_task):
            if task and not task.done():
                task.cancel()
        self.stdout_task = None
        self.stderr_task = None


_pi_travel_runtime = PiTravelRuntime()


async def _run_chat_prompt(request_id: str, prompt: str, run_id: str) -> None:
    assistant = _chat_message(f"assistant-{request_id}")
    audit_stop = asyncio.Event()
    audit_task = asyncio.create_task(
        _watch_service_agent_audit(run_id, time.time(), audit_stop),
        name=f"pi-service-audit-{request_id}",
    )
    try:
        _chat_state.update({"status": "starting", "error": None, "updated_at": _ts()})
        await _prepare_packet_capture(run_id)
        await _start_packet_probe(run_id, "server-payment")
        await _emit(run_id, "chat", "user_message", narrative="User instructed the Pi Travel Agent")
        _chat_state.update({"status": "thinking", "updated_at": _ts()})
        result = await _pi_travel_runtime.prompt(request_id, prompt)
        if assistant is not None:
            assistant["content"] = str(result.get("text") or assistant.get("content") or "")
            assistant["status"] = "completed"
            assistant["updated_at"] = _ts()
        _append_agent_audit(
            run_id,
            "travel@family.test",
            "travel",
            "response",
            "Travel response",
            {"text": str(result.get("text") or "")},
            request_id=request_id,
        )
        await _emit(run_id, "chat", "assistant_message", narrative="Pi Travel Agent completed the user turn")
        _chat_state.update({"status": "idle", "error": None, "updated_at": _ts()})
    except Exception as exc:
        message = str(exc)
        if assistant is not None:
            assistant["content"] = assistant.get("content") or f"Agent error: {message}"
            assistant["status"] = "failed"
            assistant["updated_at"] = _ts()
        _append_agent_audit(
            run_id,
            "travel@family.test",
            "travel",
            "response",
            "Travel error",
            {"error": message},
            request_id=request_id,
        )
        await _emit(run_id, "chat", "agent_error", status="failed", narrative=message)
        _chat_state.update({"status": "failed", "error": message, "updated_at": _ts()})
    finally:
        audit_stop.set()
        await audit_task
        _chat_state["active_request_id"] = None


async def _run_agent_script(run_id: str, script_name: str) -> tuple[int, str]:
    """Run an agent process and collect its JSONL diagnostics live."""
    script_path = AGENTS_DIR / script_name
    env = dict(os.environ)
    env.update({
        "ATP_RUN_ID": run_id,
        "ATP_SERVER_FAMILY": "server-family.family.test:7443",
        "ATP_TRAVEL_PASS": "travelpass",
        "ATP_ACK_DEADLINE": "60",
    })
    process = await asyncio.create_subprocess_exec(
        "python3", str(script_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stderr_lines: list[str] = []

    async def consume_stdout() -> None:
        assert process.stdout is not None
        while True:
            raw = await process.stdout.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                stderr_lines.append(line)
                continue
            event.pop("seq", None)
            await _append_event(run_id, event)

    async def consume_stderr() -> None:
        assert process.stderr is not None
        while True:
            raw = await process.stderr.readline()
            if not raw:
                break
            stderr_lines.append(raw.decode("utf-8", errors="replace").strip())

    try:
        await asyncio.gather(consume_stdout(), consume_stderr())
        rc = await process.wait()
        return rc, "\n".join(line for line in stderr_lines if line)
    except asyncio.CancelledError:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        raise


async def _snapshot_family_queue(run_id: str) -> list[dict[str, Any]]:
    state = await asyncio.to_thread(_queue_state_sync)
    # The family DB also contains the completed search and rate traffic.
    # For the scenario timeline, keep only the booking transfer whose state
    # actually changes from failed to delivered.
    rows = [
        row for row in state.get("server-family", [])
        if row.get("from") == "travel@family.test" and row.get("to") == "bill@payment.test"
    ]
    for row in rows:
        await _emit(run_id, "book", "queue_state", **row)
    return rows


async def _run_llm_travel_agent(run_id: str) -> None:
    rc, stderr = await _run_agent_script(run_id, "travel_agent.py")
    if rc != 0:
        raise RuntimeError(f"LLM travel agent failed ({rc}): {stderr[-1600:]}")


async def _recover_payment_after_booking(run_id: str) -> None:
    """Watch real queue state, then restore payment while the Agent waits."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 90
    failed_booking: list[dict[str, Any]] = []
    while loop.time() < deadline:
        state = await asyncio.to_thread(_queue_state_sync)
        failed_booking = [
            row for row in state.get("server-family", [])
            if row.get("from") == "travel@family.test"
            and row.get("to") == "bill@payment.test"
            and row.get("status") == "failed"
        ]
        if failed_booking:
            break
        await asyncio.sleep(1)
    if not failed_booking:
        raise RuntimeError("LLM agent did not produce a failed queued booking within 90s")

    _set_scenario_state(step="queue_observed", step_index=4, message="Agent booking is queued while payment is offline")
    await _snapshot_family_queue(run_id)

    _set_scenario_state(step="payment_recovery", step_index=5, message="Restoring payment.test after observing the Agent booking")
    started, start_errors = await asyncio.to_thread(_container_action, ("server-payment",), "start")
    if start_errors:
        raise RuntimeError(f"unable to start payment server: {start_errors}")
    await asyncio.sleep(3)
    await _start_packet_probe(run_id, "server-payment", duration=55)
    bill_started, bill_errors = await asyncio.to_thread(_container_action, ("agent-bill",), "start")
    if bill_errors:
        raise RuntimeError(f"unable to start bill agent: {bill_errors}")
    await asyncio.sleep(2)
    for service in started + bill_started:
        await _emit(
            run_id,
            "setup",
            "container_start",
            target=service,
            narrative=f"{service} restored after the LLM Agent queued its booking",
        )

    _set_scenario_state(step="force_retry", step_index=6, message="Releasing the Agent's queued ATP booking")
    retry_rc, retry_output = await asyncio.to_thread(_force_retry_sync)
    if retry_rc != 0:
        raise RuntimeError(f"force retry failed: {retry_output}")
    await _emit(
        run_id,
        "setup",
        "force_retry",
        target="server-family",
        narrative=retry_output.strip(),
    )
    _set_scenario_state(step="agent_waiting_ack", step_index=7, message="LLM Agent is waiting for the payment acknowledgement")


async def _run_scenario(run_id: str) -> None:
    """Run one LLM Agent while keeping packet evidence and diagnostics live."""
    success = False
    error: str | None = None
    agent_task: asyncio.Task[None] | None = None
    recovery_task: asyncio.Task[None] | None = None
    try:
        await _prepare_packet_capture(run_id)
        _set_scenario_state(status="running", step="reset", step_index=1, message="Resetting local message stores")
        await _emit(run_id, "setup", "scenario_start", narrative="LLM travel agent scenario begins from the browser")

        # Recover a previous interrupted run before resetting its databases.
        await asyncio.to_thread(_container_action, ("server-payment", "agent-bill"), "start")
        await asyncio.sleep(2)
        reset_results = await asyncio.to_thread(_soft_reset_sync)
        if any(item["rc"] != 0 for item in reset_results):
            raise RuntimeError(f"soft reset failed: {reset_results}")
        await _emit(run_id, "setup", "soft_reset", narrative="message stores cleared; keys and agents preserved")

        _set_scenario_state(step="payment_offline", step_index=2, message="Taking payment.test offline")
        stopped, stop_errors = await asyncio.to_thread(
            _container_action, ("server-payment", "agent-bill"), "stop"
        )
        if stop_errors:
            raise RuntimeError(f"unable to stop payment services: {stop_errors}")
        for service in stopped:
            await _emit(run_id, "setup", "container_stop", target=service,
                        narrative=f"{service} offline for Phase 3")
        await asyncio.sleep(2)

        _set_scenario_state(step="agent_running", step_index=3, message="LLM Agent is planning and calling ATP tools")
        agent_task = asyncio.create_task(_run_llm_travel_agent(run_id))
        recovery_task = asyncio.create_task(_recover_payment_after_booking(run_id))
        await asyncio.gather(agent_task, recovery_task)
        await _snapshot_family_queue(run_id)
        success = True
    except asyncio.CancelledError:
        error = "scenario cancelled"
        raise
    except Exception as exc:
        error = str(exc)
        await _emit(run_id, "setup", "scenario_error", status="failed", narrative=error)
    finally:
        for task in (agent_task, recovery_task):
            if task is not None and not task.done():
                task.cancel()
        pending = [task for task in (agent_task, recovery_task) if task is not None]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        # Never leave payment down after a failed or cancelled browser run.
        await asyncio.to_thread(_container_action, ("server-payment", "agent-bill"), "start")
        await _emit(
            run_id,
            "setup",
            "scenario_end",
            status="passed" if success else "failed",
            ack_rc=0 if success else 1,
            narrative="travel scenario completed" if success else f"travel scenario failed: {error or 'unknown error'}",
        )
        _set_scenario_state(
            status="passed" if success else "failed",
            step="complete",
            step_index=7,
            message="Scenario completed" if success else "Scenario failed",
            ended_at=_ts(),
            error=error,
        )


async def index(request: Request) -> FileResponse:
    return FileResponse(Path(__file__).parent / "static" / "index.html")


async def list_runs(request: Request) -> JSONResponse:
    runs = []
    run_ids = {path.stem for path in TRACES_DIR.glob("*.jsonl")}
    run_ids.update(path.stem for path in PACKETS_DIR.glob("*.jsonl"))
    for run_id in run_ids:
        try:
            path = _trace_path(run_id)
            packet_path = _packet_trace_path(run_id)
            count = 0
            if path.exists():
                with path.open(encoding="utf-8") as handle:
                    count = sum(1 for _ in handle)
            packet_count = 0
            if packet_path.exists():
                with packet_path.open(encoding="utf-8") as handle:
                    packet_count = sum(1 for _ in handle)
            stats = [candidate.stat() for candidate in (path, packet_path) if candidate.exists()]
            runs.append({
                "run_id": run_id,
                "events": count,
                "packet_events": packet_count,
                "size_bytes": sum(item.st_size for item in stats),
                "mtime": max(item.st_mtime for item in stats),
            })
        except OSError:
            continue
    runs.sort(key=lambda item: item["mtime"], reverse=True)
    return JSONResponse({"runs": runs})


async def get_trace(request: Request) -> JSONResponse:
    run_id = request.path_params["run_id"]
    if not _safe_run_id(run_id):
        return JSONResponse({"error": "invalid run id"}, status_code=400)
    path = _trace_path(run_id)
    if not path.exists():
        return JSONResponse({"error": f"run '{run_id}' not found"}, status_code=404)
    events = await asyncio.to_thread(_read_events, run_id)
    return JSONResponse({"run_id": run_id, "events": events, "count": len(events)})


async def get_packet_evidence(request: Request) -> JSONResponse:
    run_id = request.path_params["run_id"]
    if not _safe_run_id(run_id):
        return JSONResponse({"error": "invalid run id"}, status_code=400)
    return JSONResponse(await _packet_payload(run_id))


async def ingest_event(request: Request) -> JSONResponse:
    run_id = request.path_params["run_id"]
    if not _safe_run_id(run_id):
        return JSONResponse({"error": "invalid run id"}, status_code=400)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"error": "body must be JSON"}, status_code=400)
    if not isinstance(body, dict) or not body.get("phase") or not body.get("event"):
        return JSONResponse({"error": "phase and event are required"}, status_code=400)
    body.pop("seq", None)
    record = await _append_event(run_id, body)
    return JSONResponse(record, status_code=201)


async def stream_trace(request: Request) -> StreamingResponse:
    run_id = request.path_params["run_id"]
    if not _safe_run_id(run_id):
        return StreamingResponse(iter(["event: error\ndata: {\"error\":\"invalid run id\"}\n\n"]), status_code=400)
    path = _trace_path(run_id)
    try:
        after_seq = max(0, int(request.query_params.get("after", "0")))
    except ValueError:
        after_seq = 0

    async def event_gen():
        yield f"event: ready\ndata: {json.dumps({'run_id': run_id, 'ts': _ts(), 'after': after_seq})}\n\n"
        last_pos = 0
        line_number = 0
        sent_seq = after_seq

        if path.exists():
            with path.open(encoding="utf-8") as handle:
                while True:
                    line = handle.readline()
                    if not line:
                        last_pos = handle.tell()
                        break
                    last_pos = handle.tell()
                    line_number += 1
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    seq = int(event.get("seq") or line_number)
                    event["seq"] = seq
                    if seq <= sent_seq:
                        continue
                    sent_seq = seq
                    payload = json.dumps(event, ensure_ascii=False)
                    yield f"id: {seq}\nevent: trace\ndata: {payload}\n\n"

        last_heartbeat = time.time()
        while True:
            if await request.is_disconnected():
                return
            try:
                new_size = path.stat().st_size
            except FileNotFoundError:
                await asyncio.sleep(0.25)
                continue
            if new_size < last_pos:
                last_pos = 0
                line_number = 0
                sent_seq = 0
            elif new_size > last_pos:
                with path.open(encoding="utf-8") as handle:
                    handle.seek(last_pos)
                    while True:
                        line = handle.readline()
                        if not line:
                            last_pos = handle.tell()
                            break
                        last_pos = handle.tell()
                        line_number += 1
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        seq = int(event.get("seq") or line_number)
                        event["seq"] = seq
                        if seq <= sent_seq:
                            continue
                        sent_seq = seq
                        payload = json.dumps(event, ensure_ascii=False)
                        yield f"id: {seq}\nevent: trace\ndata: {payload}\n\n"
            now = time.time()
            if now - last_heartbeat > 10:
                yield f"event: heartbeat\ndata: {json.dumps({'ts': _ts(), 'last_seq': sent_seq})}\n\n"
                last_heartbeat = now
            await asyncio.sleep(0.25)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def scenario_start(request: Request) -> JSONResponse:
    global _scenario_task
    if _scenario_task is not None and not _scenario_task.done():
        return JSONResponse({"error": "a scenario is already running", "scenario": _scenario_state}, status_code=409)

    llm = _llm_runtime()
    if not llm["configured"]:
        return JSONResponse(
            {"error": "LLM API key is not configured", "agent": llm},
            status_code=503,
        )

    run_id = _next_run_id()
    _trace_path(run_id).touch()
    _set_scenario_state(
        status="starting",
        run_id=run_id,
        step="starting",
        step_index=0,
        step_total=7,
        message="Starting scenario",
        started_at=_ts(),
        ended_at=None,
        error=None,
        agent_model=llm["model"],
        agent_provider=llm["provider"],
    )
    _scenario_task = asyncio.create_task(_run_scenario(run_id))
    return JSONResponse({"run_id": run_id, "scenario": _scenario_state}, status_code=202)


async def capture_start(request: Request) -> JSONResponse:
    if _packet_capture_state.get("status") in {"starting", "capturing"}:
        return JSONResponse({"error": "a packet capture is already running", "capture": await _packet_payload(str(_packet_capture_state["run_id"]))}, status_code=409)
    run_id = _next_run_id()
    await _prepare_packet_capture(run_id)
    await _start_packet_probe(run_id, "server-payment")
    return JSONResponse({"run_id": run_id, "capture": await _packet_payload(run_id)}, status_code=202)


async def scenario_status(request: Request) -> JSONResponse:
    return JSONResponse({"scenario": _scenario_state})


async def chat_state(request: Request) -> JSONResponse:
    return JSONResponse({"chat": _chat_state})


async def chat_message(request: Request) -> JSONResponse:
    global _chat_task
    if _chat_task is not None and not _chat_task.done():
        return JSONResponse({"error": "the Pi Travel Agent is handling another message", "chat": _chat_state}, status_code=409)
    llm = _llm_runtime()
    if not llm["configured"]:
        return JSONResponse({"error": "LLM API key is not configured", "agent": llm}, status_code=503)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"error": "body must be JSON"}, status_code=400)
    prompt = str(body.get("message") or "").strip() if isinstance(body, dict) else ""
    if not prompt:
        return JSONResponse({"error": "message is required"}, status_code=400)
    if len(prompt) > 4000:
        return JSONResponse({"error": "message is too long (maximum 4000 characters)"}, status_code=400)

    request_id = str(uuid.uuid4())
    run_id = _next_run_id()
    _trace_path(run_id).touch()
    now = _ts()
    _chat_state["messages"].extend([
        {
            "id": f"user-{request_id}",
            "request_id": request_id,
            "role": "user",
            "agent_id": "user",
            "content": prompt,
            "status": "completed",
            "created_at": now,
            "updated_at": now,
        },
        {
            "id": f"assistant-{request_id}",
            "request_id": request_id,
            "role": "assistant",
            "agent_id": "travel@family.test",
            "content": "",
            "status": "queued",
            "created_at": now,
            "updated_at": now,
        },
    ])
    _chat_state.update({
        "status": "queued",
        "run_id": run_id,
        "active_request_id": request_id,
        "error": None,
        "updated_at": now,
    })
    _append_agent_audit(
        run_id,
        "travel@family.test",
        "travel",
        "user_input",
        "User instruction",
        {"text": prompt},
        request_id=request_id,
        ts=now,
    )
    _chat_task = asyncio.create_task(_run_chat_prompt(request_id, prompt, run_id), name=f"pi-chat-{request_id}")
    return JSONResponse({"request_id": request_id, "run_id": run_id, "chat": _chat_state}, status_code=202)


async def chat_reset(request: Request) -> JSONResponse:
    global _chat_task
    if _chat_task is not None and not _chat_task.done():
        return JSONResponse({"error": "cannot reset while the Pi Travel Agent is running", "chat": _chat_state}, status_code=409)
    await _pi_travel_runtime.reset()
    _chat_state.update({
        "session_id": str(uuid.uuid4()),
        "status": "idle",
        "run_id": None,
        "active_request_id": None,
        "messages": [],
        "activities": [],
        "agent_audit": [],
        "error": None,
        "updated_at": _ts(),
    })
    return JSONResponse({"chat": _chat_state})


async def payment_stop(request: Request) -> JSONResponse:
    changed, errors = await asyncio.to_thread(_container_action, ("server-payment", "agent-bill"), "stop")
    code = 200 if not errors else 500
    return JSONResponse({"stopped": changed, "errors": errors, "ts": _ts()}, status_code=code)


async def payment_start(request: Request) -> JSONResponse:
    changed, errors = await asyncio.to_thread(_container_action, ("server-payment", "agent-bill"), "start")
    code = 200 if not errors else 500
    return JSONResponse({"started": changed, "errors": errors, "ts": _ts()}, status_code=code)


async def force_retry(request: Request) -> JSONResponse:
    rc, output = await asyncio.to_thread(_force_retry_sync)
    return JSONResponse({"rc": rc, "output": output.strip(), "ts": _ts()}, status_code=200 if rc == 0 else 500)


async def soft_reset(request: Request) -> JSONResponse:
    results = await asyncio.to_thread(_soft_reset_sync)
    code = 200 if all(item["rc"] == 0 for item in results) else 500
    return JSONResponse({"results": results, "ts": _ts()}, status_code=code)


async def queue_state(request: Request) -> JSONResponse:
    state = await asyncio.to_thread(_queue_state_sync)
    return JSONResponse({"state": state, "ts": _ts()})


async def topology_state(request: Request) -> JSONResponse:
    containers, queues = await asyncio.gather(
        asyncio.to_thread(_container_states_sync),
        asyncio.to_thread(_queue_state_sync),
    )
    queue_counts = {
        service: {
            "total": len(rows),
            "pending": sum(1 for row in rows if row.get("status") in {"queued", "failed", "delivering"}),
            "delivered": sum(1 for row in rows if row.get("status") == "delivered"),
        }
        for service, rows in queues.items()
    }
    current_run_id = _packet_capture_state.get("run_id")
    packet_capture = await _packet_payload(current_run_id) if current_run_id else None
    return JSONResponse({
        "containers": containers,
        "queues": queues,
        "queue_counts": queue_counts,
        "scenario": _scenario_state,
        "chat": _chat_state,
        "packet_capture": packet_capture,
        "ts": _ts(),
    })


routes = [
    Route("/", index, methods=["GET"]),
    Route("/api/runs", list_runs, methods=["GET"]),
    Route("/api/runs/{run_id}/trace", get_trace, methods=["GET"]),
    Route("/api/runs/{run_id}/packets", get_packet_evidence, methods=["GET"]),
    Route("/api/runs/{run_id}/events", ingest_event, methods=["POST"]),
    Route("/api/stream/{run_id}", stream_trace, methods=["GET"]),
    Route("/api/control/scenario-start", scenario_start, methods=["POST"]),
    Route("/api/control/capture-start", capture_start, methods=["POST"]),
    Route("/api/state/scenario", scenario_status, methods=["GET"]),
    Route("/api/state/chat", chat_state, methods=["GET"]),
    Route("/api/chat/messages", chat_message, methods=["POST"]),
    Route("/api/chat/reset", chat_reset, methods=["POST"]),
    Route("/api/control/payment-stop", payment_stop, methods=["POST"]),
    Route("/api/control/payment-start", payment_start, methods=["POST"]),
    Route("/api/control/force-retry", force_retry, methods=["POST"]),
    Route("/api/control/soft-reset", soft_reset, methods=["POST"]),
    Route("/api/state/queue", queue_state, methods=["GET"]),
    Route("/api/state/topology", topology_state, methods=["GET"]),
]


async def shutdown_runtime() -> None:
    global _chat_task
    if _chat_task is not None and not _chat_task.done():
        _chat_task.cancel()
        await asyncio.gather(_chat_task, return_exceptions=True)
    await _pi_travel_runtime.close()


@asynccontextmanager
async def lifespan(app):
    try:
        yield
    finally:
        await shutdown_runtime()


app = Starlette(routes=routes, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
