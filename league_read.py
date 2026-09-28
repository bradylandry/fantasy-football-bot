"""Read an ESPN or Yahoo league for the recommend-only fantasy bot.

One standard-library script. It only reads. It never submits a lineup,
a waiver claim, or a trade.

ESPN public leagues need the league id. Private leagues need the espn_s2
and SWID cookies in the environment (ESPN_S2 and ESPN_SWID). Those values
are sent as cookies and are never printed.

Yahoo is UNTESTED. Yahoo gates Fantasy API access behind an application
the owner must get approved at https://sports.yahoo.com/developer/access/
before any call works. oob and localhost redirects are not accepted.

    python league_read.py espn settings --league LEAGUE_ID
    python league_read.py espn teams --league LEAGUE_ID
    python league_read.py espn roster --league LEAGUE_ID --team TEAM_ID
    python league_read.py espn free-agents --league LEAGUE_ID --limit 25
    python league_read.py espn matchup --league LEAGUE_ID --team TEAM_ID
    python league_read.py yahoo auth-url --redirect-uri https://example.com/cb
    python league_read.py yahoo connect --redirect-uri https://example.com/cb
    python league_read.py yahoo leagues
    python league_read.py yahoo get league/nfl.l.LEAGUE_ID/scoreboard

Stdout is one JSON object. Errors are JSON too, with a non-zero exit.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

UA = "league-read/1.0"
SLEEPER_STATE = "https://api.sleeper.app/v1/state/nfl"
ESPN_SEASON = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}"
ESPN_LEAGUE = (
    "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/"
    "seasons/{season}/segments/0/leagues/{league_id}"
)
YAHOO_AUTH = "https://api.login.yahoo.com/oauth2/request_auth"
YAHOO_TOKEN = "https://api.login.yahoo.com/oauth2/get_token"
YAHOO_API = "https://fantasysports.yahooapis.com/fantasy/v2/"
YAHOO_LEAGUES = "users;use_login=1/games;game_keys=nfl/leagues"
YAHOO_APPROVAL = "https://sports.yahoo.com/developer/access/"

# Lineup slot ids ESPN returns on rosterSettings.lineupSlotCounts and on
# each roster entry. Names are the ones managers use.
SLOT_NAMES = {
    0: "QB",
    1: "TQB",
    2: "RB",
    3: "RB/WR",
    4: "WR",
    5: "WR/TE",
    6: "TE",
    7: "OP",
    8: "DT",
    9: "DE",
    10: "LB",
    11: "DL",
    12: "CB",
    13: "S",
    14: "DB",
    15: "DP",
    16: "D/ST",
    17: "K",
    18: "P",
    19: "HC",
    20: "Bench",
    21: "IR",
    23: "FLEX",
    24: "EDR",
}
SLOT_ORDER = [0, 2, 4, 6, 23, 16, 17, 20, 21]

# defaultPositionId on a player.
POS_NAMES = {
    1: "QB",
    2: "RB",
    3: "WR",
    4: "TE",
    5: "K",
    16: "D/ST",
}

# Fallback when the bye-week feed has no abbrev for that id.
PRO_TEAM_ABBREV = {
    0: "FA",
    1: "ATL",
    2: "BUF",
    3: "CHI",
    4: "CIN",
    5: "CLE",
    6: "DAL",
    7: "DEN",
    8: "DET",
    9: "GB",
    10: "TEN",
    11: "IND",
    12: "KC",
    13: "LV",
    14: "LAR",
    15: "MIA",
    16: "MIN",
    17: "NE",
    18: "NO",
    19: "NYG",
    20: "NYJ",
    21: "PHI",
    22: "ARI",
    23: "PIT",
    24: "LAC",
    25: "SF",
    26: "SEA",
    27: "TB",
    28: "WSH",
    29: "CAR",
    30: "JAX",
    33: "BAL",
    34: "HOU",
}

# Free-agent slot filter. FLEX is included so the list is not only pure
# positions. D/ST and K are on it because streaming weeks need them.
POSITION_SLOTS = {
    "QB": 0,
    "RB": 2,
    "WR": 4,
    "TE": 6,
    "FLEX": 23,
    "K": 17,
    "D/ST": 16,
    "DST": 16,
    "DEF": 16,
}
DEFAULT_SLOTS = [0, 2, 4, 6, 23, 16, 17]

# statId 53 is receptions. Its points value is the PPR setting.
RECEPTION_STAT = 53
# statSourceId 1 is a projection. 0 is actual points.
PROJECTION_SOURCE = 1
# statSplitTypeId 1 is the single week. 0 is the season.
WEEK_SPLIT = 1


class HttpStatus(Exception):
    def __init__(self, status, body):
        super().__init__("HTTP {}".format(status))
        self.status = status
        self.body = body


def emit(obj):
    print(json.dumps(obj), flush=True)


def fail(error, message, **extra):
    body = {"error": error, "message": message}
    body.update(extra)
    emit(body)
    return 1


def rnd(value):
    if value is None:
        return None
    return round(float(value), 2)


def as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def redact(text, extra=()):
    """Drop env secrets from a string before it is printed.

    SWID is not redacted. It is an owner id on the team list, and the
    teams command is supposed to show owner ids.
    """
    secrets = []
    for key in ("ESPN_S2", "YAHOO_CLIENT_SECRET", "YAHOO_AUTH_CODE"):
        value = os.environ.get(key) or ""
        if len(value) >= 8:
            secrets.append(value)
    for value in extra:
        if value and len(str(value)) >= 8:
            secrets.append(str(value))
    for secret in secrets:
        text = text.replace(secret, "[redacted]")
    return text


def http_json(url, headers=None, method="GET", form=None, timeout=30):
    """GET or form-POST a URL and parse a JSON body.

    ESPN calls are GET. Yahoo's token exchange is the one POST.
    """
    hdrs = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode("utf-8")
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body = {"raw": raw[:500]}
        raise HttpStatus(exc.code, body)
    except urllib.error.URLError:
        raise HttpStatus(0, {"message": "network error"})
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise HttpStatus(0, {"message": "response was not JSON"})


def detail_type(body):
    if not isinstance(body, dict):
        return None
    for detail in body.get("details") or []:
        if isinstance(detail, dict) and detail.get("type"):
            return detail["type"]
    return None


def body_text(body):
    if isinstance(body, str):
        return body
    try:
        return json.dumps(body)
    except TypeError:
        return str(body)


# --- season ----------------------------------------------------------------

def current_season():
    """Sleeper's NFL season, else the ESPN season object for this year."""
    try:
        state = http_json(SLEEPER_STATE, timeout=15)
        season = as_int(state.get("season"))
        if season and 2000 <= season <= 2100:
            return season
    except Exception:
        pass
    year = datetime.now(timezone.utc).year
    for candidate in (year, year - 1):
        try:
            body = http_json(ESPN_SEASON.format(season=candidate), timeout=15)
        except Exception:
            continue
        if isinstance(body, dict) and (
            body.get("currentScoringPeriod") or body.get("id")
        ):
            return candidate
    return year


