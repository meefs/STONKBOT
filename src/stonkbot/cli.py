"""CLI entrypoint — operator tooling. Never prints a secret."""

from __future__ import annotations

import json
import os
import sys

import click

from .config import get_settings
from .db import permissions_report
from .fees import outstanding, pending_total, referral_earnings
from .idempotency import stuck
from .launch import preview
from .models import LaunchRequest
from .security import guard
from .stonkfun_client import StonkFunClient, StonkFunError
from .vault import encryption_status as vault_encryption_status


@click.group()
def main() -> None:
    """STONKBOT — StonkFun launcher with Bankr-style rails."""


@main.command()
def doctor() -> None:
    """Readiness check. Safe to run in production — reveals no secrets."""
    settings = get_settings()
    click.echo(json.dumps(settings.redacted(), indent=2))

    problems: list[str] = []

    if not settings.agent_vault_key:
        problems.append("AGENT_VAULT_KEY is not set — agent wallets cannot be created")
    elif len(settings.agent_vault_key) < 32:
        problems.append("AGENT_VAULT_KEY is shorter than 32 characters")

    if not settings.dry_run:
        click.echo("\n!! DRY_RUN is OFF — launches will spend real SOL")

    # --- storage backend ---------------------------------------------------
    # The dangerous misconfiguration is a serverless deploy still on SQLite:
    # the vault would be recreated empty on every invocation, stranding wallets
    # and disarming the double-payment guard.
    if settings.database_url:
        click.echo("store: postgres")
        if not settings.cron_secret:
            problems.append(
                "CRON_SECRET unset — /api/poll refuses every request without it"
            )
    else:
        click.echo(f"store: sqlite ({settings.data_dir})")
        if os.environ.get("VERCEL"):
            problems.append(
                "running on Vercel without DATABASE_URL — function disks are "
                "ephemeral, so the wallet vault would not survive an invocation"
            )

    ok, reason = guard.peek()
    click.echo(f"can_launch: {ok} ({reason})")

    # --- data protection ---------------------------------------------------
    try:
        encryption = vault_encryption_status()
        click.echo(
            f"wallets: {encryption['total_wallets']} "
            f"(scrypt {encryption['scrypt_v2']}, legacy {encryption['legacy_sha256_v1']})"
        )
        if encryption["legacy_sha256_v1"]:
            click.echo(
                "  note: legacy-encrypted wallets upgrade automatically on next use"
            )
    except Exception as e:
        problems.append(f"vault unreadable: {e}")

    for entry in permissions_report():
        if not entry["ok"]:
            problems.append(
                f"{entry['path']} is {entry['mode']}, expected {entry['expected']} "
                "— other accounts on this host can read it"
            )

    try:
        with StonkFunClient() as client:
            pairs = client.list_pairs(launchable=True)
            click.echo(f"stonkfun launchable pairs: {len(pairs)}")
            stats = client.get_stats()
            config = stats.get("config", {})
            click.echo(f"stonkfun api launches enabled: {config.get('apiLaunchesEnabled')}")
            if config.get("apiLaunchesEnabled") is False:
                problems.append("StonkFun has API launches disabled right now")
    except StonkFunError as e:
        problems.append(f"StonkFun API unreachable: {e}")

    try:
        unpaid = pending_total()
        if unpaid:
            click.echo(f"unsettled service fees: {unpaid:.4f} SOL")
    except Exception as e:
        click.echo(f"fee ledger unreadable: {e}")

    for entry in stuck():
        problems.append(f"launch stuck in 'running': {entry['key']}")

    if problems:
        click.echo("\nProblems:")
        for problem in problems:
            click.echo(f"  - {problem}")
        sys.exit(1)
    click.echo("\nAll checks passed.")


@main.command("pairs")
def list_pairs_cmd() -> None:
    """List launchable quote pairs."""
    with StonkFunClient() as client:
        for pair in client.list_pairs(launchable=True):
            click.echo(f"{pair.symbol:12} {pair.mint}  [{pair.category or '-'}]")


@main.command("preview")
@click.option("--name", required=True)
@click.option("--symbol", required=True)
@click.option("--quote", required=True, help="Quote symbol or mint, e.g. GMEX")
@click.option("--creator", required=True, help="Creator Solana wallet")
def preview_cmd(name: str, symbol: str, quote: str, creator: str) -> None:
    """Dry preview of a launch. Nothing on chain, nothing charged."""
    try:
        request = LaunchRequest(
            name=name, symbol=symbol, quote_mint=quote, creator_wallet=creator
        )
    except ValueError as e:
        click.echo(f"invalid request: {e}")
        sys.exit(1)

    result = preview(request)
    click.echo(result.message)
    click.echo(json.dumps(result.model_dump(exclude={"raw"}), indent=2, default=str))
    if result.status == "failed":
        sys.exit(1)


@main.command("fees")
def fees_cmd() -> None:
    """Show unsettled STONKBOT service fees."""
    click.echo(f"pending: {pending_total():.4f} SOL")
    rows = outstanding()
    if not rows:
        click.echo("nothing outstanding")
        return
    for row in rows:
        click.echo(
            f"#{row['id']:<5} {row['role']:<9} {row['amount_sol']:.4f} SOL  "
            f"{row['status']:<8} @{row['x_handle']}"
        )


@main.command("ref")
@click.argument("x_handle")
def ref_cmd(x_handle: str) -> None:
    """Show referral rebate totals for a handle."""
    click.echo(json.dumps(referral_earnings(x_handle), indent=2))


if __name__ == "__main__":
    main()
