#!/usr/bin/env python
"""
prepare_demo.py - Fast-forward a fresh Mars Search & Rescue mission (v3.1) to
a "mid-mission" state, entirely through the engine CLI (never by touching
game/truth.json directly, except for the one sanctioned truth read below).

Why: a live sol takes real time with five agents in flight. A training
session only has a few minutes of live-fire time. This script plays out
sols deterministically (from a fixed seed) via subprocess calls to
`python engine/engine.py ...`, so the instructor is left with a realistic,
close-to-the-action mission: rover-1 parked on the doorstep of the search
zone, a broken rover to free, oxygen ticking, and the whole zone still to
be combed on the audience's first live sol.

Run from the project root:
    python scripts/prepare_demo.py [--seed 42] [--sols 3] [--oxygen-sols 12]

`--sols` is the *maximum* number of sols the script will play before giving
up (default 3) - it stops as soon as the guarantees below hold, which is
usually well before that ceiling.

Guarantees (docs/SPEC.md S9/S11 describe the general shape of this script;
this revision tightens how close rover-1 starts to the zone):
  - outcome is still null when the script finishes.
  - objective 1 is still open (current_objective == 1, phase == "active").
  - rover-1 ends adjacent to (or inside) objective 1's search zone (every
    zone tile is within 1 more move of rover-1's final position).
  - the search zone is still 0% explored (no zone tile has been entered or
    scanned) - the astronaut has therefore not been sighted either.
  - rover-2 is broken on a rough tile it reached "on the way" (engineer
    material for the first live sol), and a rover-2 -> engineer note is on
    record.
  - oxygen_sols >= 8.
  - the astronaut is reachable by rover-1 within 2 more live sols (a BFS
    path of at most 2*MOVE_RANGE tile-steps through passable terrain from
    rover-1's final position).

rover-1 is driven one sol at a time along the shortest route toward the
zone. Because `act move <coord>` lets the engine compute its own shortest
path (which knows nothing about our plan), every candidate destination is
checked against the *actual* path the engine would take (via the same
`_bfs_path` helper engine.py uses internally, truncated at the first rough
tile exactly like `act_move` does) before it is issued: a candidate is
rejected if that real path would cross a zone tile (revealing it) or - for
the plain-preferred pass - if it would break the rover down. Rough terrain
is only used as a last resort, once no plain, zone-safe route makes any
progress that sol. rover-2 follows the same plain-preferred, zone-avoiding
routing toward the astronaut's true position until the sol rover-1 reaches
the zone edge, at which point rover-2 is deliberately driven into the
nearest reachable rough tile (still checked to never cross the zone) so it
starts the live demo broken.

If a seed/--sols combination genuinely cannot satisfy these guarantees (for
example, no rough tile is within rover-2's reach, or the search zone landed
too far away for rover-1 to close the distance within --sols sols), this
script prints a clear ERROR and exits 1 rather than looping forever trying
to force it.
"""
import argparse
import json
import subprocess
import sys
from collections import deque
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine import engine as eng  # noqa: E402  (reused only for pure grid math)

ENGINE = Path("engine/engine.py")
GAME_DIR = Path("game")
EVENTS_FILE = GAME_DIR / "events.jsonl"
DISPATCHES_FILE = GAME_DIR / "dispatches.md"
NOTES_FILE = GAME_DIR / "notes.md"
ORDERS_DIR = GAME_DIR / "orders"
FIELD_DIR = GAME_DIR / "field"
INTEL_DIR = GAME_DIR / "intel"
LEDGER_FILE = FIELD_DIR / "oxygen-ledger.md"

STATE = {"sol": 1}


# --------------------------------------------------------------------------
# Engine plumbing
# --------------------------------------------------------------------------

def _append_event(ev):
    GAME_DIR.mkdir(parents=True, exist_ok=True)
    with open(EVENTS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(ev) + "\n")


