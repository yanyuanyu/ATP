"""Tests for atp.storage.keys."""

import base64
from pathlib import Path

import pytest

from atp.core.errors import StorageError
from atp.security.sm2 import SM2PrivateKey, SM2PublicKey
from atp.storage.keys import KeyPairInfo, KeyStorage


class TestKeyStorage:
    def test_generate_creates_files(self, tmp_path: Path) -> None:
        """generate() should create SM2 key files by default."""
        ks = KeyStorage(keys_dir=tmp_path / "keys")

        info = ks.generate("default")

        assert info.selector == "default"
        assert info.algorithm == "sm2"
        assert info.private_key_path.exists()
        assert info.public_key_path.exists()
        assert info.private_key_path.name == "default.sm2.key"
        assert info.public_key_path.name == "default.sm2.pub"
        assert info.created_at > 0

    def test_load_private_key_after_generate(self, tmp_path: Path) -> None:
        """load_private_key() should return an SM2 key after generation."""
        ks = KeyStorage(keys_dir=tmp_path / "keys")
        ks.generate("default")

        priv = ks.load_private_key("default")

        assert isinstance(priv, SM2PrivateKey)
        sig = priv.sign(b"test data")
        assert priv.public_key().verify(sig, b"test data") is True

    def test_load_public_key_after_generate(self, tmp_path: Path) -> None:
        """load_public_key() should return an SM2 public key after generation."""
        ks = KeyStorage(keys_dir=tmp_path / "keys")
        ks.generate("default")

        pub = ks.load_public_key("default")

        assert isinstance(pub, SM2PublicKey)
        assert len(pub.raw_bytes()) == 65

    def test_load_private_key_missing_raises(self, tmp_path: Path) -> None:
        """load_private_key() should raise StorageError if key file missing."""
        ks = KeyStorage(keys_dir=tmp_path / "keys")

        with pytest.raises(StorageError):
            ks.load_private_key("nonexistent")

    def test_load_public_key_missing_raises(self, tmp_path: Path) -> None:
        """load_public_key() should raise StorageError if key file missing."""
        ks = KeyStorage(keys_dir=tmp_path / "keys")

        with pytest.raises(StorageError):
            ks.load_public_key("nonexistent")

    def test_get_public_key_b64(self, tmp_path: Path) -> None:
        """get_public_key_b64() should encode an uncompressed SM2 point."""
        ks = KeyStorage(keys_dir=tmp_path / "keys")
        ks.generate("default")

        b64 = ks.get_public_key_b64("default")

        raw = base64.b64decode(b64)
        assert len(raw) == 65
        assert raw[0] == 0x04

    def test_list_keys(self, tmp_path: Path) -> None:
        """list_keys() should return all generated keys."""
        ks = KeyStorage(keys_dir=tmp_path / "keys")
        ks.generate("default")
        ks.generate("backup")

        keys = ks.list_keys()

        selectors = {k.selector for k in keys}
        assert selectors == {"default", "backup"}
        for k in keys:
            assert isinstance(k, KeyPairInfo)
            assert k.algorithm == "sm2"
            assert k.created_at > 0

    def test_rotate_creates_new_key_keeps_old(self, tmp_path: Path) -> None:
        """rotate() should create a new key pair; old key should still exist."""
        ks = KeyStorage(keys_dir=tmp_path / "keys")
        ks.generate("old")

        new_info = ks.rotate("old", "new")

        assert new_info.selector == "new"
        assert new_info.private_key_path.exists()
        assert new_info.public_key_path.exists()

        # Old key still exists
        old_priv = ks.load_private_key("old")
        assert old_priv is not None

        # Both appear in list
        selectors = {k.selector for k in ks.list_keys()}
        assert selectors == {"old", "new"}

    def test_ed25519_compatibility(self, tmp_path: Path) -> None:
        """Legacy Ed25519 remains available only when explicitly selected."""
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        ks = KeyStorage(keys_dir=tmp_path / "keys")
        info = ks.generate("legacy", "ed25519")

        assert info.algorithm == "ed25519"
        assert isinstance(ks.load_private_key("legacy", "ed25519"), Ed25519PrivateKey)
        assert len(base64.b64decode(ks.get_public_key_b64("legacy", "ed25519"))) == 32

    def test_sm2_and_ed25519_can_share_selector(self, tmp_path: Path) -> None:
        ks = KeyStorage(keys_dir=tmp_path / "keys")
        ks.generate("default", "sm2")
        ks.generate("default", "ed25519")

        algorithms = {item.algorithm for item in ks.list_keys()}
        assert algorithms == {"sm2", "ed25519"}

    def test_mismatched_sm2_pair_is_rejected(self, tmp_path: Path) -> None:
        ks = KeyStorage(keys_dir=tmp_path / "keys")
        ks.generate("first", "sm2")
        ks.generate("second", "sm2")
        second_public = (tmp_path / "keys" / "second.sm2.pub").read_text(
            encoding="ascii"
        )
        (tmp_path / "keys" / "first.sm2.pub").write_text(
            second_public, encoding="ascii"
        )

        with pytest.raises(StorageError, match="does not match"):
            ks.load_key_pair("first", "sm2")

    def test_partial_key_pair_is_not_considered_complete(self, tmp_path: Path) -> None:
        ks = KeyStorage(keys_dir=tmp_path / "keys")
        ks.generate("default", "sm2")
        (tmp_path / "keys" / "default.sm2.pub").unlink()

        assert ks.has_any_key_file("default", "sm2") is True
        assert ks.has_key("default", "sm2") is False
        with pytest.raises(StorageError):
            ks.load_key_pair("default", "sm2")
