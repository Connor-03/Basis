# Changes

## 1.0.5

- **Options dashboard.** The Options page is one dashboard now: open contracts, this week's menu, and every contract you've closed by month, by how it ended and by stock (that part moved over from Analysis).
- **Wheel trades.** Your broker counts an assigned put's premium as profit, even when the shares it hands you are worth less. Wheel trades follow those shares to the end: the put, the calls you sold on them, dividends, and what the shares did until they're sold or called away.
- **Selling options switch.** Data and settings, Settings, Selling options. Off by default. Turn it on if you sell covered calls or cash-secured puts to get the weekly menu, wheel trades, and your premium in the income projection.
- **Option income is premium only.** Income counts what selling contracts brought in. Contracts you buy and sell again are trading, like stock sales, and live in the Options record.
- **Settings, in order.** Data and settings goes settings first, then accounts, then your data, with buttons up top to jump around. Brokerage syncs no longer flood the uploads list, and long lists show a few rows until you open them.
- **Phone guide.** Only shows on a copy you run on a home server, now with steps for iPhone and Android.

## 1.0.4

- **Account switches.** Turn any account off in Data and settings, Accounts, and Basis acts like it isn't there. It keeps syncing in the background, so turning it back on brings it straight back.
- **Update pop-up.** New versions now pop up in the top right. Close it with the X and it stays gone until the next one.
- **How it works, on every section.** Hover or tap the ? next to a section title for the method behind the numbers, including when the Doom dashboard says to buy.
- **Glory Days Net Worth.** The new name for Glory Days Worth. It always measured net worth; now the name says so.
- **Prices paused.** Yahoo's stock prices run 4 AM to 8 PM Eastern on weekdays. Outside that window the top bar and the Live chart say so, because overnight trading (like Robinhood's 24 Hour Market) can still move your broker's balance.

## 1.0.3

- History for a brokerage linked just before the first sync: SnapTrade lists the holdings at once but gathers the
  history over a few minutes. The first sync now waits for it (or you can go on without waiting), and an account
  SnapTrade is still loading is read again every few minutes until it is done, with a note on the home page meanwhile.
- Share counts the history does not explain become holdings from before the history starts, at the broker's cost,
  instead of appearing as bought on the day Basis was installed. A trade made just before that, whose row SnapTrade
  delivers a day later, is taken back off once its row arrives.
- Sectors, dividends and all-time highs are read during the first sync, not hours later, and a new holding gets its
  price history, sector and dividends as soon as it appears.
- Holdings that only the broker's share count knows about get their price history in charts and correlations.
- Copies set up with 1.0.1 or 1.0.2 read every account's history once more after this update and match share counts
  again on the whole history.
- Faster: the model builds about a quarter faster, pages never wait for the rebuild after new prices, a loading screen
  shows while Basis opens, and "Updating" shows in the top bar whenever something takes a moment.

## 1.0.2

- Fixed the first sync stopping with "bad parameter or other API misuse": two SnapTrade syncs could write at once.
  Only one runs at a time now, each account's history is saved in one step (much faster on Windows), and the
  background sync waits until the first one is done.
- An earlier first sync cut short could leave out an account's older history. Each copy reads every account's
  history once more after this update; if that fills a gap, the account's share counts are matched again.
- The welcome page shows progress while it downloads price history, and its SnapTrade steps point to Build, then
  API Keys in the SnapTrade dashboard.

## 1.0.1

- The credit in Data and settings links to connoraherne.com.

## 1.0.0

First public version.

- The desktop program for Windows: installer, its own window, a welcome page that sets up SnapTrade.
- Everything measured against M2, the Doom score and Doom dashboard, the Assets dashboard with Glory Days Worth,
  leverage, the margin estimate, the weekly options menu, hide-amounts.
- Bitcoin and crypto holdings grouped apart from Technology, with treasury companies found on their own.
- The Customize chat (optional): Claude Code edits the program for you, with diffs, undo and a safe restart.
- Updates from GitHub releases, with a switch to turn checking off.
