# How Basis is put together

Python standard library and SQLite, nothing to install. One process serves a single-page app and refreshes market
data in the background.

## Two ways to run (fin/paths.py)

| | Desktop | Server |
|---|---|---|
| Started by | `basis.pyw` (the installed shortcut) | `server.py`, kept running by cron or a service |
| Chosen when | there is no `config.json` beside `server.py` | there is one (`port`, `binds`, `hosts`) |
| Listens on | `127.0.0.1` only | the addresses in `binds` |
| Data, backups, logs | the user's data folder (`%LOCALAPPDATA%\Basis` on Windows) | `data/`, `backups/`, `logs/` in the program folder |
| Updates | from GitHub releases (fin/update.py) | by hand |
| Restart after a code change | `basis.pyw` restarts the server and undoes the change if it fails | `bin-agent-apply.sh` does the same with cron |

## Pieces

| File | What it does |
|---|---|
| `basis.pyw` | Desktop launcher: one copy at a time, starts `server.py`, opens the app window (Edge or Chrome in app mode, else a browser tab), restarts on request with rollback, stops when the window closes |
| `server.py` | HTTP server and API, the background worker (quotes, history, SnapTrade sync, daily backup), first-run setup, alerts |
| `fin/paths.py` | Folders, mode, version |
| `fin/db.py` | SQLite schema and helpers; settings are JSON values in the `settings` table |
| `fin/snaptrade.py` | SnapTrade client (signed requests), activity normalizing, sync |
| `fin/secret.py` | Encrypts the SnapTrade consumer key with Windows DPAPI |
| `fin/reconcile.py` | Lines share counts up with the broker after an account is first linked |
| `fin/engine.py` | Replays every transaction into FIFO lots, cash, income, option positions |
| `fin/analytics.py` | Builds the model the page shows: positions, accounts, margin estimate, history, returns, grades, M2 comparisons, Glory Days Net Worth, type and sector groups |
| `fin/market.py`, `fin/sync.py` | Yahoo Finance and Nasdaq: quotes, daily history, splits, dividends, profiles, option chains |
| `fin/intraday.py` | Live, 1D and 1W chart series |
| `fin/options.py` | The weekly options menu |
| `fin/wheel.py` | Wheel trades: an assigned put, the contracts sold on its shares and the shares themselves, as one trade; the Selling options switch |
| `fin/doom.py` | The Doom score, its cycles, each day's highest and lowest reading |
| `fin/dip.py` | The Doom dashboard's dip ranking per stock |
| `fin/warchest.py` | The Doom dashboard's war chest and liquidation line, against past crashes back to 1987 |
| `fin/euphoria.py` | The Euphoria score (the Doom score's mirror), its cycles, and your own highs against it |
| `fin/valuation.py` | Index P/E and CAPE for the Market page |
| `fin/m2.py` | M2 money supply from the Federal Reserve's H.6 release |
| `fin/macro.py` | FOMC meeting dates |
| `fin/moves.py` | The Moves log: decisions and how they played out |
| `fin/imports.py`, `fin/robinhood.py` | CSV uploads (Robinhood activity exports, positions files from any broker) |
| `fin/agent.py` | The Customize chat: runs Claude Code in the program folder, attachments, snapshots, diffs, undo |
| `fin/update.py` | Looks for and installs new versions |
| `fin/proc.py` | Starts and stops other programs the same way on Windows and Linux |
| `fin/push.py` | Phone alerts by web push (server copies only; needs the `cryptography` package and https) |
| `web/app.js`, `web/app.css`, `web/index.html` | The page. No framework; every element is built by the `h()` helper |
| `web/recover.html`, `web/recover.js` | A separate small page that can undo a Customize change when the main page is broken |
| `agent/RULES.md` | The rules the Customize chat follows |
| `tools/` | `mock_snaptrade.py` and `demo_data.json` (a made-up portfolio for tests), `build_release.py`, `smoke.py` |
| `packaging/windows/` | The Inno Setup script for the installer |

## How data flows

1. `snaptrade.sync` stores each activity as a row in `raw_rows` and the broker's cash balance for the day. Activities
   arrive a day late; balances and positions are live.
2. `engine.Replay` turns the rows into lots, cash and income. Nothing derived is stored: the model is rebuilt from the
   rows whenever data changes.
3. `analytics.build` adds prices and produces one JSON model. The page fetches it from `/api/model`.
4. The worker in `server.py` refreshes quotes (every 30 seconds in market hours), daily history, dividends, the Doom
   score, valuation, M2 and the wealth rank on their own schedules.

The first time an account is linked, `reconcile.setup` compares the replayed share counts with the broker's and
records what the history cannot explain: shares held before the history starts become opening holdings, and shares
missing become transfers out. Each account goes through this once. After that a difference is a trade on its way and
shows in Data and settings.

## Security

- Requests with an unknown `Host` header are refused, so a web page elsewhere cannot reach the app by rebinding a
  name to `127.0.0.1`.
- Every write needs the header `X-Requested-With: finance` and a same-origin `Origin`.
- A strict Content-Security-Policy: no inline scripts or styles, and text reaches the page as text, never as HTML.
- The SnapTrade key never goes back to the page. The page sees its last four characters.
- The Customize chat's file tools are confined to the program folder (`--restricted`), and the data folder is
  outside it on a desktop install.
- An update is checked against its published SHA-256 and against the manifest inside it before any file is written.

## Conventions

- Money the user owns goes through `money()` in `web/app.js`, public prices through `price()`. The hide-amounts
  button depends on it.
- Colors are the variables at the top of `web/app.css`.
- No long dashes in any text. Plain, short copy.
- Text files are opened with `encoding='utf-8'`; other programs are started through `fin/proc.py`.
