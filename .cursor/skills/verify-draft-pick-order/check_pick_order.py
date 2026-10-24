#!/usr/bin/env python3
"""Exhaustive check of draft pick order in draft_mode.py.

The property, bounds, and coverage record live in SKILL.md beside this file.
Run from anywhere:

    python3 .cursor/skills/verify-draft-pick-order/check_pick_order.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import draft_mode as dm  # noqa: E402

# Commit of draft_mode.py this checker was last confirmed to describe.
# Move this forward only after re-reading the covered functions and rerunning.
SYNC_COMMIT = "19f7923e4128322fd8c852284cd7e8bfa290494d"

# Sleeper redrafts are usually 8 to 14 teams and about 15 rounds. The check
# covers every seat in leagues of 2 to 16 teams and drafts of 1 to 20 rounds.
MIN_TEAMS = 2
MAX_TEAMS = 16
MAX_ROUNDS = 20

# Narrator.step calls recommend, so this grid is smaller. It still includes
# the seats where a snake turn or a reversal round makes two of one manager's
# picks consecutive.
NARRATOR_TEAMS = (8, 10, 12, 14)
NARRATOR_ROUNDS = (3, 15)
NARRATOR_REVERSALS = (0, 3)

ROSTER = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF", "BN", "BN"]
TINY_BOARD = [
    dm.Player("1", "Alpha Runner", "RB", "AAA", 1.0, 1.0, 8, None, 1),
    dm.Player("2", "Bravo Catch", "WR", "BBB", 12.0, 1.0, 9, None, 1),
]


class CheckFailed(Exception):
    """One counterexample. The message is the plain-words report."""


def overall(round_no, teams, position):
    return (round_no - 1) * teams + position


def positions_of(picks, teams):
    """Position inside each round. Round r occupies picks ((r-1)*teams, r*teams]."""
    found = []
    for round_no, pick in enumerate(picks, start=1):
        lo = (round_no - 1) * teams + 1
        hi = round_no * teams
        if not (lo <= pick <= hi):
            raise CheckFailed(
                "Round {round} returned overall pick {pick}, outside {lo}..{hi}."
                .format(round=round_no, pick=pick, lo=lo, hi=hi)
            )
        found.append(pick - (round_no - 1) * teams)
    return found


def check_sorted_rounds(picks, rounds):
    if len(picks) != rounds:
        raise CheckFailed(
            "Expected {rounds} picks, one per round. my_pick_numbers returned {got}: {picks}."
            .format(rounds=rounds, got=len(picks), picks=picks)
        )
    if picks != sorted(picks) or len(set(picks)) != len(picks):
        raise CheckFailed(
            "Pick numbers must increase by one round at a time, with no duplicates. "
            "Got {picks}.".format(picks=picks)
        )


def check_snake_direction(slot, teams, rounds, reversal, picks):
    """Relational reading of my_pick_numbers, not a second copy of its loop.

    Odd rounds face the same way as round 1, even rounds the other way, until
    reversal_round. That round keeps the previous round's direction, and the
    rounds after it alternate from there. reversal_round 0 leaves the snake
    alone. reversal_round 1 flips round 1 itself, so round 1 is the reverse
    seat (teams - slot + 1) and there is no previous round to copy.
    """
    pos = positions_of(picks, teams)
    if reversal == 0 or reversal > 1:
        if pos[0] != slot:
            raise CheckFailed(
                "Round 1 should be the forward seat {slot} (overall pick {pick}). "
                "my_pick_numbers returned position {got}, overall pick {got_pick}."
                .format(slot=slot, pick=slot, got=pos[0], got_pick=picks[0])
            )
    else:
        backward = teams - slot + 1
        if pos[0] != backward:
            raise CheckFailed(
                "reversal_round 1 flips round 1, so this seat should be at "
                "position {want} (overall pick {pick}). my_pick_numbers returned "
                "position {got}, overall pick {got_pick}."
                .format(want=backward, pick=overall(1, teams, backward),
                        got=pos[0], got_pick=picks[0])
            )
    for later in range(2, rounds + 1):
        prev = pos[later - 2]
        cur = pos[later - 1]
        # The reversal round is the one round that does not flip.
        same_direction = reversal >= 2 and later == reversal
        if same_direction:
            if cur != prev:
                raise CheckFailed(
                    "Round {later} is the reversal round, so it must keep round "
                    "{prev_round}'s direction. Round {prev_round} put this seat at "
                    "position {prev} (overall pick {prev_pick}). my_pick_numbers "
                    "returned overall pick {cur_pick}, position {cur}. watch and now "
                    "would call that pick this manager's turn. It is the other end "
                    "of the round."
                    .format(
                        later=later,
                        prev_round=later - 1,
                        prev=prev,
                        prev_pick=picks[later - 2],
                        cur_pick=picks[later - 1],
                        cur=cur,
                    )
                )
        elif cur + prev != teams + 1:
            raise CheckFailed(
                "Round {later} should flip direction from round {prev_round}. "
                "Positions {prev} and {cur} do not add up to {mirror} "
                "(overall picks {prev_pick} and {cur_pick})."
                .format(
                    later=later,
                    prev_round=later - 1,
                    prev=prev,
                    cur=cur,
                    mirror=teams + 1,
                    prev_pick=picks[later - 2],
                    cur_pick=picks[later - 1],
                )
            )


def check_linear(slot, teams, rounds, reversal, picks):
    """Linear keeps the same seat every round. reversal_round does not apply."""
    expected = [(round_no - 1) * teams + slot for round_no in range(1, rounds + 1)]
    if picks != expected:
        raise CheckFailed(
            "A linear draft keeps seat {slot} every round, including when "
            "reversal_round is {reversal}. Expected {expected}. my_pick_numbers "
            "returned {picks}."
            .format(slot=slot, reversal=reversal, expected=expected, picks=picks)
        )


def check_phase(picks, teams, rounds):
    """phase_at is on_clock only on this manager's picks, and two_out only
    when the next of those picks is one or two selections away."""
    total = teams * rounds
    mine = set(picks)
    for current in range(1, total + 1):
        phase = dm.phase_at(current, picks)
        upcoming = [pick for pick in picks if pick >= current]
        if not upcoming:
            if phase is not None:
                raise CheckFailed(
                    "No pick of this manager is left at overall pick {current}, "
                    "and phase_at returned {phase}.".format(current=current, phase=phase)
                )
            continue
        gap = upcoming[0] - current
        if gap == 0:
            want = "on_clock"
        elif gap in (1, 2):
            want = "two_out"
        else:
            want = None
        if phase != want:
            raise CheckFailed(
                "At overall pick {current}, the next pick of this manager is "
                "{nxt} ({gap} away). phase_at should return {want}. It returned "
                "{phase}. my_picks={picks}."
                .format(current=current, nxt=upcoming[0], gap=gap, want=want,
                        phase=phase, picks=picks)
            )
        if phase == "on_clock" and current not in mine:
            raise CheckFailed(
                "phase_at said on_clock at overall pick {current}, which is not "
                "one of this manager's picks {picks}.".format(current=current, picks=picks)
            )
    after = dm.phase_at(total + 1, picks)
    if after is not None:
        raise CheckFailed(
            "The draft is over at overall pick {done}, and phase_at returned {phase}."
            .format(done=total + 1, phase=after)
        )


def check_shape(draft_type, slot, teams, rounds, reversal):
    try:
        picks = dm.my_pick_numbers(slot, teams, rounds, reversal, draft_type)
    except dm.DraftTypeError as exc:
        raise CheckFailed(
            "my_pick_numbers raised DraftTypeError for a {kind} draft: {exc}"
            .format(kind=draft_type, exc=exc)
        )
    check_sorted_rounds(picks, rounds)
    if draft_type == "snake":
        check_snake_direction(slot, teams, rounds, reversal, picks)
    elif draft_type == "linear":
        check_linear(slot, teams, rounds, reversal, picks)
    else:
        raise CheckFailed("checker asked for an unknown type {kind}".format(kind=draft_type))
    check_phase(picks, teams, rounds)
    return picks


def check_partition(draft_type, teams, rounds, reversal, by_slot):
    owned = []
    for picks in by_slot:
        owned.extend(picks)
    want = list(range(1, teams * rounds + 1))
    if sorted(owned) != want:
        raise CheckFailed(
            "Seats 1..{teams} in a {kind} draft must cover every overall pick "
            "exactly once. reversal_round={reversal}, rounds={rounds}. "
            "Missing or repeated picks start at {sample}."
            .format(
                teams=teams,
                kind=draft_type,
                reversal=reversal,
                rounds=rounds,
                sample=_first_difference(sorted(owned), want),
            )
        )


def _first_difference(got, want):
    extra = [pick for pick in got if got.count(pick) > 1]
    missing = [pick for pick in want if pick not in got]
    return "extra {extra}, missing {missing}".format(
        extra=extra[:5], missing=missing[:5]
    )


def check_refused_types():
    """Auction, and any other type, must not produce a pick list."""
    for kind in ("auction", "dynasty", ""):
        try:
            picks = dm.my_pick_numbers(1, 10, 15, 3, kind)
        except dm.DraftTypeError as exc:
            if exc.draft_type != kind:
                raise CheckFailed(
                    "DraftTypeError for {kind!r} carried draft_type {got!r}."
                    .format(kind=kind, got=exc.draft_type)
                )
        else:
            raise CheckFailed(
                "my_pick_numbers returned {picks} for draft type {kind!r}. "
                "Only snake and linear have a pick order. Auction is refused."
                .format(picks=picks, kind=kind)
            )
    if dm.unsupported_reason("snake") is not None or dm.unsupported_reason("linear") is not None:
        raise CheckFailed("unsupported_reason refused snake or linear.")
    if dm.unsupported_reason("auction") is None:
        raise CheckFailed("unsupported_reason allowed an auction draft.")


def _event_keys(events):
    keys = []
    for event in events:
        keys.append((event["your_pick"], event["phase"], event["current_pick"]))
    return keys


def check_narrator(draft_type, slot, teams, rounds, reversal):
    """Walking every overall pick speaks on_clock once per pick of this
    manager, and two_out once when the pick before it is not also theirs.
    A poll that jumps from pick 1 to the end speaks for the same picks.
    """
    picks = dm.my_pick_numbers(slot, teams, rounds, reversal, draft_type)
    mine = set(picks)
    total = teams * rounds

    def walk(jumps):
        narrator = dm.Narrator(TINY_BOARD, slot, ROSTER, picks, teams, 8)
        last = None
        events = []
        for current in jumps:
            events.extend(dm.live_steps(narrator, [], last, current))
            last = current
        return events

    sequential = walk(range(1, total + 1))
    keys = _event_keys(sequential)
    if len(keys) != len(set(keys)):
        raise CheckFailed(
            "live_steps announced the same turn twice: {keys}."
            .format(keys=keys)
        )
    on_clock = [key for key in keys if key[1] == "on_clock"]
    if [key[0] for key in on_clock] != list(picks):
        raise CheckFailed(
            "on_clock should fire once for each pick {picks}, in order. "
            "The walk produced {got}."
            .format(picks=picks, got=on_clock)
        )
    two_out = {}
    for your_pick, phase, current in keys:
        if phase == "on_clock":
            if your_pick != current or your_pick not in mine:
                raise CheckFailed(
                    "on_clock event says your_pick {your} at current pick {current}."
                    .format(your=your_pick, current=current)
                )
        elif phase == "two_out":
            if your_pick - current not in (1, 2):
                raise CheckFailed(
                    "two_out event at current pick {current} points at your_pick "
                    "{your}, which is not one or two picks away."
                    .format(current=current, your=your_pick)
                )
            two_out[your_pick] = two_out.get(your_pick, 0) + 1
        else:
            raise CheckFailed(
                "live_steps emitted phase {phase!r}. The only recommendation "
                "phases are two_out and on_clock.".format(phase=phase)
            )
    for pick in picks:
        expect = pick > 1 and (pick - 1) not in mine
        got = two_out.get(pick, 0)
        if expect and got != 1:
            raise CheckFailed(
                "Pick {pick} is not back to back with another pick of this "
                "manager, so two_out should be announced once. It was announced "
                "{got} time(s). picks={picks}."
                .format(pick=pick, got=got, picks=picks)
            )
        if not expect and got != 0:
            raise CheckFailed(
                "Pick {pick} has no earlier moment to warn (it is pick 1, or the "
                "previous overall pick is also this manager's). two_out was "
                "announced {got} time(s). picks={picks}."
                .format(pick=pick, got=got, picks=picks)
            )
    jumped = walk([1, total])
    if _event_keys(jumped) != keys:
        raise CheckFailed(
            "A poll that jumps from overall pick 1 to {total} should announce "
            "the same turns as walking every pick. Walk={walk}. Jump={jump}."
            .format(total=total, walk=keys, jump=_event_keys(jumped))
        )


def settings_line(draft_type, slot, teams, rounds, reversal):
    return (
        "{kind} draft, {teams} teams, {rounds} rounds, reversal_round {reversal}, seat {slot}."
        .format(kind=draft_type, teams=teams, rounds=rounds, reversal=reversal, slot=slot)
    )


def run():
    shapes = 0
    partitions = 0
    for draft_type in ("snake", "linear"):
        for teams in range(MIN_TEAMS, MAX_TEAMS + 1):
            for rounds in range(1, MAX_ROUNDS + 1):
                for reversal in range(0, rounds + 1):
                    by_slot = []
                    for slot in range(1, teams + 1):
                        try:
                            by_slot.append(check_shape(
                                draft_type, slot, teams, rounds, reversal))
                        except CheckFailed as exc:
                            raise CheckFailed(
                                "{where}\n{body}\nReplay: python3 -c \"import draft_mode; "
                                "print(draft_mode.my_pick_numbers({slot}, {teams}, {rounds}, "
                                "{reversal}, {kind!r}))\""
                                .format(
                                    where=settings_line(
                                        draft_type, slot, teams, rounds, reversal),
                                    body=exc,
                                    slot=slot,
                                    teams=teams,
                                    rounds=rounds,
                                    reversal=reversal,
                                    kind=draft_type,
                                )
                            )
                        shapes += 1
                    try:
                        check_partition(draft_type, teams, rounds, reversal, by_slot)
                    except CheckFailed as exc:
                        raise CheckFailed(
                            "{kind} draft, {teams} teams, {rounds} rounds, "
                            "reversal_round {reversal}.\n{body}"
                            .format(kind=draft_type, teams=teams, rounds=rounds,
                                    reversal=reversal, body=exc)
                        )
                    partitions += 1
    check_refused_types()
    narrator = 0
    for draft_type in ("snake", "linear"):
        for teams in NARRATOR_TEAMS:
            for rounds in NARRATOR_ROUNDS:
                for reversal in NARRATOR_REVERSALS:
                    for slot in range(1, teams + 1):
                        try:
                            check_narrator(draft_type, slot, teams, rounds, reversal)
                        except CheckFailed as exc:
                            raise CheckFailed(
                                "{where}\n{body}"
                                .format(
                                    where=settings_line(
                                        draft_type, slot, teams, rounds, reversal),
                                    body=exc,
                                )
                            )
                        narrator += 1
    print("ok")
    print("sync_commit {sha}".format(sha=SYNC_COMMIT))
    print("shape_checks {n}".format(n=shapes))
    print("partition_checks {n}".format(n=partitions))
    print("narrator_walks {n}".format(n=narrator))
    print(
        "bounds teams {lo}..{hi}, rounds 1..{rounds}, reversal_round 0..rounds, "
        "snake and linear, every seat"
        .format(lo=MIN_TEAMS, hi=MAX_TEAMS, rounds=MAX_ROUNDS)
    )
    return 0


def main():
    try:
        return run()
    except CheckFailed as exc:
        print("FAIL")
        print(exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