def check_season(season):
    if season is None or season < 2000 or season > 2100:
        return fail("bad_season", "Season must be a four-digit year.")
    return None


def espn_league_url(season, league_id, pairs):
    base = ESPN_LEAGUE.format(season=int(season), league_id=league_id)
    if not pairs:
        return base
    return base + "?" + urllib.parse.urlencode(pairs)


def bye_url(season):
    return ESPN_SEASON.format(season=int(season)) + "?view=proTeamSchedules_wl"


# --- ESPN cookies ----------------------------------------------------------

def load_espn_cookies():
    """Return (s2, swid), or None when the league is public.

    A half-set pair is an error. The values are not included in it.
    """
    s2 = os.environ.get("ESPN_S2") or ""
    swid = os.environ.get("ESPN_SWID") or ""
    if not s2 and not swid:
        return None, None
    if not s2 or not swid:
        return None, fail(
            "missing_cookie",
            "Set both ESPN_S2 and ESPN_SWID, or neither. "
            "Collect them with a secret request, not in chat.",
        )
    if any(ch in s2 or ch in swid for ch in (";", "\r", "\n")):
        return None, fail(
            "bad_cookie",
            "ESPN_S2 or ESPN_SWID contains a character that cannot go in "
            "a cookie header. Collect them again with a secret request, "
            "not in chat.",
        )
    return (s2, swid), None


def espn_headers(cookies, extra=None):
    headers = {}
    if cookies:
        s2, swid = cookies
        headers["Cookie"] = "espn_s2={}; SWID={}".format(s2, swid)
    if extra:
        headers.update(extra)
    return headers


def espn_failure(status, body, league_id, season, had_cookies):
    kind = detail_type(body)
    if status == 401 and kind == "AUTH_LEAGUE_NOT_VISIBLE":
        if had_cookies:
            message = (
                "ESPN did not accept ESPN_S2 and ESPN_SWID for this private "
                "league. Collect both cookies again with a secret request, "
                "not in chat."
            )
        else:
            message = (
                "This ESPN league is private. Collect ESPN_S2 and ESPN_SWID "
                "(the espn_s2 and SWID cookies) with a secret request, not "
                "in chat, then rerun."
            )
        return fail("private_league", message)
    if status == 404 and kind == "GENERAL_NOT_FOUND":
        return fail(
            "league_not_found",
            "No ESPN league {} for season {}.".format(league_id, season),
        )
    if status == 0:
        return fail("network", "Could not reach ESPN.")
    return fail(
        "espn_http",
        "ESPN returned HTTP {}.".format(status),
        http_status=status,
    )


def espn_get(url, league_id, season, cookies, headers=None):
    try:
        return http_json(url, headers=espn_headers(cookies, headers)), None
    except HttpStatus as exc:
        return None, espn_failure(
            exc.status, exc.body, league_id, season, bool(cookies)
        )


def normalize_swid(value):
    text = urllib.parse.unquote(str(value or "")).strip().strip('"').lower()
    return text.replace("{", "").replace("}", "")


def team_owner_ids(team):
    owners = []
    for owner in team.get("owners") or []:
        if owner and owner not in owners:
            owners.append(owner)
    primary = team.get("primaryOwner")
    if primary and primary not in owners:
        owners.append(primary)
    return owners


def my_team_id(teams, cookies):
    """Match SWID to teams[].owners. None when no owner matches."""
    if not cookies:
        return None
    want = normalize_swid(cookies[1])
    if not want:
        return None
    for team in teams:
        for owner in team_owner_ids(team):
            if normalize_swid(owner) == want:
                return as_int(team.get("id"))
    return None


