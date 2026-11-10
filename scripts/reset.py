#!/usr/bin/env python
"""
reset.py - Hard reset for the Mars Search & Rescue demo folder.

Deletes game/ entirely (truth.json, team.json, events.jsonl, orders/,
field/, intel/, snapshots/, dispatches.md - whatever exists), so the next
`python engine/engine.py init` starts completely clean. This does NOT touch
engine/, hooks/, ui/, .claude/, or scripts/ - only runtime game state is
ever deleted.

Run from the project root:
    python scripts/reset.py
"""
import shutil
import sys
from pathlib import Path

GAME_DIR = Path("game")


def main():
    if not GAME_DIR.exists():
        print("Nothing to reset: game/ does not exist.")
        return 0

    if not GAME_DIR.is_dir():
        print(f"ERROR: {GAME_DIR} exists but is not a directory. Not touching it.", file=sys.stderr)
        return 1

    shutil.rmtree(GAME_DIR)
    print("Deleted game/. Run `python engine/engine.py init --seed 42` "
          "(or scripts/prepare_demo.py) to start a fresh mission.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
