"""Tests for SM4 payload encryption and SM2 key wrapping."""

from __future__ import annotations

import copy
import base64
from unittest.mock import AsyncMock, MagicMock
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from atp.security.sm2 import SM2PrivateKey
from atp.security.sm4 import (
    SM4PayloadError,
    decrypt_payload,
    encrypt_payload,
    message_aad,
)
from atp.core.message import ATPMessage
from atp.core.signature import Signer, Verifier
from atp.server.delivery import DeliveryManager
from atp.server.routes import get_routes


def _aad() -> bytes:
    return message_aad(
        from_id="travel@family.test",
        to_id="search@hotel.test",
        timestamp=1_700_000_000,
        nonce="msg-test",
        message_type="message",
    )


def test_sm4_round_trip_with_sm2_wrapped_key():
    recipient = SM2PrivateKey.generate()
    payload = {"subject": "search", "body": "巴黎住两晚", "nested": {"n": 2}}
    envelope = encrypt_payload(payload, recipient_public_key=recipient.public_key(), aad=_aad())

    assert envelope["algorithm"] == "sm4-cbc-sm3"
    assert decrypt_payload(envelope, recipient_private_key=recipient, aad=_aad()) == payload


def test_sm4_ciphertext_tamper_is_rejected():
    recipient = SM2PrivateKey.generate()
    envelope = encrypt_payload({"body": "secret"}, recipient_public_key=recipient.public_key(), aad=_aad())
    tampered = copy.deepcopy(envelope)
    ciphertext = bytearray(base64.b64decode(tampered["ciphertext"]))
    ciphertext[0] ^= 0x01
    tampered["ciphertext"] = base64.b64encode(ciphertext).decode("ascii")

    with pytest.raises(SM4PayloadError, match="authentication|decryption"):
        decrypt_payload(tampered, recipient_private_key=recipient, aad=_aad())


def test_sm4_aad_tamper_is_rejected():
    recipient = SM2PrivateKey.generate()
    envelope = encrypt_payload({"body": "secret"}, recipient_public_key=recipient.public_key(), aad=_aad())
    changed_aad = message_aad(
        from_id="attacker@family.test",
        to_id="search@hotel.test",
        timestamp=1_700_000_000,
        nonce="msg-test",
        message_type="message",
    )

    with pytest.raises(SM4PayloadError, match="authentication"):
        decrypt_payload(envelope, recipient_private_key=recipient, aad=changed_aad)


@pytest.mark.asyncio
async def test_delivery_encrypts_before_atk_signing():
    sender = SM2PrivateKey.generate()
    recipient = SM2PrivateKey.generate()
    resolver = AsyncMock()
    resolver.query_svcb = AsyncMock(return_value=MagicMock(host="server-hotel.hotel.test", port=7443))
    resolver.query_txt = AsyncMock(
        return_value=(
            "v=atp1 k=sm2 p="
            + base64.b64encode(recipient.public_key().raw_bytes()).decode("ascii")
        )
    )
    transport = AsyncMock()
    transport.post_message = AsyncMock(return_value=MagicMock(success=True))
    manager = DeliveryManager(
        message_store=MagicMock(),
        dns_resolver=resolver,
        transport=transport,
        signer=Signer(sender, "default", "family.test"),
        server_domain="family.test",
        payload_encryption=True,
    )
    message = ATPMessage.create(
        "travel@family.test", "search@hotel.test", {"body": "secret"}
    )

    assert await manager.transfer(message) is True
    sent = transport.post_message.call_args.args[1]
    assert sent.payload["_atp_encrypted"] is True
    assert Verifier.verify(sent, sender.public_key()).passed is True
    assert decrypt_payload(
        sent.payload,
        recipient_private_key=recipient,
        aad=message_aad(
            from_id=sent.from_id,
            to_id=sent.to_id,
            timestamp=sent.timestamp,
            nonce=sent.nonce,
            message_type=sent.type,
        ),
    ) == {"body": "secret"}


def test_receive_decrypts_before_local_agent_delivery():
    recipient = SM2PrivateKey.generate()
    message = ATPMessage.create(
        "travel@family.test", "search@hotel.test", {"body": "secret"}
    )
    message.payload = encrypt_payload(
        message.payload,
        recipient_public_key=recipient.public_key(),
        aad=message_aad(
            from_id=message.from_id,
            to_id=message.to_id,
            timestamp=message.timestamp,
            nonce=message.nonce,
            message_type=message.type,
        ),
    )
    stored = SimpleNamespace(id=1, message_json=message.to_json())
    server = SimpleNamespace(
        encryption_private_key=recipient,
        agent_store=SimpleNamespace(verify=lambda agent, password: agent == "search@hotel.test" and password == "pass"),
        queue=SimpleNamespace(get_for_agent=AsyncMock(return_value=[stored])),
    )
    app = Starlette(routes=get_routes())
    app.state.server = server

    credential = base64.b64encode(b"search@hotel.test:pass").decode("ascii")
    response = TestClient(app).get(
        "/.well-known/atp/v1/messages",
        headers={"Authorization": f"Basic {credential}"},
    )

    assert response.status_code == 200
    assert response.json()["messages"][0]["payload"] == {"body": "secret"}
