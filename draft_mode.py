"""Draft mode for the Fantasy Football bot.

One standard-library script. It builds a tiered ADP cheat sheet, watches a
Sleeper draft, and grades the roster afterward. It never submits a pick.

    python draft_mode.py board --league LEAGUE_ID
    python draft_mode.py watch DRAFT_ID --user USERNAME
    python draft_mode.py grade DRAFT_ID --user USERNAME
    python draft_mode.py seed

Stdout is JSON. ``watch`` writes one JSON object per line.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import NormalDist

# Everything this script needs lives next to it, so bot/ can be copied
# into its own repo. League, draft, and user identity come from argv or
# the Sleeper API, never from this file.
HERE = Path(__file__).resolve().parent
SEED_BOARD = HERE / "seed_board.json"
DEFAULT_BOARD = HERE / "board.json"
PLAYER_CACHE = HERE / ".cache" / "players.json"

SLEEPER = "https://api.sleeper.app/v1"
FFC = "https://fantasyfootballcalculator.com/api/v1/adp"
FANTASYCALC = "https://api.fantasycalc.com/values/current"
UA = {"User-Agent": "draft-mode/1.0"}

# Feed ids reported on board/seed JSON and stored on a refreshed seed.
FEED_FFC = "fantasyfootballcalculator"
FEED_FANTASYCALC = "fantasycalc"
ATTRIBUTION = {
    FEED_FFC: "Fantasy Football Calculator (https://fantasyfootballcalculator.com)",
    FEED_FANTASYCALC: "FantasyCalc (https://fantasycalc.com)",
}
# FantasyCalc accepts these league sizes. Anything else snaps to the nearest.
FANTASYCALC_TEAMS = (8, 10, 12, 14)
DEFAULT_SEED_TEAMS = 12
DEFAULT_SEED_SCORING = "ppr"
DEFAULT_SEED_QBS = 1
# Extra keys on the first seed row. player_from_dict ignores them, so an
# older copy of this script can still load the file.
SEED_PROVENANCE = (
    "generated_at", "source", "source_url", "season", "teams", "scoring",
    "num_qbs",
)

# A full redraft board is ~250 names. Below this the feed is a fragment
# (in-season FFC windows do this) and must not replace a real sheet.
MIN_BOARD = 150
MAX_ADP = 220.0
SEASON_WEEKS = 17.0
FLEX_SLOTS = {"FLEX", "SUPER_FLEX", "SUPERFLEX", "REC_FLEX", "WRRB_FLEX"}
SKILL = {"QB", "RB", "WR", "TE", "K", "DEF"}

TEAM_ABBREV = {
    "seattle": "SEA", "denver": "DEN", "houston": "HOU", "la rams": "LAR",
    "minnesota": "MIN", "detroit": "DET", "new england": "NE",
    "philadelphia": "PHI", "pittsburgh": "PIT", "la chargers": "LAC",
    "ny jets": "NYJ", "san francisco": "SF", "jacksonville": "JAX",
    "green bay": "GB", "atlanta": "ATL", "cleveland": "CLE", "dallas": "DAL",
    "buffalo": "BUF", "baltimore": "BAL", "ny giants": "NYG", "chicago": "CHI",
    "cincinnati": "CIN", "tampa bay": "TB", "tennessee": "TEN",
    "kansas city": "KC", "new orleans": "NO", "washington": "WAS",
    "arizona": "ARI", "carolina": "CAR", "indianapolis": "IND",
    "miami": "MIA", "las vegas": "LV",
}
_SUFFIXES = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")
# Tier gap proportional to ADP, with a floor so the top of the board can break.
TIER_GAP_FRAC = 0.12
TIER_GAP_FLOOR = 0.8


class BoardTooThin(RuntimeError):
    pass


class DraftTypeError(RuntimeError):
    def __init__(self, draft_type, message):
        super().__init__(message)
        self.draft_type = draft_type


@dataclass
class Player:
    player_id: str
    name: str
    position: str
    team: str
    adp: float
    stdev: float
    bye: int
    injury_status: object
    tier: int


@dataclass
class Recommendation:
    player: Player
    score: float
    reason: str


# --- draft shape -----------------------------------------------------------

def unsupported_reason(draft_type):
    """Auction is a budget problem. Snake (with an optional reversal) and
    linear drafts are pick-order problems this script can watch."""
    if draft_type == "auction":
        return ("Auction drafts need a budget, not a ranked board. "
                "This script will not watch or grade one.")
    if draft_type in ("snake", "linear"):
        return None
    return ("Draft type {!r} is not supported. This script handles snake "
            "(including a reversal round) and linear drafts."
            .format(draft_type))


def my_pick_numbers(slot, teams, rounds, reversal_round=0, draft_type="snake"):
    """Overall pick numbers for one manager.

    Snake flips direction every round. ``reversal_round`` (Sleeper's
    3rd-round-reversal knob) skips that flip from that round on, so the
    extra reversal sticks and the snake continues from the new parity.
    Linear keeps the same slot every round. Reversal does not apply to it.
    """
    slot, teams, rounds = int(slot), int(teams), int(rounds)
    reversal_round = int(reversal_round or 0)
    if draft_type == "linear":
        return [(r - 1) * teams + slot for r in range(1, rounds + 1)]
    if draft_type != "snake":
        raise DraftTypeError(draft_type, unsupported_reason(draft_type))
    out = []
    for r in range(1, rounds + 1):
        forward = (r % 2 == 1)
        if reversal_round and r >= reversal_round:
            forward = not forward
        pos = slot if forward else teams - slot + 1
        out.append((r - 1) * teams + pos)
    return out


def phase_at(current, my_picks):
    """``two_out`` when one or two picks remain before his turn, ``on_clock``
    when the current pick is his. Anything else is silence.

    One pick out still counts as ``two_out``: two picks can land inside one
    poll, and the heads-up must not be skipped when they do."""
    upcoming = [p for p in my_picks if p >= current]
    if not upcoming:
        return None
    gap = upcoming[0] - current
    if gap == 0:
        return "on_clock"
    if gap in (1, 2):
        return "two_out"
    return None


def horizon_pick(current, my_picks, phase):
    """The pick the ranker measures survival against.

    Two picks out, the question is who survives until he is on the clock.
    On the clock, the question is who survives until the pick after this one.
    """
    upcoming = [p for p in my_picks if p >= current]
    if not upcoming:
        return None
    if phase == "two_out":
        return upcoming[0]
    if len(upcoming) > 1:
        return upcoming[1]
    return None


# --- cheat sheet -----------------------------------------------------------

def scoring_format(rec):
    """Map Sleeper's points-per-reception to an FFC ADP flavor."""
    rec = float(rec)
    if rec >= 0.75:
        return "ppr"
    if rec >= 0.25:
        return "half-ppr"
    return "standard"


