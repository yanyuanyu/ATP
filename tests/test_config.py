"""Tests for atp.storage.config."""

from pathlib import Path

from atp.storage.config import ATPConfig, ConfigStorage, ServerConfig


class TestConfigStorage:
    def test_save_and_load_round_trip(self, tmp_path: Path) -> None:
        """Save a config, load it back, verify all fields match."""
        storage = ConfigStorage(config_dir=tmp_path)

        config = ATPConfig(
            agent_id="agent@example.com",
            server=ServerConfig(
                domain="example.com",
                port=8443,
                tls_cert="/path/to/cert.pem",
                tls_key="/path/to/key.pem",
                admin_token="secret-token",
                max_message_size=12345,
            ),
            local_mode=True,
            peers_file="/etc/atp/peers.json",
            dns_override_file="/etc/atp/dns.json",
            key_selector="rotate-2024",
            key_algorithm="ed25519",
        )

        storage.save(config)

        loaded = storage.load()

        assert loaded.agent_id == "agent@example.com"
        assert loaded.server.domain == "example.com"
        assert loaded.server.port == 8443
        assert loaded.server.tls_cert == "/path/to/cert.pem"
        assert loaded.server.tls_key == "/path/to/key.pem"
        assert loaded.server.admin_token == "secret-token"
        assert loaded.server.max_message_size == 12345
        assert loaded.local_mode is True
        assert loaded.peers_file == "/etc/atp/peers.json"
        assert loaded.dns_override_file == "/etc/atp/dns.json"
        assert loaded.key_selector == "rotate-2024"
        assert loaded.key_algorithm == "ed25519"

    def test_load_returns_defaults_when_file_missing(self, tmp_path: Path) -> None:
        """load() should return default ATPConfig when config.toml does not exist."""
        storage = ConfigStorage(config_dir=tmp_path)

        config = storage.load()

        assert config.agent_id == ""
        assert config.server.domain == ""
        assert config.server.port == 7443
        assert config.local_mode is False
        assert config.key_selector == "default"
        assert config.key_algorithm == "sm2"

    def test_ensure_dirs_creates_subdirectories(self, tmp_path: Path) -> None:
        """ensure_dirs() should create keys/, certs/, and data/ directories."""
        storage = ConfigStorage(config_dir=tmp_path)

        storage.ensure_dirs()

        assert (tmp_path / "keys").is_dir()
        assert (tmp_path / "certs").is_dir()
        assert (tmp_path / "data").is_dir()

    def test_config_dir_property(self, tmp_path: Path) -> None:
        """config_dir property should return the configured directory."""
        storage = ConfigStorage(config_dir=tmp_path)
        assert storage.config_dir == tmp_path

    def test_save_creates_parent_dir(self, tmp_path: Path) -> None:
        """save() should create the config directory if it does not exist."""
        config_dir = tmp_path / "nested" / "config"
        storage = ConfigStorage(config_dir=config_dir)

        storage.save(ATPConfig())

        assert (config_dir / "config.toml").exists()
