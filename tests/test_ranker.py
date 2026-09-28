"""Ranker and watcher behavior, pinned to the 2026-09-04 live draft.

During that draft five picks were overridden by hand. Each test here is one
of those corrections, or a rule that came out of them. No network.
"""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import draft_mode as dm

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DRAFT = json.loads((FIXTURES / "draft_2026.json").read_text())
BOARD = dm.load_board(FIXTURES / "board_2026-09-04.json")
BY_ID = dict((p.player_id, p) for p in BOARD)
MY_PICKS = dm.my_pick_numbers(DRAFT["slot"], DRAFT["teams"], DRAFT["rounds"],
                              DRAFT["reversal_round"], DRAFT["type"])


def on_clock(pick_no, top_n=8, board=BOARD):
    prior = [p for p in DRAFT["picks"] if p["pick_no"] < pick_no]
    _, _, recs, horizon = dm.advise(
        board, prior, DRAFT["slot"], DRAFT["roster_positions"], MY_PICKS,
        pick_no, DRAFT["teams"], top_n)
    return recs, horizon


def names(recs):
    return [r.player.name for r in recs]


def player(name):
    return next(p for p in BOARD if p.name == name)


class TestLiveOverrides(unittest.TestCase):
    def test_pick_25_bye_does_not_bury_an_11_pick_steal(self):
        self.assertEqual(names(on_clock(25)[0])[0], "Drake London")

    def test_pick_36_empty_rb_slot_is_not_drowned_by_wr_scarcity(self):
        # Old engine: eight WRs at 2x need, Breece Hall ranked 14th.
        self.assertIn("Breece Hall", names(on_clock(36)[0])[:3])

    def test_pick_76_takes_value_not_an_early_te(self):
        self.assertEqual(names(on_clock(76)[0])[0], "Rome Odunze")

    def test_pick_96_one_streamable_te_hole_is_not_a_bye_disaster(self):
        self.assertEqual(names(on_clock(96)[0])[0], "Harold Fannin Jr.")

    def test_no_top3_reach_that_would_survive_to_the_next_pick(self):
        # Pick 56 (Purdy, Kittle) and pick 85 (Goedert) were 30-pick reaches
        # that were 80-96% likely to still be there.
        for pick_no in MY_PICKS:
            recs, horizon = on_clock(pick_no)
            if horizon is None:
                continue
            for r in recs[:3]:
                reach = r.player.adp - pick_no
                survive = dm.survival_prob(r.player, horizon)
                self.assertFalse(
                    reach > 15 and survive >= 0.7,
                    "pick {}: {} reach {:.0f}, {:.0%} to survive".format(
                        pick_no, r.player.name, reach, survive))


class TestRosterRules(unittest.TestCase):
    def test_last_pick_fills_the_empty_kicker_slot(self):
        recs, _ = on_clock(MY_PICKS[-1])
        self.assertEqual({r.player.position for r in recs}, {"K"})

    def test_kicker_and_defense_wait_for_the_last_rounds(self):
        for pick_no in MY_PICKS[:-dm.LATE_ONLY_PICKS]:
            recs, _ = on_clock(pick_no)
            self.assertFalse(
                [r.player.name for r in recs if r.player.position in dm.LATE_ONLY],
                "pick {}".format(pick_no))

    def test_no_third_quarterback_in_a_one_qb_league(self):
        roster = [player("Dak Prescott"), player("Jared Goff")]
        recs = dm.recommend(BOARD, [p.player_id for p in roster], roster,
                            DRAFT["roster_positions"], 145, 156, DRAFT["teams"],
                            top_n=50)
        self.assertNotIn("QB", {r.player.position for r in recs})

    def test_hard_injury_status_is_never_recommended(self):
        board = dm.refresh_injuries(BOARD, {"5850": {"injury_status": "NA"}})
        recs, _ = on_clock(76, top_n=300, board=board)
        self.assertNotIn("Josh Jacobs", names(recs))

    def test_questionable_is_shown_not_filtered(self):
        board = dm.refresh_injuries(
            BOARD, {player("Puka Nacua").player_id: {"injury_status": "Questionable"}})
        self.assertEqual(names(on_clock(5, board=board)[0])[0], "Puka Nacua")

    def test_refresh_replaces_stale_flags(self):
        pid = player("Puka Nacua").player_id
        board = dm.refresh_injuries(BOARD, {pid: {"injury_status": None}})
        self.assertIsNone(next(p for p in board if p.player_id == pid).injury_status)


