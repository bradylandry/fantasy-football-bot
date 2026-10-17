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
python3 -m compileall -q draft_mode.py league_read.py
python3 league_read.py --help >/dev/null
python3 draft_mode.py --help >/dev/null
```

Teardown: these commands exit on their own. Nothing to kill.

## Doctor

One read-only check. It parses the shipped cheat sheet and does not fetch:

```bash
python3 -c 'import json; from pathlib import Path; rows=json.loads(Path("seed_board.json").read_text()); assert isinstance(rows, list) and len(rows) >= 150, len(rows); print(json.dumps({"ok": True, "seed_players": len(rows)}))'
```

`150` is `MIN_BOARD` in `draft_mode.py`. The shipped file currently reports 267 players. `"ok": true` is the usable signal.

## Drive

Drive ESPN from the recorded fixtures. That is the check to run. Do not execute `league_read.py espn ...` in a shell: that calls ESPN. The tests call `league_read.main` with the same arguments and serve `tests/fixtures/espn_league.json`, `espn_byes.json`, `espn_free_agents.json`, and `espn_scoreboard.json`.

```bash
python3 -m unittest tests.test_league_read.TestEspnCommands tests.test_league_read.TestEspnShapes -v
```

Fixture league `100001`, season `2026`, team `1`. `TestEspnCommands.test_settings_roster_slots_scoring_and_waivers` expects `name` `Example League`, `size` 10, `current_week` 3, `scoring` `standard`, `reception_points` 0, waiver `type` `WAIVERS_TRADITIONAL`, `uses_faab` false, `budget` 100, draft `status` `complete`, draft `type` `SNAKE`. Roster team 1: Jahmyr Gibbs, slot `RB`, `projected_points` 21.45, `bye_week` 8. A passing run is the drive.

Other features have their own files under `features/`. Drive one feature per proof run; run the rest of the map when the diff touches them.

## Evidence

Save under `/tmp/verify-fantasy-football-bot/`. That directory is outside the repo, so a token or cookie cannot land in git. There is no screen to shoot. Capture stdout.

```bash
mkdir -p /tmp/verify-fantasy-football-bot
python3 -c 'import json; from pathlib import Path; rows=json.loads(Path("seed_board.json").read_text()); assert isinstance(rows, list) and len(rows) >= 150, len(rows); print(json.dumps({"ok": True, "seed_players": len(rows)}))' > /tmp/verify-fantasy-football-bot/doctor.json
token="${XDG_CONFIG_HOME:-$HOME/.config}/fantasy-football-bot/yahoo_token.json"
token_before=$(stat -c %Y "$token" 2>/dev/null || echo absent)
python3 -m unittest tests.test_league_read.TestEspnCommands tests.test_league_read.TestEspnShapes -v > /tmp/verify-fantasy-football-bot/espn-fixtures.txt 2>&1
token_after=$(stat -c %Y "$token" 2>/dev/null || echo absent)
test ! -e board.json
git diff --quiet -- seed_board.json
test "$token_before" = "$token_after"
printf '%s\n' 'board.json absent' 'seed_board.json unchanged' "yahoo token $token_after" > /tmp/verify-fantasy-football-bot/side-effects.txt
```

The ESPN unittest is the user path (`settings`, `teams`, `roster`, `free-agents`, `matchup`) with the mock on `http_json` only. `board.json` is gitignored, so `git status` does not show it. The checks above are the side effects: no `board.json`, `seed_board.json` unchanged, Yahoo token mtime unchanged. The Yahoo connect test, when run from the full suite, writes its token inside a temporary directory and deletes it.

`--replay` on `watch` is the only dry-run flag. It skips the pre-draft wait, the injury refresh, and the poll loop. It still fetches the draft, the user, the league, and the pick list once. It does not submit a pick (`submits_picks` is false). Do not run it here. `league_read.py` has no dry-run flag. ESPN requests are GET only. `board` and `seed` have no dry-run; both fetch ranking feeds, and `seed` can replace `seed_board.json`. Do not run them here.

## Cleanup

Kill only a process this skill started. Launch and the fixture drive start none.

```bash
test -s /tmp/verify-fantasy-football-bot/doctor.json
test -s /tmp/verify-fantasy-football-bot/espn-fixtures.txt
test -s /tmp/verify-fantasy-football-bot/side-effects.txt
```

Leave `/tmp/verify-fantasy-football-bot/` in place. Do not delete `seed_board.json`.

## Repo-specific checks

Run `league_read.py` against the recorded ESPN fixtures (the Drive command above). Yahoo is untested. Do not run `yahoo connect`, `yahoo leagues`, or `yahoo get`. A green `tests.test_league_read.TestYahoo` only shows the mocked client; it does not test Yahoo. Confirm the help text still says so:

```bash
python3 league_read.py yahoo --help | grep -q -i untested
```

## Tests, lint, and build

Local suite (this is not a CI step):

```bash
python3 -m unittest
```

CI is `.github/workflows/refresh-seed.yml`. It does not run unittest, a linter, or a build. The only command is `python3 draft_mode.py seed`, and only when the UTC month is August or the workflow `force` input is true. There is no linter config and no package build. Do not run `seed` while verifying.
