---
name: verify-fantasy-football-bot
description: >-
  Verify the fantasy football bot CLI (draft_mode.py and league_read.py:
  cheat sheet, draft recommendations, grade, and ESPN league reads).
  Use before opening or updating a PR.
---

# Verify the fantasy football bot

Recommend-only CLI. No web UI, no server, no listen port. `draft_mode.py` reads a Sleeper league and prints JSON. `league_read.py` reads an ESPN league (and an untested Yahoo league) and prints one JSON object. Neither script submits a pick, lineup, claim, or trade.

Run this from the repo root before opening or updating a PR. Attach the evidence directory. Two checkouts can run side by side: there is no port. `board` writes `board.json` in the working directory unless `--out` is set. A Yahoo token file is `$XDG_CONFIG_HOME/fantasy-football-bot/yahoo_token.json`, or `~/.config/fantasy-football-bot/yahoo_token.json`. Give each instance its own directory and its own `XDG_CONFIG_HOME`. This skill does not start a second instance and does not write a token.

The harness is unittest. It patches `league_read.http_json` and `draft_mode.fetch_json`. There is no Playwright, pytest, or curl harness.

## Launch

No process stays up. Readiness is both help commands exiting 0:

```bash
set -euo pipefail
python3 -m compileall -q draft_mode.py league_read.py
python3 league_read.py --help >/dev/null
python3 draft_mode.py --help >/dev/null
```

Teardown: these commands exit on their own. Nothing to kill.

## Doctor

One read-only check. It parses the shipped cheat sheet and does not fetch:

```bash
set -euo pipefail
python3 -c 'import json; from pathlib import Path; rows=json.loads(Path("seed_board.json").read_text()); assert isinstance(rows, list) and len(rows) >= 150, len(rows); print(json.dumps({"ok": True, "seed_players": len(rows)}))'
```

`150` is `MIN_BOARD` in `draft_mode.py`. The shipped file currently reports 267 players. `"ok": true` is the usable signal.

## Drive

Drive ESPN from the recorded fixtures. That is the check to run. Do not execute `league_read.py espn ...` in a shell: that calls ESPN. The tests call `league_read.main` with the same arguments and serve `tests/fixtures/espn_league.json`, `espn_byes.json`, `espn_free_agents.json`, and `espn_scoreboard.json`.

```bash
set -euo pipefail
python3 -m unittest tests.test_league_read.TestEspnCommands tests.test_league_read.TestEspnShapes -v
```

Fixture league `100001`, season `2026`, team `1`. `TestEspnCommands.test_settings_roster_slots_scoring_and_waivers` expects `name` `Example League`, `size` 10, `current_week` 3, `scoring` `standard`, `reception_points` 0, waiver `type` `WAIVERS_TRADITIONAL`, `uses_faab` false, `budget` 100, draft `status` `complete`, draft `type` `SNAKE`. Roster team 1: Jahmyr Gibbs, slot `RB`, `projected_points` 21.45, `bye_week` 8. A passing run is the drive.

Other features have their own files under `features/`. Drive one feature per proof run; run the rest of the map when the diff touches them.

## Evidence

Save under `/tmp/verify-fantasy-football-bot/`. That directory is outside the repo, so a token or cookie cannot land in git. There is no screen to shoot. Capture stdout, exit codes, and the network lock.

From the repo root, one command writes the proof:

```bash
bash .cursor/skills/verify-fantasy-football-bot/scripts/prove.sh
```

The script removes stale files in that directory first, then writes:

