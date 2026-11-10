#!/usr/bin/env python
"""Custom Claude Code status line for the Mars Search & Rescue demo.

Configured in .claude/settings.json under "statusLine". Claude Code runs this
command as the conversation updates, passing session info as JSON on stdin;
whatever we print to stdout becomes the status line at the bottom of the
terminal.

Teaching note: this runs in the HARNESS, not as a model tool call, so
protect_truth/role_guard do not apply here. It deliberately shows only
mission summary info (sol, oxygen, rover health, whether the astronaut has
been *seen* - never the astronaut's actual coordinate unless the model
itself already saw it) so a watching audience can't cheat by reading the
terminal. The search zone is safe to show as-is: it is public knowledge from
the moment the mission starts (docs/SPEC.md §1), it's the astronaut's exact
tile that stays secret.

v3 format (docs/SPEC.md §5):
    Mars SAR │ Sol 2 │ O2 7 sols │ rover-1 ok · rover-2 broken │
    astronaut: unknown (zone D5-F7) │ <model>

v3.1 (docs/SPEC.md §11) appends ' · rescued <n>/<total>' to that last segment
whenever team.json.objective.rescued_count/total are present, and swaps the
astronaut segment for 'ORDERS?' while team.json.phase == "awaiting-orders" -
the human-facing cue that a sitrep decision is pending.
"""

import json
import sys
from pathlib import Path

try:  # Windows consoles default to cp1252; hook payloads are UTF-8 JSON
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DIM = "\033[2m"
RESET = "\033[0m"
SEP = f" {DIM}│{RESET} "


def rover_label(units):
    parts = []
    for unit_id in sorted(u for u in units if u.startswith("rover-")):
        status = units[unit_id].get("status", "?")
        parts.append(f"{unit_id} {status}")
    return " · ".join(parts)


def rescued_suffix(team):
    """' · rescued 1/3' when team.json.objective.rescued_count/total are
    present (docs/SPEC.md §11 objectives); else empty string."""
    objective = team.get("objective") or {}
    rescued_count = objective.get("rescued_count")
    total = objective.get("total")
    if rescued_count is None or total is None:
        return ""
    return f" · rescued {rescued_count}/{total}"


def astronaut_label(team):
    astronaut = team.get("astronaut") or {}
    status = astronaut.get("status", "unknown")
    if status == "rescued":
        return "rescued"
    seen_at = astronaut.get("seen_at")
    if status == "seen" or seen_at:
        return f"seen at {seen_at or '?'}"
    zone = team.get("search_zone") or []
    zone_label = f"{zone[0]}-{zone[-1]}" if zone else "?"
    return f"unknown (zone {zone_label})"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        session = json.load(sys.stdin)
    except Exception:
        session = {}

    model = (session.get("model") or {}).get("display_name", "")
    # "workspace" here is the literal JSON key Claude Code sends on stdin
    # (its schema, not our project vocabulary) - it points at the project dir.
    session_workspace = session.get("workspace") or {}
    root = Path(session_workspace.get("project_dir") or session_workspace.get("current_dir") or ".")

    team_file = root / "game" / "team.json"
    if not team_file.exists():
        print(f"Mars SAR{SEP}No mission — run scripts/prepare_demo.py")
        return

    try:
        team = json.loads(team_file.read_text(encoding="utf-8"))
        sol = team["sol"]
        oxygen = team["oxygen_sols"]
        units = team.get("units", {})

        if team.get("phase") == "awaiting-orders":
            astronaut_segment = "ORDERS?"
        else:
            astronaut_segment = f"astronaut: {astronaut_label(team)}"
        astronaut_segment += rescued_suffix(team)

        line = (
            f"Mars SAR{SEP}Sol {sol}{SEP}O2 {oxygen} sols{SEP}{rover_label(units)}"
            f"{SEP}{astronaut_segment}"
        )
    except Exception:
        line = "Mars SAR" + SEP + "mission state unreadable — try engine.py status"

    if model:
        line += SEP + model
    print(line)


if __name__ == "__main__":
    main()
