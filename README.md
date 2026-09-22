<div align="center">

# ATP: Agent Transfer Protocol

**Secure agent-to-agent communication over the Internet.**

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![IETF Draft](https://img.shields.io/badge/IETF-draft--li--atp-orange.svg)](https://datatracker.ietf.org/doc/draft-li-atp/)

A communication protocol for agent-to-agent transfer.<br>
DNS-based discovery, SM2/SM3 signing by default, server-mediated delivery.

```
  Agent A           ATP Server A          ATP Server B          Agent B
    |                    |                     |                    |
    |---[1. Submit]----->|                     |                    |
    |   (unsigned+cred)  |                     |                    |
    |              2. Credential Verify        |                    |
    |                    |                     |                    |
    |              3. DNS SVCB Discover        |                    |
    |              4. Domain-key Sign (ATK)    |                    |
    |                    |                     |                    |
    |                    |--[5. Transfer]----->|                    |
    |                    |   TLS 1.3 + POST    |                    |
    |                    |   (signed message)  |                    |
    |                    |                     |                    |
    |                    |               6. ATS+ATK Verify (DNS)   |
    |                    |               7. Sender Authenticated   |
    |                    |                     |                    |
    |<---[202 Accepted]--|                     |---[8. Deliver]--->|
    |                    |                     |                    |
```

[Quick Start](#quick-start) · [Python SDK](#python-sdk) · [CLI Reference](#cli-reference) · [Documentation](#documentation) · [Collaboration](#collaboration)

</div>

---

## Why ATP?

Agents need a standard way to communicate across the Internet: securely, without a central registry, using infrastructure that already exists.

| Feature | How |
|---------|-----|
| **Identity** | `local@domain` format, powered by DNS |
| **Discovery** | DNS SVCB records, no central registry needed |
| **Signing** | SM2/SM3 on every message by default, verified on cross-domain transfer |
| **Authorization** | ATS policies in DNS, control who can send for your domain |
| **Delivery** | Store-and-forward with retry, messages don't get lost |

## Install

```bash
pip install agent-transfer-protocol
```

> Requires Python 3.11+

## Quick Start

```bash
# 1. Start a server
atp server start --domain example.com --port 7443

# 2. Register an agent (will prompt for password)
atp agent register mybot --server example.com
# Or non-interactively: atp agent register mybot --server example.com -p mypassword

# 3. Send a message (from another terminal)
atp send agent@remote.org \
  --from mybot \
  --password mypassword \
  --server example.com \
  --body "Hello!"

# 4. Check server status
atp status --server example.com
```

### Try It: Two Servers Talking

Run two servers locally and send messages between them, no DNS needed:

```bash
# Terminal 1: Start Server A
atp server start --domain alice.local --port 7443

# Terminal 2: Start Server B
atp server start --domain bob.local --port 7444

# Register agents
atp agent register agent --server 127.0.0.1:7443 --no-verify -p alice_pass
atp agent register agent --server 127.0.0.1:7444 --no-verify -p bob_pass

# Terminal 3: Alice sends to Bob
atp send agent@bob.local \
  --from agent \
  --password alice_pass \
  --server 127.0.0.1:7443 \
  --no-verify \
  --body "Hello Bob!"

# Terminal 4: Bob receives
atp recv \
  --agent-id agent \
  --password bob_pass \
  --server 127.0.0.1:7444 \
  --no-verify
```

## Python SDK

```python
import asyncio
from atp.client import ATPClient

async def main():
    client = ATPClient(
        agent_id="mybot",
        password="mypassword",
        server="example.com",
    )

    # Send
    result = await client.send("target@remote.org", body="Hello from SDK")
    print(result)  # {"status": "accepted", "nonce": "msg-..."}

    # Receive
    messages = await client.recv()
    for msg in messages:
        print(f"{msg.from_id}: {msg.payload}")

    await client.close()

asyncio.run(main())
```

## CLI Reference

### Core Commands

| Command | Description |
|---------|-------------|
| `atp server start` | Start ATP server |
| `atp send <to>` | Send a message |
| `atp recv` | Receive messages |

### Agent Management

| Command | Description |
|---------|-------------|
| `atp agent register <name>` | Register an agent with credentials |
| `atp agent list` | List registered agents |

### Key Management (Server)

SM2 signing keys are auto-generated on first server startup. Ed25519 remains
available only as an explicit compatibility option.

| Command | Description |
|---------|-------------|
| `atp keys generate` | Generate SM2 key pair (default) |
| `atp keys show` | Show key info |
| `atp keys list` | List all keys |
| `atp keys rotate` | Rotate to a new key |

### Operations

| Command | Description |
|---------|-------------|
| `atp status` | Show server status and metrics |
| `atp inspect <nonce>` | Inspect a specific message |

### DNS

| Command | Description |
|---------|-------------|
| `atp dns generate` | Generate DNS records for your domain |
| `atp dns check` | Verify DNS records are configured |

Run `atp <command> --help` for options.

## Connection Options

| `--server` format | Protocol | Port |
|-------------------|----------|------|
| `example.com` | HTTPS | 7443 (default) |
| `example.com:8443` | HTTPS | 8443 |
| `https://example.com` | HTTPS | 7443 |
| `http://example.com` | HTTP (warns) | 7443 |

- Default: verify TLS certificates. Invalid cert prompts for confirmation.
- `--no-verify`: skip certificate verification silently.
- Agent names without `@` are auto-completed with the server domain.

## Production Deployment

For production, configure DNS records. ATP generates them for you:

```bash
# Step 1: Generate the records
atp dns generate --domain example.com --ip 203.0.113.1

# Step 2: Add them to your DNS provider (Cloudflare, Route53, etc.)

# Step 3: Verify
atp dns check --domain example.com

# Step 4: Start with TLS
atp server start --domain example.com --cert server.crt --key server.key
```

See the [DNS Setup Guide](docs/dns-setup.md) for details.

## Security

ATP provides four layers of security, inspired by email's battle-tested approach:

| Layer | ATP | Email Equivalent | Purpose |
|-------|-----|-----------------|---------|
| Transport | TLS 1.3 | STARTTLS | Encrypted connections |
| Authentication | Credential | SMTP AUTH | Agent identity (username + password) |
| Authorization | ATS | SPF | Who can send for a domain |
| Integrity | ATK (SM2/SM3) | DKIM | Message signing & verification |

Agents authenticate to their server with credentials (password). The server
signs messages with its domain-level SM2 key before forwarding. Remote servers
retrieve the SM2 public key from DNS and verify independently via ATS+ATK.

## Documentation

| | |
|---|---|
| [Quick Start](docs/quickstart.md) | Full walkthrough with two servers |
| [Configuration](docs/configuration.md) | CLI options, config file, retry policy |
| [DNS Setup](docs/dns-setup.md) | Production DNS configuration |
| [Security Model](docs/security.md) | ATS, ATK, TLS explained in depth |
| [Python SDK](docs/sdk.md) | API reference for developers |
| [Architecture](docs/architecture.md) | Module design for contributors |

## Protocol Specification

ATP is defined as an IETF Internet-Draft (Standards Track):

> **Agent Transfer Protocol (ATP)**
> draft-li-atp · March 2026
> Xiang Li, Lu Sun, Yuqi Qiu, Nankai University, AOSP Laboratory
>
> [IETF Datatracker](https://datatracker.ietf.org/doc/draft-li-atp/) · [Full Text](../Agent%20Transfer%20Protocol%20(ATP).md)

## Collaboration

- **[Iman Schrock (@FutureEnterprises)](https://github.com/FutureEnterprises), EMILIA Protocol** — collaborated on the [EP receipt over ATP composition demo](https://github.com/emiliaprotocol/emilia-protocol/tree/main/examples/ep-over-atp) for the IETF 126 Hackathon. The demo carries an EMILIA human-authorization receipt as an opaque ATP payload and verifies the two layers independently: ATP authenticates the sending agent and domain, while the EMILIA receipt proves authorization of the exact action.

## Contributing

```bash
git clone https://github.com/NKU-AOSP-Lab/AgentTransferProtocol.git
cd atp
pip install -e ".[dev]"
python -m pytest tests/ -v    # run the full test suite
```

See [Architecture](docs/architecture.md) for module design and development guide.

## License

[MIT](LICENSE)