def attach_my_team(payload, teams, cookies):
    if not cookies:
        return payload
    found = my_team_id(teams, cookies)
    payload["my_team_id"] = found
    if found is None:
        payload["my_team_note"] = "ESPN_SWID is not an owner in this league."
    return payload


# --- ESPN shapes -----------------------------------------------------------

def slot_name(slot_id):
    number = as_int(slot_id)
    if number is None:
        return None
    return SLOT_NAMES.get(number, "Slot {}".format(number))


def pos_name(position_id):
    number = as_int(position_id)
    if number is None:
        return None
    return POS_NAMES.get(number, "Pos {}".format(number))


def slot_sort_key(slot_id):
    number = as_int(slot_id)
    if number is None:
        return 1000
    try:
        return SLOT_ORDER.index(number)
    except ValueError:
        return 100 + number


def scoring_from_items(items):
    """PPR comes from scoringItems statId 53 (receptions) points.

    Missing or zero is standard. 0.5 is half-ppr. 1 is ppr.
    """
    points = None
    for item in items or []:
        if as_int(item.get("statId")) == RECEPTION_STAT:
            points = float(item.get("points") or 0)
            break
    if points is None or abs(points) < 1e-9:
        return "standard", 0
    if abs(points - 0.5) < 1e-9:
        return "half-ppr", 0.5
    if abs(points - 1.0) < 1e-9:
        return "ppr", 1
    return "custom", rnd(points)


def roster_slots(counts):
    rows = []
    for key, count in (counts or {}).items():
        number = as_int(key)
        n = as_int(count)
        if number is None or not n or n < 0:
            continue
        rows.append((number, n))
    rows.sort(key=lambda pair: (slot_sort_key(pair[0]), pair[0]))
    return [
        {"slot_id": number, "name": slot_name(number), "count": count}
        for number, count in rows
    ]


def draft_block(detail, settings):
    detail = detail or {}
    draft_settings = (settings or {}).get("draftSettings") or {}
    in_progress = bool(detail.get("inProgress"))
    drafted = bool(detail.get("drafted"))
    if in_progress:
        status = "in_progress"
    elif drafted:
        status = "complete"
    else:
        status = "not_started"
    return {
        "status": status,
        "type": draft_settings.get("type"),
        "drafted": drafted,
        "in_progress": in_progress,
    }


def waiver_block(settings):
    acq = (settings or {}).get("acquisitionSettings") or {}
    budget = acq.get("acquisitionBudget")
    return {
        "type": acq.get("acquisitionType"),
        "uses_faab": bool(acq.get("isUsingAcquisitionBudget")),
        "budget": as_int(budget) if budget is not None else None,
    }


def current_week_of(body):
    week = as_int(body.get("scoringPeriodId"))
    if week:
        return week
    status = body.get("status") or {}
    return as_int(status.get("latestScoringPeriod")) or as_int(
        status.get("currentMatchupPeriod")
    )


def team_name(team):
    name = (team.get("name") or "").strip()
    if name:
        return name
    location = (team.get("location") or "").strip()
    nickname = (team.get("nickname") or "").strip()
    return " ".join(part for part in (location, nickname) if part)


def team_rows(teams):
    rows = []
    for team in teams or []:
        team_id = as_int(team.get("id"))
        if team_id is None:
            continue
        rows.append({
            "id": team_id,
            "name": team_name(team),
            "owners": team_owner_ids(team),
        })
    rows.sort(key=lambda row: row["id"])
    return rows


def projected_points(stats, week):
    """League-scored projection for one scoring period.

    statSourceId 1 is the projection. statSplitTypeId 1 is that week,
    not the season total that shares a zero scoring period.
    """
    fallback = None
    for stat in stats or []:
        if as_int(stat.get("statSourceId")) != PROJECTION_SOURCE:
            continue
        if as_int(stat.get("scoringPeriodId")) != as_int(week):
            continue
        if stat.get("appliedTotal") is None:
            continue
        value = rnd(stat.get("appliedTotal"))
        if as_int(stat.get("statSplitTypeId")) == WEEK_SPLIT:
            return value
        if fallback is None:
            fallback = value
    return fallback


def bye_index(body):
    teams = ((body or {}).get("settings") or {}).get("proTeams") or []
    out = {}
    for team in teams:
        team_id = as_int(team.get("id"))
        if team_id is None:
            continue
        bye = as_int(team.get("byeWeek")) or None
        out[team_id] = {"abbrev": team.get("abbrev") or None, "bye": bye}
    return out


def nfl_team_and_bye(pro_team_id, byes):
    number = as_int(pro_team_id)
    info = byes.get(number) if number is not None else None
    if info and info.get("abbrev"):
        abbrev = info["abbrev"]
    elif number is None:
        abbrev = None
    else:
        abbrev = PRO_TEAM_ABBREV.get(number, "TEAM_{}".format(number))
    bye = info.get("bye") if info else None
    return abbrev, bye


def player_name(player):
    full = (player.get("fullName") or "").strip()
    if full:
        return full
    first = (player.get("firstName") or "").strip()
    last = (player.get("lastName") or "").strip()
    return " ".join(part for part in (first, last) if part)