def resolve_board(fetched, previous, minimum=MIN_BOARD):
    """Keep a full board when the feed comes back short or missing.

    Returns ``(board, status)`` with status ``refreshed`` or ``kept_previous``.
    """
    if fetched is not None and len(fetched) >= minimum:
        return list(fetched), "refreshed"
    if previous is not None and len(previous) >= minimum:
        return list(previous), "kept_previous"
    got = 0 if fetched is None else len(fetched)
    raise BoardTooThin(
        "ADP feed returned {} players and no board of at least {} is available"
        .format(got, minimum))


def load_board(path):
    rows = json.loads(Path(path).read_text())
    return [player_from_dict(r) for r in rows]


def save_board(players, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(p) for p in players], indent=2))


def player_from_dict(d):
    return Player(
        player_id=str(d["player_id"]),
        name=d["name"],
        position=d["position"],
        team=d.get("team") or "",
        adp=float(d["adp"]),
        stdev=max(float(d.get("stdev") or 0.0), 0.5),
        bye=int(d.get("bye") or 0),
        injury_status=d.get("injury_status"),
        tier=int(d.get("tier") or 0),
    )


def load_previous_board(out_path, seed_path=SEED_BOARD):
    """Best on-disk board: the bot's own file, else the 2026-09-04 seed."""
    out_path = Path(out_path)
    for path in (out_path, Path(seed_path)):
        if path.exists():
            board = load_board(path)
            if len(board) >= MIN_BOARD:
                return board, str(path)
    return None, None


def load_effective_board(path=None):
    """Board for watch/grade. An explicit path is used as given."""
    if path:
        board = load_board(path)
        return board, str(path)
    board, source = load_previous_board(DEFAULT_BOARD)
    if board is None:
        raise BoardTooThin("no cheat sheet at {} or {}".format(
            DEFAULT_BOARD, SEED_BOARD))
    return board, source


