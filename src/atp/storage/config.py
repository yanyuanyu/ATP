"""ATP configuration storage — reads and manages ~/.atp/config.toml."""

from dataclasses import dataclass, field
from pathlib import Path
import tomllib


@dataclass
class ServerConfig:
    domain: str = ""
    port: int = 7443
    tls_cert: str = ""
    tls_key: str = ""
    admin_token: str = ""
    max_message_size: int = 1_048_576  # 1 MiB


@dataclass
class ATPConfig:
    agent_id: str = ""
    server: ServerConfig = field(default_factory=ServerConfig)
    local_mode: bool = False
    peers_file: str = ""
    dns_override_file: str = ""
    key_selector: str = "default"
    key_algorithm: str = "sm2"


class ConfigStorage:
    """Reads and manages ~/.atp/config.toml."""

    def __init__(self, config_dir: Path | None = None):
        self._config_dir = config_dir or (Path.home() / ".atp")

    @property
    def config_dir(self) -> Path:
        return self._config_dir

    def load(self) -> ATPConfig:
        """Load from config.toml. Return defaults if file missing."""
        config_path = self._config_dir / "config.toml"
        if not config_path.exists():
            return ATPConfig()

        with open(config_path, "rb") as f:
            data = tomllib.load(f)

        server_data = data.get("server", {})
        server = ServerConfig(
            domain=server_data.get("domain", ""),
            port=server_data.get("port", 7443),
            tls_cert=server_data.get("tls_cert", ""),
            tls_key=server_data.get("tls_key", ""),
            admin_token=server_data.get("admin_token", ""),
            max_message_size=server_data.get("max_message_size", 1_048_576),
        )

        return ATPConfig(
            agent_id=data.get("agent_id", ""),
            server=server,
            local_mode=data.get("local_mode", False),
            peers_file=data.get("peers_file", ""),
            dns_override_file=data.get("dns_override_file", ""),
            key_selector=data.get("key_selector", "default"),
            key_algorithm=data.get("key_algorithm", "sm2"),
        )

    def save(self, config: ATPConfig) -> None:
        """Write config to config.toml."""
        self._config_dir.mkdir(parents=True, exist_ok=True)
        config_path = self._config_dir / "config.toml"

        lines: list[str] = []
        lines.append(f'agent_id = "{config.agent_id}"')
        lines.append(f"local_mode = {str(config.local_mode).lower()}")
        lines.append(f'peers_file = "{config.peers_file}"')
        lines.append(f'dns_override_file = "{config.dns_override_file}"')
        lines.append(f'key_selector = "{config.key_selector}"')
        lines.append(f'key_algorithm = "{config.key_algorithm}"')
        lines.append("")
        lines.append("[server]")
        lines.append(f'domain = "{config.server.domain}"')
        lines.append(f"port = {config.server.port}")
        lines.append(f'tls_cert = "{config.server.tls_cert}"')
        lines.append(f'tls_key = "{config.server.tls_key}"')
        lines.append("")

        config_path.write_text("\n".join(lines), encoding="utf-8")

    def ensure_dirs(self) -> None:
        """Create ~/.atp/{keys,certs,data}/ directories if needed."""
        for subdir in ("keys", "certs", "data"):
            (self._config_dir / subdir).mkdir(parents=True, exist_ok=True)
