"""Tests for atp.security.atk — ATK record parsing, validation, and verification."""

from __future__ import annotations

import base64
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from atp.core.errors import ATKError
from atp.core.message import ATPMessage
from atp.core.signature import Signer, VerifyResult
from atp.discovery.dns import BaseDNSResolver, ServerInfo
from atp.security.atk import ATKRecord, ATKVerifier
from atp.security.sm2 import SM2PrivateKey, SM2PublicKey
from atp.storage.keys import KeyStorage


# ── Helpers ─────────────────────────────────────────────────────────────────


class MockResolver(BaseDNSResolver):
    """In-memory resolver for testing."""

    def __init__(
        self,
        txt_records: dict[str, str] | None = None,
        svcb_records: dict[str, ServerInfo] | None = None,
    ):
        self._txt = txt_records or {}
        self._svcb = svcb_records or {}

    async def query_svcb(self, domain: str) -> ServerInfo | None:
        return self._svcb.get(domain)

    async def query_txt(self, name: str) -> str | None:
        return self._txt.get(name)


def _generate_key_pair():
    """Generate a fresh Ed25519 key pair and return (private, public, pub_b64)."""
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    raw_bytes = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
    pub_b64 = base64.b64encode(raw_bytes).decode()
    return private_key, public_key, pub_b64


# ── ATKRecord.parse ─────────────────────────────────────────────────────────


class TestATKRecordParse:

    def test_valid_record(self):
        record = ATKRecord.parse("v=atp1 k=ed25519 p=MCowBQYDK2VwAyEA")
        assert record.version == "atp1"
        assert record.algorithm == "ed25519"
        assert record.public_key_b64 == "MCowBQYDK2VwAyEA"
        assert record.flags == []
        assert record.expiry is None

    def test_valid_record_with_flags_and_expiry(self):
        record = ATKRecord.parse("v=atp1 k=ed25519 p=AAAA t=s x=1710000000")
        assert record.flags == ["s"]
        assert record.expiry == 1710000000

    def test_raises_on_missing_version(self):
        with pytest.raises(ATKError):
            ATKRecord.parse("k=ed25519 p=AAAA")

    def test_raises_on_missing_algorithm(self):
        with pytest.raises(ATKError):
            ATKRecord.parse("v=atp1 p=AAAA")

    def test_raises_on_missing_public_key(self):
        with pytest.raises(ATKError):
            ATKRecord.parse("v=atp1 k=ed25519")

    def test_raises_on_empty_string(self):
        with pytest.raises(ATKError):
            ATKRecord.parse("")


# ── ATKRecord.is_valid ──────────────────────────────────────────────────────


class TestATKRecordIsValid:

    def test_valid_no_flags_no_expiry(self):
        record = ATKRecord(version="atp1", algorithm="ed25519", public_key_b64="AAAA")
        assert record.is_valid() is True

    def test_revoked_flag_r(self):
        record = ATKRecord(
            version="atp1", algorithm="ed25519", public_key_b64="AAAA", flags=["r"]
        )
        assert record.is_valid() is False

    def test_legacy_revoked_flag_s(self):
        record = ATKRecord(
            version="atp1", algorithm="ed25519", public_key_b64="AAAA", flags=["s"]
        )
        assert record.is_valid() is False

    def test_expired(self):
        past = int(time.time()) - 3600  # 1 hour ago
        record = ATKRecord(
            version="atp1", algorithm="ed25519", public_key_b64="AAAA", expiry=past
        )
        assert record.is_valid() is False

    def test_not_yet_expired(self):
        future = int(time.time()) + 3600  # 1 hour from now
        record = ATKRecord(
            version="atp1", algorithm="ed25519", public_key_b64="AAAA", expiry=future
        )
        assert record.is_valid() is True


# ── ATKRecord.get_public_key ────────────────────────────────────────────────


class TestATKRecordGetPublicKey:

    def test_returns_ed25519_public_key(self):
        _, public_key, pub_b64 = _generate_key_pair()
        record = ATKRecord(version="atp1", algorithm="ed25519", public_key_b64=pub_b64)
        restored = record.get_public_key()
        assert isinstance(restored, Ed25519PublicKey)
        # Verify the restored key has the same raw bytes
        assert restored.public_bytes(Encoding.Raw, PublicFormat.Raw) == public_key.public_bytes(
            Encoding.Raw, PublicFormat.Raw
        )

    def test_invalid_base64_raises(self):
        record = ATKRecord(
            version="atp1", algorithm="ed25519", public_key_b64="!!!not-base64!!!"
        )
        with pytest.raises(ATKError):
            record.get_public_key()

    def test_returns_sm2_public_key(self):
        public_key = SM2PrivateKey.generate().public_key()
        pub_b64 = base64.b64encode(public_key.raw_bytes()).decode("ascii")
        record = ATKRecord(version="atp1", algorithm="sm2", public_key_b64=pub_b64)

        restored = record.get_public_key()

        assert isinstance(restored, SM2PublicKey)
        assert restored == public_key

    def test_unknown_algorithm_raises(self):
        record = ATKRecord(version="atp1", algorithm="rsa", public_key_b64="AAAA")
        with pytest.raises(ATKError):
            record.get_public_key()


# ── ATKVerifier.parse_key_id ────────────────────────────────────────────────


