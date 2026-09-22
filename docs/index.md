# ATP — Agent Transfer Protocol

Secure agent-to-agent communication over the Internet.

ATP enables autonomous agents to discover, authenticate, and exchange messages across organizational boundaries. Think of it as **email for AI agents** — DNS-based discovery, mandatory cryptographic signing, server-mediated delivery.

```
Agent A ──▶ ATP Server A ═══════Internet═══════▶ ATP Server B ──▶ Agent B
              ATS ✅  ATK ✅                       ATS ✅  ATK ✅
```

## Key Features

- **Agent Identity** — Agents are identified by `local@domain` (like email addresses)
- **DNS Discovery** — Find any agent's server via DNS SVCB records, no central registry
- **Mandatory Signing** — Every message is signed with SM2/SM3 by default, verified at every hop
- **Sender Authorization** — ATS policies (DNS TXT records) control who can send on behalf of a domain
- **Store-and-Forward** — Messages are persisted and retried with exponential backoff
- **CLI + SDK** — Use from the command line or import as a Python library

## Install

```bash
pip install atp
```

Requires Python 3.11+.

## 30-Second Demo

```bash
# Generate a key pair
atp keys generate

# Start a local server
atp server start --domain demo.local --port 7443 --local

# Send a message (in another terminal)
atp send bot@demo.local --from user@demo.local --body "Hello ATP!" --server localhost:7443 --local
```

## Next Steps

- [Quick Start Guide](quickstart.md) — Full walkthrough with two servers
- [Configuration Reference](configuration.md) — All options explained
- [DNS Setup Guide](dns-setup.md) — Production DNS configuration
- [Security Model](security.md) — How ATS and ATK work
- [Python SDK](sdk.md) — Use ATP from your code
- [Architecture](architecture.md) — For contributors

## Protocol

ATP is defined in `draft-li-atp-00` (Internet-Draft, Standards Track, March 2026).

Authors: Xiang Li, Lu Sun, Yuqi Qiu — Nankai University, AOSP Laboratory.
