# STONKBOT

Bankr-style bot for [StonkFun](https://www.stonkfun.xyz) on Solana.

Users link a Solana wallet → request a launch via X/Telegram/CLI → token deploys paired with xStocks (or other quotes) → **user is the creator** and claims the full 50% trading fees.

Operator takes a flat **0.1 SOL service fee** per successful launch.

## Locked model

| Item | Value |
|------|-------|
| Platform | StonkFun (Solana) |
| Creator | User's linked wallet (they get 50% of trading fees) |
| Service fee | 0.1 SOL per successful launch |
| Fee destination | `GKCJKSDJMfq4Zm4ye16oQFHRxqVqParBPvA5ja3FPBzS` |
| Responses | Short, light WallStreetBets humor |
| Security | Dry-run default, rate limits, circuit breaker, human approval gate, secrets in `.env` only |

## Quick start

```bash
cp .env.example .env
# fill TELEGRAM_*, optional RPC, etc.
pip install -r requirements.txt
python -m stonkbot.cli doctor
python -m stonkbot.cli pairs          # list launchable quotes
python -m stonkbot.cli preview --name "Apes Together" --symbol APEAMC --quote GMEX
```

Dry-run is on by default. Nothing hits the chain until you flip it.

## Status

Core scaffold in progress. Launch path, fee collection, and account linking next.
