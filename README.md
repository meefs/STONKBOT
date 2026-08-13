# STONKBOT

Bankr-style X bot for [StonkFun](https://www.stonkfun.xyz).

**@StonkFunBot**

## Model (locked)

1. User: `register` → bot creates **their agent wallet**
2. User funds it (~0.35 SOL covers launch + service fee)
3. User: `launch GameStop paired with GMEX`
4. Bot signs with **their** wallet → **they are creator** → **they keep 50% trading fees**
5. On success, **0.1 SOL service fee** → `GKCJKSDJMfq4Zm4ye16oQFHRxqVqParBPvA5ja3FPBzS`

Your wallet only collects the service charge. Fully automated for you.

## Setup

```bash
cp .env.example .env
# Set X keys, AGENT_VAULT_KEY (long random string), keep DRY_RUN=true first
pip install -r requirements.txt && pip install -e .
python -m stonkbot.cli doctor
python -m stonkbot.bot
```

## User commands
```
@StonkFunBot register
@StonkFunBot balance
@StonkFunBot launch GameStop paired with GMEX
@StonkFunBot help
```

## Security
- Agent keypairs encrypted at rest (Fernet + AGENT_VAULT_KEY)
- Secrets never logged or tweeted
- Dry-run, rate limit, circuit breaker, daily budget
- No Telegram — X only
