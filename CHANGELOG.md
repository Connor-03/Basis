# Changes

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
