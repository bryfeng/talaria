#!/usr/bin/env python3
"""Enforce lightweight Talaria commit message policy.

Rule: commit message must include a card reference like:
  [card:7ce240ee]

Bypass tokens (explicit):
  [no-card]
  [ops]
"""

from __future__ import annotations

import re
import subprocess
import sys

CARD_RE = re.compile(r"\[card:[0-9a-f]{8}\]", re.IGNORECASE)
BYPASS_TOKENS = ("[no-card]", "[ops]")


def get_message() -> str:
    if len(sys.argv) > 1:
        with open(sys.argv[1], "r", encoding="utf-8") as f:
            return f.read().strip()

    out = subprocess.check_output(["git", "log", "-1", "--pretty=%B"], text=True)
    return out.strip()


def main() -> int:
    msg = get_message()
    if any(tok in msg.lower() for tok in BYPASS_TOKENS):
        return 0
    if CARD_RE.search(msg):
        return 0

    print(
        "Commit policy violation: include Talaria card ID in message, e.g. "
        "'[card:7ce240ee] feat: ...' (or explicit bypass [no-card])."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
