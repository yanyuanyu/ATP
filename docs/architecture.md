# Architecture Overview

This document is for contributors who want to understand the codebase structure.

## Module Dependency Graph

Dependencies are strictly layered. Lower modules never import from higher modules.

```
                            ┌─────────┐
                            │  cli/*  │
                            └────┬────┘
                 ┌───────────────┼───────────────┐
                 ▼               ▼               ▼
           ┌──────────┐   ┌──────────┐    ┌───────────┐
           │  server/* │   │ client/* │    │ storage/* │
           └────┬──┬───┘   └────┬─────┘    └─────┬─────┘
                │  │            │                │
      ┌─────────┘  │     ┌──────┘                │
      ▼            ▼     ▼                       ▼
 ┌───────────┐ ┌───────────┐              ┌───────────┐
 │ security/*│ │ discovery/*│              │  core/*   │
 └─────┬─────┘ └─────┬─────┘              └───────────┘
       │              │                        ▲
       └──────────────┴────────────────────────┘
```

## Package Structure

```
src/atp/
├── core/                   # Zero-dependency protocol primitives
│   ├── errors.py           # Error codes and exception hierarchy
│   ├── identity.py         # AgentID parsing and validation
│   ├── canonicalize.py     # JCS (RFC 8785) JSON canonicalization
│   ├── message.py          # ATPMessage and SignatureEnvelope dataclasses
│   └── signature.py        # SM2/SM3 and Ed25519 Signer/Verifier
│
├── security/               # Security verification pipeline
│   ├── ats.py              # ATS policy parsing and evaluation
│   ├── atk.py              # ATK record parsing and signature verification
│   ├── sm2.py              # SM2 key and SM2/SM3 signature primitives
│   ├── tls.py              # TLS context creation, self-signed cert generation
│   └── replay.py           # Nonce cache with timestamp window
│
├── discovery/              # DNS service discovery
│   ├── dns.py              # BaseDNSResolver, DNSResolver (dnspython), ServerInfo
│   └── local.py            # LocalResolver (file-based), CompositeResolver
│
├── storage/                # Persistence
│   ├── config.py           # ~/.atp/config.toml read/write
│   ├── keys.py             # SM2-default domain key management
│   └── messages.py         # SQLite message store
│
├── server/                 # ATP Server (integration layer)
│   ├── config.py           # RuntimeServerConfig
│   ├── app.py              # ATPServer — wires everything together
│   ├── routes.py           # HTTP route handlers
│   ├── metrics.py          # ServerMetrics — message and security counters
│   ├── delivery.py         # Background delivery manager with retry
│   └── queue.py            # Message queue wrapper
│
├── client/                 # ATP Client
│   ├── client.py           # ATPClient — high-level send/recv
│   └── transport.py        # HTTPTransport — httpx-based HTTP client
│
└── cli/                    # Command-line interface
    ├── main.py             # Click entry point
    ├── server.py           # atp server start
    ├── send.py             # atp send
    ├── recv.py             # atp recv
    ├── keys.py             # atp keys generate/show/list/rotate
    └── dns.py              # atp dns generate/check
```

## Message Flow

### Sending: CLI → Server A → Server B

```
cli/send.py
  │
  ├── ATPMessage.create()           core/message.py
  ├── KeyStorage.load_private_key() storage/keys.py
  ├── Signer.sign(message)          core/signature.py
  │     ├── signable_dict()         core/message.py
  │     ├── canonicalize()          core/canonicalize.py
  │     └── SM2/SM3 sign            gmssl
  │
  └── HTTPTransport.post_message()  client/transport.py
        └── POST /.well-known/atp/v1/message → Server A
```

### Server A receives and validates:

```
server/routes.py: handle_message()
  │
  ├── ATPMessage.from_json()        core/message.py
  ├── ATSVerifier.verify()          security/ats.py
  │     └── dns_resolver.query_txt() discovery/*
  ├── ATKVerifier.verify()          security/atk.py
  │     ├── dns_resolver.query_txt() discovery/*
  │     └── Verifier.verify()       core/signature.py
  ├── ReplayGuard.check()           security/replay.py
  │
  └── Route decision:
      ├── Local domain → enqueue as DELIVERED
      └── Remote domain → enqueue as QUEUED
```

### Delivery Manager transfers to Server B:

```
server/delivery.py: DeliveryManager._delivery_loop()
  │
  ├── MessageStore.get_pending_deliveries()  storage/messages.py
  ├── dns_resolver.query_svcb(domain)        discovery/*
  ├── HTTPTransport.post_message()           client/transport.py
  │
  └── On failure:
      ├── Retry with exponential backoff
      └── Bounce notification after max retries
```

## Key Abstractions

### `BaseDNSResolver`

The central abstraction for DNS. All DNS consumers (ATS, ATK, delivery) depend on this interface, not concrete implementations.

```python
class BaseDNSResolver:
    async def query_svcb(self, domain: str) -> ServerInfo | None: ...
    async def query_txt(self, name: str) -> str | None: ...
```

Implementations:

- `DNSResolver` — Real DNS via dnspython
- `LocalResolver` — File-based (peers.toml + dns_override.toml)
- `CompositeResolver` — Chains local → DNS

This enables local development without real DNS.

### Message Serialization

Python uses `from_id` / `to_id` (because `from` is a reserved keyword), but JSON uses `"from"` / `"to"` per the protocol spec.

```python
msg.from_id = "alice@example.com"   # Python field
msg.to_dict()["from"]               # JSON key = "from"
```

## Design Decisions

See [DIVERGENCE.md](../DIVERGENCE.md) for detailed analysis. Key decisions:

1. **Protocol layer only provides Message transport** — Request/Response and Event/Subscription are application-layer concerns, defined in payload by convention
2. **`type` is always `"message"`** — The protocol doesn't differentiate message types at the envelope level
3. **ATS+ATK verification at every hop** — Symmetric verification model from the RFC
4. **ATP Server does not manage DNS** — Only reads DNS records; users configure them manually via `atp dns generate`

## Development

### Setup

```bash
git clone <repo>
cd atp
pip install -e ".[dev]"
```

### Running Tests

```bash
python -m pytest tests/ -v
```

### Test Strategy

| Layer | Test Approach |
|-------|--------------|
| `core/*` | Pure unit tests, no I/O |
| `storage/*` | `tmp_path` fixture for temp directories |
| `discovery/*` | Mock dnspython, TOML fixtures |
| `security/*` | Mock `BaseDNSResolver` |
| `server/*` | Starlette `TestClient` with mocked verifiers |
| `client/*` | `pytest-httpx` for HTTP mocking |
| `cli/*` | Click `CliRunner` |

### Adding a New Feature

1. Define the interface in the appropriate module
2. Implement with tests
3. Wire into server/routes.py or client/client.py as needed
4. Add CLI command if user-facing
5. Update documentation
