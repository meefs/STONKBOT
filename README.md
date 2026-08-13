# STONKBOT

Fully automated **X-only** bot for [StonkFun](https://www.stonkfun.xyz).

Handle: **@StonkFunBot**

User mentions bot → bot hot wallet signs + deploys → token live on StonkFun.  
No manual approvals.

## Economics

| Item | Value |
|------|-------|
| Creator wallet | Bot hot wallet (automated) |
| Trading fee share (50%) | Lands in bot hot wallet → sweep to you |
| Fee destination | `GKCJKSDJMfq4Zm4ye16oQFHRxqVqParBPvA5ja3FPBzS` |
| Surface | X mentions only |
| Security | Dry-run default, rate limit, circuit breaker, daily budget |

## Why bot is creator
StonkFun requires the `creatorWallet` to sign the launch tx.  
Full automation = bot holds a dedicated hot wallet and signs itself.  
Users trigger launches; they do not sign.

## Setup

```bash
git clone https://github.com/PhantomCapAI/STONKBOT.git
cd STONKBOT
cp .env.example .env
```

Fill:
1. X API keys for @StonkFunBot
2. `STONKBOT_HOT_WALLET_SECRET` — keypair for a **new** wallet funded with a little SOL
3. Keep `STONKBOT_DRY_RUN=true` until first test

```bash
pip install -r requirements.txt
pip install -e .
python -m stonkbot.cli doctor
python -m stonkbot.bot
```

## User commands on X
```
@StonkFunBot launch GameStop paired with GMEX
@StonkFunBot link <optional identity wallet>
@StonkFunBot whoami
```

## Status
- [x] StonkFun client
- [x] Intent parser
- [x] Hot wallet sign + submit
- [x] X mention poller
- [x] Dry-run / rate / circuit rails
- [x] Fee event tracking
- [ ] Optional auto-sweep of claimed creator fees to fee recipient

Fund the hot wallet, set keys, flip dry-run when ready.
