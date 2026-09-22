# DNS Setup Guide

This guide covers configuring DNS records for production ATP deployment.

## Overview

ATP requires three DNS records per domain:

| Record | Type | Purpose |
|--------|------|---------|
| `_atp.<domain>` | SVCB | Service discovery — where is the ATP server? |
| `ats._atp.<domain>` | TXT | Sender authorization — who can send on behalf of this domain? |
| `<selector>.atk._atp.<domain>` | TXT | Public key — verify message signatures |

This is analogous to email's MX + SPF + DKIM setup.

## Step-by-Step

### 1. Generate Keys

```bash
atp keys generate
```

### 2. Generate DNS Records

```bash
atp dns generate --domain example.com --ip 203.0.113.1
```

Output:

```
Add the following records to your DNS provider:

  _atp.example.com.  IN SVCB 1 atp.example.com. (
      port=7443 alpn="atp/1" atp-capabilities="message"
  )

  ats._atp.example.com.  IN TXT "v=atp1 allow=ip:203.0.113.1 deny=all"

  default.atk._atp.example.com.  IN TXT "v=atp1 k=sm2 p=BASE64..."
```

### 3. Add Records to Your DNS Provider

Go to your DNS provider (Cloudflare, AWS Route53, Google Cloud DNS, Aliyun DNS, etc.) and add the three records.

!!! warning "SVCB Support"
    Not all DNS providers support SVCB records yet. If yours doesn't, you can use SRV as a fallback, or rely on the `_agent.<domain>` fallback query.

#### Cloudflare Example

| Type | Name | Content |
|------|------|---------|
| SVCB | `_atp.example.com` | `1 atp.example.com. port=7443 alpn="atp/1"` |
| TXT | `ats._atp.example.com` | `v=atp1 allow=ip:203.0.113.1 deny=all` |
| TXT | `default.atk._atp.example.com` | `v=atp1 k=sm2 p=BASE64...` |

#### AWS Route53 Example

Create three records in your hosted zone with the same values as above.

### 4. Verify DNS Propagation

Wait a few minutes for DNS propagation, then verify:

```bash
atp dns check --domain example.com
```

Expected output:

```
Checking DNS records for example.com:

  SVCB  _atp.example.com           ✅ Found (atp.example.com:7443)
  ATS   ats._atp.example.com       ✅ Found (allow=ip:203.0.113.1 deny=all)
  ATK   default.atk._atp.example.com ✅ Found
```

### 5. Start Server

```bash
atp server start --domain example.com --port 7443 --cert server.crt --key server.key
```

## Record Details

### SVCB Record (Service Discovery)

```dns
_atp.example.com. IN SVCB 1 atp.example.com. (
    port=7443
    alpn="atp/1"
    atp-capabilities="message"
)
```

| Field | Description |
|-------|-------------|
| `_atp.<domain>` | Query name (standard prefix) |
| Priority `1` | Highest priority endpoint |
| `atp.example.com.` | Target hostname of the ATP server |
| `port=7443` | Service port (default: 7443) |
| `alpn="atp/1"` | Protocol identifier |
| `atp-capabilities` | Supported capabilities |

Fallback query: `_agent.<domain>` (for backward compatibility).

### ATS Record (Sender Authorization)

```dns
ats._atp.example.com. IN TXT "v=atp1 allow=ip:203.0.113.1 deny=all"
```

Controls which servers are authorized to send messages on behalf of `example.com`.

| Directive | Meaning |
|-----------|---------|
| `v=atp1` | ATS version 1 (required) |
| `allow=ip:<CIDR>` | Allow from this IP range |
| `deny=ip:<CIDR>` | Deny from this IP range |
| `allow=domain:<domain>` | Allow from servers of this domain |
| `deny=domain:<domain>` | Deny from servers of this domain |
| `allow=all` | Allow all sources (not recommended) |
| `deny=all` | Deny all (used as catch-all after specific allows) |

**Evaluation**: Directives are evaluated in order. First match wins.

**Examples**:

```dns
# Allow specific IP only
ats._atp.example.com. IN TXT "v=atp1 allow=ip:203.0.113.1 deny=all"

# Allow IP range + partner domain
ats._atp.example.com. IN TXT "v=atp1 allow=ip:203.0.113.0/24 allow=domain:partner.com deny=all"

# Allow all (development only, not recommended)
ats._atp.example.com. IN TXT "v=atp1 allow=all"
```

### ATK Record (Public Key)

```dns
default.atk._atp.example.com. IN TXT "v=atp1 k=sm2 p=BASE64..."
```

Publishes the SM2 public key used to verify SM2/SM3 message signatures.

| Field | Description |
|-------|-------------|
| `default` | Key selector (supports multiple concurrent keys) |
| `v=atp1` | ATK version 1 (required) |
| `k=sm2` | Key algorithm |
| `p=<base64>` | Base64-encoded uncompressed SM2 public point (65 bytes) |
| `t=s` | (Optional) Key is revoked — do not use for new signatures |
| `x=<timestamp>` | (Optional) Key expiry Unix timestamp |

## Key Rotation

Rotate keys every 90 days for security:

```bash
# Generate new key
atp keys rotate --old-selector default --new-selector 2026q2

# Add new ATK record to DNS
atp dns generate --domain example.com --ip 203.0.113.1 --selector 2026q2

# Update DNS: add new record, keep old record for a transition period

# After transition: mark old key as revoked
# Update old ATK record: add t=s flag
```

During rotation, maintain both old and new ATK records so in-flight messages signed with the old key can still be verified.

## Delegation

If you use a third-party ATP service provider:

```dns
# CNAME delegation
_atp.mycompany.com. IN CNAME _atp.atp-provider.net.

# Or SVCB alias
_atp.mycompany.com. IN SVCB 0 _atp.atp-provider.net.
```

Your agents keep their `@mycompany.com` identity while the provider handles routing.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `atp dns check` shows ❌ for all | DNS not propagated yet | Wait 5-10 minutes, try again |
| ATS FAIL on valid messages | Server IP not in ATS record | Check your server's public IP, update ATS CIDR |
| ATK FAIL on signed messages | Public key mismatch | Re-run `atp dns generate`, update ATK record |
| SVCB not found | Provider doesn't support SVCB | Use SRV record as fallback, or configure peers.toml |
