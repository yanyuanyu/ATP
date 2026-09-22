"""CLI commands for DNS record management."""

import asyncio

import click


@click.group("dns")
def dns_group():
    """DNS record management."""
    pass


@dns_group.command("generate")
@click.option("--domain", required=True, help="Your domain")
@click.option("--ip", required=True, help="Server public IP")
@click.option("--selector", default="default", help="Key selector")
@click.option(
    "--algorithm",
    type=click.Choice(["sm2", "ed25519"], case_sensitive=False),
    default="sm2",
    show_default=True,
)
@click.option("--port", default=7443, type=int)
def generate_cmd(domain, ip, selector, algorithm, port):
    """Generate DNS records to configure at your DNS provider."""
    from atp.storage.config import ConfigStorage
    from atp.storage.keys import KeyStorage

    config = ConfigStorage()
    config.ensure_dirs()
    keys = KeyStorage(config.config_dir / "keys")

    # Ensure key exists
    try:
        pub_b64 = keys.get_public_key_b64(selector, algorithm)
    except Exception:
        click.echo(
            f"No {algorithm} key found for selector '{selector}'. Generating..."
        )
        keys.generate(selector, algorithm)
        pub_b64 = keys.get_public_key_b64(selector, algorithm)

    click.echo("\nAdd the following records to your DNS provider:\n")
    click.echo(f"  _atp.{domain}.  IN SVCB 1 atp.{domain}. (")
    click.echo(f'      port={port} alpn="atp/1" atp-capabilities="message"')
    click.echo("  )")
    click.echo()
    click.echo(f"  ats._atp.{domain}.  IN TXT \"v=atp1 allow=ip:{ip} deny=all\"")
    click.echo()
    click.echo(
        f"  {selector}.atk._atp.{domain}.  IN TXT "
        f"\"v=atp1 k={algorithm} p={pub_b64}\""
    )
    click.echo()


@dns_group.command("check")
@click.option("--domain", required=True)
@click.option("--selector", default="default")
def check_cmd(domain, selector):
    """Check if DNS records are properly configured."""
    from atp.discovery.dns import DNSResolver

    async def _check():
        resolver = DNSResolver()

        # SVCB
        svcb = await resolver.query_svcb(domain)
        if svcb:
            click.echo(f"  SVCB  _atp.{domain}  Found ({svcb.host}:{svcb.port})")
        else:
            click.echo(f"  SVCB  _atp.{domain}  Not found")

        # ATS
        ats = await resolver.query_txt(f"ats._atp.{domain}")
        if ats:
            click.echo(f"  ATS   ats._atp.{domain}  Found ({ats})")
        else:
            click.echo(f"  ATS   ats._atp.{domain}  Not found")

        # ATK
        atk = await resolver.query_txt(f"{selector}.atk._atp.{domain}")
        if atk:
            from atp.security.atk import ATKRecord

            try:
                record = ATKRecord.parse(atk)
                click.echo(
                    f"  ATK   {selector}.atk._atp.{domain}  "
                    f"Found (algorithm={record.algorithm})"
                )
            except Exception:
                click.echo(
                    f"  ATK   {selector}.atk._atp.{domain}  Found but invalid"
                )
        else:
            click.echo(f"  ATK   {selector}.atk._atp.{domain}  Not found")

    click.echo(f"Checking DNS records for {domain}:\n")
    asyncio.run(_check())
