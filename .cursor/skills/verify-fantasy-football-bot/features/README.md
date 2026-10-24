# Fantasy football bot verification map

This directory is the maintained source for verifying the user-facing behavior of the fantasy football bot. The surface is a CLI. There is no web UI, no server, and no port. Read this index, then use the matching feature file as the recipe.

## Baseline preconditions

- Work from the repo root. Python 3 and the standard library are enough.
- Launch with `python3 -m compileall -q draft_mode.py league_read.py`, then `python3 league_read.py --help` and `python3 draft_mode.py --help`. Both help commands exit 0.
- Run `bash .cursor/skills/verify-fantasy-football-bot/scripts/prove.sh` for the ESPN proof. It writes `/tmp/verify-fantasy-football-bot/`.
- Two checkouts can run side by side. There is no port. `board` writes `board.json` in the working directory unless `--out` is set. The Yahoo token file follows `XDG_CONFIG_HOME`. Give each instance its own directory and its own `XDG_CONFIG_HOME`.
- Do not call ESPN, Sleeper, Fantasy Football Calculator, FantasyCalc, or Yahoo during verification. The drives below mock `league_read.http_json` or `draft_mode.fetch_json`, or they stop at `--help`.

## Driving conventions

- Start from the repo root.
- Treat every command as literal.
- Drive one feature per proof run. Run the rest of the map when the diff touches them.
- The ESPN proof is the default full drive. Its evidence directory survives cleanup.
- A feature whose only safe command is `--help` is not verified. Say so.

## Proof and skip reporting

- Capture the command, stdout, and exit code under `/tmp/verify-fantasy-football-bot/`.
- Record side effects: `board.json`, `seed_board.json`, the Yahoo token, and `git status --porcelain`.
- The ESPN drive also records `connect_attempts`. Zero means no socket was opened.
- Report an unreachable path with the command you refused and the missing precondition. Do not call a live league id and do not describe `--help` as a proof of the numbers.

## Features

| Feature | File | Verification |
| --- | --- | --- |
| ESPN league read | [espn-league.md](espn-league.md) | Recorded fixtures via unittest. This is the default proof. |
| Cheat sheet | [cheat-sheet.md](cheat-sheet.md) | unittest, network patched |
| Draft recommendations | [recommendations.md](recommendations.md) | unittest, network patched |
| Draft grade | [grade.md](grade.md) | Help only. The grade numbers are unverified. |
| Yahoo league read | [yahoo.md](yahoo.md) | Untested. Do not call Yahoo. |
