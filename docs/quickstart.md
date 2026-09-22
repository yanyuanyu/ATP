# Quick Start Guide

This guide walks you through running two ATP servers locally and sending messages between them. No DNS configuration needed.

## Prerequisites

- Python 3.11+
- `pip install atp`

## Step 1: Generate Key Pairs

Each server needs its own SM2 key pair for SM2/SM3 message signing.

```bash
# Create directories for two servers
mkdir -p server-a server-b

# Generate keys for Server A
HOME=./server-a atp keys generate --selector default

# Generate keys for Server B
HOME=./server-b atp keys generate --selector default
```

!!! tip
    On Windows, set `HOME` via environment variable or use the default `~/.atp/` location.

## Step 2: Create Peer Configuration

Create a `peers.toml` file so the servers can find each other without DNS:

```toml title="peers.toml"
["alice.local"]
host = "127.0.0.1"
port = 7443

["bob.local"]
host = "127.0.0.1"
port = 7444
```

Create a `dns_override.toml` to simulate ATS and ATK DNS records:

```toml title="dns_override.toml"
# ATS policies — allow localhost
["ats._atp.alice.local"]
record = "v=atp1 allow=ip:127.0.0.1 deny=all"

["ats._atp.bob.local"]
record = "v=atp1 allow=ip:127.0.0.1 deny=all"

# ATK public keys — replace with your actual keys
# Get them with: atp keys show --public
["default.atk._atp.alice.local"]
record = "v=atp1 k=sm2 p=<ALICE_PUBLIC_KEY_BASE64>"

["default.atk._atp.bob.local"]
record = "v=atp1 k=sm2 p=<BOB_PUBLIC_KEY_BASE64>"
```

Get the public keys:

```bash
HOME=./server-a atp keys show --public
HOME=./server-b atp keys show --public
```

Paste the base64 values into `dns_override.toml`.

## Step 3: Start Servers

Open two terminals:

=== "Terminal 1: Server A"

    ```bash
    atp server start \
      --domain alice.local \
      --port 7443 \
      --local \
      --peers peers.toml \
      --dns-override dns_override.toml
    ```

=== "Terminal 2: Server B"

    ```bash
    atp server start \
      --domain bob.local \
      --port 7444 \
      --local \
      --peers peers.toml \
      --dns-override dns_override.toml
    ```

## Step 4: Send a Message

In a third terminal:

```bash
atp send agent@bob.local \
  --from agent@alice.local \
  --body "Hello from Alice!" \
  --server localhost:7443 \
  --local
```

Expected output:

```json
{
  "status": "accepted",
  "nonce": "msg-a1b2c3d4e5f6",
  "timestamp": 1710000000
}
```

## Step 5: Receive the Message

In a fourth terminal:

```bash
atp recv \
  --agent-id agent@bob.local \
  --server localhost:7444 \
  --local
```

Expected output:

```json
[
  {
    "from": "agent@alice.local",
    "to": "agent@bob.local",
    "timestamp": 1710000000,
    "nonce": "msg-a1b2c3d4e5f6",
    "type": "message",
    "payload": {
      "body": "Hello from Alice!"
    },
    "signature": { ... }
  }
]
```

## What Happened

Here's the full flow that just occurred:

```
1. CLI built an unsigned ATPMessage
2. CLI POSTed the message to Server A (localhost:7443) with Credential (password)
3. Server A verified Credential:
   └── Is agent@alice.local a registered agent with valid password? → PASS ✅
4. Server A looked up bob.local in peers.toml → 127.0.0.1:7444
5. Server A signed the message with its domain-level SM2 key using SM3
6. Server A forwarded the signed message to Server B
7. Server B verified independently:
   ├── ATS: Is 127.0.0.1 authorized for alice.local? → PASS ✅
   ├── ATK: Is the SM2/SM3 signature valid? → PASS ✅
   └── Replay: Is this nonce fresh? → PASS ✅
8. Server B delivered the message to agent@bob.local's inbox
8. CLI recv fetched the message from Server B
```

## Next Steps

- [Configuration Reference](configuration.md) — Customize server behavior
- [DNS Setup](dns-setup.md) — Deploy to production with real DNS
- [Python SDK](sdk.md) — Use ATP from your code
