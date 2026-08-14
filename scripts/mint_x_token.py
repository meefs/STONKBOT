"""Mint an X access token for an account other than the developer account.

The console's "Generate" button always issues a token for the account that owns
the app — here @PhantomCap_ai. The bot posts as @stonkfunbot, so its token has
to come from the 3-legged OAuth flow: the bot account signs in and authorizes
the app, and X hands back a user token scoped to *that* account.

Billing and rate limits stay on the Phantom Capital app; only the acting
identity changes.

Run it, follow the two prompts, and it writes the pair into .env. It prints
only the last four characters of each — the values never go to the terminal in
full, so a screenshot or a shared scrollback does not leak them.

    python scripts/mint_x_token.py                 # interactive
    python scripts/mint_x_token.py --start         # print the URL only
    python scripts/mint_x_token.py --finish <url>  # exchange the verifier

The --start/--finish split exists so the flow can be driven from a browser
without the access token ever passing through a terminal prompt.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"

# Must exactly match a Callback URI registered on the app, or X rejects the
# request with "Callback URL not approved".
CALLBACK = "https://stonkfunbot.vercel.app/callback"


def _read_env() -> dict[str, str]:
    if not ENV_PATH.exists():
        sys.exit(f"no .env at {ENV_PATH}")
    values = {}
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def _write_env(updates: dict[str, str]) -> None:
    """Rewrite only the named keys, preserving comments and ordering."""
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    remaining = dict(updates)

    for index, line in enumerate(lines):
        match = re.match(r"^(\w+)=", line.strip())
        if match and match.group(1) in remaining:
            key = match.group(1)
            lines[index] = f"{key}={remaining.pop(key)}"

    for key, value in remaining.items():
        lines.append(f"{key}={value}")

    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _verifier_from(answer: str) -> str:
    """Accept either the whole redirected URL or a bare verifier."""
    answer = answer.strip()
    if answer.startswith("http"):
        params = parse_qs(urlparse(answer).query)
        found = params.get("oauth_verifier", [""])[0]
        if not found:
            sys.exit("that URL has no oauth_verifier in it")
        return found
    return answer


#: Carries the request token between --start and --finish. It is not the
#: access token and is useless once exchanged, but it is still a credential,
#: so it is written next to .env and deleted as soon as it is spent.
PENDING_PATH = ENV_PATH.parent / ".oauth-request-token"


def _handler(env: dict[str, str]):
    import tweepy

    consumer_key = env.get("X_API_KEY")
    consumer_secret = env.get("X_API_SECRET")
    if not consumer_key or not consumer_secret:
        sys.exit(
            "Fill X_API_KEY and X_API_SECRET in .env first "
            "(console.x.com → stonkbotfun → Keys & Tokens → Consumer Key)."
        )
    return tweepy.OAuth1UserHandler(consumer_key, consumer_secret, callback=CALLBACK)


def _start() -> None:
    """Print the authorization URL and stash the request token."""
    handler = _handler(_read_env())
    try:
        url = handler.get_authorization_url()
    except Exception as e:
        sys.exit(f"could not start the OAuth flow: {e}")

    PENDING_PATH.write_text(
        f"{handler.request_token['oauth_token']}\n"
        f"{handler.request_token['oauth_token_secret']}\n",
        encoding="utf-8",
    )
    print(url)


def _finish(answer: str) -> None:
    """Exchange the verifier for an access token and write it to .env."""
    if not PENDING_PATH.exists():
        sys.exit("no pending request token — run --start first")

    stored = PENDING_PATH.read_text(encoding="utf-8").split()
    handler = _handler(_read_env())
    handler.request_token = {
        "oauth_token": stored[0],
        "oauth_token_secret": stored[1],
    }

    _complete(handler, _verifier_from(answer))
    PENDING_PATH.unlink(missing_ok=True)


def _complete(handler, verifier: str) -> None:
    import tweepy

    env = _read_env()
    try:
        access_token, access_secret = handler.get_access_token(verifier)
    except Exception as e:
        sys.exit(f"token exchange failed: {e}")

    client = tweepy.Client(
        consumer_key=env["X_API_KEY"],
        consumer_secret=env["X_API_SECRET"],
        access_token=access_token,
        access_token_secret=access_secret,
    )
    me = client.get_me(user_auth=True)
    handle = me.data.username

    _write_env(
        {
            "X_ACCESS_TOKEN": access_token,
            "X_ACCESS_TOKEN_SECRET": access_secret,
            "X_BOT_USERNAME": handle,
        }
    )

    print(f"Authorized as @{handle}")
    print(f"  X_ACCESS_TOKEN        …{access_token[-4:]}")
    print(f"  X_ACCESS_TOKEN_SECRET …{access_secret[-4:]}")
    print(f"Written to {ENV_PATH}")

    if handle.lower() != "stonkfunbot":
        print(
            f"\n!! That is NOT @stonkfunbot. You authorized as @{handle}, so the "
            f"bot would post from that account. Re-run signed in as the bot."
        )


def main() -> None:
    if importlib.util.find_spec("tweepy") is None:
        sys.exit("tweepy is not installed — run: pip install -e .")

    if "--start" in sys.argv:
        _start()
        return
    if "--finish" in sys.argv:
        index = sys.argv.index("--finish")
        if index + 1 >= len(sys.argv):
            sys.exit("--finish needs the redirected URL or the verifier")
        _finish(sys.argv[index + 1])
        return

    env = _read_env()
    handler = _handler(env)

    try:
        url = handler.get_authorization_url()
    except Exception as e:
        sys.exit(f"could not start the OAuth flow: {e}")

    print("\n1. Open this in a browser where you are signed in as @stonkfunbot")
    print("   (use a private window if your main account is logged in):\n")
    print(f"   {url}\n")
    print("2. Click Authorize. You will land on a 404 page — that is expected,")
    print("   nothing is deployed at the callback path. The URL is what matters.\n")

    answer = input("3. Paste the full URL from the address bar (or just the verifier): ")

    _complete(handler, _verifier_from(answer))


if __name__ == "__main__":
    main()
