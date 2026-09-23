"""Focused tests for the Travel Agent's ATP tool/state boundary."""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from types import SimpleNamespace

import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from travel_agent import TravelAgent  # noqa: E402


class FakeATPClient:
    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []
        self.batches = [
            [self.message("s1", "search@hotel.test", "", "echo: Paris 2 nights")],
            [
                self.message("p1", "rates@hotel.test", "price-change", "ParisGarden $180/night"),
                self.message("p2", "rates@hotel.test", "price-change", "ParisGarden $170/night"),
                self.message("p3", "rates@hotel.test", "price-change", "ParisGarden $190/night"),
            ],
            [self.message("a1", "bill@payment.test", "", "bill ack: ParisGarden 2 nights $170")],
        ]

    @staticmethod
    def message(nonce: str, from_id: str, subject: str, body: str) -> SimpleNamespace:
        return SimpleNamespace(
            nonce=nonce,
            from_id=from_id,
            to_id="travel@family.test",
            payload={"subject": subject, "body": body},
        )

    async def send(self, *, to: str, subject: str, body: str) -> dict[str, str]:
        self.sent.append({"to": to, "subject": subject, "body": body})
        return {"status": "accepted", "nonce": f"send-{len(self.sent)}"}

    async def recv(self, *, wait: bool, timeout: float) -> list[SimpleNamespace]:
        del wait, timeout
        return self.batches.pop(0) if self.batches else []


class TravelAgentToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.atp = FakeATPClient()
        self.http = httpx.AsyncClient()
        self.agent = TravelAgent(
            self.atp,  # type: ignore[arg-type]
            self.http,
            api_url="https://example.invalid/chat/completions",
            api_key="test-only",
            model="test-model",
            max_turns=20,
        )

    async def asyncTearDown(self) -> None:
        await self.http.aclose()

    async def test_tool_flow_is_grounded_and_guarded(self) -> None:
        premature = await self.agent.execute_tool(
            "send_atp_message",
            {"to": "bill@payment.test", "subject": "book+pay", "body": "ParisGarden $170"},
        )
        self.assertFalse(premature["ok"])

        self.assertTrue((await self.agent.execute_tool(
            "send_atp_message",
            {"to": "search@hotel.test", "subject": "search", "body": "Paris hotel for two nights"},
        ))["ok"])
        await self.agent.execute_tool("receive_atp_messages", {"wait_seconds": 1})

        self.assertTrue((await self.agent.execute_tool(
            "send_atp_message",
            {"to": "rates@hotel.test", "subject": "subscribe", "body": "ParisGarden price changes"},
        ))["ok"])
        await self.agent.execute_tool("receive_atp_messages", {"wait_seconds": 1})
        self.assertEqual(self.agent.state.lowest_price, 170)

        wrong_price = await self.agent.execute_tool(
            "send_atp_message",
            {"to": "bill@payment.test", "subject": "book+pay", "body": "ParisGarden 2 nights $190"},
        )
        self.assertFalse(wrong_price["ok"])

        self.assertTrue((await self.agent.execute_tool(
            "send_atp_message",
            {"to": "bill@payment.test", "subject": "book+pay", "body": "ParisGarden 2 nights $170"},
        ))["ok"])
        await self.agent.execute_tool("receive_atp_messages", {"wait_seconds": 1})
        result = await self.agent.execute_tool("finish_trip", {"summary": "Booked ParisGarden at $170"})

        self.assertTrue(result["finished"])
        self.assertTrue(self.agent.state.complete)
        self.assertEqual([item["subject"] for item in self.atp.sent], ["search", "subscribe", "book+pay"])


if __name__ == "__main__":
    unittest.main()