- `launch.txt` must contain the line `launch ok` (compileall, then both `--help` commands).
- `doctor.json` is `{"ok": true, "seed_players": N}` with N at least 150. The shipped seed reports 267.
- `espn-fixtures.txt` is the verbose unittest output. A pass ends in `OK`.
- `espn-fixtures.status` is that process's exit code. `0` is a pass.
- `network-lock.txt` starts with `connect_attempts 0`. Before the tests import, the script replaces `socket.socket.connect` with a function that records the address and raises. A pass with zero attempts is the observation that this drive did not open a socket to ESPN, Sleeper, or Yahoo. The tests mock `league_read.http_json`. The lock is how you see that the mock held.
- `side-effects.txt` contains `board.json unchanged`, `seed_board.json unchanged`, and `yahoo token unchanged`. The token path is `$XDG_CONFIG_HOME/fantasy-football-bot/yahoo_token.json`, or `~/.config/fantasy-football-bot/yahoo_token.json`. Checksums are sha256, or the word `absent` when the file is missing.
- `git-status.txt` contains `git status unchanged` when `git status --porcelain` is the same before and after the drive.
- `run.status` is `0` only when the tests, the lock, the checksums, and git status all passed. A failure writes `1` and exits 1. Stale files are removed at the start, so an old `0` cannot outlive a failed run.

The ESPN unittest is the user path (`settings`, `teams`, `roster`, `free-agents`, `matchup`) with the mock on `http_json` only. An existing gitignored `board.json` stays valid when the drive does not change it. The Yahoo connect test is not part of this drive. When the full suite runs it, that test writes its token inside a temporary directory and deletes it.

`--replay` on `watch` is the only dry-run flag. It skips the pre-draft wait, the injury refresh, and the poll loop. It still fetches the draft, the user, the league, and the pick list once. It does not submit a pick (`submits_picks` is false). Do not run it here. `league_read.py` has no dry-run flag. ESPN requests are GET only. `board` and `seed` have no dry-run. Both fetch ranking feeds, and `seed` can replace `seed_board.json`. Do not run them here.

## Cleanup

Kill only a process this skill started. Launch and the fixture drive start none. `prove.sh` exits on its own.

These checks read the evidence. They do not delete it, and they do not delete `seed_board.json`:

```bash
set -euo pipefail
out=/tmp/verify-fantasy-football-bot
test "$(cat "$out/espn-fixtures.status")" = 0
test "$(cat "$out/run.status")" = 0
grep -qx 'launch ok' "$out/launch.txt"
grep -q '"ok": true' "$out/doctor.json"
test -s "$out/espn-fixtures.txt"
grep -qx 'connect_attempts 0' "$out/network-lock.txt"
grep -qx 'board.json unchanged' "$out/side-effects.txt"
grep -qx 'seed_board.json unchanged' "$out/side-effects.txt"
grep -qx 'yahoo token unchanged' "$out/side-effects.txt"
grep -qx 'git status unchanged' "$out/git-status.txt"
test -d "$out"
```

Leave `/tmp/verify-fantasy-football-bot/` in place.

## Helpers

`scripts/prove.sh` is executable. From the repo root:

```bash
bash .cursor/skills/verify-fantasy-football-bot/scripts/prove.sh
```

It finds the repo root from its own path, then runs Launch, Doctor, the ESPN drive, the evidence writes, and the Cleanup checks, in that order. Exit 0 means every Cleanup check passed. The evidence directory is still there after it exits.

## Repo-specific checks

Run `league_read.py` against the recorded ESPN fixtures (the Drive command above). Yahoo is untested. Do not run `yahoo connect`, `yahoo leagues`, or `yahoo get`. A green `tests.test_league_read.TestYahoo` only shows the mocked client; it does not test Yahoo. Confirm the help text still says so:

```bash
set -euo pipefail
python3 league_read.py yahoo --help | grep -q -i untested
```

## Tests, lint, and build

Local suite (this is not a CI step):

```bash
set -euo pipefail
python3 -m unittest
```

CI is `.github/workflows/refresh-seed.yml`. It does not run unittest, a linter, or a build. The only command is `python3 draft_mode.py seed`, and only when the UTC month is August or the workflow `force` input is true. There is no linter config and no package build. Do not run `seed` while verifying.

Pick order (`my_pick_numbers`, `phase_at`, `Narrator.step`, `live_steps`) is a separate check: `.cursor/skills/verify-draft-pick-order/SKILL.md`.

When a user-facing feature changes, update `features/` and this skill. `/maintain-verification-skill` is the upkeep loop.
