# ESPN league read

`league_read.py espn` prints one JSON object for a public ESPN league: settings, teams, roster, free agents, and the current matchup. It never sets a lineup, a claim, or a trade.

## Sub-features

- `settings` reports name, size, week, scoring, roster slots, waivers, and draft status.
- `teams` reports id, name, and owner ids. `my_team_id` appears only when both cookies are set.
- `roster` reports slot, position, NFL team, injury, this week's projection, and bye week.
- `free-agents` lists free agents and waivers, sorted by percent owned, with a position filter and a limit.
- `matchup` reports the opponent and live, final, or projected points.

## How to get to it (user POV)

The manager pastes an ESPN league id from the league URL. The agent runs one of:

- `python3 league_read.py espn settings --league LEAGUE_ID`
- `python3 league_read.py espn teams --league LEAGUE_ID`
- `python3 league_read.py espn roster --league LEAGUE_ID --team TEAM_ID`
- `python3 league_read.py espn free-agents --league LEAGUE_ID --limit 25`
- `python3 league_read.py espn matchup --league LEAGUE_ID --team TEAM_ID`

A public league needs no cookie. A private league prints `error` `private_league` until `ESPN_S2` and `ESPN_SWID` are set. Collect those with a secret request. Do not print them.

## Driving it with unittest

Preconditions:

- The repo root is the working directory.
- `tests/fixtures/espn_league.json`, `espn_byes.json`, `espn_free_agents.json`, and `espn_scoreboard.json` are present.
- `bash .cursor/skills/verify-fantasy-football-bot/scripts/prove.sh` is the proof command. It loads the tests below with `socket.socket.connect` replaced by a function that records the address and raises.

- **Settings.** The test calls `league_read.main` with `espn settings --league 100001 --season 2026`. Exit code 0. JSON `name` is `Example League`, `size` is 10, `current_week` is 3, `scoring` is `standard`, `reception_points` is 0, waiver `type` is `WAIVERS_TRADITIONAL`, `uses_faab` is false, `budget` is 100, draft `status` is `complete`, draft `type` is `SNAKE`.
- **Roster.** The test calls `espn roster --league 100001 --season 2026 --team 1`. Jahmyr Gibbs is in the roster at slot `RB`, `projected_points` 21.45, `bye_week` 8.
- **Free agents.** The test calls `espn free-agents --league 100001 --season 2026 --position QB --limit 2 --week 4`. The first two names are Cameron Dicker, then Bo Nix.
- **Matchup.** The test calls `espn matchup --league 100001 --season 2026 --team 1`. `final` is false, `opponent_id` is 3, `points` is 12.5.
- **Proof.** `prove.sh` writes `/tmp/verify-fantasy-football-bot/espn-fixtures.txt` (ends in `OK`), `espn-fixtures.status` (`0`), and `network-lock.txt` (`connect_attempts 0`).

Do not run those arguments in a shell outside the tests. That path calls ESPN.

## Gotchas

- `--season 2026` keeps the fixture path off the live season lookup. Omitting `--season` makes `settings` ask for the current NFL season first. Inside the test that call is still mocked.
- One of `ESPN_S2` or `ESPN_SWID` without the other returns `missing_cookie` and does not call ESPN.
- Budget 100 with `uses_faab` false is not a FAAB league.
- Do not put cookie values in the evidence file.
