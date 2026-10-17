# Draft grade

## Sub-features

- `grade` — one JSON object after the draft is full

## How to get to it (user POV)

After the draft the manager asks how the roster graded. The agent runs `draft_mode.py grade` with the draft id and the Sleeper username. It reports `total_value`, `biggest_steal`, `biggest_reach`, and `holes`. `value` is `pick_no - adp` (positive is a steal). Auction drafts are refused.

## Driving it with the CLI

unittest never calls `grade`. The safe check is help, which exits before any fetch:

```bash
python3 draft_mode.py grade --help >/dev/null
```

Do not run `python3 draft_mode.py grade` with a draft id. That fetches the draft, the league, and the pick list. There is no fixture flag and no dry-run.

## Gotchas

- The script never submits a pick. Grade is a read.
- A thin on-disk board (under 150 players) is an error, not a grade.
- `qb_warning` is attached when the roster is superflex or two QB.
