"""Authenticated delivery attempts, separate from immutable business identity."""
import base64

from atp.core.canonicalize import canonicalize
from atp.core.message import ATPMessage
from atp.security.sm2 import sm3_digest


def message_digest(message: ATPMessage) -> str:
    return sm3_digest(canonicalize(message.signable_dict())).hex()


def make_transfer_proof(message, signer) -> str:
    proof = ATPMessage.create(
        message.from_id, message.to_id,
        {"purpose": "atp-transfer-v1", "nonce": message.nonce,
         "sm3": message_digest(message)},
    )
    signer.sign(proof)
    return base64.b64encode(proof.to_json().encode("utf-8")).decode("ascii")


def parse_transfer_proof(encoded: str, message: ATPMessage) -> ATPMessage:
    if len(encoded) > 8192:
        raise ValueError("Transfer proof too large")
    proof = ATPMessage.from_json(base64.b64decode(encoded, validate=True).decode("utf-8"))
    if (proof.from_id != message.from_id or proof.to_id != message.to_id
            or proof.type != "message"
            or proof.payload != {"purpose": "atp-transfer-v1",
                                 "nonce": message.nonce, "sm3": message_digest(message)}):
        raise ValueError("Transfer proof does not bind this message")
    return proof
