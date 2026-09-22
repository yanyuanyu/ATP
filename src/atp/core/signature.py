"""ATP message signing and verification using SM2/SM3 or Ed25519."""

import base64
import time
from dataclasses import dataclass
from typing import Union

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from atp.core.canonicalize import canonicalize
from atp.core.message import ATPMessage, SignatureEnvelope
from atp.security.sm2 import SM2PrivateKey, SM2PublicKey


PrivateKey = Union[SM2PrivateKey, Ed25519PrivateKey]
PublicKey = Union[SM2PublicKey, Ed25519PublicKey]


@dataclass
class VerifyResult:
    """Result of a signature verification."""

    passed: bool
    error_code: str | None = None
    error_message: str | None = None


def key_algorithm(key: PrivateKey | PublicKey) -> str:
    """Return the ATP algorithm identifier for a supported key object."""
    if isinstance(key, (SM2PrivateKey, SM2PublicKey)):
        return "sm2"
    if isinstance(key, (Ed25519PrivateKey, Ed25519PublicKey)):
        return "ed25519"
    raise TypeError(f"Unsupported signing key type: {type(key).__name__}")


class Signer:
    """Sign ATP messages with a domain-level SM2 or Ed25519 key."""

    def __init__(self, private_key: PrivateKey, selector: str, domain: str):
        self._private_key = private_key
        self._selector = selector
        self._domain = domain
        self.algorithm = key_algorithm(private_key)

    def sign(self, message: ATPMessage) -> ATPMessage:
        """Canonicalize and sign a message, then attach its ATK envelope."""
        signable = message.signable_dict()
        canonical_bytes = canonicalize(signable)
        signature_bytes = self._private_key.sign(canonical_bytes)

        message.signature = SignatureEnvelope(
            key_id=f"{self._selector}.atk._atp.{self._domain}",
            algorithm=self.algorithm,
            signature=base64.b64encode(signature_bytes).decode("ascii"),
            headers=list(signable.keys()),
            timestamp=int(time.time()),
        )
        return message


class Verifier:
    """Verify ATP message signatures with the declared key algorithm."""

    @staticmethod
    def verify(message: ATPMessage, public_key: PublicKey) -> VerifyResult:
        if message.signature is None:
            return VerifyResult(
                passed=False,
                error_code="550 5.7.28",
                error_message="Message has no signature",
            )

        expected_algorithm = key_algorithm(public_key)
        if message.signature.algorithm != expected_algorithm:
            return VerifyResult(
                passed=False,
                error_code="550 5.7.28",
                error_message=(
                    f"Signature algorithm mismatch: envelope uses "
                    f"'{message.signature.algorithm}' but key uses "
                    f"'{expected_algorithm}'"
                ),
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
            signature_bytes = base64.b64decode(
                message.signature.signature, validate=True
            )
        except Exception as exc:
            return VerifyResult(
                passed=False,
                error_code="550 5.7.28",
                error_message=f"Invalid base64 signature: {exc}",
            )

        try:
            if isinstance(public_key, SM2PublicKey):
                if not public_key.verify(signature_bytes, canonical_bytes):
                    raise InvalidSignature
            else:
                public_key.verify(signature_bytes, canonical_bytes)
        except (InvalidSignature, ValueError, TypeError):
            return VerifyResult(
                passed=False,
                error_code="550 5.7.28",
                error_message="Signature verification failed",
            )

        return VerifyResult(passed=True)
