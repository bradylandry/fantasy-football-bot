# Cheat sheet

## Sub-features

- `board` — ADP for one league's scoring, team count, and season
- `seed` — generic 12-team full-PPR sheet written to `seed_board.json`

## How to get to it (user POV)

On draft day the manager asks for the sheet. The agent runs `draft_mode.py board` with the Sleeper league id and reads `status`, `kept`, `feed`, and `attribution`. Say the attribution when showing ranks. `seed` is the file the bot ships with. GitHub Actions runs it on Mondays and pushes only in August.

## Driving it with unittest

```bash
python3 -m unittest tests.test_draft_mode -v
```

The tests patch the feed HTTP calls. A full feed of at least 150 players wins. A shorter feed is discarded and the previous `board.json` or `seed_board.json` is kept (`status` `kept_previous`). The shipped seed names Fantasy Football Calculator.

Do not run `python3 draft_mode.py board` or `python3 draft_mode.py seed` for verification. Both fetch live ranking feeds. `board` writes `board.json` unless `--out` is set. `seed` can replace `seed_board.json` when the new list differs and has at least 150 players.

## Gotchas

- Feed order is Fantasy Football Calculator, then the on-disk board, then FantasyCalc for the current UTC year only. `seed` never uses FantasyCalc.
- A superflex or two-QB roster sets `qb_warning`. The sheet is 1QB ADP.
- A failed or empty `seed` leaves the existing file byte for byte, including when the file is absent (it does not create an empty file).