def roster_players(team, week, byes):
    roster = team.get("roster") or {}
    rows = []
    for entry in roster.get("entries") or []:
        pool = entry.get("playerPoolEntry") or {}
        player = pool.get("player") or {}
        player_id = as_int(player.get("id"))
        if player_id is None:
            player_id = as_int(entry.get("playerId"))
        if player_id is None:
            continue
        abbrev, bye = nfl_team_and_bye(player.get("proTeamId"), byes)
        injury = player.get("injuryStatus") or entry.get("injuryStatus")
        slot_id = as_int(entry.get("lineupSlotId"))
        rows.append({
            "player_id": player_id,
            "name": player_name(player),
            "slot": slot_name(slot_id),
            "slot_id": slot_id,
            "position": pos_name(player.get("defaultPositionId")),
            "nfl_team": abbrev,
            "injury_status": injury,
            "projected_points": projected_points(player.get("stats"), week),
            "bye_week": bye,
        })
    rows.sort(key=lambda row: (slot_sort_key(row.get("slot_id")), row["name"]))
    return rows


def find_team(teams, team_id):
    for team in teams or []:
        if as_int(team.get("id")) == as_int(team_id):
            return team
    return None


def parse_settings(body, league_id, season):
    settings = body.get("settings") or {}
    if not settings:
        return None
    scoring, receptions = scoring_from_items(
        (settings.get("scoringSettings") or {}).get("scoringItems")
    )
    week = current_week_of(body)
    size = as_int(settings.get("size"))
    if not size:
        size = len(body.get("teams") or []) or None
    return {
        "platform": "espn",
        "command": "settings",
        "league_id": str(league_id),
        "season": int(season),
        "name": settings.get("name"),
        "size": size,
        "current_week": week,
        "scoring": scoring,
        "reception_points": receptions,
        "roster_slots": roster_slots(
            (settings.get("rosterSettings") or {}).get("lineupSlotCounts")
        ),
        "waivers": waiver_block(settings),
        "draft": draft_block(body.get("draftDetail"), settings),
        "is_public": settings.get("isPublic"),
    }


def matchup_final(row):
    winner = row.get("winner") or ""
    return winner not in ("", "UNDECIDED")


def side_points(side, final):
    if final:
        return rnd(side.get("totalPoints"))
    if side.get("totalPointsLive") is not None:
        return rnd(side.get("totalPointsLive"))
    return rnd(side.get("totalPoints"))


def side_projected(side):
    if side.get("totalProjectedPointsLive") is not None:
        return rnd(side.get("totalProjectedPointsLive"))
    if side.get("totalProjectedPoints") is not None:
        return rnd(side.get("totalProjectedPoints"))
    return None


def matchup_for(body, team_id, week):
    """One team's side of the week. Final weeks use totalPoints.

    A live week keeps totalPoints at 0 and puts the score in
    totalPointsLive / totalProjectedPointsLive.
    """
    for row in body.get("schedule") or []:
        if as_int(row.get("matchupPeriodId")) != as_int(week):
            continue
        home = row.get("home") or {}
        away = row.get("away") or {}
        home_id = as_int(home.get("teamId"))
        away_id = as_int(away.get("teamId"))
        if as_int(team_id) == home_id:
            mine, opp, side = home, away, "home"
            opp_id = away_id
        elif as_int(team_id) == away_id:
            mine, opp, side = away, home, "away"
            opp_id = home_id
        else:
            continue
        final = matchup_final(row)
        return {
            "week": as_int(week),
            "team_id": as_int(team_id),
            "opponent_id": opp_id,
            "home_away": side,
            "points": side_points(mine, final),
            "projected_points": side_projected(mine),
            "opponent_points": side_points(opp, final),
            "opponent_projected_points": side_projected(opp),
            "winner": row.get("winner"),
            "final": final,
        }
    return None


def parse_positions(values):
    if not values:
        return list(DEFAULT_SLOTS), None
    slots = []
    for raw in values:
        for part in str(raw).split(","):
            name = part.strip().upper().replace(" ", "")
            if not name:
                continue
            if name not in POSITION_SLOTS:
                return None, part.strip()
            slot = POSITION_SLOTS[name]
            if slot not in slots:
                slots.append(slot)
    if not slots:
        return list(DEFAULT_SLOTS), None
    return slots, None


def free_agent_filter(slots, limit):
    payload = {
        "players": {
            "filterStatus": {"value": ["FREEAGENT", "WAIVERS"]},
            "filterSlotIds": {"value": list(slots)},
            "limit": int(limit),
            "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
        }
    }
    return json.dumps(payload, separators=(",", ":"))


def free_agent_rows(body, week, byes):
    if isinstance(body, list):
        players = body
    else:
        players = (body or {}).get("players") or []
    rows = []
    for item in players:
        if not isinstance(item, dict):
            continue
        player = item.get("player") or {}
        player_id = as_int(player.get("id")) or as_int(item.get("id"))
        if player_id is None:
            continue
        abbrev, bye = nfl_team_and_bye(player.get("proTeamId"), byes)
        owned = (player.get("ownership") or {}).get("percentOwned")
        rows.append({
            "player_id": player_id,
            "name": player_name(player),
            "position": pos_name(player.get("defaultPositionId")),
            "nfl_team": abbrev,
            "status": item.get("status"),
            "injury_status": player.get("injuryStatus"),
            "percent_owned": rnd(owned) if owned is not None else None,
            "projected_points": projected_points(player.get("stats"), week),
            "bye_week": bye,
        })
    rows.sort(key=lambda row: (
        -(row["percent_owned"] if row["percent_owned"] is not None else -1),
        row["name"],
    ))
    return rows


