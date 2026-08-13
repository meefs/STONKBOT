# STONKBOT

Bankr-style **X-only** bot for [StonkFun](https://www.stonkfun.xyz) on Solana.

Handle: **@StonkFunBot**

Users link a Solana wallet → mention the bot → token deploys paired with xStocks → **user is creator** (keeps 50% trading fees).

Operator fee: **0.1 SOL** per successful launch → `GKCJKSDJMfq4Zm4ye16oQFHRxqVqParBPvA5ja3FPBzS`

## Locked model

| Item | Value |
|------|-------|
| Surface | X only (mentions) |
| Platform | StonkFun (Solana) |
| Creator | User's linked wallet |
| Service fee | 0.1 SOL / launch |
| Responses | Short WSB |
| Security | Dry-run default, rate limit, circuit breaker, daily budget |

## Setup

```bash
git clone https://github.com/PhantomCapAI/STONKBOT.git
cd STONKBOT
cp .env.example .env
# fill X_API_* keys + optional RPC
pip install -r requirements.txt
pip install -e .
```

## CLI

```bash
python -m stonkbot.cli doctor
python -m stonkbot.cli pairs
python -m stonkbot.cli link someuser GKCJKSDJMfq4Zm4ye16oQFHRxqVqParBPvA5ja3FPBzS
python -m stonkbot.cli preview --name "Apes Together" --symbol APEAMC --quote GMEX --creator <WALLET>
```

## Run X bot

```bash
# keep STONKBOT_DRY_RUN=true until signing is tested
python -m stonkbot.bot
```

User flows on X:
- `link <SOLANA_ADDRESS>`
- `launch GameStop paired with GMEX`
- `whoami`

## Status

- [x] StonkFun API client
- [x] Account linking
- [x] Intent parser (no LLM)
- [x] Dry-run launch preview
- [x] Fee event tracking
- [x] X mention poller
- [x] Short replies
- [ ] Live signing of StonkFun payment tx (next)
- [ ] Auto 0.1 SOL fee transfer on success (next)

Dry-run stays on until you flip it and wire signing keys.
