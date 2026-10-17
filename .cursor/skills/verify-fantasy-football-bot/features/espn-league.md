# ESPN league read

## Sub-features

- `settings` — name, size, week, scoring, roster slots, waivers, draft
- `teams` — id, name, owner ids, and `my_team_id` when cookies are set
- `roster` — slot, position, NFL team, injury, projection, bye
- `free-agents` — free agents and waivers, percent-owned sort, position filter, limit
- `matchup` — opponent, live or final points, projection

## How to get to it (user POV)

The manager pastes an ESPN league id from the league URL. The agent runs `league_read.py espn` and reads the JSON. A public league needs no cookie. A private league prints `error` `private_league` until `ESPN_S2` and `ESPN_SWID` are in the environment. Collect those with a secret request. Do not print them. The script never sets a lineup.

## Driving it with unittest

```bash
python3 -m unittest tests.test_league_read.TestEspnCommands tests.test_league_read.TestEspnShapes -v
```

`tests/test_league_read.py` calls `league_read.main` and serves the fixtures under `tests/fixtures/`. The settings case uses `espn settings --league 100001 --season 2026` and expects `Example League`, size 10, week 3, `scoring` `standard`, `uses_faab` false, budget 100. The roster case uses `--team 1` and expects Jahmyr Gibbs at `projected_points` 21.45 and `bye_week` 8. The free-agent case uses `--position QB --limit 2 --week 4` and expects Cameron Dicker then Bo Nix. The live matchup case uses `--team 1` and expects `final` false, `opponent_id` 3, `points` 12.5.

Do not run those arguments in a shell. That path calls ESPN.

## Gotchas

- `--season 2026` keeps the fixture path off the season lookup. Omitting `--season` makes `settings` ask for the current NFL season first (still mocked inside the test).
- One of `ESPN_S2` or `ESPN_SWID` without the other returns `missing_cookie` and does not call ESPN.
- Budget 100 with `uses_faab` false is not a FAAB league.
- Do not put cookie values in the evidence file.