def check_league_id(league_id):
    text = str(league_id or "").strip()
    if not text.isdigit():
        return None, fail("bad_league_id", "League id must be digits.")
    return text, None


def check_week(week):
    if week is None:
        return None
    if week < 1 or week > 30:
        return fail("bad_week", "Week must be from 1 to 30.")
    return None


def resolve_team_id(explicit, teams, cookies):
    if explicit is not None:
        return explicit, None
    if cookies:
        found = my_team_id(teams, cookies)
        if found is not None:
            return found, None
        return None, fail(
            "team_required",
            "Pass --team. ESPN_SWID is not an owner in this league.",
        )
    return None, fail(
        "team_required",
        "Pass --team TEAM_ID. On a private league, set ESPN_S2 and "
        "ESPN_SWID and the owner's team is selected for you.",
    )


def load_byes(season):
    try:
        body = http_json(bye_url(season), timeout=30)
    except HttpStatus as exc:
        if exc.status == 0:
            return None, fail("network", "Could not reach ESPN for bye weeks.")
        return None, fail(
            "espn_http",
            "ESPN bye-week request returned HTTP {}.".format(exc.status),
            http_status=exc.status,
        )
    return bye_index(body), None


def cmd_settings(league_id, season, cookies):
    url = espn_league_url(season, league_id, [
        ("view", "mSettings"),
        ("view", "mStatus"),
        ("view", "mDraftDetail"),
        ("view", "mTeam"),
    ])
    body, err = espn_get(url, league_id, season, cookies)
    if err:
        return err
    payload = parse_settings(body, league_id, season)
    if payload is None:
        return fail("espn_shape", "ESPN response had no league settings.")
    attach_my_team(payload, body.get("teams") or [], cookies)
    emit(payload)
    return 0


def cmd_teams(league_id, season, cookies):
    url = espn_league_url(season, league_id, [("view", "mTeam")])
    body, err = espn_get(url, league_id, season, cookies)
    if err:
        return err
    teams = body.get("teams") or []
    payload = {
        "platform": "espn",
        "command": "teams",
        "league_id": str(league_id),
        "season": int(season),
        "teams": team_rows(teams),
    }
    attach_my_team(payload, teams, cookies)
    emit(payload)
    return 0


def cmd_roster(league_id, season, cookies, team_id, week):
    pairs = [("view", "mTeam"), ("view", "mRoster")]
    if week is not None:
        pairs.append(("scoringPeriodId", str(week)))
    url = espn_league_url(season, league_id, pairs)
    body, err = espn_get(url, league_id, season, cookies)
    if err:
        return err
    if week is None:
        week = current_week_of(body)
    if not week:
        return fail(
            "no_week",
            "ESPN did not say which week this is. Pass --week.",
        )
    teams = body.get("teams") or []
    team_id, err = resolve_team_id(team_id, teams, cookies)
    if err:
        return err
    team = find_team(teams, team_id)
    if team is None:
        return fail(
            "team_not_found",
            "No team {} in ESPN league {}.".format(team_id, league_id),
        )
    byes, err = load_byes(season)
    if err:
        return err
    payload = {
        "platform": "espn",
        "command": "roster",
        "league_id": str(league_id),
        "season": int(season),
        "team_id": as_int(team_id),
        "team_name": team_name(team),
        "week": as_int(week),
        "players": roster_players(team, week, byes),
    }
    attach_my_team(payload, teams, cookies)
    emit(payload)
    return 0


def cmd_free_agents(league_id, season, cookies, positions, limit, week):
    slots, bad = parse_positions(positions)
    if bad:
        return fail(
            "bad_position",
            "Unknown position {!r}. Use QB, RB, WR, TE, FLEX, K, or D/ST."
            .format(bad),
        )
    if week is None:
        url = espn_league_url(season, league_id, [("view", "mStatus")])
        body, err = espn_get(url, league_id, season, cookies)
        if err:
            return err
        week = current_week_of(body)
        if not week:
            return fail(
                "no_week",
                "ESPN did not say which week this is. Pass --week.",
            )
    url = espn_league_url(season, league_id, [
        ("view", "kona_player_info"),
        ("scoringPeriodId", str(week)),
    ])
    headers = {"X-Fantasy-Filter": free_agent_filter(slots, limit)}
    body, err = espn_get(url, league_id, season, cookies, headers)
    if err:
        return err
    byes, err = load_byes(season)
    if err:
        return err
    rows = free_agent_rows(body, week, byes)[: int(limit)]
    emit({
        "platform": "espn",
        "command": "free-agents",
        "league_id": str(league_id),
        "season": int(season),
        "week": as_int(week),
        "limit": int(limit),
        "players": rows,
    })
    return 0


def cmd_matchup(league_id, season, cookies, team_id, week):
    pairs = [("view", "mMatchupScore"), ("view", "mScoreboard")]
    if week is not None:
        pairs.append(("scoringPeriodId", str(week)))
    url = espn_league_url(season, league_id, pairs)
    body, err = espn_get(url, league_id, season, cookies)
    if err:
        return err
    if week is None:
        week = current_week_of(body)
    if not week:
        return fail(
            "no_week",
            "ESPN did not say which week this is. Pass --week.",
        )
    teams = body.get("teams") or []
    team_id, err = resolve_team_id(team_id, teams, cookies)
    if err:
        return err
    row = matchup_for(body, team_id, week)
    if row is None:
        return fail(
            "no_matchup",
            "Team {} has no matchup in week {}.".format(team_id, week),
        )
    payload = {
        "platform": "espn",
        "command": "matchup",
        "league_id": str(league_id),
        "season": int(season),
    }
    payload.update(row)
    attach_my_team(payload, teams, cookies)
    emit(payload)
    return 0


