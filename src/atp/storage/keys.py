"""ATP domain-key storage with SM2 as the default and Ed25519 compatibility."""

import base64
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Union

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
    load_pem_public_key,
)

from atp.core.errors import ATPErrorCode, StorageError
from atp.security.sm2 import SM2PrivateKey, SM2PublicKey


SUPPORTED_KEY_ALGORITHMS = ("sm2", "ed25519")
PrivateKey = Union[SM2PrivateKey, Ed25519PrivateKey]
PublicKey = Union[SM2PublicKey, Ed25519PublicKey]


@dataclass
class KeyPairInfo:
    selector: str
    algorithm: str
    private_key_path: Path
    public_key_path: Path
    created_at: int


class KeyStorage:
    """Manage domain signing keys under ``~/.atp/keys``.

    New keys use algorithm-qualified filenames so SM2 and legacy Ed25519 keys
    may coexist for the same selector.  Existing ``{selector}.key/.pub``
    Ed25519 files remain readable.
    """

    def __init__(self, keys_dir: Path):
        self._keys_dir = keys_dir
        self._keys_dir.mkdir(parents=True, exist_ok=True)
        self._keyring_path = self._keys_dir / "keyring.json"

    @staticmethod
    def _validate_algorithm(algorithm: str) -> str:
        value = algorithm.lower()
        if value not in SUPPORTED_KEY_ALGORITHMS:
            raise StorageError(
                ATPErrorCode.SERVER_ERROR,
                f"Unsupported key algorithm '{algorithm}'",
            )
        return value

    def _paths(self, selector: str, algorithm: str) -> tuple[Path, Path]:
        return (
            self._keys_dir / f"{selector}.{algorithm}.key",
            self._keys_dir / f"{selector}.{algorithm}.pub",
        )

    def _legacy_ed25519_paths(self, selector: str) -> tuple[Path, Path]:
        return self._keys_dir / f"{selector}.key", self._keys_dir / f"{selector}.pub"

    def _resolve_paths(self, selector: str, algorithm: str) -> tuple[Path, Path]:
        private_path, public_path = self._paths(selector, algorithm)
        if algorithm == "ed25519" and not private_path.exists():
            legacy_private, legacy_public = self._legacy_ed25519_paths(selector)
            if legacy_private.exists() or legacy_public.exists():
                return legacy_private, legacy_public
        return private_path, public_path

    def has_key(self, selector: str = "default", algorithm: str = "sm2") -> bool:
        """Return whether both private and public key files are present."""
        algorithm = self._validate_algorithm(algorithm)
        private_path, public_path = self._resolve_paths(selector, algorithm)
        return private_path.exists() and public_path.exists()

    def has_any_key_file(
        self, selector: str = "default", algorithm: str = "sm2"
    ) -> bool:
        """Return whether either half of a key pair exists."""
        algorithm = self._validate_algorithm(algorithm)
        private_path, public_path = self._resolve_paths(selector, algorithm)
        return private_path.exists() or public_path.exists()

    def _load_keyring(self) -> dict:
        if self._keyring_path.exists():
            return json.loads(self._keyring_path.read_text(encoding="utf-8"))
        return {"keys": {}}

    def _save_keyring(self, keyring: dict) -> None:
        self._keyring_path.write_text(
            json.dumps(keyring, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def generate(self, selector: str = "default", algorithm: str = "sm2") -> KeyPairInfo:
        """Generate and store a domain key pair; SM2 is the default."""
        algorithm = self._validate_algorithm(algorithm)
        private_path, public_path = self._paths(selector, algorithm)

        if algorithm == "sm2":
            private_key = SM2PrivateKey.generate()
            private_path.write_text(
                json.dumps(
                    {
                        "algorithm": "sm2",
                        "private_key": private_key.private_key_hex,
                        "public_key": private_key.public_key_hex,
                    },
                    indent=2,
                ),
                encoding="ascii",
            )
            public_path.write_text(
                json.dumps(
                    {
                        "algorithm": "sm2",
                        "public_key": private_key.public_key_hex,
                    },
                    indent=2,
                ),
                encoding="ascii",
            )
        else:
            private_key = Ed25519PrivateKey.generate()
            public_key = private_key.public_key()
            private_path.write_bytes(
                private_key.private_bytes(
                    Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
                )
            )
            public_path.write_bytes(
                public_key.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
            )

        try:
            os.chmod(private_path, 0o600)
            os.chmod(public_path, 0o644)
        except OSError:
            pass

        created_at = int(time.time())
        keyring = self._load_keyring()
        keyring["keys"][f"{algorithm}:{selector}"] = {
            "selector": selector,
            "algorithm": algorithm,
            "private_key": private_path.name,
            "public_key": public_path.name,
            "created_at": created_at,
        }
        self._save_keyring(keyring)

        return KeyPairInfo(
            selector=selector,
            algorithm=algorithm,
            private_key_path=private_path,
            public_key_path=public_path,
            created_at=created_at,
        )

    def load_private_key(
        self, selector: str = "default", algorithm: str = "sm2"
    ) -> PrivateKey:
        algorithm = self._validate_algorithm(algorithm)
        private_path, _ = self._resolve_paths(selector, algorithm)
        if not private_path.exists():
            raise StorageError(
                ATPErrorCode.SERVER_ERROR,
                f"{algorithm} private key not found for selector '{selector}'",
            )
        try:
            if algorithm == "sm2":
                data = json.loads(private_path.read_text(encoding="ascii"))
                if data.get("algorithm") != "sm2":
                    raise ValueError("key file algorithm is not sm2")
                return SM2PrivateKey(data["private_key"], data["public_key"])

            key = load_pem_private_key(private_path.read_bytes(), password=None)
            if not isinstance(key, Ed25519PrivateKey):
                raise ValueError("PEM file does not contain an Ed25519 private key")
            return key
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError(
                ATPErrorCode.SERVER_ERROR,
                f"Failed to load {algorithm} private key for selector '{selector}': {exc}",
            ) from exc

    def load_public_key(
        self, selector: str = "default", algorithm: str = "sm2"
    ) -> PublicKey:
        algorithm = self._validate_algorithm(algorithm)
        _, public_path = self._resolve_paths(selector, algorithm)
        if not public_path.exists():
            raise StorageError(
                ATPErrorCode.SERVER_ERROR,
                f"{algorithm} public key not found for selector '{selector}'",
            )
        try:
            if algorithm == "sm2":
                data = json.loads(public_path.read_text(encoding="ascii"))
                if data.get("algorithm") != "sm2":
                    raise ValueError("key file algorithm is not sm2")
                return SM2PublicKey(data["public_key"])

            key = load_pem_public_key(public_path.read_bytes())
            if not isinstance(key, Ed25519PublicKey):
                raise ValueError("PEM file does not contain an Ed25519 public key")
            return key
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError(
                ATPErrorCode.SERVER_ERROR,
                f"Failed to load {algorithm} public key for selector '{selector}': {exc}",
            ) from exc

    def get_public_key_b64(
        self, selector: str = "default", algorithm: str = "sm2"
    ) -> str:
        public_key = self.load_public_key(selector, algorithm)
        if isinstance(public_key, SM2PublicKey):
            raw = public_key.raw_bytes()
        else:
            raw = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
        return base64.b64encode(raw).decode("ascii")

    def load_key_pair(
        self, selector: str = "default", algorithm: str = "sm2"
    ) -> tuple[PrivateKey, PublicKey]:
        """Load a pair and reject missing or mismatched key files."""
        private_key = self.load_private_key(selector, algorithm)
        public_key = self.load_public_key(selector, algorithm)
        if isinstance(private_key, SM2PrivateKey):
            if not isinstance(public_key, SM2PublicKey) or (
                private_key.public_key() != public_key
            ):
                raise StorageError(
                    ATPErrorCode.SERVER_ERROR,
                    f"SM2 public key does not match private key for selector '{selector}'",
                )
        else:
            if not isinstance(public_key, Ed25519PublicKey):
                raise StorageError(
                    ATPErrorCode.SERVER_ERROR,
                    f"Ed25519 public key type mismatch for selector '{selector}'",
                )
            derived = private_key.public_key().public_bytes(
                Encoding.Raw, PublicFormat.Raw
            )
            stored = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
            if derived != stored:
                raise StorageError(
                    ATPErrorCode.SERVER_ERROR,
                    f"Ed25519 public key does not match private key for selector '{selector}'",
                )
        return private_key, public_key

    def list_keys(self) -> list[KeyPairInfo]:
        keyring = self._load_keyring()
        result: list[KeyPairInfo] = []
        for entry_name, info in keyring.get("keys", {}).items():
            # Old keyrings used the selector as the entry name and omitted the
            # algorithm.  Treat them as legacy Ed25519 entries.
            selector = info.get("selector", entry_name)
            algorithm = info.get("algorithm", "ed25519")
            result.append(
                KeyPairInfo(
                    selector=selector,
                    algorithm=algorithm,
                    private_key_path=self._keys_dir / info["private_key"],
                    public_key_path=self._keys_dir / info["public_key"],
                    created_at=info["created_at"],
                )
            )
        return result

    def rotate(
        self,
        old_selector: str,
        new_selector: str,
        algorithm: str = "sm2",
    ) -> KeyPairInfo:
        """Generate a replacement key without deleting the old selector."""
        return self.generate(new_selector, algorithm)
