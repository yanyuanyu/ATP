"""ATK (Agent Transfer Keys) — DKIM-like message authentication for ATP.

Domain owners publish Ed25519 public keys as DNS TXT records.  Sending agents
sign messages; receiving agents look up the key and verify the signature.
"""

from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from atp.core.errors import ATKError, ATPErrorCode
from atp.core.message import ATPMessage
from atp.core.signature import Verifier, VerifyResult
from atp.discovery.dns import BaseDNSResolver

logger = logging.getLogger(__name__)


@dataclass
class ATKRecord:
    """Parsed ATK DNS TXT record."""

    version: str  # "atp1"
    algorithm: str  # "ed25519"
    public_key_b64: str  # base64-encoded raw public key
    flags: list[str] = field(default_factory=list)  # e.g. ["r"] for revoked
    expiry: Optional[int] = None  # Unix timestamp

    @classmethod
    def parse(cls, txt_record: str) -> ATKRecord:
        """Parse a TXT record like ``v=atp1 k=ed25519 p=MCow... [t=s] [x=1710000000]``.

        Raises :class:`ATKError` if any of ``v``, ``k``, or ``p`` is missing.
        """
        fields: dict[str, str] = {}
        for token in txt_record.strip().split():
            if "=" in token:
                key, value = token.split("=", 1)
                fields[key] = value

        for required in ("v", "k", "p"):
            if required not in fields:
                raise ATKError(
                    ATPErrorCode.ATK_KEY_NOT_FOUND,
                    f"ATK record missing required field '{required}'",
                )

        flags_str = fields.get("t", "")
        flags = [f for f in flags_str.split(",") if f] if flags_str else []

        expiry: Optional[int] = None
        if "x" in fields:
            try:
                expiry = int(fields["x"])
            except ValueError as exc:
                raise ATKError(
                    ATPErrorCode.ATK_KEY_NOT_FOUND,
                    f"ATK record has invalid expiry value: {fields['x']}",
                ) from exc

        return cls(
            version=fields["v"],
            algorithm=fields["k"],
            public_key_b64=fields["p"],
            flags=flags,
            expiry=expiry,
        )

    def is_valid(self) -> bool:
        """Return ``True`` if the key is neither revoked nor expired."""
        if "r" in self.flags or "s" in self.flags:
            return False
        if self.expiry is not None and self.expiry <= int(time.time()):
            return False
        return True

    def get_public_key(self) -> Ed25519PublicKey:
        """Decode the base64 public key and return an :class:`Ed25519PublicKey`.

        Raises :class:`ATKError` on decode failure.
        """
        try:
            raw_bytes = base64.b64decode(self.public_key_b64)
            return Ed25519PublicKey.from_public_bytes(raw_bytes)
        except Exception as exc:
            raise ATKError(
                ATPErrorCode.ATK_SIGNATURE_FAILED,
                f"Failed to decode ATK public key: {exc}",
            ) from exc


class ATKVerifier:
    """High-level verifier: fetch ATK record from DNS, then verify the message signature."""

    def __init__(self, dns_resolver: BaseDNSResolver):
        self._resolver = dns_resolver

    async def verify(self, message: ATPMessage) -> VerifyResult:
        """Verify the ATK signature on *message*.

        1. Ensure the message carries a signature.
        2. Bind key_id domain to message sender domain.
        3. Look up the ATK TXT record via the ``key_id``.
        4. Validate the key (not revoked / expired).
        5. Verify the cryptographic signature.
        """
        if message.signature is None:
            return VerifyResult(
                passed=False,
                error_code="550 5.7.28",
                error_message="Message has no signature",
            )

        key_id = message.signature.key_id

        # P0 fix: Verify that the key_id belongs to the sender's domain.
        # Without this check, an attacker could sign with their own domain's
        # key and point key_id to their DNS, bypassing cross-domain trust.
        try:
            _selector, key_domain = self.parse_key_id(key_id)
        except ATKError:
            return VerifyResult(
                passed=False,
                error_code="550 5.7.28",
                error_message=f"Invalid ATK key_id format: {key_id!r}",
            )

        # Extract sender domain from message.from_id
        from atp.core.identity import AgentID
        try:
            sender = AgentID.parse(message.from_id)
        except Exception:
            return VerifyResult(
                passed=False,
                error_code="550 5.7.28",
                error_message=f"Invalid sender address: {message.from_id}",
            )

        if key_domain != sender.domain:
            return VerifyResult(
                passed=False,
                error_code="550 5.7.28",
                error_message=(
                    f"ATK key domain mismatch: key_id domain is '{key_domain}' "
                    f"but sender domain is '{sender.domain}'"
                ),
            )

        # key_id is already the full DNS name, e.g. "default.atk._atp.sender.com"
        try:
            txt = await self._resolver.query_txt(key_id)
        except Exception:
            return VerifyResult(
                passed=False,
                error_code="550 5.7.29",
                error_message="DNS error looking up ATK key",
            )

        if txt is None:
            return VerifyResult(
                passed=False,
                error_code="550 5.7.29",
                error_message="ATK key not found",
            )

        try:
            record = ATKRecord.parse(txt)
        except ATKError:
            return VerifyResult(
                passed=False,
                error_code="550 5.7.29",
                error_message="Failed to parse ATK record",
            )

        if not record.is_valid():
            return VerifyResult(
                passed=False,
                error_code="550 5.7.29",
                error_message="ATK key revoked or expired",
            )

        try:
            public_key = record.get_public_key()
        except ATKError as exc:
            return VerifyResult(
                passed=False,
                error_code="550 5.7.28",
                error_message=str(exc),
            )

        return Verifier.verify(message, public_key)

    @staticmethod
    def parse_key_id(key_id: str) -> tuple[str, str]:
        """Parse ``selector.atk._atp.domain.com`` into ``(selector, domain)``.

        Raises :class:`ATKError` if the expected pattern is not found.
        """
        separator = ".atk._atp."
        if separator not in key_id:
            raise ATKError(
                ATPErrorCode.ATK_KEY_NOT_FOUND,
                f"Invalid ATK key_id format: {key_id!r} (expected '<selector>.atk._atp.<domain>')",
            )
        idx = key_id.index(separator)
        selector = key_id[:idx]
        domain = key_id[idx + len(separator):]
        return selector, domain
