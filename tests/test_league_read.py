"""ESPN and Yahoo league reads. HTTP is mocked. Yahoo is not called for real."""

from __future__ import annotations

import ast
import io
import json
import os
import stat
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import league_read as lr


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"

BLANK_SECRETS = {
    "ESPN_S2": "",
    "ESPN_SWID": "",
    "YAHOO_CLIENT_ID": "",
    "YAHOO_CLIENT_SECRET": "",
    "YAHOO_AUTH_CODE": "",
    "YAHOO_REDIRECT_URI": "",
}


def load_json(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeHTTP:
    def __init__(self):
        self.calls = []
        self.routes = []

    def add(self, needle, result):
        self.routes.append((needle, result))
        return self

    def __call__(self, url, headers=None, method="GET", form=None, timeout=30):
        self.calls.append({
            "url": url,
            "headers": dict(headers or {}),
            "method": method,
            "form": form,
        })
        for needle, result in self.routes:
            if needle in url:
                if isinstance(result, Exception):
                    raise result
                return result
        raise AssertionError("unexpected URL {}".format(url))


def run_main(argv, env=None, http=None):
    merged = dict(BLANK_SECRETS)
    if env:
        merged.update(env)
    buf = io.StringIO()
    with patch.dict(os.environ, merged, clear=False), \
            patch.object(lr, "http_json", http or FakeHTTP()), \
            redirect_stdout(buf):
        code = lr.main(argv)
    raw = buf.getvalue()
    payload = json.loads(raw) if raw.strip().startswith("{") else None
    return code, payload, raw


def espn_http(league=None, byes=None, agents=None, scoreboard=None):
    http = FakeHTTP()
    http.add("kona_player_info", {} if agents is None else agents)
    http.add("proTeamSchedules", {} if byes is None else byes)
    http.add("mMatchupScore", {} if scoreboard is None else scoreboard)
    http.add("state/nfl", {"season": "2026", "week": 3})
    http.add("/leagues/", {} if league is None else league)
    return http


class TestEspnShapes(unittest.TestCase):
    def test_scoring_reads_reception_points(self):
        self.assertEqual(lr.scoring_from_items([]), ("standard", 0))
        self.assertEqual(
            lr.scoring_from_items([{"statId": 53, "points": 0}]),
            ("standard", 0),
        )
        self.assertEqual(
            lr.scoring_from_items([{"statId": 53, "points": 0.5}]),
            ("half-ppr", 0.5),
        )
        self.assertEqual(
            lr.scoring_from_items([{"statId": 4, "points": 6}, {"statId": 53, "points": 1}]),
            ("ppr", 1),
        )
        self.assertEqual(
            lr.scoring_from_items([{"statId": 53, "points": 1.5}]),
            ("custom", 1.5),
        )

    def test_projection_is_this_week_not_the_season_total(self):
        stats = [
            {"scoringPeriodId": 3, "statSourceId": 0, "statSplitTypeId": 1, "appliedTotal": 34.4},
            {"scoringPeriodId": 3, "statSourceId": 1, "statSplitTypeId": 2, "appliedTotal": 99},
            {"scoringPeriodId": 3, "statSourceId": 1, "statSplitTypeId": 1, "appliedTotal": 21.45095599},
            {"scoringPeriodId": 0, "statSourceId": 1, "statSplitTypeId": 0, "appliedTotal": 256.93},
        ]
        self.assertEqual(lr.projected_points(stats, 3), 21.45)

    def test_final_matchup_uses_total_points(self):
        row = {
            "matchupPeriodId": 2,
            "winner": "HOME",
            "home": {"teamId": 1, "totalPoints": 30, "totalPointsLive": 1},
            "away": {"teamId": 3, "totalPoints": 10, "totalPointsLive": 2},
        }
        found = lr.matchup_for({"schedule": [row]}, 1, 2)
        self.assertTrue(found["final"])
        self.assertEqual(found["points"], 30)
        self.assertEqual(found["opponent_points"], 10)

    def test_free_agent_filter_matches_the_espn_header(self):
        raw = lr.free_agent_filter([0, 2, 4, 6, 23, 16, 17], 25)
        self.assertNotIn(" ", raw)
        parsed = json.loads(raw)
        players = parsed["players"]
        self.assertEqual(players["filterStatus"]["value"], ["FREEAGENT", "WAIVERS"])
        self.assertEqual(players["filterSlotIds"]["value"], [0, 2, 4, 6, 23, 16, 17])
        self.assertEqual(players["limit"], 25)
        self.assertEqual(players["sortPercOwned"]["sortAsc"], False)
        self.assertEqual(players["sortPercOwned"]["sortPriority"], 1)

    def test_position_names(self):
        slots, bad = lr.parse_positions(["wr, rb"])
        self.assertIsNone(bad)
        self.assertEqual(slots, [4, 2])
        slots, bad = lr.parse_positions(["D/ST", "def"])
        self.assertEqual(slots, [16])
        slots, bad = lr.parse_positions(None)
        self.assertEqual(slots, lr.DEFAULT_SLOTS)
        _slots, bad = lr.parse_positions(["QB1"])
        self.assertEqual(bad, "QB1")

    def test_swid_match_ignores_braces_and_case(self):
        teams = [{
            "id": 7,
            "owners": ["{00000000-0000-0000-0000-00000000000A}"],
        }]
        cookies = ("s2", "%7B00000000-0000-0000-0000-00000000000A%7D")
        self.assertEqual(lr.my_team_id(teams, cookies), 7)
        self.assertIsNone(lr.my_team_id(teams, ("s2", "{00000000-0000-0000-0000-00000000000B}")))


class TestEspnCommands(unittest.TestCase):
    def setUp(self):
        self.league = load_json("espn_league.json")
        self.byes = load_json("espn_byes.json")
        self.agents = load_json("espn_free_agents.json")
        self.board = load_json("espn_scoreboard.json")

    def test_settings_roster_slots_scoring_and_waivers(self):
        http = espn_http(self.league)
        code, payload, raw = run_main(
            ["espn", "settings", "--league", "100001", "--season", "2026"],
            http=http,
        )
        self.assertEqual(code, 0, raw)
        self.assertEqual(payload["name"], "Example League")
        self.assertEqual(payload["size"], 10)
        self.assertEqual(payload["current_week"], 3)
        self.assertEqual(payload["scoring"], "standard")
        self.assertEqual(payload["reception_points"], 0)
        self.assertEqual(
            [row["name"] for row in payload["roster_slots"]],
            ["QB", "RB", "WR", "TE", "FLEX", "D/ST", "K", "Bench", "IR"],
        )
        self.assertEqual(payload["roster_slots"][1]["count"], 2)
        self.assertEqual(payload["waivers"]["type"], "WAIVERS_TRADITIONAL")
        self.assertFalse(payload["waivers"]["uses_faab"])
        self.assertEqual(payload["waivers"]["budget"], 100)
        self.assertEqual(payload["draft"]["status"], "complete")
        self.assertEqual(payload["draft"]["type"], "SNAKE")
        self.assertNotIn("my_team_id", payload)
        self.assertFalse(any("state/nfl" in call["url"] for call in http.calls))
        self.assertNotIn("Cookie", http.calls[0]["headers"])

    def test_season_defaults_to_sleeper(self):
        http = espn_http(self.league)
        code, payload, raw = run_main(
            ["espn", "settings", "--league", "100001"],
            http=http,
        )
        self.assertEqual(code, 0, raw)
        self.assertEqual(payload["season"], 2026)
        self.assertTrue(any("api.sleeper.app/v1/state/nfl" in call["url"]
                            for call in http.calls))

    def test_teams_and_private_league_errors(self):
        swid = "{00000000-0000-0000-0000-000000000001}"
        secret = "s2-secret-value-zzzz"
        http = espn_http(self.league)
        code, payload, raw = run_main(
            ["espn", "teams", "--league", "100001", "--season", "2026"],
            env={"ESPN_S2": secret, "ESPN_SWID": swid},
            http=http,
        )
        self.assertEqual(code, 0, raw)
        self.assertEqual([row["id"] for row in payload["teams"]], [1, 3])
        self.assertEqual(payload["teams"][0]["name"], "Team 1")
        self.assertEqual(payload["teams"][0]["owners"], [swid])
        self.assertEqual(payload["my_team_id"], 1)
        self.assertNotIn(secret, raw)
        cookie = http.calls[0]["headers"]["Cookie"]
        self.assertIn("espn_s2=" + secret, cookie)
        self.assertIn("SWID=" + swid, cookie)

        missing = FakeHTTP().add(
            "/leagues/",
            lr.HttpStatus(401, {"details": [{"type": "AUTH_LEAGUE_NOT_VISIBLE"}]}),
        )
        code, payload, raw = run_main(
            ["espn", "teams", "--league", "100001", "--season", "2026"],
            http=missing,
        )
        self.assertEqual(code, 1)
        self.assertEqual(payload["error"], "private_league")
        self.assertIn("secret request", payload["message"])
        self.assertNotIn("Cookie", missing.calls[0]["headers"])

        rejected = FakeHTTP().add(
            "/leagues/",
            lr.HttpStatus(401, {"details": [{"type": "AUTH_LEAGUE_NOT_VISIBLE"}]}),
        )
        code, payload, raw = run_main(
            ["espn", "settings", "--league", "100001", "--season", "2026"],
            env={"ESPN_S2": secret, "ESPN_SWID": swid},
            http=rejected,
        )
        self.assertEqual(payload["error"], "private_league")
        self.assertIn("did not accept", payload["message"])
        self.assertNotIn(secret, raw)

        gone = FakeHTTP().add(
            "/leagues/",
            lr.HttpStatus(404, {"details": [{"type": "GENERAL_NOT_FOUND"}]}),
        )
        code, payload, raw = run_main(
            ["espn", "settings", "--league", "100001", "--season", "2026"],
            http=gone,
        )
        self.assertEqual(payload["error"], "league_not_found")
        self.assertIn("100001", payload["message"])

    def test_half_set_cookie_does_not_call_espn(self):
        http = FakeHTTP()
        code, payload, raw = run_main(
            ["espn", "teams", "--league", "100001", "--season", "2026"],
            env={"ESPN_S2": "s2-secret-value-zzzz"},
            http=http,
        )
        self.assertEqual(code, 1)
        self.assertEqual(payload["error"], "missing_cookie")
        self.assertEqual(http.calls, [])
        self.assertNotIn("s2-secret-value-zzzz", raw)

    def test_roster_projection_slot_and_bye(self):
        http = espn_http(self.league, self.byes)
        code, payload, raw = run_main(
            ["espn", "roster", "--league", "100001", "--team", "1",
             "--season", "2026"],
            http=http,
        )
        self.assertEqual(code, 0, raw)
        self.assertEqual(payload["week"], 3)
        names = [row["name"] for row in payload["players"]]
        self.assertEqual(names, ["Jahmyr Gibbs", "Bench Player", "Reserve Player"])
        gibbs = payload["players"][0]
        self.assertEqual(gibbs["slot"], "RB")
        self.assertEqual(gibbs["position"], "RB")
        self.assertEqual(gibbs["nfl_team"], "DET")
        self.assertEqual(gibbs["injury_status"], "ACTIVE")
        self.assertEqual(gibbs["projected_points"], 21.45)
        self.assertEqual(gibbs["bye_week"], 8)
        self.assertEqual(payload["players"][1]["slot"], "Bench")
        self.assertEqual(payload["players"][2]["slot"], "IR")
        self.assertEqual(payload["players"][2]["nfl_team"], "FA")
        self.assertIsNone(payload["players"][2]["bye_week"])
        self.assertIsNone(payload["players"][2]["projected_points"])
        roster_call = next(call for call in http.calls if "mRoster" in call["url"])
        self.assertNotIn("scoringPeriodId", roster_call["url"])

        called = espn_http(self.league, self.byes)
        code, payload, raw = run_main(
            ["espn", "roster", "--league", "100001", "--team", "1",
             "--week", "4", "--season", "2026"],
            http=called,
        )
        self.assertEqual(code, 0, raw)
        self.assertEqual(payload["week"], 4)
        self.assertEqual(payload["players"][0]["projected_points"], 22.09)
        roster_call = next(call for call in called.calls if "mRoster" in call["url"])
        self.assertIn("scoringPeriodId=4", roster_call["url"])

    def test_roster_uses_swid_when_team_is_omitted(self):
        http = espn_http(self.league, self.byes)
        code, payload, raw = run_main(
            ["espn", "roster", "--league", "100001", "--season", "2026"],
            env={
                "ESPN_S2": "s2-secret-value-zzzz",
                "ESPN_SWID": "00000000-0000-0000-0000-000000000001",
            },
            http=http,
        )
        self.assertEqual(code, 0, raw)
        self.assertEqual(payload["team_id"], 1)
        self.assertEqual(payload["my_team_id"], 1)

    def test_free_agents_sorted_by_percent_owned(self):
        http = espn_http(self.league, self.byes, self.agents)
        code, payload, raw = run_main(
            ["espn", "free-agents", "--league", "100001", "--week", "4",
             "--season", "2026", "--position", "QB", "--limit", "2"],
            http=http,
        )
        self.assertEqual(code, 0, raw)
        self.assertEqual(
            [row["name"] for row in payload["players"]],
            ["Cameron Dicker", "Bo Nix"],
        )
        self.assertEqual(payload["players"][0]["projected_points"], 7.72)
        self.assertEqual(payload["players"][0]["position"], "K")
        nix = payload["players"][1]
        self.assertEqual(nix["nfl_team"], "DEN")
        self.assertEqual(nix["bye_week"], 12)
        self.assertEqual(nix["injury_status"], "ACTIVE")
        kona = next(call for call in http.calls if "kona_player_info" in call["url"])
        header = json.loads(kona["headers"]["X-Fantasy-Filter"])
        self.assertEqual(header["players"]["filterSlotIds"]["value"], [0])
        self.assertEqual(header["players"]["limit"], 2)
        self.assertIn("scoringPeriodId=4", kona["url"])
        self.assertTrue(kona["url"].startswith("https://lm-api-reads.fantasy.espn.com/"))

    def test_matchup_live_and_final(self):
        http = espn_http(scoreboard=self.board)
        code, payload, raw = run_main(
            ["espn", "matchup", "--league", "100001", "--team", "1",
             "--season", "2026"],
            http=http,
        )
        self.assertEqual(code, 0, raw)
        self.assertEqual(payload["week"], 3)
        self.assertFalse(payload["final"])
        self.assertEqual(payload["home_away"], "home")
        self.assertEqual(payload["opponent_id"], 3)
        self.assertEqual(payload["points"], 12.5)
        self.assertEqual(payload["projected_points"], 80.25)
        self.assertEqual(payload["opponent_points"], 8.0)
        self.assertNotEqual(payload["points"], 0)

        code, payload, raw = run_main(
            ["espn", "matchup", "--league", "100001", "--team", "3",
             "--week", "2", "--season", "2026"],
            http=espn_http(scoreboard=self.board),
        )
        self.assertEqual(code, 0, raw)
        self.assertTrue(payload["final"])
        self.assertEqual(payload["home_away"], "away")
        self.assertEqual(payload["points"], 88.1)
        self.assertEqual(payload["opponent_points"], 74.2)
        self.assertEqual(payload["winner"], "AWAY")
        self.assertIsNone(payload["projected_points"])

        code, payload, raw = run_main(
            ["espn", "matchup", "--league", "100001", "--team", "9",
             "--week", "3", "--season", "2026"],
            http=espn_http(scoreboard=self.board),
        )
        self.assertEqual(payload["error"], "no_matchup")


class TestYahoo(unittest.TestCase):
    def test_help_says_untested(self):
        for argv in (["--help"], ["yahoo", "--help"]):
            buf = io.StringIO()
            err = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(err):
                with self.assertRaises(SystemExit) as caught:
                    lr.main(argv)
            self.assertEqual(caught.exception.code, 0)
            text = (buf.getvalue() + err.getvalue()).lower()
            self.assertIn("untested", text)
            self.assertIn("developer/access", text)

    def test_auth_url_and_rejected_redirects(self):
        http = FakeHTTP()
        code, payload, raw = run_main(
            ["yahoo", "auth-url", "--redirect-uri", "https://example.com/cb"],
            env={"YAHOO_CLIENT_ID": "cidvalue"},
            http=http,
        )
        self.assertEqual(code, 0, raw)
        self.assertTrue(payload["untested"])
        url = payload["authorization_url"]
        self.assertTrue(url.startswith("https://api.login.yahoo.com/oauth2/request_auth?"))
        self.assertIn("client_id=cidvalue", url)
        self.assertIn("response_type=code", url)
        self.assertIn("redirect_uri=https%3A%2F%2Fexample.com%2Fcb", url)
        self.assertEqual(http.calls, [])

        for redirect in ("http://example.com/cb", "https://localhost/cb", "oob"):
            code, payload, raw = run_main(
                ["yahoo", "auth-url", "--redirect-uri", redirect],
                env={"YAHOO_CLIENT_ID": "cidvalue"},
                http=FakeHTTP(),
            )
            self.assertEqual(code, 1, redirect)
            self.assertEqual(payload["error"], "bad_redirect_uri")
            self.assertTrue(payload["untested"])

    def test_connect_stores_mode_0600_and_hides_the_token(self):
        secret = "client-secret-value"
        refresh = "refresh-token-value"
        code_value = "auth-code-value"

        def http(url, headers=None, method="GET", form=None, timeout=30):
            http.calls.append(form)
            http.headers = headers
            return {
                "access_token": "access-token-value",
                "refresh_token": refresh,
                "expires_in": 3600,
                "token_type": "bearer",
            }

        http.calls = []
        with TemporaryDirectory() as tmp:
            with patch.object(lr, "config_dir", lambda: Path(tmp)):
                code, payload, raw = run_main(
                    ["yahoo", "connect", "--redirect-uri", "https://example.com/cb"],
                    env={
                        "YAHOO_CLIENT_ID": "cidvalue",
                        "YAHOO_CLIENT_SECRET": secret,
                        "YAHOO_AUTH_CODE": code_value,
                    },
                    http=http,
                )
                path = Path(tmp) / "yahoo_token.json"
                saved = json.loads(path.read_text(encoding="utf-8"))
                file_mode = stat.S_IMODE(path.stat().st_mode)
                dir_mode = stat.S_IMODE(Path(tmp).stat().st_mode)
            self.assertEqual(code, 0, raw)
            self.assertTrue(payload["connected"])
            self.assertNotIn(refresh, raw)
            self.assertNotIn(secret, raw)
            self.assertNotIn(code_value, raw)
            self.assertNotIn("access-token-value", raw)
            self.assertEqual(saved["refresh_token"], refresh)
            self.assertEqual(file_mode, 0o600)
            self.assertEqual(dir_mode, 0o700)
            self.assertEqual(http.calls[0]["grant_type"], "authorization_code")
            self.assertEqual(http.calls[0]["code"], code_value)
            encoded = http.headers["Authorization"].split(" ", 1)[1]
            import base64
            decoded = base64.b64decode(encoded).decode("utf-8")
            self.assertEqual(decoded, "cidvalue:" + secret)

    def test_refresh_keeps_a_rotated_token(self):
        self.assertEqual(
            lr.apply_token_response(
                {"refresh_token": "refresh-old"},
                {"access_token": "access-new", "expires_in": 3600},
            )["refresh_token"],
            "refresh-old",
        )
        self.assertEqual(
            lr.apply_token_response(
                {"refresh_token": "refresh-old"},
                {"access_token": "access-new", "refresh_token": "refresh-new",
                 "expires_in": 3600},
            )["refresh_token"],
            "refresh-new",
        )

        def http(url, headers=None, method="GET", form=None, timeout=30):
            http.calls.append({
                "url": url, "method": method, "form": form, "headers": headers or {},
            })
            if "get_token" in url:
                return {
                    "access_token": "access-new",
                    "refresh_token": "refresh-new",
                    "expires_in": 3600,
                }
            return {"fantasy_content": {"leagues": []}}

        http.calls = []
        with TemporaryDirectory() as tmp:
            path = Path(tmp)
            with patch.object(lr, "config_dir", lambda: path):
                lr.write_token({
                    "refresh_token": "refresh-old",
                    "access_token": "access-old",
                    "expires_at": 0,
                })
                code, payload, raw = run_main(
                    ["yahoo", "leagues"],
                    env={
                        "YAHOO_CLIENT_ID": "cidvalue",
                        "YAHOO_CLIENT_SECRET": "client-secret-value",
                    },
                    http=http,
                )
                saved = json.loads((path / "yahoo_token.json").read_text(encoding="utf-8"))
            self.assertEqual(code, 0, raw)
            self.assertTrue(payload["untested"])
            self.assertEqual(saved["refresh_token"], "refresh-new")
            self.assertNotIn("refresh-new", raw)
            self.assertNotIn("refresh-old", raw)
            self.assertEqual(http.calls[0]["method"], "POST")
            self.assertEqual(http.calls[0]["form"]["grant_type"], "refresh_token")
            self.assertEqual(http.calls[0]["form"]["refresh_token"], "refresh-old")
            self.assertEqual(http.calls[1]["headers"]["Authorization"], "Bearer access-new")
            self.assertEqual(
                http.calls[1]["url"],
                "https://fantasysports.yahooapis.com/fantasy/v2/"
                "users;use_login=1/games;game_keys=nfl/leagues?format=json",
            )

    def test_not_approved_is_a_plain_error(self):
        def http(url, headers=None, method="GET", form=None, timeout=30):
            raise lr.HttpStatus(403, {
                "error": {
                    "description": "This application is not authorized to perform this action",
                },
            })

        with TemporaryDirectory() as tmp:
            with patch.object(lr, "config_dir", lambda: Path(tmp)):
                lr.write_token({
                    "refresh_token": "refresh-old",
                    "access_token": "access-old",
                    "expires_at": time_far(),
                })
                code, payload, raw = run_main(
                    ["yahoo", "get", "league/nfl.l.1/scoreboard"],
                    env={
                        "YAHOO_CLIENT_ID": "cidvalue",
                        "YAHOO_CLIENT_SECRET": "client-secret-value",
                    },
                    http=http,
                )
        self.assertEqual(code, 1)
        self.assertEqual(payload["error"], "yahoo_not_approved")
        self.assertIn("approved", payload["message"])
        self.assertTrue(payload["untested"])
        self.assertNotIn("refresh-old", raw)
        self.assertNotIn("access-old", raw)

    def test_get_rejects_a_url_and_adds_format(self):
        self.assertEqual(
            lr.yahoo_resource_url("league/nfl.l.1/scoreboard"),
            "https://fantasysports.yahooapis.com/fantasy/v2/"
            "league/nfl.l.1/scoreboard?format=json",
        )
        self.assertIn(
            "format=json",
            lr.yahoo_resource_url("league/nfl.l.1/scoreboard?week=1"),
        )
        self.assertIsNone(lr.yahoo_resource_url("https://evil.example/league"))
        self.assertIsNone(lr.yahoo_resource_url("../secret"))
        code, payload, raw = run_main(
            ["yahoo", "get", "https://evil.example/league"],
            http=FakeHTTP(),
        )
        self.assertEqual(payload["error"], "bad_path")


class TestSurface(unittest.TestCase):
    def test_imports_are_stdlib(self):
        tree = ast.parse((ROOT / "league_read.py").read_text(encoding="utf-8"))
        allowed = {
            "__future__", "argparse", "base64", "json", "os", "sys", "time",
            "urllib", "datetime", "pathlib",
        }
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module.split(".")[0])
        self.assertTrue(found)
        self.assertEqual(found - allowed, set())

    def test_current_season_falls_back_when_sleeper_fails(self):
        year = datetime.now(timezone.utc).year

        def http(url, headers=None, method="GET", form=None, timeout=30):
            if "sleeper" in url:
                raise lr.HttpStatus(0, {})
            return {"id": year, "currentScoringPeriod": {"id": 3}}

        with patch.object(lr, "http_json", http):
            self.assertEqual(lr.current_season(), year)


def time_far():
    return lr.time.time() + 86400


if __name__ == "__main__":
    unittest.main()
