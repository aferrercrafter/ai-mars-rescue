#!/usr/bin/env python
"""
scripts/check_sol.py <sol> - Did the agents do their jobs this sol?

Reads what a sol left behind in game/ (the event log written by
hooks/log_events.py, the ledger, the dispatches, the lessons) and checks it
against what the skills ask for. No model is involved: this is the cheap,
repeatable half of evaluating a skill. Change a SKILL.md, run a sol, run
this, and compare.

    python scripts/prepare_demo.py --seed 42
    claude -p "Mission Control, run the next sol" --permission-mode acceptEdits
    python scripts/check_sol.py 3

Exits 0 when every check passes, 1 otherwise.
"""
import json
import re
import sys
from pathlib import Path

GAME = Path(__file__).resolve().parent.parent / "game"
ALWAYS = ["rover", "life-support", "navigator"]


def load_events():
    path = GAME / "events.jsonl"
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    # prepare_demo.py writes scripted history; only real agents count here
    return [e for e in events if not str(e.get("agent_type") or "").startswith("script")
            and e.get("source") != "script"]


def main():
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        print(__doc__)
        return 2
    sol = int(sys.argv[1])
    events = load_events()
    in_sol = [e for e in events if e.get("sol") == sol]
    starts = [e for e in in_sol if e.get("kind") == "agent_start"]
    stops = [e for e in in_sol if e.get("kind") == "agent_stop"]
    results = []

    def check(name, passed, detail=""):
        results.append((name, passed, detail))

    # 1. the wave: two rovers, life support, the navigator
    types = [e.get("agent_type") for e in starts]
    check("two rovers spawned", types.count("rover") == 2, f"rover x{types.count('rover')}")
    for role in ALWAYS[1:]:
        check(f"{role} spawned", role in types)

    # 2. every rover actually reached the engine (a denied Bash call leaves no event)
    rover_ids = {e.get("agent_id") for e in starts if e.get("agent_type") == "rover"}
    acted = {e.get("agent_id") for e in in_sol
             if e.get("kind") == "tool" and e.get("agent_type") == "rover"}
    check("every rover ran an engine command", rover_ids <= acted,
          f"{len(rover_ids & acted)} of {len(rover_ids)}")

    # 3. one-line reports: `<who> | <did> | <result> | <observation>`
    bad = []
    for e in stops:
        msg = (e.get("message") or "").strip()
        if "\n" in msg or len(msg.split(" | ")) < 4:
            bad.append(e.get("agent_type"))
    check("reports are one line with four fields", not bad and bool(stops),
          "bad: " + ", ".join(map(str, bad)) if bad else f"{len(stops)} reports")

    # 4. nothing was blocked by a hook
    blocked = [e for e in in_sol if e.get("kind") == "blocked"]
    check("no action blocked by a hook", not blocked,
          "; ".join((e.get("reason") or "")[:60] for e in blocked))

    # 5. life support set the ledger for this sol
    ledger_edits = [e for e in in_sol if e.get("agent_type") == "life-support"
                    and str(e.get("file") or "").endswith("oxygen-ledger.md")]
    check("oxygen ledger edited", bool(ledger_edits))

    # 6. the navigator recorded lessons
    lessons = GAME / "intel" / "lessons.md"
    check("lessons.md has lessons", lessons.exists() and "- " in lessons.read_text(encoding="utf-8"))

    # 7. the dispatch: 60-90 words (skipped when the sol ended in a rescue,
    #    because the turn stops for the human's decision before the log is written)
    team = json.loads((GAME / "team.json").read_text(encoding="utf-8"))
    text = (GAME / "dispatches.md").read_text(encoding="utf-8") if (GAME / "dispatches.md").exists() else ""
    match = re.search(rf"^## Sol {sol}\s*\n(.*?)(?=^## Sol |\Z)", text, re.S | re.M)
    if match:
        body = re.sub(r"<!--.*?-->", "", match.group(1), flags=re.S)
        words = len(body.split())
        check("dispatch is 60-90 words", 60 <= words <= 90, f"{words} words")
        check("dispatch has its events footer", "<!-- events:" in match.group(1))
    elif team.get("phase") == "awaiting-orders":
        check("dispatch (skipped: team is waiting for orders)", True)
    else:
        check("dispatch written", False, f"no '## Sol {sol}' section")

    width = max(len(name) for name, _, _ in results)
    for name, passed, detail in results:
        print(f"{'PASS' if passed else 'FAIL'}  {name.ljust(width)}  {detail}")
    failed = sum(1 for _, passed, _ in results if not passed)
    print(f"\nSol {sol}: {len(results) - failed}/{len(results)} checks passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
