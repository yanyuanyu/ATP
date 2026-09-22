# Python SDK Reference

ATP can be used as a Python library in addition to the CLI.

## Installation

```bash
pip install atp
```

## Quick Example

```python
import asyncio
from atp.client import ATPClient

async def main():
    client = ATPClient(
        agent_id="mybot@example.com",
        server_url="localhost:7443",
        local_mode=True,
    )

    # Send a message
    result = await client.send(
        to="target@remote.org",
        body="Hello from Python!",
        subject="Greeting",
    )
    print(result)
    # {"status": "accepted", "nonce": "msg-a1b2c3d4e5f6", "timestamp": 1710000000}

    # Receive messages
    messages = await client.recv(limit=10)
    for msg in messages:
        print(f"From: {msg.from_id}")
        print(f"Payload: {msg.payload}")

    await client.close()

asyncio.run(main())
```

## Core Classes

### `ATPClient`

High-level client for sending and receiving messages.

```python
from atp.client import ATPClient

client = ATPClient(
    agent_id="bot@example.com",      # Your agent identity
    config_dir=Path("~/.atp"),       # Config directory (optional)
    server_url="localhost:7443",     # Server address (optional, defaults to config)
    local_mode=False,                # Disable TLS verification for dev
)
```

#### `send(to, payload=None, body=None, subject=None) -> dict`

Send a message to another agent.

```python
# Simple text message
result = await client.send("target@remote.org", body="Hello!")

# Custom payload
result = await client.send("target@remote.org", payload={
    "action": "get_weather",
    "params": {"location": "Beijing"},
})

# Result
# {"status": "accepted", "nonce": "msg-...", "timestamp": 1710000000}
# or
# {"status": "error", "error": "connection refused", "nonce": "msg-..."}
```

#### `recv(limit=50, wait=False, timeout=30.0) -> list[ATPMessage]`

Receive messages from the server.

```python
# Get all pending messages
messages = await client.recv()

# Wait for messages (long-poll)
messages = await client.recv(wait=True, timeout=60.0)

# Limit results
messages = await client.recv(limit=5)
```

#### `close()`

Close the HTTP connection.

```python
await client.close()
```

### `ATPMessage`

The message data structure.

```python
from atp.core.message import ATPMessage

# Create a message
msg = ATPMessage.create(
    from_id="sender@example.com",
    to_id="recipient@remote.org",
    payload={"body": "Hello", "subject": "Greeting"},
    cc=["observer@third.party"],
)

# Access fields
print(msg.from_id)      # "sender@example.com"
print(msg.to_id)        # "recipient@remote.org"
print(msg.timestamp)    # 1710000000
print(msg.nonce)        # "msg-a1b2c3d4e5f6"
print(msg.type)         # "message"
print(msg.payload)      # {"body": "Hello", "subject": "Greeting"}
print(msg.signature)    # SignatureEnvelope or None

# Serialize
json_str = msg.to_json()
msg_dict = msg.to_dict()

# Deserialize
msg2 = ATPMessage.from_json(json_str)
msg3 = ATPMessage.from_dict(msg_dict)
```

!!! note "Field naming"
    In Python, fields are `from_id` and `to_id` (to avoid conflicting with the `from` keyword). In JSON serialization, they become `"from"` and `"to"` per the protocol spec.

### `Signer` and `Verifier`

Low-level cryptographic operations. In production, signing is performed
by the ATP Server (domain-level key), not by the client. These primitives
are exposed for server-side use and testing.

```python
from atp.core.signature import Signer, Verifier
from atp.security.sm2 import SM2PrivateKey

# Server-side: sign a message with domain key before transfer
private_key = SM2PrivateKey.generate()
signer = Signer(private_key, selector="default", domain="example.com")
msg = ATPMessage.create("bot@example.com", "target@remote.org", {"body": "hi"})
signer.sign(msg)  # Signs in-place, attaches signature

# Receiving server: verify signature with sender's DNS public key
public_key = private_key.public_key()
result = Verifier.verify(msg, public_key)
print(result.passed)        # True
print(result.error_code)    # None
```

