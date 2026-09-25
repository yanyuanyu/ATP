"""Tests for atp.core.message."""

import json

import pytest

from atp.core.errors import MessageFormatError
from atp.core.message import ATPMessage, SignatureEnvelope


class TestATPMessageCreate:
    """Test ATPMessage.create() auto-fills fields."""

    def test_create_fills_timestamp(self):
        msg = ATPMessage.create("alice@a.com", "bob@b.com", {"text": "hi"})
        assert isinstance(msg.timestamp, int)
        assert msg.timestamp > 0

    def test_create_fills_nonce(self):
        msg = ATPMessage.create("alice@a.com", "bob@b.com", {"text": "hi"})
        assert msg.nonce.startswith("msg-")
        assert len(msg.nonce) == 16  # "msg-" + 12 hex chars

    def test_create_fills_type(self):
        msg = ATPMessage.create("alice@a.com", "bob@b.com", {"text": "hi"})
        assert msg.type == "message"

    def test_create_default_cc_empty(self):
        msg = ATPMessage.create("alice@a.com", "bob@b.com", {"text": "hi"})
        assert msg.cc == []

    def test_create_with_cc(self):
        msg = ATPMessage.create(
            "alice@a.com", "bob@b.com", {"text": "hi"}, cc=["carol@c.com"]
        )
        assert msg.cc == ["carol@c.com"]

    def test_create_signature_is_none(self):
        msg = ATPMessage.create("alice@a.com", "bob@b.com", {"text": "hi"})
        assert msg.signature is None

    def test_create_unique_nonces(self):
        msg1 = ATPMessage.create("alice@a.com", "bob@b.com", {"text": "hi"})
        msg2 = ATPMessage.create("alice@a.com", "bob@b.com", {"text": "hi"})
        assert msg1.nonce != msg2.nonce


class TestATPMessageSerialization:
    """Test to_dict/from_dict and to_json/from_json round-trips."""

    def test_to_dict_uses_from_to_keys(self):
        msg = ATPMessage.create("alice@a.com", "bob@b.com", {"text": "hi"})
        d = msg.to_dict()
        assert "from" in d
        assert "to" in d
        assert "from_id" not in d
        assert "to_id" not in d

    def test_to_dict_from_dict_roundtrip(self):
        msg = ATPMessage.create("alice@a.com", "bob@b.com", {"text": "hi"})
        d = msg.to_dict()
        restored = ATPMessage.from_dict(d)
        assert restored.from_id == msg.from_id
        assert restored.to_id == msg.to_id
        assert restored.timestamp == msg.timestamp
        assert restored.nonce == msg.nonce
        assert restored.type == msg.type
        assert restored.payload == msg.payload

    def test_to_json_from_json_roundtrip(self):
        msg = ATPMessage.create("alice@a.com", "bob@b.com", {"text": "hello"})
        json_str = msg.to_json()
        restored = ATPMessage.from_json(json_str)
        assert restored.from_id == msg.from_id
        assert restored.to_id == msg.to_id
        assert restored.payload == msg.payload

    def test_to_json_is_valid_json(self):
        msg = ATPMessage.create("alice@a.com", "bob@b.com", {"text": "hi"})
        json_str = msg.to_json()
        parsed = json.loads(json_str)
        assert parsed["from"] == "alice@a.com"

    def test_omits_none_optional_fields(self):
        msg = ATPMessage.create("alice@a.com", "bob@b.com", {"text": "hi"})
        d = msg.to_dict()
        assert "signature" not in d
        assert "routing" not in d

    def test_omits_empty_cc(self):
        msg = ATPMessage.create("alice@a.com", "bob@b.com", {"text": "hi"})
        d = msg.to_dict()
        assert "cc" not in d

    def test_includes_cc_when_present(self):
        msg = ATPMessage.create(
            "alice@a.com", "bob@b.com", {"text": "hi"}, cc=["carol@c.com"]
        )
        d = msg.to_dict()
        assert d["cc"] == ["carol@c.com"]


class TestATPMessageSignableDict:
    """Test signable_dict() excludes signature."""

    def test_signable_dict_excludes_signature(self):
        msg = ATPMessage.create("alice@a.com", "bob@b.com", {"text": "hi"})
        msg.signature = SignatureEnvelope(
            key_id="default.atk._atp.a.com",
            algorithm="ed25519",
            signature="abc123",
            headers=["from", "to", "timestamp", "nonce", "type"],
            timestamp=12345,
        )
        sd = msg.signable_dict()
        assert "signature" not in sd

    def test_signable_dict_has_required_fields(self):
        msg = ATPMessage.create("alice@a.com", "bob@b.com", {"text": "hi"})
        sd = msg.signable_dict()
        assert "from" in sd
        assert "to" in sd
        assert "timestamp" in sd
        assert "nonce" in sd
        assert "type" in sd
        assert "payload" in sd


class TestATPMessageFromDictErrors:
    """Test from_dict raises MessageFormatError on missing fields."""

    def test_missing_from(self):
        with pytest.raises(MessageFormatError):
            ATPMessage.from_dict({
                "to": "bob@b.com",
                "timestamp": 123,
                "nonce": "msg-abc",
                "type": "message",
                "payload": {},
            })

    def test_missing_to(self):
        with pytest.raises(MessageFormatError):
            ATPMessage.from_dict({
                "from": "alice@a.com",
                "timestamp": 123,
                "nonce": "msg-abc",
                "type": "message",
                "payload": {},
            })

    def test_missing_payload(self):
        with pytest.raises(MessageFormatError):
            ATPMessage.from_dict({
                "from": "alice@a.com",
                "to": "bob@b.com",
                "timestamp": 123,
                "nonce": "msg-abc",
                "type": "message",
            })

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("from", 123),
            ("to", ["bob@b.com"]),
            ("timestamp", "123"),
            ("nonce", 123),
            ("type", 1),
            ("payload", "not-an-object"),
        ],
    )
    def test_invalid_field_types_raise_format_error(self, field, value):
        data = {
            "from": "alice@a.com",
            "to": "bob@b.com",
            "timestamp": 123,
            "nonce": "msg-abc",
            "type": "message",
            "payload": {},
        }
        data[field] = value

        with pytest.raises(MessageFormatError):
            ATPMessage.from_dict(data)

    def test_invalid_optional_field_types_raise_format_error(self):
        data = ATPMessage.create("alice@a.com", "bob@b.com", {"text": "hi"}).to_dict()
        data["cc"] = "bob@b.com"

        with pytest.raises(MessageFormatError):
            ATPMessage.from_dict(data)

    def test_invalid_signature_envelope_raises_format_error(self):
        data = ATPMessage.create("alice@a.com", "bob@b.com", {"text": "hi"}).to_dict()
        data["signature"] = {"algorithm": "sm2"}

        with pytest.raises(MessageFormatError):
            ATPMessage.from_dict(data)


class TestSignatureEnvelope:
    """Test SignatureEnvelope round-trip."""

    def test_to_dict_from_dict_roundtrip(self):
        env = SignatureEnvelope(
            key_id="default.atk._atp.example.com",
            algorithm="ed25519",
            signature="dGVzdHNpZw==",
            headers=["from", "to", "timestamp", "nonce", "type"],
            timestamp=1700000000,
        )
        d = env.to_dict()
        restored = SignatureEnvelope.from_dict(d)
        assert restored.key_id == env.key_id
        assert restored.algorithm == env.algorithm
        assert restored.signature == env.signature
        assert restored.headers == env.headers
        assert restored.timestamp == env.timestamp
