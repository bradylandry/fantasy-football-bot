# Draft recommendations

`draft_mode.py watch` prints one JSON object per line while a Sleeper draft is polled. `now` prints one recommendation for a host that cannot keep a process. Neither command submits a pick.

## Sub-features

- `watch` polls and emits `recommendations` with `phase` `two_out` or `on_clock`.
- `watch --replay` walks the pick list once.
- `now` emits one `recommendations` event with `phase` `on_clock` or `upcoming`.

## How to get to it (user POV)

The manager names a Sleeper username and a draft id.

- If the agent can keep a process, it runs `python3 draft_mode.py watch DRAFT_ID --user USERNAME` and speaks on each `recommendations` event. `top` lists eight players.
- Otherwise it runs `python3 draft_mode.py now DRAFT_ID --user USERNAME`.
- An auction draft prints `refused` and exits 2.

## Driving it with unittest

Preconditions:

- The repo root is the working directory.
- `tests/fixtures/draft_2026.json` is a snake draft, 10 teams, slot 5, `reversal_round` 0.
- `tests/fixtures/board_2026-09-04.json` is the board.
- The tests patch `fetch_json`, `fetch_user`, `fetch_players`, and `time.sleep`.

```bash
python3 -m unittest tests.test_ranker.TestWatcher tests.test_ranker.TestNow tests.test_ranker.TestQbWarning -v
```

- **Heads-up.** Pick 25 is this seat's turn. `phase_at(24)` and `phase_at(23)` are `two_out`. `phase_at(25)` is `on_clock`. `phase_at(22)` is silence.
- **Once.** A narrator stepped at 23, 24, 25, and 25 again emits `two_out`, then nothing, then `on_clock`, then nothing.
- **Fields.** The on-clock event has eight `top` rows. Each row has `survival`, `value_vs_pick`, and `falling`. `value_vs_pick` is the manager's pick minus ADP. The status event has `submits_picks` false.
- **Jump.** A poll that skips from 20 picks to 29 still emits `two_out` and `on_clock` for pick 25.
- **Now.** With the picks before 36 loaded, `now_event` has `phase` `on_clock` and `your_pick` 36.
- **Proof.** Exit code 0 and the verbose log ends in `OK`.

Do not run `watch` or `now` against a live draft id.

## Gotchas

- `tests/fixtures/draft_2026.json` sets `reversal_round` to 0. This drive does not exercise a reversal round. That property is `.cursor/skills/verify-draft-pick-order/SKILL.md`.
- Default `watch` on a finished draft emits `status` and `complete` and exits. `--replay` emits `complete` with `"replay": true`. `--replay` still fetches the draft, the user, the league, and the picks once. It does not submit a pick.
- A draft whose order is not set emits `waiting` and, without `--replay`, polls for up to an hour.
- `Out`, `IR`, `PUP`, `NA`, `Sus`, `COV`, and `DNR` are never recommended. `Questionable` and `Doubtful` are shown.
