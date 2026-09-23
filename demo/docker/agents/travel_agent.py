"""LLM-driven travel agent for the IETF 126 local Docker demo.

The model plans the trip through OpenAI-compatible function calling.  It can
only interact with the demo through four explicit tools: send ATP messages,
receive ATP messages, inspect its accumulated trip state, and finish after the
payment acknowledgement has arrived.

The API key is read from ``LLM_API_KEY``/``DASHSCOPE_API_KEY`` or from the file
named by ``LLM_API_KEY_FILE``.  It is never included in trace output.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from atp.client import ATPClient  # noqa: E402

import trace  # noqa: E402


DEFAULT_API_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_API_PATH = "/chat/completions"
DEFAULT_MODEL = "qwen3.7-plus"

SYSTEM_PROMPT = """You are travel@family.test, an autonomous travel agent using ATP tools.

Your objective is to arrange a two-night Paris stay:
1. Ask search@hotel.test to search for a Paris hotel for two nights.
2. After receiving its reply, subscribe to rates@hotel.test for ParisGarden prices.
3. Receive exactly three price-change messages and compare them.
4. Book ParisGarden for two nights at the lowest observed price by sending a
   book+pay message to bill@payment.test.
5. Payment may temporarily be offline. The ATP server will queue the message;
   keep receiving until bill@payment.test sends an acknowledgement.
6. Call finish_trip only after that acknowledgement is present in trip state.

