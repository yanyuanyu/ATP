"""ATP message signing and verification using Ed25519."""

import base64
import time
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from atp.core.canonicalize import canonicalize
from atp.core.message import ATPMessage, SignatureEnvelope


@dataclass
class VerifyResult:
    """Result of a signature verification."""

    passed: bool
    error_code: str | None = None
    error_message: str | None = None


class Signer:
    """Signs ATP messages using an Ed25519 private key."""

    def __init__(self, private_key: Ed25519PrivateKey, selector: str, domain: str):
        self._private_key = private_key
        self._selector = selector
        self._domain = domain

    def sign(self, message: ATPMessage) -> ATPMessage:
        """Sign an ATP message.

        1. Get the signable dict (without signature)
        2. Canonicalize it to bytes
        3. Sign with the private key
        4. Create a SignatureEnvelope and attach it to the message
        5. Return the message
        """
        signable = message.signable_dict()
        canonical_bytes = canonicalize(signable)
        signature_bytes = self._private_key.sign(canonical_bytes)

        envelope = SignatureEnvelope(
            key_id=f"{self._selector}.atk._atp.{self._domain}",
            algorithm="ed25519",
            signature=base64.b64encode(signature_bytes).decode(),
            headers=list(signable.keys()),
            timestamp=int(time.time()),
        )

        message.signature = envelope
        return message


class Verifier:
    """Verifies ATP message signatures using Ed25519 public keys."""

    @staticmethod
    def verify(message: ATPMessage, public_key: Ed25519PublicKey) -> VerifyResult:
        """Verify an ATP message signature.

        1. Extract the signature envelope
        2. Get the signable dict and canonicalize it
        3. Decode the base64 signature
        4. Verify with the public key
        5. Return VerifyResult
        """
        if message.signature is None:
            return VerifyResult(
                passed=False,
                error_code="550 5.7.28",
                error_message="Message has no signature",
            )

        signable = message.signable_dict()

        if set(message.signature.headers) != set(signable.keys()) or len(
            message.signature.headers
        ) != len(signable):
            return VerifyResult(
                passed=False,
                error_code="550 5.7.28",
                error_message="Signature headers do not match message fields",
            )

        canonical_bytes = canonicalize(signable)

        try:
            sig_bytes = base64.b64decode(message.signature.signature)
        except Exception as exc:
            return VerifyResult(
                passed=False,
                error_code="550 5.7.28",
                error_message=f"Invalid base64 signature: {exc}",
            )

        try:
            public_key.verify(sig_bytes, canonical_bytes)
        except InvalidSignature:
            return VerifyResult(
                passed=False,
                error_code="550 5.7.28",
                error_message="Signature verification failed",
            )

        return VerifyResult(passed=True)
