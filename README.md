# pm-football-bot

YAML-driven Polymarket harvest bot for **Premier League**, **LaLiga**, **Ligue 1**, **Serie A**, and **Bundesliga**.

This is **not** a Swisstony clone. His wallet runs millions of fills and a ~$60k two-league book. This repo is the part you can actually run on **~$400**: scan mismatch fixtures, then rest maker bids on a few high-cent tails.

## Format to write the bot

Keep **strategy as YAML**, **execution as Python**. Do not bury league rules inside `if team == "Arsenal"` code.

```
config/
  leagues.yaml      # which competitions (EPL / LaLiga / Ligue 1 / Serie A / Bundesliga)
  strategy.yaml     # mismatch filter + which tickets to buy
  settings.yaml     # $400 bankroll, ticket size, dry-run
  keeper.yaml       # watchlist keeper thresholds (5 shares, 85/90, 98c cap)
src/pm_football_bot/
  gamma.py          # discover fixtures + more-markets
  signals.py        # turn a fixture into tickets
  keeper.py         # watchlist form-checked 5-share buys
  risk.py           # cap size so one weekend cannot spend the stack
  execution.py      # GTC maker bids (off until --live)
  scan.py           # the KEEP harvest loop
```

A ticket is always the same shape:

```text
BUY  token_id  @ best_bid   size >= 5 shares   GTC
```

Rules in `config/strategy.yaml` are the Swisstony **harvest template**, not his spray:

| Rule | When | Buy |
|---|---|---|
| `fade_dog` | dog win price ≤ 12¢ and favorite ≥ 70¢ | dog **No** |
| `over_0_5` | same mismatch | totals **Over 0.5** |
| `under_5_5` | same mismatch | totals **Under 5.5** |

Even games are skipped. Illiquid 98¢ books (spread > 3¢) are skipped. That is deliberate: those were the lines you could not copy.

Live trading uses **`py-clob-client-v2`** (CLOB V2). The old `py-clob-client` package is dead.

## $400 bankroll

Default caps in `config/settings.yaml`:

- **$18** per ticket (~20 shares at 90¢, still above the 5-share minimum)
- **3 tickets per fixture**
- **$240** max open notional (leave cash for the next matchweek)

A full weekend of 4–6 mismatch tickets is about **$70–$110**, not $400 all at once. At 90¢ paper, a clean week is a few dollars. A single dog win can wipe several weeks of harvest. Size as if that will happen.

## Setup

```powershell
cd C:\Users\WORK\pm-football-bot
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev,ui]"
```

### Friendly UI (recommended)

Opens a browser. Dry-run only — it never places orders.

```powershell
python -m streamlit run app.py
```

Click **Scan live boards**. Matches are grouped. **KEEP** is the three-ticket harvest on the YAML mismatch (dog ≤ 12¢, favorite ≥ 70¢). **SKIP** is the wrong market. **BORDERLINE** only appears if a ticket slipped outside that cutoff.

Open **Watchlist keeper** in the sidebar: **Scan**, then **Send live orders** (tick live GTC bids). **Auto-run** repeats scan/buy while that tab stays open.

Open the **Compare with swisstony** tab to see his bought shares and average price on the same match. **SAME LINE** means he holds the ticket you would buy. His extra lots (team Under 2.5, exact scores) stay on the right so you can see what not to copy.

### Terminal dry-run

```powershell
python -m pm_football_bot
```

Loop every 5 minutes:

```powershell
python -m pm_football_bot --loop
```

Live GTC bids (only after dry-run looks right):

```powershell
copy .env.example .env
pip install -e .[live]
python -m pm_football_bot --live
```

Fill `.env` with the Polymarket wallet private key (`PK`) and the **deposit wallet**
address (`FUNDER`, the address on your polymarket.com profile — not the EOA).
The Watchlist keeper UI can save both. Keep `dry_run: true` in YAML until you pass `--live`.

## Website (online desk)

The harvest desk and Upcoming board are a Streamlit app (`app.py`). Telegram alerts stay on GitHub Actions — this site does not send them.

Deploy from the GitHub repo at [share.streamlit.io](https://share.streamlit.io): **Create app** → `Bandolin22/pm-football-bot` → branch `main` → file `app.py`.

**Do not put secrets in a committed `.env`.** Auto-deploy never copies `.env`.

- **Telegram pings** (~3 hours before watchlist kickoff, every listed league/cup): already set as GitHub Actions secrets `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. The Streamlit site does not need them.
- **KEEP briefings** on the website: in Streamlit, open the app menu → **Settings** → **Secrets** and paste:

```toml
FOOTBALL_DATA_TOKEN = "your-football-data-token"
```

Then reboot the app. Locally, keep using `.env` on this PC.

## 24/7 Telegram watchlist (cloud)

The Streamlit desk does **not** send Telegram. GitHub Actions checks every 10 minutes. The alert window is 3 hours before kickoff so delayed cron still catches EPL and LaLiga. `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are already in the repo Actions secrets.

```powershell
# local smoke test
.\.venv\Scripts\python.exe -m pm_football_bot.notify --dry-run
```

## Watchlist keeper (form-checked ~5 shares)

Separate from KEEP. When a watchlist club's market is listed, the keeper checks recent form / scoring (football-data.org) and proposes about **5 shares**:

| Ticket | When | Skip |
|---|---|---|
| Dog **No** | Watchlist home ≥ 80% or away ≥ 85%, plus strong form or H2H | Two watchlist clubs; missing form+H2H unless the dog is tiny (≤12¢) |
| Over 0.5 (or Neither-first **No** / 0-0 **No**) | Cheapest of those three | Price ≥ 98¢; watchlist looks goal-shy |
| Over 1.5 | All Over 0.5 books ≥ 98¢ | Over 1.5 also ≥ 98¢ |
| Under 5.5 | Favorite away; if ≥98¢ buy Any Other Score No instead | Two watchlist clubs; either side’s last matches average ≥2.2 GF or ≥4.0 total goals |
| Over 7.5 corners | Two watchlist clubs, price ~75¢ | Any other match |

Default is dry-run. KEEP harvest is unchanged. GitHub Actions does not run this bot.

```powershell
python -m streamlit run app.py
# Watchlist keeper → Scan, optionally tick Place live GTC bids, Send live orders or Auto-run

python -m pm_football_bot.keeper
python -m pm_football_bot.keeper --live
```

Tune thresholds in `config/keeper.yaml`.

## Tune without rewriting code

- Add/remove a competition → `config/leagues.yaml` (`enabled: true` = harvest; `false` = Upcoming + Telegram only)
- Cups (EFL, FA Cup, UCL, Europa, DFB-Pokal, …) are on the Upcoming board even when harvest is off
- Play closer games → raise `mismatch.max_dog_yes`
- Take less risk → lower `ticket_usd` or `max_open_usd`
- Drop a template (for example skip Over 0.5) → `enabled: false` on that rule

Do not add exact-score No sprays or both-sides inventory on $400. That is the HFT/desk layer, not this bot.