Use tools for every action. Never invent a message or claim success from model
knowledge. Inspect tool results, retry receiving when no message is available,
and do not repeat a send that has already succeeded. Tool execution is serial.
"""

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "send_atp_message",
            "description": "Send one message through ATP after prerequisite observations are complete.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "enum": [
                            "search@hotel.test",
                            "rates@hotel.test",
                            "bill@payment.test",
                        ],
                    },
                    "subject": {
                        "type": "string",
                        "enum": ["search", "subscribe", "book+pay"],
                    },
                    "body": {
                        "type": "string",
                        "description": "Concise request body grounded in the trip state.",
                    },
                },
                "required": ["to", "subject", "body"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "receive_atp_messages",
            "description": "Long-poll the travel agent mailbox for real ATP replies or events.",
            "parameters": {
                "type": "object",
                "properties": {
                    "wait_seconds": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 15,
                        "description": "How long to wait when no message is immediately available.",
                    }
                },
                "required": ["wait_seconds"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_trip_state",
            "description": "Read the observations and successful actions accumulated so far.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish_trip",
            "description": "Finish only after search, three prices, booking, and bill acknowledgement are observed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Short factual summary including the selected observed price.",
                    }
                },
                "required": ["summary"],
                "additionalProperties": False,
            },
        },
    },
]


@dataclass
class TripState:
    search_sent: bool = False
    search_reply: str | None = None
    subscription_sent: bool = False
    prices: list[dict[str, Any]] = field(default_factory=list)
    booking_sent: bool = False
    booking_body: str | None = None
    bill_ack: str | None = None
    finished: bool = False
    seen_nonces: set[str] = field(default_factory=set)

    @property
    def lowest_price(self) -> int | None:
        values = [item["price"] for item in self.prices if isinstance(item.get("price"), int)]
        return min(values) if values else None

    @property
    def complete(self) -> bool:
        return bool(
            self.search_reply
            and len(self.prices) >= 3
            and self.booking_sent
            and self.bill_ack
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "search_sent": self.search_sent,
            "search_reply": self.search_reply,
            "subscription_sent": self.subscription_sent,
            "prices": self.prices,
            "lowest_price": self.lowest_price,
            "booking_sent": self.booking_sent,
            "booking_body": self.booking_body,
            "bill_ack": self.bill_ack,
            "ready_to_finish": self.complete,
        }


def _read_api_key() -> str:
    value = os.environ.get("LLM_API_KEY") or os.environ.get("DASHSCOPE_API_KEY")
    if value:
        return value.strip()
    key_file = os.environ.get("LLM_API_KEY_FILE")
    if key_file:
        path = Path(key_file)
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    raise RuntimeError("LLM API key is not configured")


def _provider_label(api_url: str) -> str:
    parsed = urlsplit(api_url)
    return parsed.netloc or "openai-compatible"


def register_travel(server_family: str, travel_pass: str) -> None:
    result = subprocess.run(
        [
            "atp",
            "agent",
            "register",
            "travel",
            "--server",
            server_family,
            "-p",
            travel_pass,
            "--no-verify",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        trace.log(f"[travel-agent] register returned {result.returncode}: {result.stderr.strip()}")


class TravelAgent:
    def __init__(
        self,
        atp_client: ATPClient,
        http_client: httpx.AsyncClient,
        *,
        api_url: str,
        api_key: str,
        model: str,
        max_turns: int,
    ) -> None:
        self.atp = atp_client
        self.http = http_client
        self.api_url = api_url
        self.api_key = api_key
        self.model = model
        self.max_turns = max_turns
        self.state = TripState()
        self.started_phases: set[str] = set()
        self.ended_phases: set[str] = set()

    def _start_phase(self, phase: str, narrative: str) -> None:
        if phase not in self.started_phases:
            trace.phase_start(phase, narrative=narrative)
            self.started_phases.add(phase)

    def _end_phase(self, phase: str, narrative: str) -> None:
        if phase not in self.ended_phases:
            trace.phase_end(phase, narrative=narrative)
            self.ended_phases.add(phase)

    async def _chat_completion(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        request_body = {
            "model": self.model,
            "messages": messages,
            "tools": TOOLS,
            "tool_choice": "required",
            "parallel_tool_calls": False,
            "temperature": 0.1,
            "max_tokens": 700,
            "stream": False,
            "enable_thinking": False,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self.http.post(self.api_url, headers=headers, json=request_body)
                if response.status_code == 429 or response.status_code >= 500:
                    raise RuntimeError(f"LLM API transient status {response.status_code}")
                response.raise_for_status()
                payload = response.json()
                choices = payload.get("choices") or []
                if not choices or not isinstance(choices[0].get("message"), dict):
                    raise RuntimeError("LLM API returned no assistant message")
                return choices[0]["message"]
            except (httpx.HTTPError, ValueError, RuntimeError) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"LLM API call failed after retries: {last_error}")

    async def _send(self, arguments: dict[str, Any]) -> dict[str, Any]:
        to = str(arguments.get("to", ""))
        subject = str(arguments.get("subject", ""))
        body = str(arguments.get("body", "")).strip()
        expected = {
            "search": "search@hotel.test",
            "subscribe": "rates@hotel.test",
            "book+pay": "bill@payment.test",
        }
        if expected.get(subject) != to:
            return {"ok": False, "error": f"subject {subject!r} must be sent to {expected.get(subject)!r}"}
        if not body:
            return {"ok": False, "error": "body must not be empty"}

        if subject == "search":
            if self.state.search_sent:
                return {"ok": False, "error": "search was already sent", "state": self.state.snapshot()}
            phase = "search"
            narrative = "LLM travel agent searches for a Paris hotel"
        elif subject == "subscribe":
            if not self.state.search_reply:
                return {"ok": False, "error": "receive the search reply before subscribing", "state": self.state.snapshot()}
            if self.state.subscription_sent:
                return {"ok": False, "error": "subscription was already sent", "state": self.state.snapshot()}
            phase = "subscribe"
            narrative = "LLM travel agent subscribes to hotel price changes"
        else:
            if len(self.state.prices) < 3:
                return {"ok": False, "error": "receive all three prices before booking", "state": self.state.snapshot()}
            if self.state.booking_sent:
                return {"ok": False, "error": "booking was already sent", "state": self.state.snapshot()}
            lowest = self.state.lowest_price
            if lowest is None or str(lowest) not in body:
                return {
                    "ok": False,
                    "error": f"booking body must use the lowest observed price ${lowest}",
                    "state": self.state.snapshot(),
                }
            phase = "book"
            narrative = "LLM travel agent books the lowest observed rate while payment is offline"

        self._start_phase(phase, narrative)
        result = await self.atp.send(to=to, subject=subject, body=body)
        status = str(result.get("status"))
        accepted = status == "accepted"
        trace.send_event(
            phase=phase,
            nonce=str(result.get("nonce", "")),
            from_id="travel@family.test",
            to_id=to,
            subject=subject,
            body=body,
            status=status,
            narrative=narrative,
        )
        if accepted:
            if subject == "search":
                self.state.search_sent = True
            elif subject == "subscribe":
                self.state.subscription_sent = True
            else:
                self.state.booking_sent = True
                self.state.booking_body = body
                self._start_phase("ack", "LLM travel agent waits for the real payment acknowledgement")
        return {"ok": accepted, "status": status, "nonce": result.get("nonce"), "state": self.state.snapshot()}

    async def _receive(self, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            wait_seconds = max(1, min(15, int(arguments.get("wait_seconds", 10))))
        except (TypeError, ValueError):
            return {"ok": False, "error": "wait_seconds must be an integer"}
        messages = await self.atp.recv(wait=True, timeout=float(wait_seconds))
        observations: list[dict[str, Any]] = []
        for message in messages:
            if message.nonce in self.state.seen_nonces:
                continue
            self.state.seen_nonces.add(message.nonce)
            payload = message.payload if isinstance(message.payload, dict) else {}
            subject = str(payload.get("subject") or "")
            body = str(payload.get("body") or "")
            observation = {
                "nonce": message.nonce,
                "from": message.from_id,
                "to": message.to_id,
                "subject": subject,
                "body": body,
            }
            observations.append(observation)

            if message.from_id == "search@hotel.test":
                self.state.search_reply = body
                trace.recv_event(
                    phase="search",
                    nonce=message.nonce,
                    from_id=message.from_id,
                    to_id=message.to_id,
                    subject=subject,
                    body=body,
                    narrative="LLM agent observed the search reply",
                )
                self._end_phase("search", "search reply observed by the LLM agent")
            elif message.from_id == "rates@hotel.test" and subject == "price-change":
                match = re.search(r"\$(\d+)", body)
                price = int(match.group(1)) if match else None
                self.state.prices.append({"body": body, "price": price, "nonce": message.nonce})
                trace.recv_event(
                    phase="subscribe",
                    nonce=message.nonce,
                    from_id=message.from_id,
                    to_id=message.to_id,
                    subject=subject,
                    body=body,
                    narrative="LLM agent observed a price-change event",
                )
                if len(self.state.prices) >= 3:
                    self._end_phase("subscribe", "three price changes observed and ready for comparison")
            elif message.from_id == "bill@payment.test" and "bill ack" in body.lower():
                self.state.bill_ack = body
                trace.recv_event(
                    phase="ack",
                    nonce=message.nonce,
                    from_id=message.from_id,
                    to_id=message.to_id,
                    subject=subject,
                    body=body,
                    narrative="LLM agent observed the bill acknowledgement",
                )
                self._end_phase("ack", "payment acknowledgement observed")
                self._end_phase("book", "booking completed after queued delivery")
            else:
                trace.note("setup", "LLM agent observed an unrelated ATP message", **observation)

        return {
            "ok": True,
            "received": len(observations),
            "messages": observations,
            "state": self.state.snapshot(),
        }

    async def execute_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "send_atp_message":
            return await self._send(arguments)
        if name == "receive_atp_messages":
            return await self._receive(arguments)
        if name == "get_trip_state":
            return {"ok": True, "state": self.state.snapshot()}
        if name == "finish_trip":
            if not self.state.complete:
                return {"ok": False, "error": "trip is incomplete", "state": self.state.snapshot()}
            self.state.finished = True
            summary = str(arguments.get("summary", "")).strip()
            trace.emit(
                phase="setup",
                event="agent_finish",
                model=self.model,
                summary=summary,
                trip_state=self.state.snapshot(),
                narrative="LLM travel agent completed its objective",
            )
            return {"ok": True, "finished": True, "summary": summary, "state": self.state.snapshot()}
        return {"ok": False, "error": f"unknown tool {name!r}"}

    async def run(self) -> None:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "Plan and complete the Paris two-night trip now. Use only observed ATP tool results.",
            },
        ]
        trace.emit(
            phase="setup",
            event="agent_start",
            model=self.model,
            provider=_provider_label(self.api_url),
            tools=[item["function"]["name"] for item in TOOLS],
            narrative="LLM travel agent started with ATP tools",
        )

        for turn in range(1, self.max_turns + 1):
            assistant = await self._chat_completion(messages)
            tool_calls = assistant.get("tool_calls") or []
            assistant_record: dict[str, Any] = {
                "role": "assistant",
                "content": assistant.get("content"),
            }
            if tool_calls:
                assistant_record["tool_calls"] = tool_calls
            messages.append(assistant_record)
            trace.emit(
                phase="setup",
                event="agent_turn",
                model=self.model,
                turn=turn,
                tool_count=len(tool_calls),
                narrative="LLM selected the next ATP action",
            )
            if not tool_calls:
                messages.append({
                    "role": "user",
                    "content": "Continue by calling exactly one available tool; do not answer in prose.",
                })
                continue

            for call in tool_calls:
                function = call.get("function") or {}
                name = str(function.get("name", ""))
                raw_arguments = function.get("arguments") or "{}"
                try:
                    arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else dict(raw_arguments)
                    if not isinstance(arguments, dict):
                        raise ValueError("arguments are not an object")
                except (json.JSONDecodeError, TypeError, ValueError) as exc:
                    result = {"ok": False, "error": f"invalid tool arguments: {exc}"}
                else:
                    trace.emit(
                        phase="setup",
                        event="agent_tool_call",
                        model=self.model,
                        turn=turn,
                        tool=name,
                        arguments=arguments,
                        narrative=f"LLM requested tool {name}",
                    )
                    try:
                        result = await self.execute_tool(name, arguments)
                    except Exception as exc:
                        result = {"ok": False, "error": f"tool execution failed: {exc}"}
                    trace.emit(
                        phase="setup",
                        event="agent_tool_result",
                        model=self.model,
                        turn=turn,
                        tool=name,
                        ok=bool(result.get("ok")),
                        trip_state=self.state.snapshot(),
                        narrative=f"tool {name} returned to the LLM",
                    )
                messages.append({
                    "role": "tool",
                    "tool_call_id": str(call.get("id", f"call-{turn}")),
                    "content": json.dumps(result, ensure_ascii=False),
                })
                if self.state.finished:
                    return

        raise RuntimeError(f"agent exceeded {self.max_turns} model turns without finishing")


async def main() -> int:
    api_base = os.environ.get("LLM_API_BASE", DEFAULT_API_BASE).rstrip("/")
    api_path = os.environ.get("LLM_API_PATH", DEFAULT_API_PATH)
    api_url = f"{api_base}/{api_path.lstrip('/')}"
    model = os.environ.get("LLM_API_MODEL", DEFAULT_MODEL)
    api_key = _read_api_key()
    timeout = float(os.environ.get("LLM_API_TIMEOUT", "60"))
    max_turns = int(os.environ.get("LLM_AGENT_MAX_TURNS", "30"))
    server_family = os.environ.get("ATP_SERVER_FAMILY", "server-family.family.test:7443")
    travel_pass = os.environ.get("ATP_TRAVEL_PASS", "travelpass")

    register_travel(server_family, travel_pass)
    atp_client = ATPClient(
        agent_id="travel@family.test",
        server=server_family,
        password=travel_pass,
        no_verify=True,
    )
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as http_client:
            agent = TravelAgent(
                atp_client,
                http_client,
                api_url=api_url,
                api_key=api_key,
                model=model,
                max_turns=max_turns,
            )
            await agent.run()
        return 0
    except Exception as exc:
        trace.emit(
            phase="setup",
            event="agent_error",
            model=model,
            provider=_provider_label(api_url),
            error=str(exc),
            narrative="LLM travel agent failed",
        )
        trace.log(f"[travel-agent] failed: {exc}")
        return 1
    finally:
        await atp_client.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
