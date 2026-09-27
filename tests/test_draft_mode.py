"""Feed order, the MIN_BOARD floor, and the seed refresh. Network is mocked."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import draft_mode as dm


def named_rows(count, prefix="Player"):
    return [
        {
            "name": "{} {:03d}".format(prefix, i),
            "position": "WR",
            "team": "BUF",
            "adp": float(i + 1),
            "stdev": 1.2,
            "bye": 7,
        }
        for i in range(count)
    ]


def sleeper_players(rows, id_base=1):
    out = {}
    for i, row in enumerate(rows):
        pid = str(id_base + i)
        out[pid] = {
            "player_id": pid,
            "full_name": row["name"],
            "position": row["position"],
            "team": row["team"],
        }
    return out


def calc_payload(rows, id_base=1):
    items = []
    for i, row in enumerate(rows):
        items.append({
            "overallRank": i + 1,
            "player": {
                "name": row["name"],
                "position": row["position"],
                "maybeTeam": row["team"],
                "sleeperId": str(id_base + i),
            },
        })
    return items


class Router:
    def __init__(self, ffc_rows=None, calc_rows=None, ffc_error=None,
                 calc_error=None, players=None):
        self.ffc_rows = [] if ffc_rows is None else ffc_rows
        self.calc_rows = [] if calc_rows is None else calc_rows
        self.ffc_error = ffc_error
        self.calc_error = calc_error
        self.players = {} if players is None else players
        self.urls = []

    def fetch_json(self, url, timeout=30):
        self.urls.append(url)
        if "fantasyfootballcalculator.com" in url:
            if self.ffc_error:
                raise RuntimeError(self.ffc_error)
            return {"players": self.ffc_rows}
        if "api.fantasycalc.com" in url:
            if self.calc_error:
                raise RuntimeError(self.calc_error)
            return calc_payload(self.calc_rows)
        raise AssertionError(url)

    def fetch_players(self, cache_path=None, max_age_hours=12):
        return self.players


def _run_board(router, league, out, seed, minimum=dm.MIN_BOARD):
    def fetch_json(url, timeout=30):
        if "/league/" in url:
            router.urls.append(url)
            return league
        return router.fetch_json(url, timeout)

    with patch.object(dm, "fetch_json", fetch_json), \
            patch.object(dm, "fetch_players", router.fetch_players), \
            patch.object(dm, "calendar_year", lambda: 2026):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = dm.cmd_board("42", str(out), str(seed), minimum)
    return code, json.loads(buf.getvalue()), router.urls


class TestMinBoard(unittest.TestCase):
    def test_fragment_does_not_replace_a_full_board(self):
        full = [dm.Player(
            player_id=str(i), name="P {}".format(i), position="WR", team="BUF",
            adp=float(i + 1), stdev=1.0, bye=7, injury_status=None, tier=1,
        ) for i in range(dm.MIN_BOARD)]
        thin = full[:dm.MIN_BOARD - 1]
        board, status = dm.resolve_board(thin, full, dm.MIN_BOARD)
        self.assertEqual(status, "kept_previous")
        self.assertEqual(len(board), dm.MIN_BOARD)

    def test_fragment_with_no_previous_board_raises(self):
        thin = [dm.Player(
            player_id=str(i), name="P {}".format(i), position="WR", team="BUF",
            adp=float(i + 1), stdev=1.0, bye=7, injury_status=None, tier=1,
        ) for i in range(dm.MIN_BOARD - 1)]
        with self.assertRaises(dm.BoardTooThin):
            dm.resolve_board(thin, None, dm.MIN_BOARD)
        with self.assertRaises(dm.BoardTooThin):
            dm.resolve_board(None, None, dm.MIN_BOARD)


class TestFeedOrder(unittest.TestCase):
    def _league(self, season="2026", rec=1.0, teams=12, roster=None):
        return {
            "season": season,
            "total_rosters": teams,
            "scoring_settings": {"rec": rec},
            "roster_positions": roster or ["QB", "RB", "WR", "TE", "FLEX"],
        }

    def test_ffc_wins_and_fantasycalc_is_not_called(self):
        rows = named_rows(dm.MIN_BOARD)
        router = Router(ffc_rows=rows, calc_rows=named_rows(dm.MIN_BOARD, "Other"),
                        players=sleeper_players(rows))
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "board.json"
            seed = Path(tmp) / "seed.json"
            code, event, urls = _run_board(router, self._league(), out, seed)
        self.assertEqual(code, 0)
        self.assertEqual(event["status"], "refreshed")
        self.assertEqual(event["feed"], dm.FEED_FFC)
        self.assertEqual(event["matched"], event["kept"])
        self.assertEqual(event["kept"], dm.MIN_BOARD)
        self.assertIn("attribution", event)
        self.assertFalse(any("fantasycalc" in url for url in urls))
        self.assertTrue(any("fantasyfootballcalculator.com" in url for url in urls))

    def test_thin_ffc_falls_through_to_fantasycalc(self):
        calc_rows = named_rows(dm.MIN_BOARD, "Calc")
        router = Router(ffc_rows=named_rows(3), calc_rows=calc_rows,
                        players=sleeper_players(calc_rows))
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "board.json"
            seed = Path(tmp) / "seed.json"
            code, event, urls = _run_board(
                router, self._league(roster=["QB", "SUPER_FLEX"]), out, seed)
        self.assertEqual(code, 0)
        self.assertEqual(event["status"], "refreshed")
        self.assertEqual(event["feed"], dm.FEED_FANTASYCALC)
        self.assertEqual(event["matched"], dm.MIN_BOARD)
        ffc_at = next(i for i, url in enumerate(urls)
                      if "fantasyfootballcalculator.com" in url)
        calc_at = next(i for i, url in enumerate(urls) if "fantasycalc.com" in url)
        self.assertLess(ffc_at, calc_at)
        calc_url = urls[calc_at]
        self.assertIn("numQbs=2", calc_url)
        self.assertIn("numTeams=12", calc_url)
        self.assertIn("ppr=1", calc_url)
        self.assertEqual(event["source"], str(out))

    def test_both_fragments_keep_the_seed(self):
        seed_rows = named_rows(dm.MIN_BOARD, "Seed")
        players = [
            dm.Player(
                player_id=str(i + 1), name=row["name"], position="WR",
                team="BUF", adp=row["adp"], stdev=1.2, bye=7,
                injury_status=None, tier=1,
            )
            for i, row in enumerate(seed_rows)
        ]
        router = Router(ffc_rows=named_rows(4), calc_rows=named_rows(5, "Calc"),
                        ffc_error="ffc down")
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "board.json"
            seed = Path(tmp) / "seed.json"
            dm.save_board(players, seed)
            before = seed.read_bytes()
            code, event, urls = _run_board(router, self._league(), out, seed)
            self.assertEqual(seed.read_bytes(), before)
        self.assertEqual(code, 0)
        self.assertEqual(event["status"], "kept_previous")
        self.assertIsNone(event["feed"])
        self.assertEqual(event["kept"], dm.MIN_BOARD)
        self.assertEqual(event["feed_error"], "ffc down")
        self.assertIn("warning", event)
        feeds = [a["feed"] for a in event["attempts"]]
        # A full seed on disk beats FantasyCalc, so it is not called.
        self.assertEqual(feeds, [dm.FEED_FFC])

    def test_old_season_does_not_call_fantasycalc(self):
        router = Router(ffc_rows=named_rows(2), calc_rows=named_rows(dm.MIN_BOARD))
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "board.json"
            seed = Path(tmp) / "seed.json"
            code, event, urls = _run_board(
                router, self._league(season="2018"), out, seed)
        self.assertEqual(code, 1)
        self.assertFalse(any("fantasycalc.com" in url for url in urls))
        self.assertEqual(event["event"], "error")

    def test_half_ppr_mapping(self):
        rows = named_rows(dm.MIN_BOARD)
        router = Router(ffc_rows=[], calc_rows=rows, players=sleeper_players(rows))
        with TemporaryDirectory() as tmp:
            code, event, urls = _run_board(
                router, self._league(rec=0.5, teams=16),
                Path(tmp) / "board.json", Path(tmp) / "seed.json")
        self.assertEqual(code, 0)
        self.assertEqual(event["scoring"], "half-ppr")
        calc_url = next(url for url in urls if "fantasycalc.com" in url)
        self.assertIn("ppr=0.5", calc_url)
        self.assertIn("numTeams=14", calc_url)


class TestSeedRefresh(unittest.TestCase):
    def test_bad_feed_does_not_overwrite_a_good_seed(self):
        good = named_rows(dm.MIN_BOARD, "Kept")
        players = [
            dm.Player(
                player_id=str(i + 1), name=row["name"], position="WR",
                team="BUF", adp=row["adp"], stdev=1.2, bye=7,
                injury_status=None, tier=1,
            )
            for i, row in enumerate(good)
        ]
        router = Router(ffc_rows=named_rows(1), calc_rows=named_rows(2, "X"))
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "seed_board.json"
            dm.save_seed(players, path, {
                "generated_at": "2026-08-01T00:00:00Z",
                "source": dm.FEED_FFC,
                "source_url": "https://example.invalid/old",
                "season": 2026,
                "teams": 12,
                "scoring": "ppr",
                "num_qbs": 1,
            })
            before = path.read_bytes()
            with patch.object(dm, "fetch_json", router.fetch_json), \
                    patch.object(dm, "fetch_players", router.fetch_players), \
                    patch.object(dm, "calendar_year", lambda: 2026):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = dm.cmd_seed(str(path), year=2026)
            self.assertEqual(path.read_bytes(), before)
            loaded = dm.load_board(path)
        self.assertEqual(code, 1)
        event = json.loads(buf.getvalue())
        self.assertEqual(event["status"], "kept")
        self.assertIsNone(event["wrote"])
        self.assertEqual(len(loaded), dm.MIN_BOARD)
        self.assertEqual(loaded[0].name, "Kept 000")

    def test_full_feed_writes_provenance_without_breaking_loader(self):
        rows = named_rows(dm.MIN_BOARD, "New")
        router = Router(ffc_rows=rows, players=sleeper_players(rows))
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "seed_board.json"
            dm.save_board([
                dm.Player(
                    player_id="9", name="Old Name", position="RB", team="DET",
                    adp=1.0, stdev=1.0, bye=6, injury_status=None, tier=1,
                )
            ], path)
            with patch.object(dm, "fetch_json", router.fetch_json), \
                    patch.object(dm, "fetch_players", router.fetch_players), \
                    patch.object(dm, "calendar_year", lambda: 2026):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = dm.cmd_seed(str(path), year=2026, teams=12,
                                       scoring="ppr")
            raw = json.loads(path.read_text())
            loaded = dm.load_board(path)
            written = path.read_bytes()
            buf2 = io.StringIO()
            with patch.object(dm, "fetch_json", router.fetch_json), \
                    patch.object(dm, "fetch_players", router.fetch_players), \
                    patch.object(dm, "calendar_year", lambda: 2026), \
                    redirect_stdout(buf2):
                again = dm.cmd_seed(str(path), year=2026, teams=12, scoring="ppr")
            self.assertEqual(path.read_bytes(), written)
        self.assertEqual(code, 0)
        event = json.loads(buf.getvalue())
        self.assertEqual(event["status"], "refreshed")
        self.assertEqual(event["feed"], dm.FEED_FFC)
        self.assertGreaterEqual(len(loaded), dm.MIN_BOARD)
        self.assertEqual(raw[0]["source"], dm.FEED_FFC)
        self.assertIn("generated_at", raw[0])
        self.assertEqual(raw[0]["season"], 2026)
        self.assertEqual(raw[0]["teams"], 12)
        self.assertEqual(raw[0]["scoring"], "ppr")
        self.assertNotIn("source", raw[1])
        self.assertEqual(again, 0)
        self.assertEqual(json.loads(buf2.getvalue())["status"], "unchanged")

    def test_bad_feed_does_not_create_an_empty_file(self):
        router = Router(ffc_rows=[], calc_rows=[])
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "seed_board.json"
            with patch.object(dm, "fetch_json", router.fetch_json), \
                    patch.object(dm, "fetch_players", router.fetch_players), \
                    patch.object(dm, "calendar_year", lambda: 2026):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = dm.cmd_seed(str(path), year=2026)
            self.assertFalse(path.exists())
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(buf.getvalue())["status"], "kept")

    def test_provenance_keys_do_not_change_the_player(self):
        row = {
            "player_id": "7",
            "name": "Someone",
            "position": "QB",
            "team": "KC",
            "adp": 12.0,
            "stdev": 1.0,
            "bye": 8,
            "injury_status": None,
            "tier": 1,
            "generated_at": "2026-08-04T00:00:00Z",
            "source": dm.FEED_FANTASYCALC,
            "source_url": "https://api.fantasycalc.com/values/current",
        }
        player = dm.player_from_dict(row)
        self.assertEqual(player.player_id, "7")
        self.assertEqual(player.name, "Someone")
        self.assertFalse(hasattr(player, "source"))


if __name__ == "__main__":
    unittest.main()