class TestWatcher(unittest.TestCase):
    def test_one_pick_out_still_gets_the_heads_up(self):
        self.assertEqual(dm.phase_at(24, MY_PICKS), "two_out")
        self.assertEqual(dm.phase_at(23, MY_PICKS), "two_out")
        self.assertEqual(dm.phase_at(25, MY_PICKS), "on_clock")
        self.assertIsNone(dm.phase_at(22, MY_PICKS))

    def test_each_pick_is_announced_once_per_phase(self):
        n = dm.Narrator(BOARD, DRAFT["slot"], DRAFT["roster_positions"],
                        MY_PICKS, DRAFT["teams"], 8)
        events = [n.step(DRAFT["picks"], c) for c in (23, 24, 25, 25)]
        phases = [e and e["phase"] for e in events]
        self.assertEqual(phases, ["two_out", None, "on_clock", None])

    def test_event_carries_survival_and_value_fields(self):
        n = dm.Narrator(BOARD, DRAFT["slot"], DRAFT["roster_positions"],
                        MY_PICKS, DRAFT["teams"], 8)
        event = n.step(DRAFT["picks"], 25)
        self.assertEqual(len(event["top"]), 8)
        top = event["top"][0]
        for key in ("survival", "value_vs_pick", "falling"):
            self.assertIn(key, top)
        self.assertAlmostEqual(top["value_vs_pick"], 25 - top["adp"], places=1)

    def test_plan_change_is_narrated(self):
        n = dm.Narrator(BOARD, DRAFT["slot"], DRAFT["roster_positions"],
                        MY_PICKS, DRAFT["teams"], 8)
        n.step(DRAFT["picks"], 23)
        plan = n.plan[25]
        # Pretend the two-out pick went to someone else before our turn.
        stolen = [dict(p) for p in DRAFT["picks"] if p["pick_no"] < 24]
        stolen.append({"pick_no": 24, "draft_slot": 4, "player_id": plan[0]})
        event = n.step(stolen, 25)
        self.assertEqual(event["changed_from"]["player_id"], plan[0])
        self.assertEqual(event["changed_from"]["why"], "taken")

    def test_falling_player_is_flagged(self):
        recs, _ = on_clock(116)
        stafford = next(r for r in recs if r.player.name == "Matthew Stafford") \
            if "Matthew Stafford" in names(recs) else None
        reason = dm._reason(player("Matthew Stafford"), 116, 125, 0, None, 0)
        self.assertIn("check news", reason)
        if stafford:
            self.assertIn("check news", stafford.reason)

    def test_waits_through_pre_draft_instead_of_failing(self):
        base = {"league_id": "1", "status": "pre_draft", "type": "snake",
                "settings": {"teams": DRAFT["teams"], "rounds": DRAFT["rounds"]},
                "draft_order": None}
        ready = dict(base, draft_order={"u1": 5}, status="complete")
        drafts = iter([base, base, ready])

        def fetch_json(url, timeout=30):
            if url.endswith("/picks"):
                return DRAFT["picks"]
            if "/draft/" in url:
                return next(drafts)
            if "/league/" in url:
                return {"roster_positions": DRAFT["roster_positions"]}
            raise AssertionError(url)

        with patch.object(dm, "fetch_json", fetch_json), \
                patch.object(dm, "fetch_user", lambda u: {"user_id": "u1", "username": u}), \
                patch.object(dm, "fetch_players", lambda **k: {}), \
                patch.object(dm, "load_effective_board", lambda p: (BOARD, "fixture")), \
                patch.object(dm.time, "sleep", lambda s: None):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = dm.cmd_watch("d", "someone", 1.0)
        events = [json.loads(line)["event"] for line in buf.getvalue().splitlines()]
        self.assertEqual(code, 0)
        self.assertEqual(events[0], "waiting")
        self.assertIn("status", events)
        self.assertEqual(events[-1], "complete")


class TestNow(unittest.TestCase):
    def test_matches_the_watcher_on_the_clock(self):
        prior = [p for p in DRAFT["picks"] if p["pick_no"] < 36]
        event = dm.now_event(BOARD, prior, DRAFT["slot"],
                             DRAFT["roster_positions"], MY_PICKS, DRAFT["teams"])
        self.assertEqual(event["phase"], "on_clock")
        self.assertEqual(event["your_pick"], 36)
        self.assertEqual(event["top"][0]["name"], names(on_clock(36)[0])[0])
        self.assertEqual(len(event["roster"]), 3)

    def test_between_turns_measures_survival_to_his_pick(self):
        prior = [p for p in DRAFT["picks"] if p["pick_no"] < 30]
        event = dm.now_event(BOARD, prior, DRAFT["slot"],
                             DRAFT["roster_positions"], MY_PICKS, DRAFT["teams"])
        self.assertEqual(event["phase"], "upcoming")
        self.assertEqual(event["horizon"], 36)

    def test_no_picks_left(self):
        event = dm.now_event(BOARD, DRAFT["picks"][:158], DRAFT["slot"],
                             DRAFT["roster_positions"], MY_PICKS, DRAFT["teams"])
        self.assertEqual(event["event"], "done")


class TestSeedFeed(unittest.TestCase):
    def test_seed_never_comes_from_fantasycalc(self):
        from tests.test_draft_mode import Router, named_rows, sleeper_players
        rows = named_rows(dm.MIN_BOARD, "Calc")
        router = Router(ffc_rows=named_rows(3), calc_rows=rows,
                        players=sleeper_players(rows))
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp, \
                patch.object(dm, "fetch_json", router.fetch_json), \
                patch.object(dm, "fetch_players", router.fetch_players), \
                patch.object(dm, "calendar_year", lambda: 2026):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = dm.cmd_seed(str(Path(tmp) / "seed.json"), year=2026)
        self.assertEqual(code, 1)
        self.assertFalse(any("fantasycalc.com" in u for u in router.urls))


if __name__ == "__main__":
    unittest.main()
