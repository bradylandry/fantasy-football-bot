#!/usr/bin/env bash
# Proof run for the fantasy football bot CLI. See ../SKILL.md.
# Leaves /tmp/verify-fantasy-football-bot/ in place. Does not delete it.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../../../.." && pwd)
cd "$ROOT"

out=/tmp/verify-fantasy-football-bot
mkdir -p "$out"
rm -f \
  "$out/launch.txt" \
  "$out/doctor.json" \
  "$out/espn-fixtures.txt" \
  "$out/espn-fixtures.status" \
  "$out/network-lock.txt" \
  "$out/side-effects.txt" \
  "$out/git-status.txt" \
  "$out/run.status"
trap 'rc=$?; [ "$rc" -eq 0 ] || printf "%s\n" 1 >"$out/run.status"' EXIT

{
  python3 -m compileall -q draft_mode.py league_read.py
  python3 league_read.py --help >/dev/null
  python3 draft_mode.py --help >/dev/null
  printf '%s\n' 'launch ok'
} >"$out/launch.txt"

python3 -c 'import json; from pathlib import Path; rows=json.loads(Path("seed_board.json").read_text()); assert isinstance(rows, list) and len(rows) >= 150, len(rows); print(json.dumps({"ok": True, "seed_players": len(rows)}))' \
  >"$out/doctor.json"

fingerprint() {
  if [ -e "$1" ]; then
    sha256sum -- "$1" | awk '{print $1}'
  else
    printf '%s\n' absent
  fi
}

board_before=$(fingerprint board.json)
seed_before=$(fingerprint seed_board.json)
token="${XDG_CONFIG_HOME:-$HOME/.config}/fantasy-football-bot/yahoo_token.json"
token_before=$(fingerprint "$token")
git_before=$(git status --porcelain)

set +e
python3 - <<'PY' >"$out/espn-fixtures.txt" 2>&1
import socket
from pathlib import Path

attempts = []

def blocked(self, address):
    attempts.append(repr(address))
    raise OSError("verification network lock: refused connect to %r" % (address,))

socket.socket.connect = blocked
code = 1
try:
    import unittest
    suite = unittest.TestLoader().loadTestsFromNames([
        "tests.test_league_read.TestEspnCommands",
        "tests.test_league_read.TestEspnShapes",
    ])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    code = 0 if result.wasSuccessful() else 1
finally:
    lines = ["connect_attempts %d" % len(attempts)]
    lines.extend(attempts)
    Path("/tmp/verify-fantasy-football-bot/network-lock.txt").write_text(
        "\n".join(lines) + "\n")
raise SystemExit(code)
PY
espn_status=$?
set -e
printf '%s\n' "$espn_status" >"$out/espn-fixtures.status"

board_after=$(fingerprint board.json)
seed_after=$(fingerprint seed_board.json)
token_after=$(fingerprint "$token")
git_after=$(git status --porcelain)

side_ok=0
if [ "$board_before" = "$board_after" ] && [ "$seed_before" = "$seed_after" ] && [ "$token_before" = "$token_after" ]; then
  printf '%s\n' 'board.json unchanged' 'seed_board.json unchanged' 'yahoo token unchanged' >"$out/side-effects.txt"
  side_ok=1
else
  printf '%s\n' \
    "board.json $board_before -> $board_after" \
    "seed_board.json $seed_before -> $seed_after" \
    "yahoo token $token_before -> $token_after" >"$out/side-effects.txt"
fi

if [ "$git_before" = "$git_after" ]; then
  printf '%s\n' 'git status unchanged' >"$out/git-status.txt"
  git_ok=1
else
  {
    printf '%s\n' 'git status changed during the drive'
    printf '%s\n' '--- before ---'
    printf '%s\n' "$git_before"
    printf '%s\n' '--- after ---'
    printf '%s\n' "$git_after"
  } >"$out/git-status.txt"
  git_ok=0
fi

lock_ok=0
if [ -f "$out/network-lock.txt" ] && grep -qx 'connect_attempts 0' "$out/network-lock.txt"; then
  lock_ok=1
fi

if [ "$espn_status" -ne 0 ] || [ "$side_ok" -ne 1 ] || [ "$git_ok" -ne 1 ] || [ "$lock_ok" -ne 1 ]; then
  printf '%s\n' 1 >"$out/run.status"
  exit 1
fi

printf '%s\n' 0 >"$out/run.status"

# Cleanup checks. These read the evidence. They do not delete it.
test "$(cat "$out/espn-fixtures.status")" = 0
test "$(cat "$out/run.status")" = 0
grep -qx 'launch ok' "$out/launch.txt"
test -s "$out/doctor.json"
grep -q '"ok": true' "$out/doctor.json"
test -s "$out/espn-fixtures.txt"
grep -qx 'connect_attempts 0' "$out/network-lock.txt"
grep -qx 'board.json unchanged' "$out/side-effects.txt"
grep -qx 'seed_board.json unchanged' "$out/side-effects.txt"
grep -qx 'yahoo token unchanged' "$out/side-effects.txt"
grep -qx 'git status unchanged' "$out/git-status.txt"