class TestParseKeyId:

    def test_valid_key_id(self):
        selector, domain = ATKVerifier.parse_key_id("default.atk._atp.example.com")
        assert selector == "default"
        assert domain == "example.com"

    def test_valid_key_id_with_subdomain(self):
        selector, domain = ATKVerifier.parse_key_id("sel1.atk._atp.sub.example.com")
        assert selector == "sel1"
        assert domain == "sub.example.com"

    def test_invalid_key_id_raises(self):
        with pytest.raises(ATKError):
            ATKVerifier.parse_key_id("invalid-key-id")

    def test_missing_separator_raises(self):
        with pytest.raises(ATKError):
            ATKVerifier.parse_key_id("default.example.com")


# ── ATKVerifier.verify ──────────────────────────────────────────────────────


class TestATKVerifierVerify:

    async def test_verify_signed_message_passes(self):
        private_key, public_key, pub_b64 = _generate_key_pair()

        resolver = MockResolver(
            txt_records={
                "default.atk._atp.sender.com": f"v=atp1 k=ed25519 p={pub_b64}",
            }
        )

        msg = ATPMessage.create(
            "alice@sender.com", "bob@receiver.com", {"text": "hello"}
        )
        signer = Signer(private_key, "default", "sender.com")
        signer.sign(msg)

        verifier = ATKVerifier(resolver)
        result = await verifier.verify(msg)
        assert result.passed is True
        assert result.error_code is None

    async def test_verify_no_key_in_dns(self):
        private_key, _, _ = _generate_key_pair()

        resolver = MockResolver()  # empty — no records

        msg = ATPMessage.create(
            "alice@sender.com", "bob@receiver.com", {"text": "hello"}
        )
        signer = Signer(private_key, "default", "sender.com")
        signer.sign(msg)

        verifier = ATKVerifier(resolver)
        result = await verifier.verify(msg)
        assert result.passed is False
        assert result.error_code == "550 5.7.29"
        assert "not found" in result.error_message.lower()

    async def test_verify_unsigned_message(self):
        resolver = MockResolver()
        msg = ATPMessage.create(
            "alice@sender.com", "bob@receiver.com", {"text": "hello"}
        )

        verifier = ATKVerifier(resolver)
        result = await verifier.verify(msg)
        assert result.passed is False
        assert result.error_code == "550 5.7.28"

    async def test_verify_sm2_signed_message_passes(self):
        private_key = SM2PrivateKey.generate()
        pub_b64 = base64.b64encode(private_key.public_key().raw_bytes()).decode("ascii")
        resolver = MockResolver(
            txt_records={
                "default.atk._atp.sender.com": f"v=atp1 k=sm2 p={pub_b64}",
            }
        )
        msg = ATPMessage.create(
            "alice@sender.com", "bob@receiver.com", {"text": "hello"}
        )
        Signer(private_key, "default", "sender.com").sign(msg)

        result = await ATKVerifier(resolver).verify(msg)

        assert result.passed is True

    async def test_dns_and_envelope_algorithm_mismatch_is_rejected(self):
        private_key = SM2PrivateKey.generate()
        pub_b64 = base64.b64encode(private_key.public_key().raw_bytes()).decode("ascii")
        resolver = MockResolver(
            txt_records={
                "default.atk._atp.sender.com": f"v=atp1 k=sm2 p={pub_b64}",
            }
        )
        msg = ATPMessage.create(
            "alice@sender.com", "bob@receiver.com", {"text": "hello"}
        )
        Signer(private_key, "default", "sender.com").sign(msg)
        assert msg.signature is not None
        msg.signature.algorithm = "ed25519"

        result = await ATKVerifier(resolver).verify(msg)

        assert result.passed is False
        assert "algorithm mismatch" in result.error_message.lower()

    async def test_sm2_storage_dns_signing_integration(self, tmp_path):
        """Exercise stored key -> DNS record -> ATK verification end to end."""
        keys = KeyStorage(tmp_path / "keys")
        keys.generate("default", "sm2")
        private_key = keys.load_private_key("default", "sm2")
        pub_b64 = keys.get_public_key_b64("default", "sm2")
        resolver = MockResolver(
            txt_records={
                "default.atk._atp.sender.com": f"v=atp1 k=sm2 p={pub_b64}",
            }
        )
        msg = ATPMessage.create(
            "alice@sender.com", "bob@receiver.com", {"text": "hello"}
        )
        Signer(private_key, "default", "sender.com").sign(msg)

        result = await ATKVerifier(resolver).verify(msg)

        assert result.passed is True

    async def test_verify_revoked_key(self):
        private_key, public_key, pub_b64 = _generate_key_pair()

        resolver = MockResolver(
            txt_records={
                "default.atk._atp.sender.com": f"v=atp1 k=ed25519 p={pub_b64} t=s",
            }
        )

        msg = ATPMessage.create(
            "alice@sender.com", "bob@receiver.com", {"text": "hello"}
        )
        signer = Signer(private_key, "default", "sender.com")
        signer.sign(msg)

        verifier = ATKVerifier(resolver)
        result = await verifier.verify(msg)
        assert result.passed is False
        assert result.error_code == "550 5.7.29"
        assert "revoked" in result.error_message.lower() or "expired" in result.error_message.lower()

    async def test_verify_wrong_key_fails(self):
        private_key, _, _ = _generate_key_pair()
        _, _, wrong_pub_b64 = _generate_key_pair()  # different key pair

        resolver = MockResolver(
            txt_records={
                "default.atk._atp.sender.com": f"v=atp1 k=ed25519 p={wrong_pub_b64}",
            }
        )

        msg = ATPMessage.create(
            "alice@sender.com", "bob@receiver.com", {"text": "hello"}
        )
        signer = Signer(private_key, "default", "sender.com")
        signer.sign(msg)

        verifier = ATKVerifier(resolver)
        result = await verifier.verify(msg)
        assert result.passed is False
        assert result.error_code == "550 5.7.28"
