# STONKBOT — session handoff

Repo `PhantomCapAI/STONKBOT` · branch `claude/stonkbot-audit-redesign-2qhaov` ·
PR [#1](https://github.com/PhantomCapAI/STONKBOT/pull/1) · CI green ·
`mergeable_state: clean`

---

## 0. OPERATING MODE — apply for the whole session

Ultra-efficient expert system. Tokens are scarce. Maximize task completion per
token.

**Always**
- Fewest words. Answer immediately. No intro, no conclusion, no filler.
- Bullets > prose. Outputs > explanations. Patches > rewrites.
- Deep reasoning internal; output conclusions only.
- Treat this file + prior context as persistent memory. Return deltas, not
  re-summaries. Never regenerate established frameworks.
- Stop when the task is done.

**Never**
- Restate the request, re-explain code, add wrap-ups, mirror user wording,
  pad with examples, or expand beyond scope.

**Priority order:** correctness → reasoning quality → token efficiency →
clarity → speed.

**Code:** working code first, minimal comments, patch-style edits.
**Exception:** never compress away a safety property in §2/§3.

---

## 1. State

Done and merged into the PR (5 commits, 46 files, +6k/−717):

- Backend was **non-functional**: solana-py dropped sync `Client` (every RPC
  import raised), `models.LinkedAccount` missing (CLI crashed on import),
  pyproject omitted tweepy/solders/cryptography, all `STONKBOT_*` env vars
  silently ignored (no prefix declared). All fixed.
- `txguard.py` — verify payment tx before signing (was blind-signing).
- `idempotency.py` — exactly-once launches (was none).
- `rpc.py` — direct JSON-RPC over httpx, replaces broken solana-py.
- `state.py` — durable poll cursor + mention dedupe.
- `db.py` — shared SQLite, 0700 dir / 0600 files, fixes handle leak in 4 stores.
- `vault.py` — scrypt KDF + transparent v1→v2 migration.
- Referral rebate actually paid (was advertised, never implemented).
- Site rebuilt in-repo (`web/`) + `/privacy` + live board + `api/live.mjs`.
- 86 tests, ruff clean, CI in `.github/workflows/ci.yml`.
- Deleted dead `wallet.py`, `accounts.py`.

---

## 2. DO NOT BREAK

| File | Property | Failure mode |
|---|---|---|
| `txguard.py` | Fee payer == user's wallet; exactly 1 signer; debit ≤ `max_launch_cost_sol` | Wallet drain |
| `idempotency.py` + `launch.py` failure branches | One paid launch per command | Double payment → second token |
| `vault.py` | v1 rows still decrypt | Every pre-upgrade wallet stranded |
| `config.py` defaults | `dry_run=True`, `agent_vault_key=None` | Fresh deploy spends real SOL |

Rule: 86 tests stay green; none deleted or loosened. CI enforces.
```bash
pytest tests/ -q && ruff check src/ tests/
```

## 3. Idempotency release is asymmetric — read before touching `launch.py`

StonkFun: *"never pay twice — a second payment creates a second token."*

| Outcome | Release key? |
|---|---|
| Anything pre-`submit` | ✅ |
| `service_unavailable` + `charged:false` | ✅ |
| Dry run | ✅ |
| Rate limited | ✅ |
| **`conflict` (409)** | ❌ payment landed |
| Ambiguous error | ❌ can't prove no charge |
| Unexpected exception | ❌ |

This bug shipped once and was caught on second-pass review. 8 regression tests
in `tests/test_double_payment.py`.

## 4. Verified economics — do not re-derive

`stonkfun.xyz` is **egress-blocked** in the sandbox. A fresh session cannot
check these. Trust the table.

| Item | Value | Source |
|---|---|---|
| Creator share | **50%**, `standard` mode only | [API docs](https://www.stonkfun.xyz/developers) |
| `reward` mode | **No creator fee**, admin-only | Same |
| STONKBOT fee | 0.1 SOL / successful launch | `service_fee_sol` |
| Referral rebate | 0.03 SOL = 30% of **our** fee | `referral_share` |
| StonkFun launch cost | **Not published** — quoted per launch in `/launches/prepare` tx | API docs |
| StonkFun referrals | **Do not exist** | Full crawl |
| Graduation | $40k mcap | `/stats` |
| Dev buy cap | 2.5% supply | API docs |

- `0.35 SOL` is **not** a minimum. Guidance only (`recommended_funding_sol`).
- Referral rebate is **our** money, never "a StonkFun programme".

## 5. TODO

**Blocking (none is code):**
1. ~~Connect Vercel project `stonkfunbot` to this repo~~ — **done**
   (2026-08-13). `prj_1zsufCIVJoI3Z5uACDlumZqKP7zJ`, linked to
   `PhantomCapAI/STONKBOT`, production branch `main`.
   **Root Directory must stay blank (repo root).** It was set to `web`, which
   hid `api/` and 404'd `/api/live`; it is now cleared. `vercel.json` handles
   the static site via `outputDirectory: web`. Do not re-set it.
2. ~~`GET /api/live` returns JSON~~ — **done**. Live on
   `https://stonkfunbot.vercel.app/api/live`, HTTP 200, `ok:true` with
   `stats` / `newest` / `graduated` / `volume`. No further debugging needed.
3. Confirm live launch cost: `python -m stonkbot.cli doctor` (DRY_RUN=true)
   from an unblocked host. Then one real launch with tight
   `max_launch_cost_sol`. Update site copy with the confirmed figure.
   **Still open — the only blocking item left besides bot env vars.**
4. ~~Merge PR #1~~ — **done**, squash-merged to `main` (`f7f382b`),
   branch deleted, all three CI checks green.

**Non-blocking:** PR body (auto-generated) claims the guard "rejects any
transaction that doesn't match the quoted cost". It enforces fee payer + signer
count + hard cap. Correct it.

## 5b. Deployment shape

Decided 2026-08-13: **all-in on Vercel**, one platform for site and bot.

- `/api/poll` (Python) is invoked by cron every minute and runs exactly one
  `x_bot.poll_once` cycle. `run_poll_loop` still exists for a long-running
  host; both drive the same function.
- **`DATABASE_URL` is mandatory on Vercel.** It switches the whole state layer
  from SQLite to Postgres. Function disks are ephemeral: on SQLite the wallet
  vault would be recreated empty every invocation and the double-payment guard
  would silently stop guarding. `cli doctor` fails if it sees `VERCEL` set
  without `DATABASE_URL`.
- `CRON_SECRET` is mandatory too. `/api/poll` makes the bot post, so it fails
  closed — no secret, no requests served, including Vercel's own.
- **The cron schedule in `vercel.json` is `0 3 * * *` (daily) and that is a
  placeholder.** Hobby rejects anything more frequent *at deploy time* — a
  `* * * * *` schedule fails the build, which takes the static site down with
  it, not just the bot. So the frequent schedule cannot be committed until the
  account is on Pro.
  After upgrading, change that one line to `* * * * *`. Nothing else moves.
  (Pro also gives per-minute precision; Hobby is ±59 min even on a daily job.)
- **Cadence is the only thing that costs money.** Verified against the docs
  2026-08-14, correcting two earlier wrong assumptions:
  - Function duration on Hobby is **300s default and 300s maximum** — exactly
    what `vercel.json` already declares. A launch polls ~60s, so there is no
    duration risk on Hobby and no reason to upgrade for it.
  - **Vercel Postgres no longer exists.** Durable storage is Marketplace now,
    and a free plan is provisionable (`vercel install neon --plan free`), so
    `DATABASE_URL` does **not** require Pro or any spend.
  - Cron cadence genuinely does: Hobby minimum interval is once per day and
    sub-daily expressions fail at deploy.
- **Do not test the serverless path with the cron.** Trigger it directly —
  `vercel crons run /api/poll`, or curl with the bearer token. That exercises
  auth, persistence, cursor advance and reply generation on demand. The cron
  proves exactly one extra thing: that the scheduler fires. ±59 min is fine
  for a single yes/no.
- One SQL flavour is written (SQLite's, `?` placeholders); `dialect.py`
  rewrites it for Postgres. Postgres aborts a transaction on a failed
  statement, so `claim()` uses `INSERT OR IGNORE` + rowcount rather than
  catching a uniqueness violation — do not "simplify" that back.

**Env vars** — site: none (static + keyless proxy).
Bot:
- Required: `AGENT_VAULT_KEY` (32+ chars; **losing/changing strands every
  wallet**), `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`,
  `X_ACCESS_TOKEN_SECRET`, `STONKBOT_DRY_RUN`
- Required **on Vercel**: `DATABASE_URL` (or `POSTGRES_URL`), `CRON_SECRET`.
  Off Vercel: `STONKBOT_DATA_DIR` on a persistent volume instead.
- `X_BOT_USERNAME` is now advisory only: the loop calls `get_me()` and trusts
  the handle the token actually authenticates as, logging a warning on
  mismatch. Set it anyway as a tripwire against wiring the wrong account.
- **The bot posts as @stonkfunbot, but the X app is owned by @PhantomCap_ai.**
  The console's "Generate access token" button always mints a token for the
  app owner, so using it would make the bot post from the company account.
  Use `python scripts/mint_x_token.py` instead: it runs 3-legged OAuth so
  @stonkfunbot authorizes the app and X issues a token scoped to the bot.
  Billing and rate limits stay on the Phantom Capital app either way. The
  script refuses quietly-wrong outcomes by printing a warning if the
  authorized handle is not @stonkfunbot.
- The X surface is **v2** (`tweepy.Client`). v1.1 `statuses/mentions_timeline`
  and `statuses/update` are retired — do not reintroduce `tweepy.API`.
  Auth is OAuth 1.0a user context, not app-only bearer: replies need it.
- Recommended: `SOLANA_RPC_URL` (dedicated; public endpoint rate-limited),
  `STONKBOT_FEE_RECIPIENT`, `STONKBOT_MAX_LAUNCH_COST_SOL`, `STONKBOT_DATA_DIR`

---

## 5c. Public posture — the CTA gate and the backlog guard

Two switches decide whether STONKBOT makes a promise it can't keep. Both fail
closed, and neither should be "fixed" by loosening the default.

**`data-cta` on `<html>` in `web/index.html`.** Decides whether the site asks
anyone to tweet at the bot. `"soon"` gates every CTA; `"live"` restores them.
Going live is editing that one attribute — no other change. The CSS hides the
live CTAs unless the value is exactly `"live"`, so a typo or a dropped
attribute leaves the gated state rather than restoring the invitation.

- The beta strip states **no date**, deliberately. Do not add a countdown, an
  ETA, or "launching this week" — it becomes a promise someone has to honor.
- `styles.css` is referenced with `?v=N` **on purpose**. The gate is enforced
  in CSS, and the stylesheet is served `max-age=3600`, so unversioned it means
  a returning visitor gets new HTML (both CTA variants) against old CSS (hides
  neither) and sees the live button next to the gated one. This was observed on
  production. **Bump `v` whenever a `styles.css` change must land with an
  `index.html` change.**
- The two remaining `x.com/stonkfunbot` links (hero lede, footer) are plain
  links to the profile, not calls to action. They stay in both states.

**Backlog guard in `poll_once`.** The cursor only moves forward, so mentions
that arrive while the bot is offline are all still waiting when it returns and
would be answered in one burst — the account's first public act after a
silence. The poll refuses above `STONKBOT_BACKLOG_LIMIT` (5) pending or
`STONKBOT_BACKLOG_MAX_AGE_HOURS` (24) old, logs each mention it would have
answered with the parsed intent, and **leaves the cursor and the handled set
untouched** so nothing is consumed and the decision stays open.

- Proceed deliberately: `STONKBOT_ACCEPT_BACKLOG=true`, or
  `python scripts/dry_run.py --accept-backlog`.
- Skip the pile instead: `stonkbot cursor --set <newest mention id>`.
- The long-running loop **stops** on a trip rather than retrying every 30s.
  `/api/poll` answers **409**, not 500 — nothing is broken and nothing was
  consumed, so it stays out of the "cron is failing" bucket.
- Do not raise the limits to make a trip go away. A trip means a human has to
  look at what is waiting.

---

## 6. Site roadmap

Current: `/` (landing + live board) and `/privacy`. Thin because there was
nothing truthful to fill more with. That's now solvable.

### Reference-site policy
Take **structure/IA** from mature competitors (Bankr et al.) — layout
conventions aren't ownable. Do **not** copy visual design, copy, or assets:
takedown risk, and it makes STONKBOT look like a clone of a competitor.
Original brief said *"do NOT copy any brand"* — keep that.

`bankr.bot` is **egress-blocked** in the sandbox. Do not describe a site you
can't load. Paste text/screenshots in, or compare from an unblocked machine.

### Buildable now — all on verified endpoints

| Feature | Endpoint | Value |
|---|---|---|
| **"Launched with STONKBOT" leaderboard** | `/launches?creator=<wallet>` | Strongest proof product works. **Build first.** |
| **Creator fee checker** | `/tokens/{mint}/fees` | Demonstrates the 50% instead of asserting it. **Build second.** |
| Pairs explorer | `/pairs?category=` | xStocks, PreStocks, Backpack, Currencies, Leverage, Solana, Custom |
| Token detail page | `/tokens/{mint}` | Market data, bonding, quote pair |
| Platform stats | `/revenue`, `/rewards`, `/tokens/{mint}/burns` | Volume, buybacks, burns |
| Commands reference | — | Full syntax + error meanings |
| Changelog | — | Cheap credibility for a money product |

**Rules for any of these:**
1. Route through `api/live.mjs` (allowlisted view names). Never let the browser
   call StonkFun: leaks visitor IPs, burns 300/min per-IP limit, forces CSP open.
2. Token names/symbols are attacker-controlled → `textContent`, never
   `innerHTML`. No remote token images (launcher-controlled URLs); use monogram.

**Never build:** price-history charts. No OHLC endpoint exists — a sparkline
would be fabricated data on a money page. Bonding %, 24h change, mcap, volume
are real; use those.

---

## 7. Environment gotchas

- Blocked domains: `stonkfun.xyz`, `bankr.bot`, `stonkfunbot.vercel.app`.
  `cli doctor` reporting `403 Forbidden` for StonkFun in-sandbox is expected.
- Playwright: bundled Chromium version mismatches. Use
  `executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome"`.
- Headless Chromium `--screenshot` viewport is ~85px shorter than
  `--window-size` (silently truncated the OG image twice). Use Playwright with
  explicit viewport.
- Site local preview: `python -m http.server 8899 --directory web`.

---

## 8. BLOCK — pump.fun integration, free launch + opt-in treasury

Depends on: Privy migration (no signing outside Privy, no live SOL/dev-buy funds
until that path is real — same rule as StonkFun). Do not wire real funds through
this before Privy sign-path is confirmed working end-to-end.

### 8.1 Scope

- Free launch on pump.fun (no platform fee to create — confirmed via pump.fun's
  current docs, ~0.02 SOL is Solana rent/network cost only, not a bot fee).
- No dev buy = still free. Dev buy is optional, user-specified SOL amount.
- Opt-in treasury: 0.1% of dev-buy SOL amount routed to treasury wallet, ONLY
  when the user explicitly sets a flag at launch time. Never silent, never
  retroactive, never applied to an existing wallet's token balance after the
  fact. This is not the StonkFun 1% skim idea reincarnated — if it ends up
  looking like a post-launch transfer out of the user's wallet without an
  explicit per-launch confirm, stop and flag it, don't ship it.

### 8.2 No first-party REST API — bigger lift than StonkFun

- Unlike StonkFun's /launches/prepare + /launches/submit, pump.fun has no
  official hosted REST API for creation. Third-party wrappers (pumpdev.io,
  pumpportal.fun) exist but are unofficial middlemen — some offer server-side
  "Lightning" signing where THEY hold the transaction path. Do not route any
  signing through a third-party wrapper. If a wrapper is used at all, restrict
  it to non-signing utility (e.g. metadata/IPFS upload), never transaction
  construction or signing.
- Real integration = build the create (and optional buy) instruction directly
  against pump.fun's on-chain program. Before writing any code:
    a. Pull the current pump.fun program ID and IDL from their own repo/docs
       (verify current address — do not hardcode from memory/training data,
       this has to be confirmed live).
    b. Confirm whether create + first buy can land in one atomic transaction
       (same block) or need to be sequenced.
    c. Confirm current metadata/IPFS requirements (pump.fun's own upload
       endpoint has reportedly changed — verify current requirement before
       building the upload step).
- Mint keypair is generated CLIENT-SIDE on pump.fun (unlike StonkFun, where
  StonkFun generates it server-side). This means:
    - Bot generates a fresh mint keypair per launch.
    - No custody concern on the mint itself — mint keypair only ever signs
      once at creation, doesn't need to be stored/vaulted afterward.
    - Vanity mint (previously ruled out for StonkFun) is technically viable
      here if wanted later — not in scope for this brick, note for backlog.

### 8.3 Treasury flow (opt-in only)

- New launch param: `treasury_opt_in: bool` (default false), surfaced explicitly
  in the launch command/UX, e.g.:
    `@StonkFunBot launch NAME paired with QUOTE devbuy 0.5 treasury`
  Bot confirms before executing: "0.1% of your 0.5 SOL dev buy (0.0005 SOL)
  goes to treasury. confirm?" — require explicit yes, don't default it on.
- On confirmed dev buy: split the dev-buy SOL itself pre-buy (treasury cut
  taken from the SOL spent, not clawed back from tokens after they land).
  This avoids ever touching the user's post-launch token balance — cleaner
  than the StonkFun skim proposal structurally, not just smaller.
- Store in existing fee_events-style ledger (same pattern as StonkFun fees.py):
  `x_user_id`, `mint`, `devbuy_sol`, `treasury_cut_sol`, `opted_in` (bool),
  `created_at`.
- All signing (create tx, buy tx, treasury-cut transfer) goes through Privy —
  same as every other signing path in this stack. No exceptions for pump.fun.

### 8.4 Out of scope for this brick (do not build)

- Robinhood Chain deployment — blocked on the same US-person legal clearance
  already open for BankrStonks/RobinWallet. Not duplicated here.
- Any Bankr feature beyond token launch (swaps, limit orders, bridging,
  mining, rug analysis, skills marketplace) — not this brick.
- Vanity mint address — technically possible now, not requested, backlog only.
- Referral integration with pump.fun launches — StonkFun referral spec exists
  separately; extend to pump.fun only after both are independently stable.

### 8.5 Sequencing

1. Pull current pump.fun program ID/IDL, confirm atomic create+buy capability.
2. Build create+optional-buy instruction construction, dry-run only (no real
   SOL), verify against devnet or a simulated tx first.
3. Wire treasury opt-in flag + confirmation prompt, dry-run.
4. Only after Privy sign-path is live and StonkFun launches are already going
   through it successfully — point pump.fun signing at Privy too.
5. Real SOL / real treasury flow: single test launch, small amount, manual
   verification the treasury cut landed correctly, before opening it up.
