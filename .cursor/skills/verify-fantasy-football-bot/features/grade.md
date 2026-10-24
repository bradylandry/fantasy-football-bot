# Draft grade

`draft_mode.py grade` prints one JSON object after the draft. `value` is `pick_no - adp` (positive is a steal). The object also carries `total_value`, `biggest_steal`, `biggest_reach`, and `holes`.

## Sub-features

- `grade` reads the finished pick list and grades this manager's picks.

## How to get to it (user POV)

After the draft the manager asks how the roster graded. The agent runs `python3 draft_mode.py grade DRAFT_ID --user USERNAME`. An auction draft is refused.

## Driving it with the CLI

Preconditions:

- A grade of real numbers needs a draft id, a username, and a board of at least 150 players.
- This repo has no fixture flag and no dry-run for `grade`. Running it fetches the draft, the league, and the pick list from Sleeper.

The full grade path is unverified. The unmet precondition is a draft payload that does not call Sleeper. `--help` only proves the command is wired. It does not prove `value`, `total_value`, or `holes`.

```bash
python3 draft_mode.py grade --help >/dev/null
```

Exit code 0 means the parser accepted the subcommand. Do not run `python3 draft_mode.py grade` with a draft id during verification.

## Gotchas

- The script never submits a pick. Grade is a read, and it still performs that read over the network.
- A thin on-disk board (under 150 players) is an error, not a grade.
- `qb_warning` is attached when the roster is superflex or two QB.
- Do not treat the help check as a passing grade proof.
