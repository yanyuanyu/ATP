"""Tests for the ATP CLI."""

import pytest
from click.testing import CliRunner

from atp.cli.main import cli


@pytest.fixture
def runner():
    return CliRunner()


def test_cli_help(runner):
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "ATP" in result.output


def test_cli_version(runner):
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "1.0.0a8" in result.output


def test_keys_help(runner):
    result = runner.invoke(cli, ["keys", "--help"])
    assert result.exit_code == 0


def test_dns_help(runner):
    result = runner.invoke(cli, ["dns", "--help"])
    assert result.exit_code == 0


def test_server_help(runner):
    result = runner.invoke(cli, ["server", "--help"])
    assert result.exit_code == 0


def test_send_help(runner):
    result = runner.invoke(cli, ["send", "--help"])
    assert result.exit_code == 0
    assert "--no-verify" in result.output
    assert "--server" in result.output


def test_recv_help(runner):
    result = runner.invoke(cli, ["recv", "--help"])
    assert result.exit_code == 0
    assert "--no-verify" in result.output
    assert "--server" in result.output


def test_keys_generate(runner, tmp_path, monkeypatch):
    """Test key generation via CLI."""
    monkeypatch.setenv("HOME", str(tmp_path))
    # On Windows, also patch Path.home()
    import pathlib

    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    result = runner.invoke(cli, ["keys", "generate", "--selector", "test"])
    assert result.exit_code == 0
    assert "generated" in result.output.lower() or "Key pair" in result.output


def test_status_help(runner):
    result = runner.invoke(cli, ["status", "--help"])
    assert result.exit_code == 0
    assert "--no-verify" in result.output
    assert "--server" in result.output


def test_inspect_help(runner):
    result = runner.invoke(cli, ["inspect", "--help"])
    assert result.exit_code == 0
    assert "--no-verify" in result.output
    assert "--server" in result.output


def test_agent_help(runner):
    result = runner.invoke(cli, ["agent", "--help"])
    assert result.exit_code == 0
    assert "agent" in result.output.lower()


def test_agent_register_help(runner):
    result = runner.invoke(cli, ["agent", "register", "--help"])
    assert result.exit_code == 0
    assert "--server" in result.output
    assert "--no-verify" in result.output

def test_agent_list_help(runner):
    result = runner.invoke(cli, ["agent", "list", "--help"])
    assert result.exit_code == 0
    assert "--server" in result.output
    assert "--no-verify" in result.output


def test_dns_generate(runner, tmp_path, monkeypatch):
    """Test DNS record generation."""
    import pathlib

    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    # First generate a key
    runner.invoke(cli, ["keys", "generate"])
    # Then generate DNS records
    result = runner.invoke(
        cli, ["dns", "generate", "--domain", "example.com", "--ip", "1.2.3.4"]
    )
    assert result.exit_code == 0
    assert "_atp.example.com" in result.output
    assert "ats._atp.example.com" in result.output
    assert "atk._atp.example.com" in result.output
    assert "k=sm2" in result.output
