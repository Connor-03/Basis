# Changes

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
