---
name: verify-draft-pick-order
description: >-
  Formally check draft_mode.py pick order: every snake and linear seat,
  including a reversal round, and when watch says on_clock or two_out.
  Use when a change touches my_pick_numbers, phase_at, Narrator.step,
  live_steps, or unsupported_reason, or when a draft alert fires on the
  wrong pick.
---

# Verify draft pick order

`draft_mode.py` is a recommend-only CLI. `watch` and `now` tell a manager when a pick is theirs. That moment comes from `my_pick_numbers` and `phase_at`. The unit tests build one 10-team snake whose `reversal_round` is 0, so a broken 3rd-round reversal still passes those tests.

## Property

For every snake or linear draft inside the bounds below, each seat gets a disjoint set of overall picks that together cover the draft exactly once, snake direction follows the documented parity (a reversal round keeps the previous round's direction, and linear ignores reversal), and `phase_at` plus `live_steps` say `on_clock` only on that seat's picks and `two_out` only one or two picks before the next one.

Harm this prevents: `watch` or `now` tells the manager they are on the clock on someone else's pick, or stays silent on their own turn, including after a reversal round.

## Tool

The check runs the real functions on every input inside a finite bound. That is the cheap tool that answers this question. The property is "what does this function return for every draft we claim to support," and the bound is small enough to finish in a few seconds with the standard library.

A TLA+ model would be a second copy of the formula. It could pass while `draft_mode.py` is wrong. Hypothesis would sample and can miss one reversal corner. Python has no verifier in this repo, and none is required: every input up to the bound is executed.

This is not a TLA+ model. Do not run it through `/statechart`.

## Run

From the repo root:

```bash
python3 .cursor/skills/verify-draft-pick-order/check_pick_order.py
```

Exit 0 prints `ok`, the sync commit, and the counts. Exit 1 prints `FAIL` and one counterexample: the draft settings, what the function returned, what the documented rule required, and a `python3 -c` replay that calls `my_pick_numbers`. The script does not write a file and does not use the network.

## Coverage

| Code | What the check asserts |
| --- | --- |
| `draft_mode.my_pick_numbers` | Snake and linear pick lists, including the reversal flip |
| `draft_mode.phase_at` | `on_clock`, `two_out`, or silence from that list |
| `draft_mode.Narrator.step` | Each `(your_pick, phase)` is spoken once along a full walk |
| `draft_mode.live_steps` | A jump from pick 1 to the end announces the same turns as the walk |
| `draft_mode.unsupported_reason` | Returns `None` for `snake` and `linear`, and a reason for `auction`. Separately, `my_pick_numbers` raises `DraftTypeError` for `auction`, `dynasty`, and `""` |

`Narrator.step` calls `recommend` on a two-player board so the walk can run. The check does not judge scores, names, or injury filters. A wrong recommendation still passes.

Sync commit: `SYNC_COMMIT` in `.cursor/skills/verify-draft-pick-order/check_pick_order.py`. That commit is the `draft_mode.py` this checker was confirmed to describe. Move it forward only after you re-read the functions in the table at the new commit, confirm the checker still describes them, and rerun this command. A pass on a checker that no longer matches the code is not a pass. If the new commit changes the promise on purpose, update the property sentence and the checker together, and cite the docstring of `my_pick_numbers` or the snake paragraph in `README.md`. Do not change product code from this skill.

## Bounds

- Teams 2 through 16, every seat from 1 through the team count.
- Rounds 1 through 20.
- `reversal_round` from 0 (the snake is not flipped) through `rounds`.
- Draft types `snake` and `linear` for the full grid.
- `Narrator` / `live_steps`: teams 8, 10, 12, and 14; rounds 3 and 15; `reversal_round` 0 and 3; both draft types; every seat. The walk calls the real `Narrator` and `live_steps`.

The shape grid is 62100 seat checks and 6900 partition checks. The narrator grid is 352 walks. A normal run is a few seconds.

## What a failure means

A real bug: the printed rule is the one in the `my_pick_numbers` docstring and the README snake paragraph, and the replay call returns the printed pick. Report it in those words. Do not edit the checker so the bad pick passes, and do not edit `draft_mode.py` from this skill.

A modeling error: the checker demands a behavior those two texts do not state (for example treating a linear draft as a snake, or flipping a snake when `reversal_round` is 0). Fix the checker. Say what was wrong with it.

The scratch that showed the check can fail: in `my_pick_numbers`, the reversal test `r >= reversal_round` was temporarily changed to `r > reversal_round`, and the command exited 1. The first counterexample was a snake draft, 2 teams, 1 round, `reversal_round` 1, seat 1. Round 1 should sit at position 2. The broken function returned overall pick 1. That edit was reverted. Do not commit a scratch edit. Repeat it only when you change the checker and need to see the check fail again, then restore the line.

## Out of scope

- Seats outside 1..teams, team counts above 16, rounds above 20, and a negative `reversal_round`. `_draft_settings` passes `int(settings["teams"])` through, and a negative reversal would flip every round. That input is not in the bound.
- Whether the recommended player is the right one: scoring, bye, tier, injury status, kicker timing, depth caps, and must-fill. Those live in `recommend`.
- `grade_picks` (`pick_no - adp`).
- The `cmd_watch` poll loop: the pre-draft wait, `poll_error` retry, `--replay` fetches, and the "already complete, stay quiet" branch. `live_steps` is checked. The sleep and the HTTP loop are not.
- Cheat-sheet feeds, ESPN, and Yahoo.
- `submits_picks` is the constant `False` on the status event. This check does not read that event.

A close second target, if you add another check later, is `recommend`: an `Out` / `IR` player, a third quarterback in a one-QB league, or a kicker before the last three picks must never be the suggestion. The current unit tests cover single cases on one board, not the combination.

Keep this skill in step with the code using `/maintain-formal-verification-skill` after a change that touched the functions above and did not rerun this command.
