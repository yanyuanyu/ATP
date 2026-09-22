"""CLI commands for managing the ATP server."""

import click


@click.group("server")
def server_group():
    """Manage ATP server."""
    pass


@server_group.command("start")
@click.option("--domain", required=True, help="Server domain name")
@click.option("--port", default=7443, type=int, help="Listen port")
@click.option("--host", default="0.0.0.0", help="Bind address")
@click.option("--no-tls", is_flag=True, help="Run without TLS (plaintext HTTP). NOT recommended for production.")
@click.option("--cert", type=click.Path(), help="TLS certificate path")
@click.option("--key", "tls_key", type=click.Path(), help="TLS private key path")
@click.option("--key-selector", default=None, help="ATK domain-key selector")
@click.option(
    "--key-algorithm",
    type=click.Choice(["sm2", "ed25519"], case_sensitive=False),
    default=None,
    help="ATK signature algorithm (config default: sm2)",
)
@click.option("--admin-token", default=None, help="Admin bearer token for /stats, /inspect, /agents endpoints")
@click.option(
    "--log-level",
    default="INFO",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"]),
)
def start_cmd(
    domain,
    port,
    host,
    no_tls,
    cert,
    tls_key,
    key_selector,
    key_algorithm,
    admin_token,
    log_level,
):
    """Start the ATP server."""
    from atp.server.app import ATPServer
    from atp.server.config import RuntimeServerConfig
    from atp.storage.config import ConfigStorage

    # Enforce TLS unless explicitly opted out
    if not no_tls and not (cert and tls_key):
        raise click.ClickException(
            "TLS is required. Provide --cert and --key, or use --no-tls for development.\n"
            "WARNING: --no-tls exposes credentials and messages in plaintext."
        )

    if no_tls:
        click.echo("WARNING: Running without TLS. Credentials and messages are transmitted in plaintext.")
        click.echo("         Do NOT use --no-tls in production.")

    config_storage = ConfigStorage()
    atp_config = config_storage.load()

    cli_args = {
        "domain": domain,
        "port": port,
        "host": host,
        "cert": cert if not no_tls else None,
        "key": tls_key if not no_tls else None,
        "key_selector": key_selector,
        "key_algorithm": key_algorithm,
        "log_level": log_level,
        "admin_token": admin_token,
    }
    runtime_config = RuntimeServerConfig.from_cli_and_config(cli_args, atp_config)

    server = ATPServer(runtime_config)
    protocol = "HTTPS" if (cert and tls_key and not no_tls) else "HTTP"
    click.echo(f"Starting ATP Server: {domain} on {protocol}://{host}:{port}")
    server.run()
