# The Grey Ledger — live data setup

## What's in this folder
- `ipo-ledger.html` — the site. Works standalone with a built-in snapshot even with no setup at all.
- `fetch_ipos.py` — pulls official IPO data from Upstox and writes `ipos.json`.
- `.github/workflows/fetch-ipos.yml` — runs the script daily and commits the result.

## One-time setup
1. Create a private GitHub repo, push these files to it, and turn on **GitHub Pages** (Settings → Pages → deploy from the `main` branch). This gives `ipo-ledger.html` a same-origin URL to fetch `ipos.json` from — opening the HTML file directly from disk won't let it fetch a local JSON file due to browser security rules.
2. Register an app at [Upstox Developer Console](https://account.upstox.com/developer/apps) to get a `client_id` and `client_secret`.
3. Generate an access token by completing Upstox's login flow once (their docs walk through this: redirect → login → auth code → token exchange).
4. In your repo, go to **Settings → Secrets and variables → Actions** and add a secret named `UPSTOX_ACCESS_TOKEN` with that token.

## The part that needs your attention daily
Upstox access tokens expire every night around 3:30 AM IST — this is Upstox's design, not something to work around. Practically, that means:

- **Each morning**, log into the Upstox developer console, generate a fresh token, and paste it into the `UPSTOX_ACCESS_TOKEN` secret (takes under a minute).
- The workflow runs at 6:00 AM IST daily, so refresh the secret before then, or just trigger it manually afterward from the **Actions** tab (`Run workflow` button — the `workflow_dispatch` trigger is already wired in).

If you'd rather not do this by hand, Upstox's login can be scripted with a stored TOTP secret (there are open-source examples for this). That means storing your trading account credentials in a script and a GitHub secret — a real decision, not a small one — so it's left out of this by default. Say the word if you want to go that route and I'll build it.

## What's automated vs. not
| Field | Source |
|---|---|
| Open/close/allotment/listing dates | Upstox — automatic |
| Price band, lot size, issue size, min. amount | Upstox — automatic |
| Subscription multiple | Upstox — automatic |
| Actual listing price (for the "did the verdict hold" check) | Upstox, once listed — automatic |
| **GMP** | Still manual — no broker publishes unofficial grey-market data |
| **Verdict / hold call / rationale** | Still Claude's research — a new IPO Upstox returns won't have one until it's researched and added |

New IPOs that show up from Upstox but aren't in the researched list will still appear as plain cards — dates, price, subscription — just without the auto-read block until someone (me, on request) does the research pass on them.