def _base_event(agent_type, unit, kind):
    return {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "sol": STATE["sol"],
        "kind": kind,
        "agent_type": agent_type,
        "agent_id": None,
        "unit": unit,
        "tool": None,
        "command": None,
        "result": None,
        "result_full": None,
        "blocked": False,
        "reason": None,
        "source": "script",
        "file": None,
        "prompt": None,
        "message": None,
        "from": None,
        "to": None,
        "text": None,
    }


def append_script_event(agent_type, unit, command, result):
    """Log this script's own tool call, per docs/SPEC.md S9. The engine
    never writes events.jsonl itself; the script is the one "source:
    script" writer allowed to."""
    ev = _base_event(agent_type, unit, "tool")
    ev["tool"] = "script"
    ev["command"] = command
    ev["result"] = (result or "")[:200]
    ev["result_full"] = result
    _append_event(ev)


def append_message_event(agent_type, unit, frm, to, text):
    """Log a kind:"message" row, mirroring what hooks/log_events.py writes
    for a live `order`/`note` call - this script bypasses hooks entirely
    (it calls the CLI directly), so it is responsible for its own copy."""
    ev = _base_event(agent_type, unit, "message")
    ev["from"] = frm
    ev["to"] = to
    ev["text"] = text
    ev["message"] = text
    _append_event(ev)


