# Draft recommendations

## Sub-features

- `watch` — one JSON object per line while a draft is polled
- `watch --replay` — walk the pick list once
- `now` — one recommendation, for a host that cannot keep `watch` running

## How to get to it (user POV)

The manager names a Sleeper username and a draft id. If the agent can keep a process, it runs `watch` and speaks on each `recommendations` event (`phase` `two_out` or `on_clock`, `top` of eight). Otherwise it runs `now` (`phase` `on_clock` or `upcoming`). Auction drafts emit `refused` and exit 2.

## Driving it with unittest

```bash
set -euo pipefail
python3 -m unittest tests.test_ranker.TestWatcher tests.test_ranker.TestNow tests.test_ranker.TestQbWarning -v
```

Fixtures are `tests/fixtures/draft_2026.json` (snake, 10 teams, slot 5) and `tests/fixtures/board_2026-09-04.json`. The tests patch `fetch_json`. A recommendation row has `survival`, `value_vs_pick`, and `falling`. `submits_picks` is false.

Do not run `watch` or `now` against a live draft id. `--replay` skips the pre-draft wait, the injury refresh, and the five-second poll. It still fetches the draft, the user, the league, and the picks. It does not submit a pick.

## Gotchas

- Default `watch` on a finished draft emits `status` and `complete` and exits. `--replay` emits `complete` with `"replay": true`.
- A draft whose order is not set emits `waiting` and, without `--replay`, polls for up to an hour.
- `Out`, `IR`, `PUP`, `NA`, `Sus`, `COV`, and `DNR` are never recommended. `Questionable` and `Doubtful` are shown.
