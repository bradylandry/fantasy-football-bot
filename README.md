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

`board` tries feeds in this order. The first one that matches at least
150 players wins. A shorter list is a fragment and is discarded.
The JSON field `feed` names the source that was used (`source` is still
the on-disk file path). `attempts` lists each feed that was tried.

1. Fantasy Football Calculator ADP, for the league's scoring format, team
   count, and season. Their API docs allow this use and ask for attribution.
2. The previous `board.json`, then `seed_board.json`.
3. FantasyCalc current redraft ranks (`overallRank`), same scoring format
   (`ppr` / `half-ppr` / `standard` → `1` / `0.5` / `0`). Team count is
   sent when it is 8, 10, 12, or 14; any other size uses the nearest of
   those. A superflex roster sends `numQbs=2`. FantasyCalc has no season
   parameter, so it is used only when the requested season is the current
   UTC calendar year. Skill positions only (no kicker or defense) and no
   bye week. Their API docs allow this endpoint, ask that results be
   cached, and require a visible attribution. Because it has no standard
   deviation, bye week, kicker, or defense, survival odds and the bye rule
   go blind on it. It is used only when no full board exists on disk.

`seed` uses Fantasy Football Calculator only. The seed ships to every
user, so a day when FFC is down never replaces it with FantasyCalc.

When a live feed is used, the JSON includes `attribution`. Say that name
when you show the cheat sheet.

## board

Reads `scoring_settings.rec` and the league's team count, then requests
ADP for `league.season`.

- `rec >= 0.75` → full PPR
- `rec >= 0.25` → half PPR
- otherwise → standard

If FFC returns fewer than 150 matched players, or the request fails, the
previous full board is kept. The fallback file is
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

Polls `GET /draft/{id}/picks` every 5 seconds (`--poll` to change it).
Reads `draft.type` and `settings.reversal_round`.

Start it early. While the draft is `pre_draft` and the order is not set,
it emits one `waiting` event and polls (up to an hour) instead of failing.

On start it refreshes every player's `injury_status` from Sleeper's
player list (cached one hour). The seed's own flags are from the day it
was built.

- **snake** — direction flips every round. From `reversal_round` onward
  the flip is skipped once, which is Sleeper's 3rd-round reversal, and
  the snake continues from the new parity.
- **linear** — same slot every round.
- **auction** — refused. Exit code 2 and a `refused` event. No picks,
  no grades.

Events:

- `status` — slot, pick list, board in use, `"submits_picks": false`
- `recommendations` — `phase` is `two_out` (one or two picks before the
  manager's turn) or `on_clock`. Each is sent once per pick, even when
  several picks land inside one poll. `top` lists eight players
  (`--top` to change it). Each has `survival` (chance he is still there
  at `horizon`, 0–1), `value_vs_pick` (your pick minus ADP; positive is a
  steal), `falling` (the room has let him go 20+ picks past ADP: check
  the news before taking him), `injury_status`, and a `reason` string.
  On the clock, `changed_from` appears when the two-out favorite is no
  longer first, with `why`: `taken` or `outscored`.
- `waiting` — the draft order is not set yet.
- `warning` — the injury refresh failed; board flags are used.
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

Score is ADP value plus the value lost by waiting (how much worse the
best player at that position is expected to be at the horizon), then:

- **Need** is a nudge: ×1.15 for a player who fills an empty dedicated
  starting slot, ×1.05 for an empty flex. Timing comes from waiting
  cost, not need. (A flat ×2 for any empty slot pushed QBs and TEs 30
  picks early in a live draft, when they were 80–96% likely to still be
  there.)
- **Bye** applies only when that week would leave a starting slot empty
  that this player would otherwise have filled. One empty slot costs
  one week out of 17. A surplus receiver on a crowded bye is not
  penalized.
- **Tier** gives the best available player at a position a lift of up
  to one round of ADP when the next player at that position is a tier
  down. It does not jump that player ahead of a better one at the same
  position.

Filters, before scoring:

- **Injury.** `Out`, `IR`, `PUP`, `NA`, `Sus`, `COV`, and `DNR` are never
  recommended. `Questionable` and `Doubtful` are shown, not filtered.
- **Kicker and defense** wait for the manager's last three picks.
- **Depth cap.** No third QB in a one-QB league, no third TE, no second
  kicker or defense.
- **Must fill.** When the picks left equal the empty dedicated starting
  slots, only those positions are listed.

Waiting is priced from ADP survival: a player still available at the
next pick is not urgent.

## Not in this script

Auction bidding, keeper prices, and news. ADP does not know why a player
is falling; `falling` says when to look. Waiver scans and Sunday lineup
checks live in the bot, not in this repository. The script never submits
a pick.

## License

MIT. See [LICENSE](LICENSE). Ranking data belongs to its feed; show the
`attribution` string when you display a cheat sheet.