def normalize_name(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = s.lower().replace(".", "").replace("'", "").replace("-", " ")
    s = _SUFFIXES.sub("", s)
    return " ".join(s.split())


def ffc_key(name, position):
    pos = "K" if position == "PK" else position
    if pos == "DEF":
        city = normalize_name(name).replace(" defense", "").strip()
        return (TEAM_ABBREV.get(city, city.upper()), "DEF")
    return (normalize_name(name), pos)


def sleeper_index(players):
    idx = {}
    for pid, v in players.items():
        if not isinstance(v, dict):
            continue
        pos = v.get("position")
        if pos == "DEF":
            idx.setdefault((pid, "DEF"), pid)
            continue
        name = v.get("full_name") or v.get("last_name") or ""
        if name and pos:
            idx.setdefault((normalize_name(name), pos), pid)
    return idx


def join_adp(adp, players):
    idx = sleeper_index(players)
    known = set(str(k) for k in players.keys())
    matched, unmatched = [], []
    for row in adp:
        sid = row.get("sleeper_id")
        pid = str(sid) if sid and str(sid) in known else None
        if pid is None:
            pid = idx.get(ffc_key(row["name"], row["position"]))
        if pid:
            matched.append(dict(row, player_id=str(pid)))
        else:
            unmatched.append(row)
    return matched, unmatched


def assign_tiers(players):
    by_pos = {}
    for p in players:
        by_pos.setdefault(p.position, []).append(p)
    for group in by_pos.values():
        group.sort(key=lambda p: p.adp)
        tier = 1
        for i, p in enumerate(group):
            if i > 0:
                prev = group[i - 1].adp
                threshold = max(TIER_GAP_FLOOR, TIER_GAP_FRAC * prev)
                if (p.adp - prev) >= threshold:
                    tier += 1
            p.tier = tier


def assemble_board(adp_rows, sleeper_players):
    matched, unmatched = join_adp(adp_rows, sleeper_players)
    out = []
    for r in matched:
        meta = sleeper_players.get(r["player_id"], {}) or {}
        pos = "K" if r["position"] == "PK" else r["position"]
        if pos not in SKILL:
            continue
        out.append(Player(
            player_id=str(r["player_id"]),
            name=r["name"],
            position=pos,
            team=r.get("team") or meta.get("team") or "",
            adp=float(r["adp"]),
            stdev=max(float(r.get("stdev") or 0.0), 0.5),
            bye=int(r.get("bye") or 0),
            injury_status=meta.get("injury_status"),
            tier=0,
        ))
    assign_tiers(out)
    out.sort(key=lambda p: p.adp)
    return out, [u.get("name") for u in unmatched]


# --- ranker ----------------------------------------------------------------

def value_of(adp):
    """Better ADP is worth more. ADP already prices positional replacement,
    so this is not value-over-replacement."""
    return max(MAX_ADP - float(adp), 0.0)


def survival_prob(player, pick_no):
    """P(player is still on the board at pick_no), from a normal around ADP."""
    nd = NormalDist(player.adp, max(player.stdev, 0.5))
    return min(1.0, max(0.0, 1.0 - nd.cdf(float(pick_no))))


def eligible_positions(slot):
    if slot == "FLEX":
        return {"RB", "WR", "TE"}
    if slot in ("SUPER_FLEX", "SUPERFLEX"):
        return {"QB", "RB", "WR", "TE"}
    if slot == "REC_FLEX":
        return {"WR", "TE"}
    if slot == "WRRB_FLEX":
        return {"WR", "RB"}
    return {slot}


def _ordered_starters(roster_positions):
    starters = [s for s in roster_positions if s != "BN"]
    specific = [s for s in starters if s not in FLEX_SLOTS]
    flexes = [s for s in starters if s in FLEX_SLOTS]
    return specific + flexes


def slots_filled(players, roster_positions):
    remaining = list(players)
    filled = 0
    for slot in _ordered_starters(roster_positions):
        elig = eligible_positions(slot)
        idx = next((i for i, p in enumerate(remaining) if p.position in elig), None)
        if idx is not None:
            remaining.pop(idx)
            filled += 1
    return filled


def starter_total(roster_positions):
    return sum(1 for s in roster_positions if s != "BN")


def marginal_bye_holes(candidate, teammates, roster_positions):
    """How many extra starting slots this player's bye week would empty.

    Headcount does not matter. A fourth receiver on a bye the other receivers
    already cover costs nothing. A backup quarterback who shares his starter's
    bye costs the QB slot. The comparison is the same week with this player
    sitting versus playing, so a bye that only benches him when someone else
    already fills the slot is not a penalty.
    """
    if not candidate.bye:
        return 0
    bye = candidate.bye
    total = starter_total(roster_positions)

    def holes(available):
        return total - slots_filled(available, roster_positions)

    others = [p for p in teammates if p.bye != bye]
    return max(0, holes(others) - holes(others + [candidate]))


def bye_multiplier(holes):
    """One empty starting slot costs one week of a 17-week season."""
    if holes <= 0:
        return 1.0
    return max(0.80, 1.0 - (holes / SEASON_WEEKS))


def open_slots(roster_positions, roster):
    """Positions that fill an empty starting slot, split into dedicated
    slots (QB, RB, TE, K ...) and flex-only slots."""
    remaining = list(roster)
    dedicated, flex = set(), set()
    for slot in _ordered_starters(roster_positions):
        elig = eligible_positions(slot)
        idx = next((i for i, p in enumerate(remaining) if p.position in elig), None)
        if idx is not None:
            remaining.pop(idx)
        elif slot in FLEX_SLOTS:
            flex.update(elig)
        else:
            dedicated.update(elig)
    return dedicated, flex


def open_dedicated_count(roster_positions, roster):
    """How many dedicated (non-flex) starting slots are still empty."""
    remaining = list(roster)
    empty = 0
    for slot in _ordered_starters(roster_positions):
        if slot in FLEX_SLOTS:
            continue
        idx = next((i for i, p in enumerate(remaining) if p.position == slot), None)
        if idx is None:
            empty += 1
        else:
            remaining.pop(idx)
    return empty


def position_starter_counts(roster_positions):
    counts = {}
    for slot in roster_positions:
        if slot == "BN" or slot in FLEX_SLOTS:
            continue
        counts[slot] = counts.get(slot, 0) + 1
    return counts


# Need is a nudge, not a doubling. Timing comes from ``urgency`` (value lost
# by waiting). A flat 2x for any empty slot made the 2026-09-04 engine push
# QBs and TEs 30 picks early that were 80-96% likely to still be there.
NEED_DEDICATED = 1.15
NEED_FLEX = 1.05
# Kickers and defenses wait until this many of his picks are left.
LATE_ONLY = {"K", "DEF"}
LATE_ONLY_PICKS = 3
# Bench depth beyond starters. A third QB or a second kicker is dead weight.
EXTRA_ALLOWED = {"QB": 1, "TE": 1, "K": 0, "DEF": 0}
# Hard avoid. These players cannot play now. ``Questionable`` and
# ``Doubtful`` are shown but not filtered: at draft time Questionable covered
# 46 of 264 players, including first-rounders.
AVOID_STATUS = {"Out", "IR", "PUP", "NA", "Sus", "COV", "DNR"}
# A player this many picks past ADP is falling for a reason the ADP does not
# know. Flag it so a human (or the bot) checks the news before taking him.
FALLING_PICKS = 20


def avoided(player):
    return (player.injury_status or "") in AVOID_STATUS


def position_capped(position, my_players, roster_positions):
    if position not in EXTRA_ALLOWED:
        return False
    starters = position_starter_counts(roster_positions).get(position, 0)
    have = sum(1 for p in my_players if p.position == position)
    if position == "QB" and any(s in ("SUPER_FLEX", "SUPERFLEX")
                                for s in roster_positions):
        starters += 1
    return have >= starters + EXTRA_ALLOWED[position]


def expected_value_later(available_at_pos, next_pick):
    """Expected value of the best player at this position still available
    at ``next_pick``. Waiting is cheap when this number is close to the
    player being scored."""
    if next_pick is None:
        return 0.0
    group = sorted(available_at_pos, key=lambda p: -value_of(p.adp))
    exp, none_yet = 0.0, 1.0
    for p in group:
        s = survival_prob(p, next_pick)
        exp += value_of(p.adp) * s * none_yet
        none_yet *= (1.0 - s)
        if none_yet < 1e-6:
            break
    return exp


def tier_lift(player, available, cap):
    """Bonus for the best player still in a tier when the next option at
    his position is a tier down (or gone).

    Earlier players in the same tier get nothing from this — if someone
    better at the position is available, he is the pick, and a lift on the
    last name in the tier would jump him unfairly. The cap is one round
    of ADP (the team count): a cliff is worth up to one full round, not
    the whole gap.
    """
    same = [p for p in available
            if p.position == player.position and p.player_id != player.player_id]
    if any(p.adp < player.adp for p in same):
        return 0.0
    later = sorted((p for p in same if p.adp > player.adp), key=lambda p: p.adp)
    if not later:
        return float(cap)
    nxt = later[0]
    if nxt.tier <= player.tier:
        return 0.0
    return min(float(cap), max(0.0, nxt.adp - player.adp))


def _reason(player, current_pick, next_pick, lift, need, holes):
    bits = ["ADP {:.1f}".format(player.adp)]
    if current_pick:
        delta = current_pick - player.adp
        if delta >= FALLING_PICKS:
            bits.append("falling {:.0f} past ADP, check news".format(delta))
        elif delta <= -10:
            bits.append("reach of {:.0f}".format(-delta))
    if lift > 0:
        bits.append("last in tier {}".format(player.tier))
    if next_pick is not None:
        bits.append("{:.0f}% left at {}".format(
            survival_prob(player, next_pick) * 100, next_pick))
    if need == "dedicated":
        bits.append("fills {}".format(player.position))
    elif need == "flex":
        bits.append("fills flex")
    if holes > 0:
        bits.append("bye {} empties {}".format(player.bye, holes))
    return "; ".join(bits)


def recommend(board, taken, my_players, roster_positions, current_pick,
              next_pick, teams, top_n=8, picks_left=None):
    """Top available players for the pick he is about to make.

    ``next_pick`` is the horizon: his upcoming pick when he is two out, or
    the pick after this one when he is on the clock. Survival uses that
    horizon. ``picks_left`` counts his picks from this one on; when it is
    given, kickers and defenses wait for the last rounds and the final
    picks are forced into empty starting slots.
    """
    taken = set(str(t) for t in taken)
    available = [p for p in board
                 if p.player_id not in taken and not avoided(p)]
    dedicated, flex = open_slots(roster_positions, my_players)
    if picks_left is not None:
        empty = open_dedicated_count(roster_positions, my_players)
        if 0 < empty and picks_left <= empty:
            only = [p for p in available if p.position in dedicated]
            if only:
                available = only
        available = [
            p for p in available
            if p.position not in LATE_ONLY
            or picks_left <= LATE_ONLY_PICKS
            or not any(q.position not in LATE_ONLY for q in available)]
    available = [p for p in available
                 if not position_capped(p.position, my_players, roster_positions)]
    by_pos = {}
    for p in available:
        by_pos.setdefault(p.position, []).append(p)
    later = dict((pos, expected_value_later(group, next_pick))
                 for pos, group in by_pos.items())

    cap = max(1, int(teams))
    recs = []
    for p in available:
        if p.position in dedicated:
            need, need_m = "dedicated", NEED_DEDICATED
        elif p.position in flex:
            need, need_m = "flex", NEED_FLEX
        else:
            need, need_m = None, 1.0
        val = value_of(p.adp)
        urgency = val - later.get(p.position, 0.0)
        lift = tier_lift(p, available, cap)
        holes = marginal_bye_holes(p, my_players, roster_positions)
        score = (val + urgency + lift) * need_m * bye_multiplier(holes)
        recs.append(Recommendation(
            player=p, score=score,
            reason=_reason(p, current_pick, next_pick, lift, need, holes)))
    recs.sort(key=lambda r: (-r.score, r.player.adp, r.player.name))
    return recs[:top_n]


# --- grade -----------------------------------------------------------------

def _lineup(players, roster_positions):
    remaining = list(players)
    filled, holes = [], []
    for slot in _ordered_starters(roster_positions):
        elig = eligible_positions(slot)
        idx = next((i for i, p in enumerate(remaining) if p.position in elig), None)
        if idx is None:
            holes.append(slot)
            continue
        p = remaining.pop(idx)
        filled.append({"slot": slot, "player_id": p.player_id, "name": p.name,
                       "position": p.position, "bye": p.bye})
    return filled, holes


def grade_picks(board, picks, slot, roster_positions):
    """Value is pick number minus ADP. Positive means he drafted the player
    later than the market (a steal). Negative means he reached."""
    by_id = dict((p.player_id, p) for p in board)
    mine = [p for p in picks
            if p.get("draft_slot") == slot and p.get("player_id")]
    mine.sort(key=lambda p: p.get("pick_no") or 0)
    rows = []
    roster = []
    for p in mine:
        pid = str(p["player_id"])
        pl = by_id.get(pid)
        meta = p.get("metadata") or {}
        fallback = "{} {}".format(meta.get("first_name") or "",
                                  meta.get("last_name") or "").strip()
        if pl is None:
            rows.append({"pick_no": p.get("pick_no"), "round": p.get("round"),
                         "player_id": pid, "name": fallback or pid,
                         "value": None, "on_board": False})
            continue
        value = round(p["pick_no"] - pl.adp, 2)
        rows.append({
            "pick_no": p.get("pick_no"), "round": p.get("round"),
            "player_id": pid, "name": pl.name, "position": pl.position,
            "team": pl.team, "adp": pl.adp, "value": value, "on_board": True,
        })
        roster.append(pl)
    valued = [r for r in rows if r["value"] is not None]
    by_pos = {}
    for pl in roster:
        by_pos[pl.position] = by_pos.get(pl.position, 0) + 1
    filled, holes = _lineup(roster, roster_positions)
    steal = max(valued, key=lambda r: (r["value"], -r["pick_no"])) if valued else None
    reach = min(valued, key=lambda r: (r["value"], r["pick_no"])) if valued else None
    return {
        "value_definition": "pick_no - adp; positive is a steal, negative is a reach",
        "picks": rows,
        "total_value": round(sum(r["value"] for r in valued), 2) if valued else 0,
        "picks_graded": len(valued),
        "starters": filled,
        "holes": holes,
        "by_position": by_pos,
        "biggest_steal": steal,
        "biggest_reach": reach,
    }


# --- network ---------------------------------------------------------------

def fetch_json(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def fetch_user(username):
    quoted = urllib.parse.quote(username)
    try:
        user = fetch_json("{}/user/{}".format(SLEEPER, quoted))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise LookupError("no Sleeper user {!r}".format(username))
        raise
    if not user or not user.get("user_id"):
        raise LookupError("no Sleeper user {!r}".format(username))
    return user


def fetch_players(cache_path=PLAYER_CACHE, max_age_hours=12):
    cache_path = Path(cache_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.exists():
        age = time.time() - cache_path.stat().st_mtime
        if age < max_age_hours * 3600:
            return json.loads(cache_path.read_text())
    data = fetch_json(SLEEPER + "/players/nfl", timeout=180)
    cache_path.write_text(json.dumps(data))
    return data


def emit(obj):
    print(json.dumps(obj), flush=True)


def _league_teams(league):
    if league.get("total_rosters"):
        return int(league["total_rosters"])
    return int((league.get("settings") or {})["num_teams"])


def _draft_settings(draft):
    s = draft.get("settings") or {}
    return {
        "teams": int(s["teams"]),
        "rounds": int(s["rounds"]),
        "reversal_round": int(s.get("reversal_round") or 0),
        "pick_timer": int(s.get("pick_timer") or 0),
    }


def _slot_for(draft, user_id):
    order = draft.get("draft_order") or {}
    slot = order.get(user_id)
    if slot is None:
        slot = order.get(str(user_id))
    return None if slot is None else int(slot)


# --- rankings feeds --------------------------------------------------------

def calendar_year():
    return datetime.now(timezone.utc).year


def current_season():
    """Sleeper's NFL season, or the UTC calendar year if that call fails."""
    try:
        state = fetch_json(SLEEPER + "/state/nfl", timeout=30)
        return int(state["season"])
    except Exception:
        return calendar_year()


def num_qbs_for(roster_positions):
    for slot in roster_positions or []:
        if slot in ("SUPER_FLEX", "SUPERFLEX"):
            return 2
    return 1


def ffc_url(fmt, teams, year):
    return "{}/{}?teams={}&year={}".format(FFC, fmt, int(teams), int(year))


def ppr_param(fmt):
    value = {"ppr": 1, "half-ppr": 0.5, "standard": 0}[fmt]
    if value == 0.5:
        return "0.5"
    return str(int(value))


def nearest_fantasycalc_teams(teams):
    teams = int(teams)
    if teams in FANTASYCALC_TEAMS:
        return teams
    return min(FANTASYCALC_TEAMS, key=lambda n: (abs(n - teams), -n))


def fantasycalc_url(fmt, teams, num_qbs):
    qbs = 2 if int(num_qbs) >= 2 else 1
    return ("{}?isDynasty=false&numQbs={}&numTeams={}&ppr={}"
            .format(FANTASYCALC, qbs, nearest_fantasycalc_teams(teams),
                    ppr_param(fmt)))


def fetch_ffc_rows(fmt, teams, year):
    body = fetch_json(ffc_url(fmt, teams, year), timeout=60)
    if not isinstance(body, dict):
        raise ValueError("fantasyfootballcalculator response was not an object")
    rows = body.get("players") or []
    if not isinstance(rows, list):
        raise ValueError("fantasyfootballcalculator players was not a list")
    return rows


def fetch_fantasycalc_rows(fmt, teams, year, num_qbs=DEFAULT_SEED_QBS,
                           now_year=None):
    """Current redraft ranks. FantasyCalc has no season argument.

    ``overallRank`` is the consensus order (their ADP field is empty on this
    endpoint). Bye week is not in the payload. Skill positions only.
    """
    now_year = calendar_year() if now_year is None else int(now_year)
    if int(year) != now_year:
        raise LookupError(
            "fantasycalc has no historical season; skipped year {}"
            .format(year))
    body = fetch_json(fantasycalc_url(fmt, teams, num_qbs), timeout=60)
    if not isinstance(body, list):
        raise ValueError("fantasycalc response was not a list")
    rows = []
    for item in body:
        if not isinstance(item, dict):
            continue
        player = item.get("player") or {}
        name = player.get("name") or ""
        pos = player.get("position")
        if not name or pos not in SKILL:
            continue
        try:
            rank = float(item.get("overallRank"))
        except (TypeError, ValueError):
            continue
        if rank <= 0:
            continue
        row = {
            "name": name,
            "position": pos,
            "team": player.get("maybeTeam") or "",
            "adp": rank,
        }
        sid = player.get("sleeperId")
        if sid:
            row["sleeper_id"] = str(sid)
        rows.append(row)
    return rows


def _attempt(feed, url, fetched=0, matched=None, error=None):
    out = {"feed": feed, "url": url, "fetched": fetched}
    if matched is not None:
        out["matched"] = matched
    if error:
        out["error"] = error
    return out


def pull_live_board(fmt, teams, year, minimum=MIN_BOARD,
                    num_qbs=DEFAULT_SEED_QBS, now_year=None, players=None,
                    feeds=(FEED_FFC, FEED_FANTASYCALC)):
    """Try the named feeds in order (default: Fantasy Football Calculator,
    then FantasyCalc).

    Returns ``(board, unmatched, feed, source_url, attempts)``. ``board`` is
    ``None`` when neither feed produced at least ``minimum`` matched players.
    The Sleeper player list is downloaded only after a feed clears the raw
    row floor.
    """
    sources = (
        (FEED_FFC, ffc_url(fmt, teams, year),
         lambda: fetch_ffc_rows(fmt, teams, year)),
        (FEED_FANTASYCALC, fantasycalc_url(fmt, teams, num_qbs),
         lambda: fetch_fantasycalc_rows(fmt, teams, year, num_qbs, now_year)),
    )
    attempts = []
    for name, url, fetch_rows in sources:
        if name not in feeds:
            continue
        try:
            rows = fetch_rows()
        except Exception as e:
            attempts.append(_attempt(name, url, error=str(e)))
            continue
        raw = len(rows)
        if raw < minimum:
            attempts.append(_attempt(name, url, fetched=raw, error="fragment"))
            continue
        try:
            if players is None:
                players = fetch_players()
            board, unmatched = assemble_board(rows, players)
        except Exception as e:
            attempts.append(_attempt(name, url, fetched=raw, error=str(e)))
            continue
        if len(board) < minimum:
            attempts.append(_attempt(name, url, fetched=raw, matched=len(board),
                                     error="fragment"))
            continue
        attempts.append(_attempt(name, url, fetched=raw, matched=len(board)))
        return board, unmatched, name, url, attempts
    return None, [], None, None, attempts


def board_fingerprint(players):
    return [
        (p.player_id, p.name, p.position, p.team, float(p.adp), float(p.stdev),
         int(p.bye), int(p.tier), p.injury_status)
        for p in players
    ]


def save_seed(players, path, meta):
    """Write a seed list. Provenance sits on the first row only."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [asdict(p) for p in players]
    if rows and meta:
        first = dict(rows[0])
        for key in SEED_PROVENANCE:
            if key in meta and meta[key] is not None:
                first[key] = meta[key]
        rows[0] = first
    text = json.dumps(rows, indent=2) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


# --- commands --------------------------------------------------------------

def _ffc_attempt(attempts):
    for attempt in attempts:
        if attempt.get("feed") == FEED_FFC:
            return attempt
    return {}


def cmd_board(league_id, out_path, seed_path=SEED_BOARD, minimum=MIN_BOARD):
    previous, previous_source = load_previous_board(out_path, seed_path)
    league = fetch_json("{}/league/{}".format(SLEEPER, league_id))
    scoring = league.get("scoring_settings") or {}
    if "rec" not in scoring:
        emit({"event": "error",
              "message": "league {} has no scoring_settings.rec".format(league_id)})
        return 1
    fmt = scoring_format(scoring["rec"])
    teams = _league_teams(league)
    year = int(league["season"])
    num_qbs = num_qbs_for(league.get("roster_positions"))
    # FantasyCalc has no stdev, bye, kicker, or defense. Survival odds and
    # the bye rule go blind on it, so any full board on disk beats it.
    board_live, unmatched_names, feed, _url, attempts = pull_live_board(
        fmt, teams, year, minimum, num_qbs, feeds=(FEED_FFC,))
    if board_live is None and previous is None:
        board_live, unmatched_names, feed, _url, more = pull_live_board(
            fmt, teams, year, minimum, num_qbs, feeds=(FEED_FANTASYCALC,))
        attempts += more
    ffc_try = _ffc_attempt(attempts)
    try:
        board, status = resolve_board(board_live, previous, minimum)
    except BoardTooThin as e:
        emit({"event": "error", "message": str(e),
              "feed_error": ffc_try.get("error"),
              "fetched": ffc_try.get("fetched", 0),
              "attempts": attempts})
        return 1
    wrote = False
    if status == "refreshed" or not Path(out_path).exists():
        save_board(board, out_path)
        wrote = True
    source = str(out_path) if status == "refreshed" else previous_source
    if status == "refreshed":
        fetched_n = next(a["fetched"] for a in attempts if a.get("feed") == feed
                         and not a.get("error"))
        matched = len(board)
        used_feed = feed
    else:
        fetched_n = ffc_try.get("fetched", 0)
        matched = ffc_try.get("matched")
        used_feed = None
    event = {
        "event": "board",
        "status": status,
        "league_id": str(league_id),
        "season": year,
        "teams": teams,
        "scoring": fmt,
        "rec": scoring["rec"],
        "fetched": fetched_n,
        "matched": matched,
        "unmatched": (unmatched_names or [])[:20],
        "kept": len(board),
        "source": source,
        "wrote": str(out_path) if wrote else None,
        "seed": str(seed_path),
        "feed": used_feed,
        "attempts": attempts,
    }
    if used_feed:
        event["attribution"] = ATTRIBUTION.get(used_feed)
    ffc_error = ffc_try.get("error")
    if ffc_error and ffc_error != "fragment" and status != "refreshed":
        event["feed_error"] = ffc_error
    if status == "kept_previous":
        event["warning"] = (
            "Feed returned {} players (minimum {}). Kept the {}-player board "
            "at {}."
            .format(fetched_n, minimum, len(board), previous_source))
    emit(event)
    return 0


def cmd_seed(out_path, teams=DEFAULT_SEED_TEAMS, scoring=DEFAULT_SEED_SCORING,
             year=None, minimum=MIN_BOARD, num_qbs=DEFAULT_SEED_QBS):
    """Rebuild the shipped seed. A short or failed feed leaves the file alone."""
    if year is None:
        year = current_season()
    year = int(year)
    teams = int(teams)
    num_qbs = int(num_qbs)
    path = Path(out_path)
    existing = None
    if path.exists():
        try:
            loaded = load_board(path)
        except Exception:
            loaded = None
        if loaded and len(loaded) >= minimum:
            existing = loaded
    before = path.read_bytes() if path.exists() else None
    # The seed ships to every user. Only FFC carries stdev and bye weeks,
    # so a FantasyCalc day must never replace it.
    board, _unmatched, feed, source_url, attempts = pull_live_board(
        scoring, teams, year, minimum, num_qbs, feeds=(FEED_FFC,))
    if board is None or len(board) < minimum:
        emit({
            "event": "seed",
            "status": "kept",
            "season": year,
            "teams": teams,
            "scoring": scoring,
            "num_qbs": num_qbs,
            "feed": None,
            "kept": 0 if existing is None else len(existing),
            "wrote": None,
            "minimum": minimum,
            "attempts": attempts,
            "message": (
                "Refusing to replace the seed. No feed matched at least {} "
                "players."
                .format(minimum)),
        })
        if before is not None and path.exists():
            # pull_live_board must not have touched the seed. Check anyway.
            if path.read_bytes() != before:
                path.write_bytes(before)
        return 1
    if existing is not None and board_fingerprint(existing) == board_fingerprint(board):
        emit({
            "event": "seed",
            "status": "unchanged",
            "season": year,
            "teams": teams,
            "scoring": scoring,
            "num_qbs": num_qbs,
            "feed": feed,
            "kept": len(existing),
            "wrote": None,
            "minimum": minimum,
            "attempts": attempts,
            "attribution": ATTRIBUTION.get(feed),
        })
        return 0
    meta = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": feed,
        "source_url": source_url,
        "season": year,
        "teams": teams,
        "scoring": scoring,
        "num_qbs": num_qbs,
    }
    save_seed(board, path, meta)
    emit({
        "event": "seed",
        "status": "refreshed",
        "season": year,
        "teams": teams,
        "scoring": scoring,
        "num_qbs": num_qbs,
        "feed": feed,
        "kept": len(board),
        "wrote": str(path),
        "minimum": minimum,
        "attempts": attempts,
        "attribution": ATTRIBUTION.get(feed),
        "generated_at": meta["generated_at"],
    })
    return 0


def _context(draft_id, username):
    user = fetch_user(username)
    draft = fetch_json("{}/draft/{}".format(SLEEPER, draft_id))
    kind = draft.get("type")
    reason = unsupported_reason(kind)
    if reason:
        raise DraftTypeError(kind, reason)
    league_id = draft.get("league_id")
    if not league_id:
        raise LookupError("draft {} has no league_id".format(draft_id))
    league = fetch_json("{}/league/{}".format(SLEEPER, league_id))
    settings = _draft_settings(draft)
    slot = _slot_for(draft, user["user_id"])
    roster_positions = list(league.get("roster_positions") or [])
    return {
        "user": user,
        "draft": draft,
        "league": league,
        "league_id": str(league_id),
        "draft_type": kind,
        "slot": slot,
        "settings": settings,
        "roster_positions": roster_positions,
        "my_picks": (
            my_pick_numbers(slot, settings["teams"], settings["rounds"],
                            settings["reversal_round"], kind)
            if slot is not None else None),
    }


def _status_event(ctx, board_source, board_size, poll, replay):
    s = ctx["settings"]
    return {
        "event": "status",
        "draft_id": str(ctx["draft"].get("draft_id")),
        "league_id": ctx["league_id"],
        "user": ctx["user"].get("username"),
        "user_id": ctx["user"].get("user_id"),
        "draft_type": ctx["draft_type"],
        "reversal_round": s["reversal_round"],
        "teams": s["teams"],
        "rounds": s["rounds"],
        "pick_timer": s["pick_timer"],
        "slot": ctx["slot"],
        "your_picks": ctx["my_picks"],
        "board_source": board_source,
        "board_size": board_size,
        "poll_seconds": poll,
        "replay": replay,
        "submits_picks": False,
    }


def _recs_event(phase, current, your_pick, recs, horizon=None, changed=None):
    event = {
        "event": "recommendations",
        "phase": phase,
        "current_pick": current,
        "your_pick": your_pick,
        "picks_away": your_pick - current,
        "horizon": horizon,
        "top": [{
            "player_id": r.player.player_id,
            "name": r.player.name,
            "position": r.player.position,
            "team": r.player.team,
            "adp": r.player.adp,
            "tier": r.player.tier,
            "bye": r.player.bye,
            "injury_status": r.player.injury_status,
            "score": round(r.score, 1),
            "survival": (None if horizon is None
                         else round(survival_prob(r.player, horizon), 2)),
            "value_vs_pick": round(your_pick - r.player.adp, 1),
            "falling": (current - r.player.adp) >= FALLING_PICKS,
            "reason": r.reason,
        } for r in recs],
    }
    if changed:
        event["changed_from"] = changed
    return event


def plan_change(previous_top, recs, taken):
    """Narrate a reversal: the two-out pick is no longer the on-clock pick.

    ``previous_top`` is the player id recommended first at two out. Returns
    ``None`` when the plan held."""
    if not previous_top or not recs:
        return None
    pid, name = previous_top
    if recs[0].player.player_id == pid:
        return None
    return {"player_id": pid, "name": name,
            "why": "taken" if pid in taken else "outscored"}


def advise(board, prior_picks, slot, roster_positions, my_picks, current, teams,
           top_n=8):
    phase = phase_at(current, my_picks)
    your_pick = next(p for p in my_picks if p >= current)
    horizon = horizon_pick(current, my_picks, phase)
    picks_left = sum(1 for p in my_picks if p >= your_pick)
    taken = [str(p["player_id"]) for p in prior_picks if p.get("player_id")]
    mine = [p for p in prior_picks
            if p.get("draft_slot") == slot and p.get("player_id")]
    mine.sort(key=lambda p: p.get("pick_no") or 0)
    by_id = dict((p.player_id, p) for p in board)
    my_players = [by_id[str(p["player_id"])] for p in mine
                  if str(p["player_id"]) in by_id]
    recs = recommend(board, taken, my_players, roster_positions, your_pick,
                     horizon, teams, top_n=top_n, picks_left=picks_left)
    return phase, your_pick, recs, horizon


class Narrator:
    """Speaks once per (pick, phase), and says so when the plan changes."""

    def __init__(self, board, slot, roster_positions, my_picks, teams, top_n):
        self.args = (board, slot, roster_positions, my_picks, teams)
        self.top_n = top_n
        self.announced = set()
        self.plan = {}

    def step(self, picks, current):
        board, slot, roster_positions, my_picks, teams = self.args
        phase = phase_at(current, my_picks)
        if not phase:
            return None
        your_pick = next(p for p in my_picks if p >= current)
        if (your_pick, phase) in self.announced:
            return None
        self.announced.add((your_pick, phase))
        prior = [p for p in picks if (p.get("pick_no") or 0) < current]
        _, your_pick, recs, horizon = advise(
            board, prior, slot, roster_positions, my_picks, current, teams,
            self.top_n)
        changed = None
        if phase == "on_clock":
            taken = set(str(p["player_id"]) for p in prior if p.get("player_id"))
            changed = plan_change(self.plan.get(your_pick), recs, taken)
        elif recs:
            self.plan[your_pick] = (recs[0].player.player_id, recs[0].player.name)
        return _recs_event(phase, current, your_pick, recs, horizon, changed)


def iter_replay(board, picks, slot, roster_positions, my_picks, teams, rounds,
                top_n=8):
    """Walk a finished (or partial) pick list the way the live watcher would
    have spoken, without sleeping."""
    narrator = Narrator(board, slot, roster_positions, my_picks, teams, top_n)
    for current in range(1, teams * rounds + 1):
        event = narrator.step(picks, current)
        if event:
            yield event


def refresh_injuries(board, players):
    """Board with ``injury_status`` taken from today's Sleeper player list.

    The shipped seed carries the flags from the day it was built, which are
    stale a week later and wrong a season later."""
    out = []
    for p in board:
        meta = players.get(p.player_id)
        if isinstance(meta, dict):
            p = Player(**dict(asdict(p), injury_status=meta.get("injury_status")))
        out.append(p)
    return out


def cmd_watch(draft_id, username, poll, board_path=None, replay=False, top_n=8,
              wait_seconds=3600):
    try:
        ctx = _context(draft_id, username)
        deadline = time.time() + wait_seconds
        waiting = False
        # The draft order is set shortly before the draft starts. Starting
        # early is normal, so wait for it instead of failing.
        while (ctx["slot"] is None and not replay
               and ctx["draft"].get("status") == "pre_draft"
               and time.time() < deadline):
            if not waiting:
                emit({"event": "waiting", "draft_status": "pre_draft",
                      "message": "Draft order is not set yet. Waiting."})
                waiting = True
            time.sleep(poll)
            ctx = _context(draft_id, username)
    except DraftTypeError as e:
        emit({"event": "refused", "draft_type": e.draft_type, "message": str(e)})
        return 2
    except LookupError as e:
        emit({"event": "error", "message": str(e)})
        return 1
    if ctx["slot"] is None:
        emit({"event": "error", "message": (
            "user {} is not in the draft order for {} (status={})"
            .format(username, draft_id, ctx["draft"].get("status")))})
        return 1
    try:
        board, source = load_effective_board(board_path)
    except BoardTooThin as e:
        emit({"event": "error", "message": str(e)})
        return 1
    if not replay:
        try:
            board = refresh_injuries(board, fetch_players(max_age_hours=1))
        except Exception as e:
            emit({"event": "warning",
                  "message": "injury refresh failed, using board flags: {}"
                  .format(e)})
    emit(_status_event(ctx, source, len(board), poll, replay))
    s = ctx["settings"]
    if replay:
        picks = fetch_json("{}/draft/{}/picks".format(SLEEPER, draft_id), timeout=30)
        for event in iter_replay(board, picks, ctx["slot"], ctx["roster_positions"],
                                 ctx["my_picks"], s["teams"], s["rounds"], top_n):
            emit(event)
        emit({"event": "complete", "picks": len(picks), "replay": True})
        return 0

    narrator = Narrator(board, ctx["slot"], ctx["roster_positions"],
                        ctx["my_picks"], s["teams"], top_n)
    total = s["teams"] * s["rounds"]
    while True:
        try:
            picks = fetch_json("{}/draft/{}/picks".format(SLEEPER, draft_id),
                               timeout=15)
        except Exception as e:
            emit({"event": "poll_error", "message": str(e)})
            time.sleep(poll)
            continue
        if len(picks) >= total:
            emit({"event": "complete", "picks": len(picks),
                  "teams": s["teams"], "rounds": s["rounds"]})
            return 0
        event = narrator.step(picks, len(picks) + 1)
        if event:
            emit(event)
        time.sleep(poll)


def cmd_grade(draft_id, username, board_path=None):
    try:
        ctx = _context(draft_id, username)
    except DraftTypeError as e:
        emit({"event": "refused", "draft_type": e.draft_type, "message": str(e)})
        return 2
    except LookupError as e:
        emit({"event": "error", "message": str(e)})
        return 1
    if ctx["slot"] is None:
        emit({"event": "error", "message": (
            "user {} is not in the draft order for {}"
            .format(username, draft_id))})
        return 1
    try:
        board, source = load_effective_board(board_path)
    except BoardTooThin as e:
        emit({"event": "error", "message": str(e)})
        return 1
    picks = fetch_json("{}/draft/{}/picks".format(SLEEPER, draft_id), timeout=30)
    summary = grade_picks(board, picks, ctx["slot"], ctx["roster_positions"])
    s = ctx["settings"]
    summary.update({
        "event": "grade",
        "draft_id": str(draft_id),
        "league_id": ctx["league_id"],
        "user": ctx["user"].get("username"),
        "slot": ctx["slot"],
        "draft_type": ctx["draft_type"],
        "reversal_round": s["reversal_round"],
        "draft_status": ctx["draft"].get("status"),
        "board_source": source,
        "board_size": len(board),
    })
    emit(summary)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="draft_mode")
    sub = parser.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("board", help="build or keep the ADP cheat sheet")
    b.add_argument("--league", required=True)
    b.add_argument("--out", default=str(DEFAULT_BOARD))
    b.add_argument("--seed", default=str(SEED_BOARD))
    b.add_argument("--min-players", type=int, default=MIN_BOARD)

    w = sub.add_parser("watch", help="poll a draft and print top-3 JSON lines")
    w.add_argument("draft_id")
    w.add_argument("--user", required=True, help="Sleeper username")
    w.add_argument("--poll", type=float, default=5.0)
    w.add_argument("--top", type=int, default=8,
                   help="how many players each recommendation lists")
    w.add_argument("--board", default=None)
    w.add_argument("--replay", action="store_true",
                   help="walk the pick list once instead of polling")

    g = sub.add_parser("grade", help="post-draft value, starters, reach, steal")
    g.add_argument("draft_id")
    g.add_argument("--user", required=True, help="Sleeper username")
    g.add_argument("--board", default=None)

    s = sub.add_parser(
        "seed",
        help="refresh seed_board.json from the rankings feeds")
    s.add_argument("--out", default=str(SEED_BOARD))
    s.add_argument("--teams", type=int, default=DEFAULT_SEED_TEAMS,
                   help="default 12")
    s.add_argument("--scoring", default=DEFAULT_SEED_SCORING,
                   choices=("ppr", "half-ppr", "standard"),
                   help="default ppr")
    s.add_argument("--year", type=int, default=None,
                   help="default is Sleeper's current NFL season")
    s.add_argument("--num-qbs", type=int, default=DEFAULT_SEED_QBS,
                   help="1, or 2 for superflex; default 1")
    s.add_argument("--min-players", type=int, default=MIN_BOARD)

    args = parser.parse_args(argv)
    if args.cmd == "board":
        return cmd_board(args.league, args.out, args.seed, args.min_players)
    if args.cmd == "seed":
        if args.teams < 1 or args.num_qbs < 1:
            emit({"event": "error",
                  "message": "--teams and --num-qbs must be positive"})
            return 1
        return cmd_seed(args.out, args.teams, args.scoring, args.year,
                        args.min_players, args.num_qbs)
    if args.cmd == "watch":
        if args.poll <= 0:
            emit({"event": "error", "message": "--poll must be positive"})
            return 1
        if args.top < 1:
            emit({"event": "error", "message": "--top must be positive"})
            return 1
        return cmd_watch(args.draft_id, args.user, args.poll, args.board,
                         args.replay, args.top)
    if args.cmd == "grade":
        return cmd_grade(args.draft_id, args.user, args.board)
    emit({"event": "error", "message": "unknown command"})
    return 1


if __name__ == "__main__":
    sys.exit(main())
