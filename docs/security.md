# Security Model

ATP provides four layers of security, each addressing a different threat:

```
┌──────────────────────────────────────────────────────┐
│  ATK (Agent Transfer Key)                             │
│  "Is this message authentic and untampered?"          │
│  → SM2/SM3 digital signatures (verified at Server B)  │
├──────────────────────────────────────────────────────┤
│  ATS (Agent Transfer Sender Policy)                   │
│  "Is this server authorized to send for this domain?" │
│  → DNS-published authorization (verified at Server B) │
├──────────────────────────────────────────────────────┤
│  Credential                                           │
│  "Is this agent who they claim to be?"                │
│  → Username + password (verified at Server A)         │
├──────────────────────────────────────────────────────┤
│  TLS 1.3                                              │
│  "Is the connection encrypted and authenticated?"     │
│  → Mandatory encrypted transport                      │
└──────────────────────────────────────────────────────┘
```

The security model is **asymmetric by design**:

- **Server A** (agent's own server): verifies **Credential** (username + password via Basic Auth over TLS)
- **Server B** (remote receiving server): verifies **ATS + ATK** (DNS-based sender authorization + message signature)

## ATS — Agent Transfer Sender Policy

### What It Solves

When Server B receives a message claiming to be from `agent@example.com`, how does it know the sending server is actually authorized by `example.com`?

ATS answers this by publishing an authorization policy in DNS.

### How It Works

```
Server B receives a message:
  from: agent@example.com
  source IP: 10.0.0.99

Step 1: Query DNS
  → ats._atp.example.com TXT
  → "v=atp1 allow=ip:192.0.2.0/24 deny=all"

Step 2: Evaluate
  → Is 10.0.0.99 in 192.0.2.0/24? NO
  → deny=all matches → FAIL

Step 3: Reject
  → 550 5.7.26 ATS validation failed
```

### Analogy

ATS is like a company's **authorized sender list**:

> "I am example.com. Only messages from IP range `192.0.2.0/24` are legitimate. Reject everything else."

### Email Equivalent

ATS is directly inspired by **SPF** (Sender Policy Framework, RFC 7208) used in email.

| SPF (Email) | ATS (ATP) |
|-------------|-----------|
| `v=spf1 ip4:192.0.2.0/24 -all` | `v=atp1 allow=ip:192.0.2.0/24 deny=all` |
| Published at `example.com TXT` | Published at `ats._atp.example.com TXT` |

### Policy Directives

| Directive | Meaning |
|-----------|---------|
| `allow=ip:<CIDR>` | Authorize this IP range |
| `deny=ip:<CIDR>` | Block this IP range |
| `allow=domain:<domain>` | Authorize servers of this domain |
| `deny=domain:<domain>` | Block servers of this domain |
| `allow=all` | Authorize all (not recommended) |
| `deny=all` | Block all (use as catch-all after specific allows) |

Directives are evaluated **in order**. The first match determines the result.

### Results

| Result | Action |
|--------|--------|
| **PASS** | Message accepted, proceed to ATK verification |
| **FAIL** | Message rejected with `550 5.7.26` |
| **NEUTRAL** | No ATS record found; accept but flag |

---

## ATK — Agent Transfer Key

### What It Solves

Even if ATS confirms the source server is authorized, how do we know the message content hasn't been tampered with in transit?

ATK solves this with **digital signatures**.

### How It Works

**Sender side (signing)**:

```
1. Build message → remove signature field
2. JCS canonicalize (RFC 8785) → deterministic bytes
3. SM2/SM3 sign with private key → DER signature bytes
4. Attach SignatureEnvelope to message
```

**Receiver side (verification)**:

```
1. Extract key_id from signature: "default.atk._atp.example.com"
2. Query DNS: default.atk._atp.example.com TXT
   → "v=atp1 k=sm2 p=BASE64..."
3. Decode base64 → SM2 public key
4. Remove signature field from message
5. JCS canonicalize → bytes
6. SM2/SM3 verify(public_key, bytes, signature)
   → PASS ✅ or FAIL ❌
```

### Tamper Detection

```
Original:    payload.body = "Transfer $10 to Bob"
Attacker:    payload.body = "Transfer $10000 to Eve"

Verification:
  canonicalize(tampered message) → different bytes
  SM2/SM3 verify → FAIL ❌
  "Signature doesn't match content"
```

### Analogy

ATK is like a **tamper-evident seal** on a package:

> "If the seal is broken, the contents may have been altered."

### Email Equivalent

ATK is inspired by **DKIM** (DomainKeys Identified Mail, RFC 6376).

| DKIM (Email) | ATK (ATP) |
|-------------|-----------|
| `selector._domainkey.example.com` | `selector.atk._atp.example.com` |
| RSA/Ed25519 signature in email header | SM2/SM3 signature in message envelope |
| Covers selected headers + body | Covers all fields (from, to, timestamp, nonce, type, payload) |

### Key Management

| Recommendation | Detail |
|---------------|--------|
| Algorithm | SM2 signature with SM3 digest (default); Ed25519 compatibility is explicit |
| Rotation | Every 90 days |
| Concurrent keys | Maintain current + previous (for in-flight messages) |
| Revocation | Set `t=s` flag in ATK record |
| Expiry | Optional `x=<unix_timestamp>` |

---

## TLS 1.3 — Transport Encryption

### Requirements

| Requirement | Specification |
|-------------|---------------|
| Minimum version | TLS 1.3 (MUST) |
| Certificate validation | CA-issued certificates (MUST) |
| Forward secrecy | ECDHE key exchange (MUST) |
| ALPN | `atp/1` |

### What It Protects

- **Eavesdropping** — All traffic encrypted
- **Man-in-the-middle** — Certificate validation prevents interception
- **Downgrade attacks** — TLS 1.3 minimum enforced

### What It Doesn't Protect

- **Metadata** — ATP Server can see `from`, `to`, `timestamp`, `type`
- **Payload at server** — ATP Servers must inspect messages for routing

For end-to-end confidentiality, applications should encrypt payload content before sending.

---

## Replay Protection

Every ATP message includes:

- **`nonce`** — Cryptographically random unique identifier
- **`timestamp`** — Unix timestamp of message creation

Servers maintain an in-memory nonce cache and reject messages where:

- Timestamp is more than 5 minutes old (default)
- Timestamp is more than 60 seconds in the future
- Nonce has been seen before

---

## Verification at Every Hop

A key security property: **each ATP Server independently verifies ATS and ATK**.

```
Agent A → Server A → Server B → Agent B
           │            │
           ├─ ATS ✅     ├─ ATS ✅
           ├─ ATK ✅     ├─ ATK ✅
           └─ Replay ✅  └─ Replay ✅
```

Server B does **not** trust Server A's verification. It queries DNS and verifies the signature independently. This means:

- A compromised intermediate server cannot forge messages
- Each server enforces its own security policy
- The system is resilient to single points of failure

---

## Threat Model Summary

| Threat | Mitigation |
|--------|-----------|
| Identity spoofing | ATS sender authorization |
| Message tampering | ATK SM2/SM3 signatures |
| Eavesdropping | TLS 1.3 encryption |
| Replay attacks | Nonce + timestamp checking |
| DNS poisoning | DNSSEC (recommended) |
| Key compromise | Key rotation + revocation via `t=s` flag |

## ATS + ATK Together

Neither mechanism alone is sufficient:

| Scenario | ATS only | ATK only | ATS + ATK |
|----------|----------|----------|-----------|
| Authorized server, untampered message | ✅ | ✅ | ✅ |
| Unauthorized server, valid signature | ❌ PASS | ✅ | ❌ Caught by ATS |
| Authorized server, tampered message | ✅ PASS | ❌ Caught | ❌ Caught by ATK |
| Unauthorized server, tampered message | ❌ | ❌ | ❌ Caught by both |
