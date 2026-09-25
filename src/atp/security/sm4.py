"""SM4 payload protection for ATP cross-domain delivery.

The ATP server signs the encrypted envelope with ATK (SM2/SM3 by default).
The payload itself is encrypted with a fresh SM4-CBC session key and an SM3
HMAC.  That session key is transported with the recipient domain's SM2 public
key.  The server holding the recipient domain key decrypts the payload before
local Agent delivery; intermediate ATP servers only route the ciphertext.

This module deliberately keeps the envelope explicit and self-describing so
legacy Ed25519 signing remains usable when payload encryption is disabled.
"""

from __future__ import annotations

import base64
import hmac
import json
import secrets
from typing import Any

from gmssl import sm2, sm4

from atp.core.canonicalize import canonicalize
from atp.security.sm2 import SM2PrivateKey, SM2PublicKey


SM4_ENVELOPE_VERSION = "atp1"
SM4_ALGORITHM = "sm4-cbc-sm3"
SM4_KEY_SIZE = 16
SM4_BLOCK_SIZE = 16


class SM4PayloadError(ValueError):
    """Raised when an encrypted ATP payload is malformed or unauthenticated."""


def message_aad(*, from_id: str, to_id: str, timestamp: int, nonce: str, message_type: str) -> bytes:
    """Build stable authenticated data binding ciphertext to its ATP envelope."""

    return canonicalize(
        {
            "from": from_id,
            "to": to_id,
            "timestamp": timestamp,
            "nonce": nonce,
            "type": message_type,
        }
    )


def _sm3_digest(data: bytes) -> bytes:
    from gmssl import sm3

    return bytes.fromhex(sm3.sm3_hash(list(data)))


def _sm3_hmac(key: bytes, data: bytes) -> bytes:
    """HMAC-SM3 using the standard 64-byte hash block size."""

    block_size = 64
    if len(key) > block_size:
        key = _sm3_digest(key)
    key = key.ljust(block_size, b"\x00")
    inner = _sm3_digest(bytes(byte ^ 0x36 for byte in key) + data)
    return _sm3_digest(bytes(byte ^ 0x5C for byte in key) + inner)


def _derive_subkeys(session_key: bytes) -> tuple[bytes, bytes]:
    """Separate the SM4 encryption and SM3 authentication keys."""

    enc_key = _sm3_digest(b"ATP-SM4-ENC\x00" + session_key)[:SM4_KEY_SIZE]
    mac_key = _sm3_digest(b"ATP-SM4-MAC\x00" + session_key)
    return enc_key, mac_key


def _b64encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _b64decode(value: Any, field: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise SM4PayloadError(f"SM4 field {field!r} must be a non-empty base64 string")
    try:
        return base64.b64decode(value, validate=True)
    except Exception as exc:
        raise SM4PayloadError(f"SM4 field {field!r} is not valid base64") from exc


def encrypt_payload(
    payload: dict[str, Any],
    *,
    recipient_public_key: SM2PublicKey,
    aad: bytes,
) -> dict[str, Any]:
    """Encrypt a JSON payload with SM4 and wrap its session key with SM2."""

    if not isinstance(payload, dict):
        raise SM4PayloadError("ATP payload must be an object")

    session_key = secrets.token_bytes(SM4_KEY_SIZE)
    enc_key, mac_key = _derive_subkeys(session_key)
    iv = secrets.token_bytes(SM4_BLOCK_SIZE)
    plaintext = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")

    cipher = sm4.CryptSM4()
    cipher.set_key(enc_key, sm4.SM4_ENCRYPT)
    ciphertext = cipher.crypt_cbc(iv, plaintext)
    tag = _sm3_hmac(mac_key, aad + iv + ciphertext)

    key_cipher = sm2.CryptSM2(private_key="", public_key=recipient_public_key.public_key_hex)
    # gmssl returns ``None`` for the SM2 KDF's negligible all-zero output;
    # retry with a fresh ephemeral point instead of serializing ``None``.
    wrapped_key = None
    for _ in range(8):
        wrapped_key = key_cipher.encrypt(session_key)
        if wrapped_key is not None:
            break
    if wrapped_key is None:
        raise SM4PayloadError("SM2 session-key wrapping failed after retries")
    return {
        "_atp_encrypted": True,
        "version": SM4_ENVELOPE_VERSION,
        "algorithm": SM4_ALGORITHM,
        "key_wrap": "sm2",
        "wrapped_key": _b64encode(wrapped_key),
        "iv": _b64encode(iv),
        "ciphertext": _b64encode(ciphertext),
        "tag": _b64encode(tag),
    }


def decrypt_payload(
    envelope: dict[str, Any],
    *,
    recipient_private_key: SM2PrivateKey,
    aad: bytes,
) -> dict[str, Any]:
    """Verify and decrypt an SM4 payload envelope with the domain SM2 key."""

    if not isinstance(envelope, dict) or envelope.get("_atp_encrypted") is not True:
        raise SM4PayloadError("payload is not an ATP encrypted envelope")
    if envelope.get("version") != SM4_ENVELOPE_VERSION:
        raise SM4PayloadError("unsupported SM4 envelope version")
    if envelope.get("algorithm") != SM4_ALGORITHM or envelope.get("key_wrap") != "sm2":
        raise SM4PayloadError("unsupported ATP payload encryption algorithm")

    wrapped_key = _b64decode(envelope.get("wrapped_key"), "wrapped_key")
    iv = _b64decode(envelope.get("iv"), "iv")
    ciphertext = _b64decode(envelope.get("ciphertext"), "ciphertext")
    tag = _b64decode(envelope.get("tag"), "tag")
    if len(iv) != SM4_BLOCK_SIZE or not ciphertext or len(ciphertext) % SM4_BLOCK_SIZE:
        raise SM4PayloadError("invalid SM4 IV or ciphertext length")
    if len(tag) != 32:
        raise SM4PayloadError("invalid SM3 tag length")

    key_cipher = sm2.CryptSM2(
        private_key=recipient_private_key.private_key_hex,
        public_key=recipient_private_key.public_key_hex,
    )
    try:
        session_key = key_cipher.decrypt(wrapped_key)
    except Exception as exc:
        raise SM4PayloadError("SM2 session-key unwrap failed") from exc
    if len(session_key) != SM4_KEY_SIZE:
        raise SM4PayloadError("invalid SM4 session-key length")

    enc_key, mac_key = _derive_subkeys(session_key)
    expected_tag = _sm3_hmac(mac_key, aad + iv + ciphertext)
    if not hmac.compare_digest(tag, expected_tag):
        raise SM4PayloadError("SM4 payload authentication failed")

    cipher = sm4.CryptSM4()
    cipher.set_key(enc_key, sm4.SM4_DECRYPT)
    try:
        plaintext = cipher.crypt_cbc(iv, ciphertext)
        result = json.loads(plaintext.decode("utf-8"))
    except Exception as exc:
        raise SM4PayloadError("SM4 payload decryption failed") from exc
    if not isinstance(result, dict):
        raise SM4PayloadError("decrypted ATP payload must be an object")
    return result
