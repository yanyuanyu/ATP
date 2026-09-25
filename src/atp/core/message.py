"""ATP message format and serialization."""

import json
import time
from dataclasses import dataclass, field
from typing import Optional
from uuid import uuid4

from atp.core.errors import ATPErrorCode, MessageFormatError


@dataclass
class SignatureEnvelope:
    """Envelope containing an ATK signature over a message."""

    key_id: str  # e.g. "default.atk._atp.example.com"
    algorithm: str  # "sm2" (default) or "ed25519"
    signature: str  # base64-encoded
    headers: list[str]  # e.g. ["from", "to", "timestamp", "nonce", "type"]
    timestamp: int

    def to_dict(self) -> dict:
        """Serialize the signature envelope to a dictionary."""
        return {
            "key_id": self.key_id,
            "algorithm": self.algorithm,
            "signature": self.signature,
            "headers": self.headers,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SignatureEnvelope":
        """Deserialize a signature envelope from a dictionary."""
        if not isinstance(data, dict):
            raise MessageFormatError(
                ATPErrorCode.INVALID_MESSAGE_FORMAT,
                "signature must be an object",
            )
        required_fields = ["key_id", "algorithm", "signature", "headers", "timestamp"]
        for field_name in required_fields:
            if field_name not in data:
                raise MessageFormatError(
                    ATPErrorCode.INVALID_MESSAGE_FORMAT,
                    f"Missing signature field: {field_name!r}",
                )
        for field_name in ("key_id", "algorithm", "signature"):
            if not isinstance(data[field_name], str) or not data[field_name]:
                raise MessageFormatError(
                    ATPErrorCode.INVALID_MESSAGE_FORMAT,
                    f"Signature field {field_name!r} must be a non-empty string",
                )
        if (
            not isinstance(data["headers"], list)
            or not all(isinstance(item, str) and item for item in data["headers"])
        ):
            raise MessageFormatError(
                ATPErrorCode.INVALID_MESSAGE_FORMAT,
                "Signature field 'headers' must be a list of non-empty strings",
            )
        if isinstance(data["timestamp"], bool) or not isinstance(data["timestamp"], int):
            raise MessageFormatError(
                ATPErrorCode.INVALID_MESSAGE_FORMAT,
                "Signature field 'timestamp' must be an integer",
            )
        return cls(
            key_id=data["key_id"],
            algorithm=data["algorithm"],
            signature=data["signature"],
            headers=data["headers"],
            timestamp=data["timestamp"],
        )


@dataclass
class ATPMessage:
    """An ATP protocol message."""

    from_id: str
    to_id: str
    timestamp: int
    nonce: str  # "msg-{uuid4_hex[:12]}"
    type: str
    payload: dict
    signature: Optional[SignatureEnvelope] = None
    cc: list[str] = field(default_factory=list)
    in_reply_to: Optional[str] = None
    task_id: Optional[str] = None
    context_id: Optional[str] = None
    routing: Optional[dict] = None

    VALID_TYPES = frozenset({"message", "request", "response", "event"})

    @classmethod
    def create(
        cls,
        from_id: str,
        to_id: str,
        payload: dict,
        cc: list[str] | None = None,
        message_type: str = "message",
        in_reply_to: str | None = None,
        task_id: str | None = None,
        context_id: str | None = None,
    ) -> "ATPMessage":
        """Create a new ATPMessage with protocol correlation fields."""
        if message_type not in cls.VALID_TYPES:
            raise MessageFormatError(
                ATPErrorCode.INVALID_MESSAGE_FORMAT,
                f"Unsupported message type: {message_type!r}",
            )
        if message_type == "response" and not in_reply_to:
            raise MessageFormatError(
                ATPErrorCode.INVALID_MESSAGE_FORMAT,
                "Response messages require in_reply_to",
            )
        return cls(
            from_id=from_id,
            to_id=to_id,
            timestamp=int(time.time()),
            nonce=f"msg-{uuid4().hex[:12]}",
            type=message_type,
            payload=payload,
            cc=cc or [],
            in_reply_to=in_reply_to,
            task_id=task_id,
            context_id=context_id,
        )

    def to_dict(self) -> dict:
        """Serialize to dict.

        Uses "from" and "to" as keys (matching the protocol spec).
        Omits None/empty optional fields.
        """
        d: dict = {
            "from": self.from_id,
            "to": self.to_id,
            "timestamp": self.timestamp,
            "nonce": self.nonce,
            "type": self.type,
            "payload": self.payload,
        }
        if self.signature is not None:
            d["signature"] = self.signature.to_dict()
        if self.cc:
            d["cc"] = self.cc
        if self.in_reply_to is not None:
            d["in_reply_to"] = self.in_reply_to
        if self.task_id is not None:
            d["task_id"] = self.task_id
        if self.context_id is not None:
            d["context_id"] = self.context_id
        if self.routing is not None:
            d["routing"] = self.routing
        return d

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict) -> "ATPMessage":
        """Deserialize from dict.

        Reads "from"/"to" keys. Raises MessageFormatError on missing required fields.
        """
        if not isinstance(data, dict):
            raise MessageFormatError(
                ATPErrorCode.INVALID_MESSAGE_FORMAT,
                "Message must be a JSON object",
            )

        required_fields = ["from", "to", "timestamp", "nonce", "type", "payload"]
        for field_name in required_fields:
            if field_name not in data:
                raise MessageFormatError(
                    ATPErrorCode.INVALID_MESSAGE_FORMAT,
                    f"Missing required field: {field_name!r}",
                )

        for field_name in ("from", "to", "nonce", "type"):
            if not isinstance(data[field_name], str) or not data[field_name]:
                raise MessageFormatError(
                    ATPErrorCode.INVALID_MESSAGE_FORMAT,
                    f"Field {field_name!r} must be a non-empty string",
                )
        if isinstance(data["timestamp"], bool) or not isinstance(data["timestamp"], int):
            raise MessageFormatError(
                ATPErrorCode.INVALID_MESSAGE_FORMAT,
                "Field 'timestamp' must be an integer",
            )
        if not isinstance(data["payload"], dict):
            raise MessageFormatError(
                ATPErrorCode.INVALID_MESSAGE_FORMAT,
                "Field 'payload' must be an object",
            )

        message_type = data["type"]
        if message_type not in cls.VALID_TYPES:
            raise MessageFormatError(
                ATPErrorCode.INVALID_MESSAGE_FORMAT,
                f"Unsupported message type: {message_type!r}",
            )
        if message_type == "response" and not data.get("in_reply_to"):
            raise MessageFormatError(
                ATPErrorCode.INVALID_MESSAGE_FORMAT,
                "Response messages require in_reply_to",
            )
        if "cc" in data and (
            not isinstance(data["cc"], list)
            or not all(isinstance(item, str) and item for item in data["cc"])
        ):
            raise MessageFormatError(
                ATPErrorCode.INVALID_MESSAGE_FORMAT,
                "Field 'cc' must be a list of non-empty strings",
            )
        if "routing" in data and data["routing"] is not None and not isinstance(data["routing"], dict):
            raise MessageFormatError(
                ATPErrorCode.INVALID_MESSAGE_FORMAT,
                "Field 'routing' must be an object",
            )
        for field_name in ("in_reply_to", "task_id", "context_id"):
            if field_name in data and data[field_name] is not None and not isinstance(data[field_name], str):
                raise MessageFormatError(
                    ATPErrorCode.INVALID_MESSAGE_FORMAT,
                    f"Field {field_name!r} must be a string",
                )

        signature = None
        if "signature" in data and data["signature"] is not None:
            signature = SignatureEnvelope.from_dict(data["signature"])

        return cls(
            from_id=data["from"],
            to_id=data["to"],
            timestamp=data["timestamp"],
            nonce=data["nonce"],
            type=message_type,
            payload=data["payload"],
            signature=signature,
            cc=data.get("cc", []),
            in_reply_to=data.get("in_reply_to"),
            task_id=data.get("task_id"),
            context_id=data.get("context_id"),
            routing=data.get("routing"),
        )

    @classmethod
    def from_json(cls, json_str: str) -> "ATPMessage":
        """Deserialize from JSON string."""
        return cls.from_dict(json.loads(json_str))

    def signable_dict(self) -> dict:
        """Same as to_dict() but without the 'signature' key."""
        d = self.to_dict()
        d.pop("signature", None)
        return d
