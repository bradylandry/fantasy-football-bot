# Fantasy football bot

Recommend-only assistant for Sleeper fantasy football. It reads league data
from the public Sleeper API (no login). It builds a draft cheat sheet, sends
the top three picks while a draft is live, and grades the roster afterward.
During the season it runs a Tuesday waiver scan (including defense streaming
and FAAB bid suggestions) and a Sunday lineup check.

It never logs in to Sleeper and never makes a move. The manager makes every
move in the Sleeper app. No Sleeper credentials are required.

## What this repo contains

The draft helper the bot downloads on first run:

- `draft_mode.py` — one standard-library Python script
- `seed_board.json` — the cheat sheet it ships with (267 players, a public
  full-PPR ADP snapshot from 2026-09-04)

League ids, draft ids, and usernames are arguments or API responses. Nothing
here is tied to one league.

A run of `board` may write `board.json` next to the script. That file is
generated. The seed stays until a full feed replaces it.

## Download and run

No clone. Save both files in the same directory. The script reads
`seed_board.json` next to itself.

```bash
curl -fsSL -o draft_mode.py https://raw.githubusercontent.com/bradylandry/fantasy-football-bot/main/draft_mode.py
curl -fsSL -o seed_board.json https://raw.githubusercontent.com/bradylandry/fantasy-football-bot/main/seed_board.json
```

## Requirements

Python 3. Standard library only. Network access to `api.sleeper.app`.
Refreshing the cheat sheet also calls Fantasy Football Calculator.

## Commands

Stdout is JSON. `watch` prints one object per line. The username is
resolved with `GET /user/{username}`, and the draft object points at
the league.

```bash
python3 draft_mode.py board --league LEAGUE_ID
python3 draft_mode.py watch DRAFT_ID --user USERNAME
python3 draft_mode.py watch DRAFT_ID --user USERNAME --replay
python3 draft_mode.py grade DRAFT_ID --user USERNAME
python3 draft_mode.py seed
```

## Rankings feeds

`board` and `seed` try feeds in this order. The first one that matches at
least 150 players wins. A shorter list is a fragment and is discarded.
The JSON field `feed` names the source that was used (`source` is still
the on-disk file path). `attempts` lists each feed that was tried.

1. Fantasy Football Calculator ADP, for the league's scoring format, team
   count, and season. Their API docs allow this use and ask for attribution.
2. FantasyCalc current redraft ranks (`overallRank`), same scoring format
   (`ppr` / `half-ppr` / `standard` → `1` / `0.5` / `0`). Team count is
   sent when it is 8, 10, 12, or 14; any other size uses the nearest of
   those. A superflex roster sends `numQbs=2`. FantasyCalc has no season
   parameter, so it is used only when the requested season is the current
   UTC calendar year. Skill positions only (no kicker or defense) and no
   bye week. Their API docs allow this endpoint, ask that results be
   cached, and require a visible attribution.
3. The previous `board.json`, then `seed_board.json`.

When a live feed is used, the JSON includes `attribution`. Say that name
when you show the cheat sheet.

## board

Reads `scoring_settings.rec` and the league's team count, then requests
ADP for `league.season`.

- `rec >= 0.75` → full PPR
- `rec >= 0.25` → half PPR
- otherwise → standard

If every live feed returns fewer than 150 matched players, or the requests
fail, the previous full board is kept. The fallback file is
`seed_board.json` beside the script. A refreshed sheet is written to
`board.json` beside the script.

## seed

Rebuilds `seed_board.json` for a generic league. Defaults are 12 teams,
full PPR, 1 quarterback, and the season from Sleeper `GET /state/nfl`
(the UTC calendar year if that call fails).

```bash
python3 draft_mode.py seed
python3 draft_mode.py seed --teams 12 --scoring ppr --year 2026
```

The file is replaced only when the new board matches at least 150 players
and the player list actually changed. A fragment, an error, or an empty
response leaves the existing file byte for byte. On a write, the first
player object also carries `generated_at`, `source`, `source_url`,
`season`, `teams`, `scoring`, and `num_qbs`. Those keys are ignored by
the loader, including older copies of this script. A bare list with no
provenance still loads.

GitHub Actions runs this weekly on Mondays and pushes to `main` only in
August, plus whenever someone starts the workflow by hand. Scheduled
runs in other months exit without fetching. The workflow file has to be
on `main` before the schedule will fire.

## watch

Polls `GET /draft/{id}/picks` every 20 seconds (`--poll` to change it).
Reads `draft.type` and `settings.reversal_round`.

- **snake** — direction flips every round. From `reversal_round` onward
  the flip is skipped once, which is Sleeper's 3rd-round reversal, and
  the snake continues from the new parity.
- **linear** — same slot every round.
- **auction** — refused. Exit code 2 and a `refused` event. No picks,
  no grades.

Events:

- `status` — slot, pick list, board in use, `"submits_picks": false`
- `recommendations` — `phase` is `two_out` (two picks before the
  manager's turn) or `on_clock`. `top` is three players, each with one
  `reason` string.
- `complete` — the draft is full; the process exits 0.
- `poll_error` — a fetch failed; it waits and tries again.

`--replay` fetches the pick list once and walks it without sleeping, so
a finished draft can be re-read. The default watch on a finished draft
emits `status` and `complete` and exits.

Two picks out, survival is measured until the manager's pick. On the
clock, it is measured until the pick after this one.

## grade

One JSON object. `value` is `pick_no - adp` (positive is a steal,
negative is a reach), plus `total_value`, the starting lineup, `holes`,
`biggest_steal`, and `biggest_reach`. Auction drafts are refused.

## Ranker

Three rules, applied the same way every pick:

- **Need** scales with how many startable players at that position are
  likely still available at the horizon. Startable means ADP at or
  before replacement (starters at that position times team count). A
  deep pool that will survive stays near weight 1. An empty room goes
  to 2.
- **Bye** applies only when that week would leave a starting slot empty
  that this player would otherwise have filled. One empty slot costs
  one week out of 17. A surplus receiver on a crowded bye is not
  penalized.
- **Tier** gives the best available player at a position a lift of up
  to one round of ADP when the next player at that position is a tier
  down. It does not jump that player ahead of a better one at the same
  position.

Waiting is priced from ADP survival: a player still available at the
next pick is not urgent.

## Not in this script

Auction bidding, keeper prices, and injury as a score. Injury status is
passed through on the player object only. Waiver scans and Sunday lineup
checks live in the bot, not in this repository. The script never submits
a pick.
