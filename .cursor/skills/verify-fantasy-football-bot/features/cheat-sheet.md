# Cheat sheet

`draft_mode.py board` prints the ADP cheat sheet for one Sleeper league. `seed` rebuilds the generic sheet that ships in `seed_board.json`. The manager reads `status`, `kept`, `feed`, and `attribution`.

## Sub-features

- `board` requests ADP for the league's scoring, team count, and season, and writes `board.json` unless `--out` is set.
- `seed` writes a generic 12-team full-PPR sheet to `seed_board.json` when the new list has at least 150 players and differs from the file on disk.

## How to get to it (user POV)

On draft day the manager asks for the sheet. The agent runs `python3 draft_mode.py board --league LEAGUE_ID` and says the `attribution` string when it shows ranks. `python3 draft_mode.py seed` is the refresh that GitHub Actions runs on Mondays. That workflow pushes only in August.

## Driving it with unittest

Preconditions:

- The repo root is the working directory.
- `tests/test_draft_mode.py` is present. It patches `fetch_json` and `fetch_players` and points writes at a temporary directory.

```bash
python3 -m unittest tests.test_draft_mode -v
```

- **Full feed.** A feed of at least 150 matched players wins. The JSON `status` is `refreshed` and `feed` names that source.
- **Short feed.** A shorter feed is discarded. The previous `board.json` or `seed_board.json` is kept, `status` is `kept_previous`, and a missing previous board raises `BoardTooThin`.
- **Seed file.** The shipped `seed_board.json` names Fantasy Football Calculator. A failed or empty `seed` leaves the existing file byte for byte, and does not create a file when none exists.
- **Proof.** Exit code 0 and the verbose log ends in `OK`. The temporary directory from the tests is gone when the process exits.

Do not run `python3 draft_mode.py board` or `python3 draft_mode.py seed` for verification. Both fetch live ranking feeds.

## Gotchas

- Feed order is Fantasy Football Calculator, then the on-disk board, then FantasyCalc for the current UTC year only. `seed` never uses FantasyCalc.
- A superflex or two-QB roster sets `qb_warning`. The sheet stays 1QB ADP.
- `board` writes `board.json` in the working directory unless `--out` is set. The unittest uses a temporary path. A hand run would not.
