# STONKBOT

Bankr-style **X-only** bot for [StonkFun](https://www.stonkfun.xyz) on Solana.

Users link a Solana wallet to their X handle → mention/DM the bot → token deploys paired with xStocks (or other quotes) → **user is the creator** and claims the full 50% trading fees.

Operator takes a flat **0.1 SOL service fee** per successful launch.

## Locked model

| Item | Value |
|------|-------|
| Surface | **X only** (mentions + DMs) |
| Platform | StonkFun (Solana) |
| Creator | User's linked wallet (they get 50% of trading fees) |
| Service fee | 0.1 SOL per successful launch |
| Fee destination | `GKCJKSDJMfq4Zm4ye16oQFHRxqVqParBPvA5ja3FPBzS` |
| Responses | Short, light WallStreetBets humor |
| Security | Dry-run default, rate limits, circuit breaker, daily budget, secrets in `.env` only |

## Quick start

```bash
cp .env.example .env
pip install -r requirements.txt
python -m stonkbot.cli doctor
python -m stonkbot.cli pairs
python -m stonkbot.cli preview --name "Apes Together" --symbol APEAMC --quote GMEX --creator <WALLET>
```

Dry-run is on by default. Nothing hits the chain until you flip it.

## Flow (X)

1. User DMs or mentions bot: `link <SOLANA_ADDRESS>`
2. User: `launch GameStop paired with AMC` (or similar)
3. Bot resolves quote, prepares launch under **their** wallet as creator
4. User pays 0.1 SOL service fee + signs StonkFun payment
5. Token lands on stonkfun.xyz → short confirmation reply

## Status

Core API client, account linking, security rails, CLI, short replies — done.  
Next: X listener + signing/fee path.
