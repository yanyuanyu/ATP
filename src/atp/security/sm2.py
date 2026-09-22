"""SM2/SM3 primitives used by ATP's ATK implementation.

The third-party ``gmssl`` package exposes the SM2 arithmetic and the
SM2-with-SM3 message signature construction.  This module gives ATP a small,
typed interface around it and replaces the package's non-cryptographic
default nonce generator with ``secrets``.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from gmssl import sm2, sm3


SM2_ALGORITHM = "sm2"
SM2_PRIVATE_KEY_SIZE = 32
SM2_PUBLIC_KEY_SIZE = 65  # uncompressed point: 0x04 || X || Y

_CURVE = sm2.default_ecc_table
_N = int(_CURVE["n"], 16)
_P = int(_CURVE["p"], 16)
_A = int(_CURVE["a"], 16)
_B = int(_CURVE["b"], 16)


def sm3_digest(data: bytes) -> bytes:
    """Return the 32-byte SM3 digest of *data*."""
    return bytes.fromhex(sm3.sm3_hash(list(data)))


def _secure_scalar_hex() -> str:
    """Return a uniformly sampled non-zero scalar in the SM2 group."""
    value = secrets.randbelow(_N - 1) + 1
    return f"{value:064x}"


def _normalize_public_hex(public_key_hex: str) -> str:
    value = public_key_hex.strip().lower()
    # A bare SM2 point is 128 hex characters (X || Y).  Only interpret 04 as
    # the uncompressed-point marker when the input is 130 characters long.
    # A valid bare X coordinate may itself begin with 04.
    if len(value) == 130 and value.startswith("04"):
        value = value[2:]
    if len(value) != 128:
        raise ValueError("SM2 public key must contain 64-byte X/Y coordinates")
    try:
        x = int(value[:64], 16)
        y = int(value[64:], 16)
    except ValueError as exc:
        raise ValueError("SM2 public key is not valid hexadecimal") from exc
    if not (0 <= x < _P and 0 <= y < _P):
        raise ValueError("SM2 public key coordinates are out of range")
    if (y * y - (x * x * x + _A * x + _B)) % _P != 0:
        raise ValueError("SM2 public key point is not on the SM2 curve")
    return value


@dataclass(frozen=True)
class SM2PublicKey:
    """Validated SM2 public key represented by affine X/Y coordinates."""

    public_key_hex: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "public_key_hex", _normalize_public_hex(self.public_key_hex))

    @classmethod
    def from_raw_bytes(cls, data: bytes) -> "SM2PublicKey":
        if len(data) != SM2_PUBLIC_KEY_SIZE or data[0] != 0x04:
            raise ValueError("SM2 public key must be a 65-byte uncompressed point")
        return cls(data[1:].hex())

    def raw_bytes(self) -> bytes:
        return b"\x04" + bytes.fromhex(self.public_key_hex)

    def verify(self, signature: bytes, data: bytes) -> bool:
        verifier = sm2.CryptSM2(
            private_key="",
            public_key=self.public_key_hex,
            asn1=True,
        )
        # gmssl's constructor uses ``public_key.lstrip("04")``.  ``lstrip``
        # removes every leading ``0`` or ``4`` character instead of only an
        # optional SEC1 ``04`` prefix, corrupting valid X coordinates that
        # begin with either nibble.  Restore the already validated bare point.
        verifier.public_key = self.public_key_hex
        try:
            return bool(verifier.verify_with_sm3(signature.hex(), data))
        except (ValueError, TypeError, IndexError):
            return False


@dataclass(frozen=True)
class SM2PrivateKey:
    """SM2 private key with its matching public key."""

    private_key_hex: str
    public_key_hex: str

    def __post_init__(self) -> None:
        private_value = self.private_key_hex.strip().lower()
        if len(private_value) != 64:
            raise ValueError("SM2 private key must be 32 bytes")
        try:
            scalar = int(private_value, 16)
        except ValueError as exc:
            raise ValueError("SM2 private key is not valid hexadecimal") from exc
        if not 1 <= scalar < _N:
            raise ValueError("SM2 private key scalar is out of range")
        public_value = _normalize_public_hex(self.public_key_hex)

        # Reject corrupted key files where the stored public key does not
        # correspond to the private scalar.
        derived = sm2.CryptSM2(private_key=private_value, public_key="")._kg(
            scalar, _CURVE["g"]
        )
        if derived.lower() != public_value:
            raise ValueError("SM2 public key does not match private key")

        object.__setattr__(self, "private_key_hex", private_value)
        object.__setattr__(self, "public_key_hex", public_value)

    @classmethod
    def generate(cls) -> "SM2PrivateKey":
        private_hex = _secure_scalar_hex()
        scalar = int(private_hex, 16)
        public_hex = sm2.CryptSM2(private_key=private_hex, public_key="")._kg(
            scalar, _CURVE["g"]
        )
        return cls(private_hex, public_hex)

    def public_key(self) -> SM2PublicKey:
        return SM2PublicKey(self.public_key_hex)

    def sign(self, data: bytes) -> bytes:
        signer = sm2.CryptSM2(
            private_key=self.private_key_hex,
            public_key=self.public_key_hex,
            asn1=True,
        )
        # See SM2PublicKey.verify(): preserve leading coordinate nibbles that
        # gmssl's constructor would otherwise strip.
        signer.public_key = self.public_key_hex
        # SM2 signatures have two very-low-probability retry conditions.
        for _ in range(16):
            signature_hex = signer.sign_with_sm3(data, _secure_scalar_hex())
            if signature_hex:
                return bytes.fromhex(signature_hex)
        raise RuntimeError("SM2 signature generation failed after repeated retries")
