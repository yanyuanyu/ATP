import time

import pytest

from atp.core.errors import MessageFormatError
from atp.core.message import ATPMessage


def test_draft02_correlation_fields_round_trip():
    message = ATPMessage.create(
        from_id="service@example.com",
        to_id="client@example.net",
        payload={"status": "success"},
        message_type="response",
        in_reply_to="req-123",
        task_id="task-1",
        context_id="context-1",
    )

    parsed = ATPMessage.from_json(message.to_json())

    assert parsed.type == "response"
    assert parsed.in_reply_to == "req-123"
    assert parsed.task_id == "task-1"
    assert parsed.context_id == "context-1"


def test_response_requires_in_reply_to():
    with pytest.raises(MessageFormatError):
        ATPMessage.from_dict(
            {
                "from": "service@example.com",
                "to": "client@example.net",
                "timestamp": int(time.time()),
                "nonce": "resp-123",
                "type": "response",
                "payload": {},
            }
        )


def test_unknown_message_type_is_rejected():
    with pytest.raises(MessageFormatError):
        ATPMessage.create(
            from_id="a@example.com",
            to_id="b@example.net",
            payload={},
            message_type="unknown",
        )