def run_engine(args, agent_type="script:mission-control", unit=None, log=True):
    """Run `python engine/engine.py <args>`, return stdout, or die loudly
    showing the exact command (any ERROR aborts)."""
    cmd = [sys.executable, str(ENGINE), *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    stdout = result.stdout or ""
    first_line = stdout.splitlines()[0] if stdout.strip() else ""
    if result.returncode != 0 or first_line.startswith("ERROR"):
        print(f"\nERROR while running: {' '.join(cmd)}", file=sys.stderr)
        print(stdout + (result.stderr or ""), file=sys.stderr)
        sys.exit(1)
    if log:
        append_script_event(agent_type, unit, "engine.py " + " ".join(args), first_line)
    return stdout


def fetch_unit(uid):
    """Re-read one unit's true post-move state (at/status) via `intel`. Used
    after every `act move`, since the engine computes its own path to the
    requested coordinate and may halt earlier than planned (rough terrain) -
    never trust the requested target as the rover's actual resting place."""
    team = json.loads(run_engine(["intel"], log=False))
    return team["units"][uid]


def do_order(unit, text):
    run_engine(["order", unit, text], agent_type="script:mission-control", unit=unit)
    append_message_event("script:mission-control", unit, "mission-control", unit, text)


def do_note(frm, to, text, agent_type):
    run_engine(["note", frm, to, text], agent_type=agent_type, unit=frm)
    append_message_event(agent_type, frm, frm, to, text)


# --------------------------------------------------------------------------
# Local (read-only) grid planning - reuses engine.py's pure helpers so the
# script's notion of "reachable" and "passable" never drifts from the
# engine's. No truth.json file access happens here; it all works off the
# one truth snapshot fetched via `map --view truth --json` in main().
# --------------------------------------------------------------------------

def bounded_reachable(tiles, start, limit, avoid=frozenset()):
    """coord -> steps, for every passable tile reachable from start within
    `limit` steps, skipping any coord in `avoid`. This is our own *planning*
    graph, used only to shortlist candidate destinations - the engine's
    real `act move` still computes its own path (see actual_entered)."""
    seen = {start: 0}
    q = deque([start])
    while q:
        cur = q.popleft()
        d = seen[cur]
        if d >= limit:
            continue
        for n in eng.neighbors(cur):
            if n in seen or n in avoid:
                continue
            if not eng.passable(tiles[n]):
                continue
            seen[n] = d + 1
            q.append(n)
    return seen


def sorted_candidates(reach, metric, current):
    """Reachable coords (excluding `current`) best-first by `metric`, ties
    broken in favour of using more of the move budget (closing distance on
    a second axis later beats idling in place now)."""
    return sorted((c for c in reach if c != current), key=lambda c: (metric(c), -reach[c]))


def actual_entered(tiles, start, target, limit):
    """What `act <unit> move <target>` will *really* do: the exact shortest
    path engine.py's own `_bfs_path` computes (it knows nothing about our
    avoid-sets), truncated at the first rough tile exactly like
    engine.act_move. Returns (entered_coords, breaks_down) or None if
    `target` is unreachable within `limit` real engine moves. Used to
    verify a planning candidate against reality before committing to it."""
    path = eng._bfs_path({"tiles": tiles}, start, target, limit)
    if path is None:
        return None
    entered = []
    for c in path:
        entered.append(c)
        if tiles[c]["terrain"] == "rough":
            return entered, True
    return entered, False


def find_breakdown_targets(tiles, start, limit, avoid=frozenset()):
    """Rough tiles reachable within `limit` moves via an otherwise-plain
    prefix path, nearest first - since entering a rough tile halts movement
    there, mirroring engine.act_move exactly. Coordinates in `avoid` are
    never traversed through nor returned as a candidate (used to keep the
    search away from the secret search zone)."""
    seen = {start: 0}
    frontier = deque([start])
    candidates = []
    while frontier:
        cur = frontier.popleft()
        d = seen[cur]
        for n in eng.neighbors(cur):
            if n in seen or n in avoid:
                continue
            terrain = tiles[n]["terrain"]
            if terrain == "rock":
                continue
            nd = d + 1
            if terrain == "rough":
                if nd <= limit:
                    candidates.append((nd, n))
                continue  # never expand past a rough tile - a real move halts there
            if nd < limit:
                seen[n] = nd
                frontier.append(n)
    candidates.sort()
    return [c for _, c in candidates]


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--sols", type=int, default=3, help="max sols to attempt before giving up")
    parser.add_argument("--oxygen-sols", type=int, default=12, dest="oxygen_sols")
    args = parser.parse_args()

    if not ENGINE.exists():
        print(f"ERROR: {ENGINE} not found. Run this script from the project root.", file=sys.stderr)
        sys.exit(1)
    if args.sols < 1:
        print("ERROR: --sols must be >= 1.", file=sys.stderr)
        sys.exit(1)

    # 1. Fresh mission, then wipe anything init doesn't wipe on --force.
    print(run_engine(["init", "--force", "--seed", str(args.seed), "--oxygen-sols", str(args.oxygen_sols)],
                      log=False).strip())
    EVENTS_FILE.write_text("", encoding="utf-8")
    for d in (ORDERS_DIR, FIELD_DIR, INTEL_DIR):
        if d.exists():
            for p in d.glob("*"):
                if p.is_file():
                    p.unlink()
    STATE["sol"] = 1

    # 2. The one sanctioned truth read: astronaut position + full terrain,
    # so the script can plan paths and deliberately avoid/trigger a
    # breakdown. Agents themselves never get this view. init opens
    # objective 1 immediately (SPEC S11), so current_objective is always 1
    # here - the script never rescues anyone, so it stays that way.
    truth_snapshot = json.loads(run_engine(["map", "--view", "truth", "--json"], log=False))
    objective_1 = truth_snapshot["objectives"][truth_snapshot["current_objective"] - 1]
    astro_true = objective_1["astronaut_at"]
    tiles = truth_snapshot["tiles"]
    zone = objective_1["zone"]
    zone_set = set(zone)
    rough_all = {c for c, t in tiles.items() if t["terrain"] == "rough"}

    def dist_to_zone(c):
        return min(eng.dist(c, z) for z in zone_set)

    def scan_is_safe(c):
        # A scan (radius 1) from `c` can only reach into the zone when the
        # nearest zone tile is 0 or 1 tiles away; movement targets never
        # land inside the zone itself, so dist_to_zone(c) == 1 is the only
        # unsafe case that can actually occur here.
        return dist_to_zone(c) >= 2

    # 3. Play sols, one at a time, until rover-1 sits adjacent to (or
    # inside) the search zone - stopping the moment that guarantee holds,
    # up to --sols sols.
    sol_num = 0
    is_final_sol = False
    while not is_final_sol:
        sol_num += 1
        if sol_num > args.sols:
            print(
                f"ERROR: seed {args.seed} did not get rover-1 next to the search zone within "
                f"{args.sols} sols. Try a different --seed or a higher --sols.",
                file=sys.stderr,
            )
            sys.exit(1)
        STATE["sol"] = sol_num
        team = json.loads(run_engine(["intel"], agent_type="script:mission-control"))

        do_order("rover-1", "advance toward the search zone and scan")
        do_order("rover-2", "push forward on the lead route; watch for rough ground")

        # -- rover-1: shortest route toward the zone. Every candidate is
        # checked against the engine's *real* path (actual_entered) before
        # being issued: reject anything that would cross a zone tile
        # (revealing it), and - on the first, plain-preferred pass - reject
        # anything that would break the rover down. Rough is only allowed
        # once no plain, zone-safe candidate makes any progress. --
        u1 = team["units"]["rover-1"]
        target1 = u1["at"]
        if u1["status"] != "broken":
            reach = bounded_reachable(tiles, u1["at"], eng.MOVE_RANGE, avoid=rough_all | zone_set)
            for c in sorted_candidates(reach, dist_to_zone, u1["at"]):
                result = actual_entered(tiles, u1["at"], c, eng.MOVE_RANGE)
                if result is None:
                    continue
                entered, breaks = result
                if breaks or any(e in zone_set for e in entered):
                    continue
                target1 = c
                break
            if target1 == u1["at"]:
                # No plain-only, zone-safe route makes progress; rough is
                # the only way to get closer this sol (may break rover-1).
                reach = bounded_reachable(tiles, u1["at"], eng.MOVE_RANGE, avoid=zone_set)
                for c in sorted_candidates(reach, dist_to_zone, u1["at"]):
                    result = actual_entered(tiles, u1["at"], c, eng.MOVE_RANGE)
                    if result is None:
                        continue
                    entered, _breaks = result
                    if any(e in zone_set for e in entered):
                        continue
                    target1 = c
                    break
            if target1 != u1["at"]:
                run_engine(["act", "rover-1", "move", target1], agent_type="script:rover", unit="rover-1")
                u1 = fetch_unit("rover-1")

        # target1 (and hence u1["at"] after a real move) is never a zone
        # tile - every candidate above was rejected if its path touched
        # one - so dist_to_zone(u1["at"]) is never 0 here; <= 1 means
        # "adjacent", the stopping condition.
        is_final_sol = dist_to_zone(u1["at"]) <= 1
        if u1["status"] != "broken" and not is_final_sol:
            run_engine(["act", "rover-1", "scan"], agent_type="script:rover", unit="rover-1")

        # -- rover-2: advance toward the astronaut's true position with the
        # same plain-preferred, zone-avoiding, reality-checked routing,
        # except on the sol rover-1 reaches the zone edge, where it is
        # deliberately driven into the nearest reachable rough tile (still
        # verified to never cross the zone) so it starts the live demo
        # broken. --
        u2 = team["units"]["rover-2"]
        if u2["status"] != "broken":
            if is_final_sol:
                breakdown_coord = None
                for c in find_breakdown_targets(tiles, u2["at"], eng.MOVE_RANGE, avoid=zone_set):
                    result = actual_entered(tiles, u2["at"], c, eng.MOVE_RANGE)
                    if result is None:
                        continue
                    entered, _breaks = result
                    if any(e in zone_set for e in entered):
                        continue
                    breakdown_coord = c
                    break
                if breakdown_coord is None:
                    print(
                        f"ERROR: seed {args.seed} has no rover-2-reachable rough tile on sol {sol_num}; "
                        "cannot stage the rover-2 breakdown guarantee. Try a different --seed.",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                run_engine(["act", "rover-2", "move", breakdown_coord], agent_type="script:rover", unit="rover-2")
            else:
                metric2 = lambda c: eng.dist(c, astro_true)  # noqa: E731
                reach2 = bounded_reachable(tiles, u2["at"], eng.MOVE_RANGE, avoid=rough_all | zone_set)
                target2 = u2["at"]
                for c in sorted_candidates(reach2, metric2, u2["at"]):
                    result = actual_entered(tiles, u2["at"], c, eng.MOVE_RANGE)
                    if result is None:
                        continue
                    entered, breaks = result
                    if breaks or any(e in zone_set for e in entered):
                        continue
                    target2 = c
                    break
                if target2 != u2["at"]:
                    run_engine(["act", "rover-2", "move", target2], agent_type="script:rover", unit="rover-2")
                    u2 = fetch_unit("rover-2")
                if u2["status"] != "broken" and scan_is_safe(u2["at"]):
                    run_engine(["act", "rover-2", "scan"], agent_type="script:rover", unit="rover-2")

        # -- life support: steady as she goes. --
        FIELD_DIR.mkdir(parents=True, exist_ok=True)
        LEDGER_FILE.write_text(f"# Oxygen ledger\nsol: {sol_num}\nmode: normal\n", encoding="utf-8")
        run_engine(["apply-ledger"], agent_type="script:life-support")

        run_engine(["resolve"], agent_type="script:mission-control")

    # 4. Record the rover-2 -> engineer note, now that the mission clock
    # sits at sol N+1 (the sol the live demo actually starts on).
    final_team = json.loads(run_engine(["intel"], agent_type="script:mission-control"))
    STATE["sol"] = final_team["sol"]
    if final_team["units"]["rover-2"]["status"] != "broken":
        print(f"ERROR: rover-2 did not end up broken for seed {args.seed} (sol {sol_num}).", file=sys.stderr)
        sys.exit(1)
    rover2_at = final_team["units"]["rover-2"]["at"]
    do_note("rover-2", "engineer", f"broke down at {rover2_at}, need a repair", "script:rover")

    # 5. Verify the guarantees instead of silently trusting the plan above.
    final_team = json.loads(run_engine(["intel"], agent_type="script:mission-control"))
    problems = []
    if final_team["outcome"] is not None:
        problems.append(f"outcome is {final_team['outcome']}, expected null")
    if final_team["phase"] != "active":
        problems.append(f"phase is {final_team['phase']}, expected active")
    if final_team["objective"]["current"] != 1:
        problems.append(f"current objective is {final_team['objective']['current']}, expected 1")
    if final_team["units"]["rover-2"]["status"] != "broken":
        problems.append("rover-2 is not broken")

    zone_explored = sum(1 for c in zone if c in final_team["tiles"])
    if zone_explored != 0:
        problems.append(f"{zone_explored} zone tiles already explored, need 0")
    if final_team["astronaut"]["status"] != "unknown":
        problems.append(f"astronaut status is {final_team['astronaut']['status']}, expected unknown")
    if final_team["oxygen_sols"] < 8:
        problems.append(f"oxygen_sols is {final_team['oxygen_sols']}, need >= 8")

    rover1_at = final_team["units"]["rover-1"]["at"]
    if dist_to_zone(rover1_at) > 1:
        problems.append(f"rover-1 (at {rover1_at}) is not adjacent to (or inside) the search zone")

    reach_path = eng._bfs_path({"tiles": tiles}, rover1_at, astro_true, 2 * eng.MOVE_RANGE)
    if reach_path is None:
        problems.append(
            f"astronaut ({astro_true}) is not reachable by rover-1 (at {rover1_at}) within 2 live sols"
        )

    if problems:
        print(
            f"ERROR: prepare_demo could not satisfy its guarantees for seed={args.seed} "
            f"(stopped at sol {final_team['sol']}): " + "; ".join(problems),
            file=sys.stderr,
        )
        sys.exit(1)

    # 6. Final report for the instructor.
    print()
    print(run_engine(["status"], log=False).rstrip())
    print()
    print(run_engine(["map"], log=False).rstrip())
    print()
    zone_label = f"{zone[0]}-{zone[-1]}"
    print(
        f"Ready: sol {final_team['sol']}, objective {final_team['objective']['current']} of "
        f"{final_team['objective']['total']}, rover-1 at {rover1_at} next to zone {zone_label}, "
        f"rover-2 broken at {rover2_at}, oxygen {final_team['oxygen_sols']}/{final_team['oxygen_max']}."
    )


if __name__ == "__main__":
    main()