def run_espn(args):
    league_id, err = check_league_id(args.league)
    if err:
        return err
    if args.season is None:
        season = current_season()
    else:
        season = args.season
    err = check_season(season)
    if err:
        return err
    cookies, err = load_espn_cookies()
    if err:
        return err
    week = getattr(args, "week", None)
    err = check_week(week)
    if err:
        return err
    command = args.espn_cmd
    if command == "settings":
        return cmd_settings(league_id, season, cookies)
    if command == "teams":
        return cmd_teams(league_id, season, cookies)
    if command == "roster":
        return cmd_roster(league_id, season, cookies, args.team, week)
    if command == "free-agents":
        if args.limit < 1 or args.limit > 200:
            return fail("bad_limit", "Limit must be from 1 to 200.")
        return cmd_free_agents(
            league_id, season, cookies, args.position, args.limit, week
        )
    if command == "matchup":
        return cmd_matchup(league_id, season, cookies, args.team, week)
    return fail("unknown_command", "Unknown ESPN command.")


# --- Yahoo (untested) ------------------------------------------------------

YAHOO_NOTE = (
    "Untested. Yahoo gates Fantasy API access behind an application at "
    "https://sports.yahoo.com/developer/access/. The owner must be approved "
    "before any call works. oob and localhost redirects are not accepted."
)


def yahoo_fail(error, message, **extra):
    extra.setdefault("platform", "yahoo")
    extra.setdefault("untested", True)
    return fail(error, message, **extra)


def config_dir():
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / "fantasy-football-bot"
    return Path.home() / ".config" / "fantasy-football-bot"


def token_path():
    return config_dir() / "yahoo_token.json"


def ensure_config_dir():
    path = config_dir()
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def write_token(payload):
    """Store the refresh token where only this user can read it.

    The file mode is 0600. Callers must not print the payload.
    """
    ensure_config_dir()
    path = token_path()
    tmp = path.with_suffix(".json.tmp")
    raw = (json.dumps(payload) + "\n").encode("utf-8")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, raw)
    finally:
        os.close(fd)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    os.chmod(path, 0o600)
    return path


def read_token():
    path = token_path()
    if not path.exists():
        return None, yahoo_fail(
            "not_connected",
            "No Yahoo token stored. Run yahoo connect first. "
            "Collect the client secret and auth code with a secret "
            "request, not in chat.",
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, yahoo_fail(
            "token_unreadable",
            "The Yahoo token file could not be read. Run yahoo connect again.",
        )
    if not isinstance(payload, dict) or not payload.get("refresh_token"):
        return None, yahoo_fail(
            "token_unreadable",
            "The Yahoo token file has no refresh token. Run yahoo connect again.",
        )
    return payload, None


def redirect_uri_of(explicit):
    value = (explicit or os.environ.get("YAHOO_REDIRECT_URI") or "").strip()
    if not value:
        return None, yahoo_fail(
            "missing_redirect_uri",
            "Set --redirect-uri or YAHOO_REDIRECT_URI to an https URL "
            "you control. oob and localhost are not accepted.",
        )
    lowered = value.lower()
    if lowered in ("oob", "urn:ietf:wg:oauth:2.0:oob"):
        return None, yahoo_fail(
            "bad_redirect_uri",
            "Yahoo does not accept an oob redirect. Use an https URL you control.",
        )
    parsed = urllib.parse.urlparse(value)
    host = (parsed.hostname or "").lower()
    if (parsed.scheme != "https" or not parsed.netloc
            or host in ("localhost", "127.0.0.1", "::1")
            or host.endswith(".localhost")):
        return None, yahoo_fail(
            "bad_redirect_uri",
            "Yahoo requires an https redirect URL you control. "
            "oob and localhost are not accepted.",
        )
    return value, None


def yahoo_client_id():
    return (os.environ.get("YAHOO_CLIENT_ID") or "").strip()


def yahoo_client_secret():
    return (os.environ.get("YAHOO_CLIENT_SECRET") or "").strip()


def basic_auth_header(client_id, client_secret):
    raw = "{}:{}".format(client_id, client_secret).encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def auth_url(client_id, redirect):
    query = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect,
        "response_type": "code",
    })
    return YAHOO_AUTH + "?" + query


def yahoo_resource_url(path):
    text = str(path or "").strip()
    if not text or "://" in text or text.startswith("//") or "\\" in text or ".." in text:
        return None
    if text.startswith("/"):
        text = text[1:]
    parts = urllib.parse.urlsplit(YAHOO_API + text)
    if parts.netloc != "fantasysports.yahooapis.com":
        return None
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    if not any(key == "format" for key, _value in query):
        query.append(("format", "json"))
    return urllib.parse.urlunsplit((
        "https",
        "fantasysports.yahooapis.com",
        parts.path,
        urllib.parse.urlencode(query),
        "",
    ))


def token_not_approved(status, body):
    if status != 403:
        return False
    return "not authorized to perform this action" in body_text(body).lower()


