"""CLI entrypoint."""

from __future__ import annotations

import json
import sys

import click

from .config import get_settings
from .models import LaunchRequest
from .stonkfun_client import StonkFunClient
from .launch import preview, run_launch
from .accounts import link, get as get_account
from .security import guard


@click.group()
def main() -> None:
    """STONKBOT — StonkFun launcher with Bankr-style rails."""


@main.command()
def doctor() -> None:
    """Readiness check (safe)."""
    s = get_settings()
    click.echo(json.dumps(s.redacted(), indent=2))
    ok, reason = guard.can_launch()
    click.echo(f"can_launch: {ok} ({reason})")
    try:
        client = StonkFunClient()
        pairs = client.list_pairs(launchable=True)
        click.echo(f"stonkfun pairs launchable: {len(pairs)}")
        client.close()
    except Exception as e:
        click.echo(f"stonkfun error: {e}")
        sys.exit(1)


@main.command("pairs")
def list_pairs() -> None:
    """List launchable quote pairs."""
    client = StonkFunClient()
    try:
        for p in client.list_pairs(launchable=True):
            click.echo(f"{p.symbol:12} {p.mint}  [{p.category or '-'}]")
    finally:
        client.close()


@main.command("link")
@click.argument("x_handle")
@click.argument("solana_wallet")
def link_cmd(x_handle: str, solana_wallet: str) -> None:
    """Link an X handle to a Solana wallet."""
    acc = link(x_handle, solana_wallet)
    click.echo(f"Linked @{acc.x_handle} → {acc.solana_wallet}")


@main.command("who")
@click.argument("x_handle")
def who_cmd(x_handle: str) -> None:
    """Show linked wallet for a handle."""
    acc = get_account(x_handle)
    if not acc:
        click.echo("Not linked.")
        sys.exit(1)
    click.echo(f"@{acc.x_handle} → {acc.solana_wallet}")


@main.command("preview")
@click.option("--name", required=True)
@click.option("--symbol", required=True)
@click.option("--quote", required=True, help="Quote symbol or mint (e.g. GMEX or mint address)")
@click.option("--creator", required=True, help="Creator Solana wallet (user's linked wallet)")
def preview_cmd(name: str, symbol: str, quote: str, creator: str) -> None:
    """Dry preview of a launch (nothing on chain)."""
    client = StonkFunClient()
    try:
        pairs = client.list_pairs(launchable=True)
        match = next((p for p in pairs if p.symbol.upper() == quote.upper() or p.mint == quote), None)
        if not match:
            click.echo(f"Quote not found / not launchable: {quote}")
            sys.exit(1)
        req = LaunchRequest(
            name=name,
            symbol=symbol.upper(),
            quote_mint=match.mint,
            creator_wallet=creator,
            mode="standard",
        )
        result = preview(req)
        click.echo(result.message)
        click.echo(json.dumps(result.model_dump(exclude={"raw"}), indent=2))
    finally:
        client.close()


if __name__ == "__main__":
    main()
