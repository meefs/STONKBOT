# STONKBOT

An X bot that launches stock-paired tokens on [StonkFun](https://www.stonkfun.xyz) (Solana).

**[@stonkfunbot](https://x.com/stonkfunbot)** · [stonkfunbot.vercel.app](https://stonkfunbot.vercel.app)

A user registers on X, funds a per-user agent wallet, and tweets a launch command.
The bot signs the launch with *that user's* wallet, so the user is the creator on
chain and the creator share of trading fees accrues to them.

```
X mention → intent parser → guard → agent wallet → StonkFun /prepare
          → transaction verification → sign → /submit → confirm → fees
```

## Model

1. `register` — the bot creates an agent wallet for the handle and replies with the address.
2. The user funds that address with SOL.
3. `launch <name> paired with <QUOTE>` — the bot quotes the launch, verifies the
   payment transaction, signs it with the user's wallet, and submits it.
4. The user's wallet is the creator, so StonkFun's creator fee share is theirs to claim.
5. On success, STONKBOT's service fee transfers from the user's wallet to the operator.

## Economics

Two different things, kept separate on purpose.

| | Amount | Source |
|---|---|---|
| **Creator share of trading fees** | 50% | [StonkFun API docs](https://www.stonkfun.xyz/developers) — standard mode splits the 1% pool fee 50/50 between creator and platform |
| **STONKBOT service fee** | 0.1 SOL per successful launch | `service_fee_sol` in this repo |
| **Referral rebate** | 0.03 SOL (30% of the service fee) | `referral_share` in this repo |
| **StonkFun launch cost** | **Quoted per launch — not a fixed number** | Read from the payment transaction returned by `/launches/prepare` |

### On the launch cost

StonkFun does not publish a fixed launch price. The real cost rides inside the
payment transaction that `/launches/prepare` returns, and it varies with the
launch (including any dev buy). This repo therefore **does not hardcode it**:

- `txguard.py` decodes the prepared transaction and measures what it actually
  debits from the user's wallet.
- The balance check runs against that measured figure plus the service fee and a
  network reserve — not against a guessed constant.
- `max_launch_cost_sol` (default 1.0) is a hard ceiling. A quote above it is
  refused *unsigned*.

Earlier versions of this project advertised `0.35 SOL` as the required funding.
That number is not sourced from StonkFun and is not treated as authoritative
anywhere in the code. It survives only as `recommended_funding_sol`, a guidance
figure for copy, clearly labelled as a suggestion rather than a minimum.

### Creator fees, precisely

- Applies to `standard` mode, which is the only mode this bot submits.
- StonkFun's `reward` mode carries **no creator fee position** and is admin-only
  on their API, so the bot never uses it.
- Fees accrue only when people trade the token. Nothing here is income.

### Referrals

The rebate is a share of **STONKBOT's own fee**. StonkFun publishes no referral
programme, and this repo does not claim one. Someone adds `ref <handle>` to their
launch command; on success, 30% of the 0.1 SOL fee goes to that referrer's agent
wallet. One level, no self-referrals, and it is only owed if the referrer has an
agent wallet to receive it.

## Setup

```bash
cp .env.example .env
# Set the X credentials and AGENT_VAULT_KEY. Leave DRY_RUN=true until tested.
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt && pip install -e .
python -m stonkbot.cli doctor      # readiness check; prints no secrets
python -m stonkbot.bot             # start the poll loop
```

Generate a vault key with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

**`AGENT_VAULT_KEY` decrypts every user wallet.** Losing or changing it makes every
stored wallet permanently unrecoverable. Back it up in a secret manager.

## User commands

```
@stonkfunbot register
@stonkfunbot balance
@stonkfunbot launch GameStop paired with GMEX
@stonkfunbot launch GameStop paired with GMEX ref somehandle
@stonkfunbot ref
@stonkfunbot help
```

## Operator commands

```bash
python -m stonkbot.cli doctor    # config, StonkFun reachability, stuck launches
python -m stonkbot.cli pairs     # launchable quote pairs
python -m stonkbot.cli fees      # unsettled service fees
python -m stonkbot.cli ref <handle>
python -m stonkbot.cli preview --name X --symbol X --quote GMEX --creator <pubkey>
```

## Security

What the code actually does:

- **Encrypted at rest.** Agent wallet secrets are Fernet-encrypted (AES-128-CBC
  + HMAC-SHA256) under a key derived from `AGENT_VAULT_KEY` with **scrypt** and a
  random per-install salt. Master keys shorter than 32 characters are rejected.
  Rows written by the older SHA-256 derivation still decrypt and are transparently
  re-encrypted on first read, so existing vaults migrate themselves.
- **Owner-only storage.** The data directory is created `0700` and every database
  file (including WAL sidecars) `0600`, so nothing else on the host can read the
  vault or the fee ledger. `cli doctor` flags any file that drifts.
- **Transactions are verified before signing** (`txguard.py`). The bot refuses to
  sign unless the fee payer is that user's own wallet, exactly one signature is
  required, and the SOL debited is within `max_launch_cost_sol`. Where the RPC
  supports it, the debit is measured by simulating the transaction rather than
  trusting its declared instructions.
- **Exactly-once launches** (`idempotency.py`). Each launch is keyed to the tweet
  that requested it. A replayed mention returns the original result instead of
  paying again — StonkFun's docs are explicit that a second payment mints a
  second token.
- **Error codes are handled per StonkFun's contract.** A `conflict` (payment
  landed, needs recovery) never retries. A `service_unavailable` with
  `charged: false` is safe to retry. An ambiguous failure is reported as
  ambiguous rather than guessed.
- **Wallet isolation.** A keypair is only ever loaded for the handle that owns it.
- **Rails.** Per-user and global rate limits, a daily launch budget, and a circuit
  breaker. These apply in dry-run too, so behaviour under test matches production.
- **Dry-run by default.** `DRY_RUN=true` unless explicitly disabled.
- **Secrets never leave the server.** They are not logged, tweeted, returned by
  the CLI, or referenced by the website, which is fully static.

### Known trade-off

Agent wallet keys are generated and held server-side so the bot can sign from a
tweet. **This is custody.** Users are trusting the operator and the host. The
wallet is intended to hold what a launch needs, not to store SOL. This is stated
plainly on the site rather than papered over.

## Website

`web/` is a dependency-free static site — no framework, no build step, no runtime
secrets. The browser makes no third-party requests at all; a strict CSP in
`vercel.json` enforces it.

- `/` — landing page, with a live token board (new pairs / top volume / bonded)
- `/privacy` — what is stored, how it is encrypted, and who else sees it

Live market data is read from StonkFun's public API by `api/live.mjs`, a Vercel
function, and cached at the edge for 30s. The browser never contacts StonkFun
directly: that keeps visitor IPs private, keeps the CSP at `connect-src 'self'`,
and means the whole site costs a few upstream calls a minute instead of one per
visitor. Token names are attacker-controlled, so they are rendered with
`textContent` and never as markup.

```bash
python -m http.server 8899 --directory web
```

The previously deployed site was a Next.js app that existed only on a local
machine and was never committed here. This replaces it, so what is deployed is
what is in version control.

## Tests

```bash
python -m pytest tests/ -q
ruff check src/ tests/
```

Covers intent parsing, transaction-guard rejections (including a simulated
wallet-drain), vault isolation, encryption-at-rest and the scrypt migration,
file permissions, fee splits, idempotency and double-payment regressions, safety
rails, and launch-flow error handling. Nothing in the suite touches the network.

## Deployment

The bot is a long-running process and needs a persistent volume for `data/`
(the encrypted vault lives there). Vercel hosts the static site only — see
`vercel.json`. It has no API routes and holds no secrets.