def yahoo_http_error(status, body, tokens=()):
    if token_not_approved(status, body):
        return yahoo_fail(
            "yahoo_not_approved",
            "This Yahoo application is not authorized to perform this "
            "action. The owner has to be approved at {} before league "
            "calls work.".format(YAHOO_APPROVAL),
        )
    if status == 0:
        return yahoo_fail("network", "Could not reach Yahoo.")
    message = "Yahoo returned HTTP {}.".format(status)
    if isinstance(body, dict):
        description = body.get("error_description") or body.get("error")
        if isinstance(description, str) and description:
            message = redact(description, tokens)
        elif isinstance(description, dict):
            text = description.get("description") or ""
            if text:
                message = redact(str(text), tokens)
    return yahoo_fail("yahoo_http", message, http_status=status)


def post_token(form):
    client_id = yahoo_client_id()
    client_secret = yahoo_client_secret()
    headers = {"Authorization": basic_auth_header(client_id, client_secret)}
    return http_json(YAHOO_TOKEN, headers=headers, method="POST", form=form, timeout=30)


def apply_token_response(previous, body):
    """Keep a rotated refresh token when Yahoo sends a new one."""
    refresh = body.get("refresh_token") or (previous or {}).get("refresh_token")
    access = body.get("access_token")
    if not refresh or not access:
        return None
    expires_in = as_int(body.get("expires_in")) or 3600
    return {
        "refresh_token": refresh,
        "access_token": access,
        "expires_at": time.time() + expires_in,
        "expires_in": expires_in,
    }


def access_token(force_refresh=False):
    saved, err = read_token()
    if err:
        return None, err
    fresh = (not force_refresh and saved.get("access_token")
             and float(saved.get("expires_at") or 0) > time.time() + 60)
    if fresh:
        return saved["access_token"], None
    try:
        body = post_token({
            "grant_type": "refresh_token",
            "refresh_token": saved["refresh_token"],
        })
    except HttpStatus as exc:
        return None, yahoo_http_error(
            exc.status, exc.body, tokens=(saved.get("refresh_token"),)
        )
    updated = apply_token_response(saved, body)
    if updated is None:
        return None, yahoo_fail(
            "yahoo_token",
            "Yahoo's token response had no access token.",
        )
    write_token({
        "refresh_token": updated["refresh_token"],
        "access_token": updated["access_token"],
        "expires_at": updated["expires_at"],
    })
    return updated["access_token"], None


def yahoo_get(path):
    """Return ``(body, None)`` or ``(None, exit_code)``.

    The exit code path has already printed a JSON error.
    """
    url = yahoo_resource_url(path)
    if url is None:
        return None, yahoo_fail(
            "bad_path",
            "Path must be a fantasy/v2 resource, for example "
            "league/nfl.l.LEAGUE_ID/scoreboard.",
        )
    token, err = access_token()
    if err:
        return None, err
    try:
        body = http_json(url, headers={"Authorization": "Bearer " + token}, timeout=30)
    except HttpStatus as exc:
        if exc.status == 401:
            token, err = access_token(force_refresh=True)
            if err:
                return None, err
            try:
                body = http_json(
                    url, headers={"Authorization": "Bearer " + token}, timeout=30
                )
            except HttpStatus as again:
                return None, yahoo_http_error(
                    again.status, again.body, tokens=(token,)
                )
        else:
            return None, yahoo_http_error(exc.status, exc.body, tokens=(token,))
    return body, None


def cmd_yahoo_auth_url(redirect):
    redirect, err = redirect_uri_of(redirect)
    if err:
        return err
    client_id = yahoo_client_id()
    if not client_id:
        return yahoo_fail(
            "missing_client_id",
            "Set YAHOO_CLIENT_ID. Collect it with a secret request, not in chat.",
        )
    emit({
        "platform": "yahoo",
        "untested": True,
        "command": "auth-url",
        "authorization_url": auth_url(client_id, redirect),
        "note": YAHOO_NOTE,
    })
    return 0


def cmd_yahoo_connect(redirect, code):
    redirect, err = redirect_uri_of(redirect)
    if err:
        return err
    client_id = yahoo_client_id()
    client_secret = yahoo_client_secret()
    if not client_id or not client_secret:
        return yahoo_fail(
            "missing_client",
            "Set YAHOO_CLIENT_ID and YAHOO_CLIENT_SECRET. Collect the "
            "secret with a secret request, not in chat.",
        )
    code = (code or os.environ.get("YAHOO_AUTH_CODE") or "").strip()
    if not code:
        return yahoo_fail(
            "missing_auth_code",
            "Set --code or YAHOO_AUTH_CODE to the code from the redirect. "
            "Collect it with a secret request, not in chat.",
        )
    try:
        body = post_token({
            "grant_type": "authorization_code",
            "redirect_uri": redirect,
            "code": code,
        })
    except HttpStatus as exc:
        return yahoo_http_error(exc.status, exc.body, tokens=(code, client_secret))
    updated = apply_token_response(None, body)
    if updated is None:
        return yahoo_fail(
            "yahoo_token",
            "Yahoo's token response had no refresh token. "
            "The application may still be waiting on approval.",
        )
    path = write_token({
        "refresh_token": updated["refresh_token"],
        "access_token": updated["access_token"],
        "expires_at": updated["expires_at"],
    })
    emit({
        "platform": "yahoo",
        "untested": True,
        "command": "connect",
        "connected": True,
        "token_path": str(path),
        "expires_in": updated["expires_in"],
        "note": YAHOO_NOTE,
    })
    return 0


