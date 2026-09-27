"""Fail if published files contain Sleeper-style ids or the owner username.

The username is checked by SHA-256 so the literal is not stored here.
Run from the repo root: python3 -m unittest
"""

from __future__ import annotations

import hashlib
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# SHA-256 of the owner's Sleeper username, lowercased. Not the username.
OWNER_USERNAME_SHA256 = (
    "5c563d4563b437c08795536b334626475757c83ae8e60d1438e4b62cc43f262c"
)

# Sleeper user, league, and draft ids are 18 or 19 digits.
LONG_ID = re.compile(r"(?<!\d)\d{18,19}(?!\d)")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
TOKEN = re.compile(r"[A-Za-z0-9_]+")

SKIP_DIRS = {".git", "__pycache__", ".cache"}


def published_files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        yield path


class TestNoPersonalIds(unittest.TestCase):
    def test_no_long_numeric_ids(self):
        for path in published_files():
            text = path.read_text(encoding="utf-8", errors="replace")
            match = LONG_ID.search(text)
            self.assertIsNone(
                match,
                "{} contains a long numeric id".format(path.relative_to(ROOT)),
            )

    def test_no_owner_username(self):
        for path in published_files():
            text = path.read_text(encoding="utf-8", errors="replace")
            for token in TOKEN.findall(text):
                digest = hashlib.sha256(token.lower().encode("utf-8")).hexdigest()
                self.assertNotEqual(
                    digest,
                    OWNER_USERNAME_SHA256,
                    "{} contains the owner username".format(
                        path.relative_to(ROOT)),
                )

    def test_no_email_addresses(self):
        for path in published_files():
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in EMAIL.finditer(text):
                # The Actions workflow commits as GitHub's own bot account.
                if match.group(0).lower().endswith("@users.noreply.github.com"):
                    continue
                self.fail("{} contains an email address".format(
                    path.relative_to(ROOT)))


if __name__ == "__main__":
    unittest.main()
