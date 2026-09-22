# Configuration Reference

## Config File

ATP reads configuration from `~/.atp/config.toml`:

```toml title="~/.atp/config.toml"
agent_id = "mybot@example.com"
key_selector = "default"
key_algorithm = "sm2"
local_mode = false
peers_file = ""
dns_override_file = ""

[server]
domain = "example.com"
port = 7443
tls_cert = "/path/to/cert.pem"
tls_key = "/path/to/key.pem"
```

## Directory Structure

```
~/.atp/
├── config.toml          # Global configuration
├── keys/
│   ├── default.sm2.key  # Domain-level SM2 private key
│   ├── default.sm2.pub  # Domain-level SM2 public key published through ATK
│   └── keyring.json     # Key metadata index
├── certs/
│   ├── server.crt       # TLS certificate
│   └── server.key       # TLS private key
└── data/
    └── messages.db      # SQLite message store
```

Directories are created automatically by `atp keys generate` or `atp server start`.

## CLI Options

### `atp server start`

| Option | Default | Description |
|--------|---------|-------------|
| `--domain` | (required) | Server domain name |
| `--port` | `7443` | Listen port |
| `--host` | `0.0.0.0` | Bind address |
| `--local` | `false` | Use local file-based discovery (no real DNS) |
| `--peers` | — | Path to `peers.toml` for local discovery |
| `--dns-override` | — | Path to `dns_override.toml` for local ATS/ATK |
| `--cert` | — | TLS certificate path |
| `--key` | — | TLS private key path |
| `--key-selector` | config `key_selector` | ATK domain-key selector |
| `--key-algorithm` | config `key_algorithm` (`sm2`) | ATK signature algorithm |
| `--log-level` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

### `atp agent`

| Subcommand | Description |
|-----------|-------------|
| `register <agent_id>` | Register an agent with password (prompted interactively) |
| `list` | List all registered agents |
| `remove <agent_id>` | Remove a registered agent |

### `atp send`

| Option | Default | Description |
|--------|---------|-------------|
| `<to>` | (required) | Recipient agent ID (`agent@domain`) |
| `--from` | config `agent_id` | Sender agent ID |
| `--password`, `-P` | — | Agent password for Credential authentication |
| `--body`, `-b` | — | Message body text |
| `--subject`, `-s` | — | Message subject |
| `--payload`, `-p` | — | Path to JSON file with custom payload |
| `--server` | `localhost:7443` | Server URL (`host:port`) |
| `--local` | `false` | Disable TLS verification |
| `--output` | `json` | Output format (`json` or `text`) |

### `atp recv`

| Option | Default | Description |
|--------|---------|-------------|
| `--agent-id` | config `agent_id` | Agent ID to receive for |
| `--server` | `localhost:7443` | Server URL (`host:port`) |
| `--local` | `false` | Disable TLS verification |
| `--wait` | `false` | Block until messages arrive |
| `--limit` | `50` | Maximum messages to return |
| `--output` | `json` | Output format (`json` or `text`) |

### `atp keys`

| Subcommand | Description |
|-----------|-------------|
| `generate --selector <name> [--algorithm sm2|ed25519]` | Generate a key pair (SM2 default) |
| `show --selector <name> [--public]` | Display key information |
| `list` | List all key pairs |
| `rotate --old-selector <old> --new-selector <new>` | Generate new key, keep old |

### `atp status`

| Option | Default | Description |
|--------|---------|-------------|
| `--server` | `localhost:7443` | Server URL (`host:port`) |
| `--local` | `false` | Disable TLS verification |
| `--output` | `text` | Output format (`json` or `text`) |

### `atp inspect`

| Option | Default | Description |
|--------|---------|-------------|
| `<nonce>` | (required) | Message nonce to inspect |
| `--server` | `localhost:7443` | Server URL (`host:port`) |
| `--local` | `false` | Disable TLS verification |
| `--output` | `text` | Output format (`json` or `text`) |

### `atp dns`

| Subcommand | Description |
|-----------|-------------|
| `generate --domain <domain> --ip <ip> [--selector <sel>] [--port <port>]` | Print DNS records to configure |
| `check --domain <domain> [--selector <sel>]` | Verify DNS records exist |

## Priority Order

Configuration values are resolved in this priority (highest first):

1. **CLI arguments** — `--domain`, `--port`, etc.
2. **Config file** — `~/.atp/config.toml`
3. **Defaults** — Built-in default values

## Server Runtime Defaults

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_message_size` | 1,048,576 (1 MB) | Maximum message size in bytes |
| `replay_max_age` | 300 (5 min) | Nonce expiry window in seconds |
| `retry_max_attempts` | 6 | Max delivery retries before bounce |

### Retry Schedule

Failed deliveries use exponential backoff:

| Attempt | Delay |
|---------|-------|
| 1 | 60 seconds |
| 2 | 5 minutes |
| 3 | 30 minutes |
| 4 | 2 hours |
| 5 | 8 hours |
| 6 | 24 hours |

After 6 failures, a bounce notification is sent to the original sender.

## Local Mode Files

### `peers.toml`

Maps domain names to server addresses for local discovery:

```toml
["alice.local"]
host = "127.0.0.1"
port = 7443

["bob.local"]
host = "192.168.1.100"
port = 7443
```

### `dns_override.toml`

Simulates DNS TXT records for local ATS/ATK verification:

```toml
["ats._atp.alice.local"]
record = "v=atp1 allow=ip:127.0.0.1 deny=all"

["default.atk._atp.alice.local"]
record = "v=atp1 k=sm2 p=<SM2_PUBLIC_KEY_BASE64>"
```

Query priority: local override → real DNS → fallback.