### `AgentID`

Parse and validate agent identifiers.

```python
from atp.core.identity import AgentID

agent = AgentID.parse("Alice@Example.COM")
print(agent.local_part)  # "alice" (lowercase)
print(agent.domain)      # "example.com" (lowercase)
print(str(agent))        # "alice@example.com"
```

## Storage Classes

### `KeyStorage`

Manage SM2 keys by default, with optional Ed25519 compatibility.

```python
from pathlib import Path
from atp.storage.keys import KeyStorage

keys = KeyStorage(Path("~/.atp/keys"))

# Generate
info = keys.generate(selector="default", algorithm="sm2")

# Load
private_key = keys.load_private_key("default", "sm2")
public_key = keys.load_public_key("default", "sm2")

# Get base64 public key (for DNS ATK record)
b64 = keys.get_public_key_b64("default", "sm2")
print(b64)

# List all keys
for k in keys.list_keys():
    print(f"{k.selector} ({k.algorithm}): created {k.created_at}")
```

### `MessageStore`

SQLite message persistence.

```python
from pathlib import Path
from atp.storage.messages import MessageStore, MessageStatus

store = MessageStore(Path("~/.atp/data/messages.db"))
store.init_db()

# Store a message
msg = ATPMessage.create("a@x.com", "b@y.com", {"body": "hi"})
row_id = store.enqueue(msg, MessageStatus.DELIVERED)

# Retrieve
stored = store.get_by_nonce(msg.nonce)
print(stored.status)  # MessageStatus.DELIVERED

# Get messages for an agent
messages = store.get_messages_for_agent("b@y.com", limit=10)
```

### `ConfigStorage`

Read and write `~/.atp/config.toml`.

```python
from atp.storage.config import ConfigStorage, ATPConfig

config_store = ConfigStorage()
config_store.ensure_dirs()  # Create ~/.atp/ subdirectories

# Load (returns defaults if file missing)
config = config_store.load()
print(config.agent_id)
print(config.server.domain)

# Modify and save
config.agent_id = "mybot@example.com"
config.server.domain = "example.com"
config_store.save(config)
```

## Server (Advanced)

Start an ATP server programmatically:

```python
from atp.server.config import RuntimeServerConfig
from atp.server.app import ATPServer

config = RuntimeServerConfig(
    domain="example.com",
    port=7443,
    local_mode=True,
)

server = ATPServer(config)
server.run()  # Blocking — starts uvicorn
```

## Observability API

Query server metrics and message status programmatically:

```python
import httpx, asyncio

async def check_server():
    async with httpx.AsyncClient(verify=False) as client:
        # Server stats
        resp = await client.get("https://localhost:7443/.well-known/atp/v1/stats")
        stats = resp.json()
        print(f"Messages received: {stats['messages']['received']}")
        print(f"ATS failures: {stats['security']['ats_fail']}")
        print(f"Queue backlog: {stats['queue']['queued']}")

        # Inspect specific message
        resp = await client.get(
            "https://localhost:7443/.well-known/atp/v1/inspect",
            params={"nonce": "msg-a1b2c3d4e5f6"}
        )
        msg = resp.json()
        print(f"Status: {msg['status']}, Retries: {msg['retry_count']}")

asyncio.run(check_server())
```

## Error Handling

All ATP errors inherit from `ATPError`:

```python
from atp.core.errors import (
    ATPError,
    ATSError,
    ATKError,
    MessageFormatError,
    StorageError,
    DiscoveryError,
)

try:
    AgentID.parse("invalid")
except MessageFormatError as e:
    print(e.code)     # ATPErrorCode.INVALID_MESSAGE_FORMAT
    print(e.message)  # "Invalid agent ID format: ..."
```