def cmd_yahoo_leagues():
    body, err = yahoo_get(YAHOO_LEAGUES)
    if err:
        return err
    emit({
        "platform": "yahoo",
        "untested": True,
        "command": "leagues",
        "data": body,
        "note": YAHOO_NOTE,
    })
    return 0


def cmd_yahoo_get(path):
    body, err = yahoo_get(path)
    if err:
        return err
    emit({
        "platform": "yahoo",
        "untested": True,
        "command": "get",
        "path": path,
        "data": body,
        "note": YAHOO_NOTE,
    })
    return 0


def run_yahoo(args):
    command = args.yahoo_cmd
    if command == "auth-url":
        return cmd_yahoo_auth_url(args.redirect_uri)
    if command == "connect":
        return cmd_yahoo_connect(args.redirect_uri, args.code)
    if command == "leagues":
        return cmd_yahoo_leagues()
    if command == "get":
        return cmd_yahoo_get(args.path)
    return yahoo_fail("unknown_command", "Unknown Yahoo command.")


# --- CLI -------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog="league_read",
        description=(
            "Read a fantasy league for the recommend-only bot. "
            "ESPN public leagues need only --league. Private ESPN leagues "
            "need ESPN_S2 and ESPN_SWID in the environment; those cookies "
            "are never printed. Yahoo is UNTESTED and approval-gated: the "
            "owner must be approved at https://sports.yahoo.com/developer/access/ "
            "before any Yahoo call works. This script never writes to a league. "
            "ESPN requests are GET only."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--league", required=True, help="ESPN league id")
    common.add_argument(
        "--season", type=int, default=None,
        help="season year (default: Sleeper NFL state, else ESPN)",
    )

    espn = sub.add_parser(
        "espn",
        help="read an ESPN league (public, or private with cookies)",
    )
    espn_sub = espn.add_subparsers(dest="espn_cmd", required=True)
    espn_sub.add_parser(
        "settings", parents=[common],
        help="name, size, week, scoring, roster slots, waivers, draft",
    )
    espn_sub.add_parser(
        "teams", parents=[common],
        help="team id, name, and owner ids",
    )
    roster = espn_sub.add_parser(
        "roster", parents=[common],
        help="one team's roster, projections, and bye weeks",
    )
    roster.add_argument("--team", type=int, default=None, help="ESPN team id")
    roster.add_argument("--week", type=int, default=None, help="scoring period")
    agents = espn_sub.add_parser(
        "free-agents", parents=[common],
        help="free agents and waivers, sorted by percent owned",
    )
    agents.add_argument(
        "--position", action="append", default=None,
        help="QB, RB, WR, TE, FLEX, K, or D/ST (repeatable; default is all)",
    )
    agents.add_argument("--limit", type=int, default=25)
    agents.add_argument("--week", type=int, default=None)
    matchup = espn_sub.add_parser(
        "matchup", parents=[common],
        help="one team's score and projection for a week",
    )
    matchup.add_argument("--team", type=int, default=None, help="ESPN team id")
    matchup.add_argument("--week", type=int, default=None)

    yahoo = sub.add_parser(
        "yahoo",
        help="Yahoo Fantasy (UNTESTED; app approval required)",
        description=(
            "UNTESTED. No Yahoo account was available when this was written. "
            "Yahoo gates Fantasy API access behind an application at "
            "https://sports.yahoo.com/developer/access/. The owner must be "
            "approved before any call works. Third-party reports say oob and "
            "localhost redirects are no longer accepted, so pass an https "
            "redirect URL the owner controls. Docs: "
            "https://sports.yahoo.com/developer/docs/ and "
            "https://developer.yahoo.com/oauth2/guide/flows_authcode/ ."
        ),
    )
    yahoo_sub = yahoo.add_subparsers(dest="yahoo_cmd", required=True)
    auth = yahoo_sub.add_parser(
        "auth-url",
        help="print the OAuth authorization URL (untested)",
    )
    auth.add_argument("--redirect-uri", default=None)
    connect = yahoo_sub.add_parser(
        "connect",
        help="exchange an auth code and store the refresh token (untested)",
    )
    connect.add_argument("--redirect-uri", default=None)
    connect.add_argument("--code", default=None, help="or set YAHOO_AUTH_CODE")
    yahoo_sub.add_parser(
        "leagues",
        help="NFL leagues for the connected Yahoo user (untested)",
    )
    get = yahoo_sub.add_parser(
        "get",
        help="GET a fantasy/v2 path as JSON (untested)",
    )
    get.add_argument(
        "path",
        help="resource under fantasysports.yahooapis.com/fantasy/v2",
    )
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.cmd == "espn":
            return run_espn(args)
        if args.cmd == "yahoo":
            return run_yahoo(args)
    except HttpStatus as exc:
        return fail(
            "http_error",
            redact("Request failed with HTTP {}.".format(exc.status or "error")),
        )
    except Exception as exc:
        return fail(
            "error",
            redact("{}: {}".format(type(exc).__name__, exc)),
        )
    return fail("unknown_command", "Unknown command.")


if __name__ == "__main__":
    sys.exit(main())
