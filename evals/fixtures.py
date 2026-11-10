#!/usr/bin/env python
"""
evals/fixtures.py <name> - Build the mission state one eval case starts from.

Run from the root of a throwaway copy of the repo (run_evals.py does this).
Everything goes through the engine CLI, the same way scripts/prepare_demo.py
works, so a fixture can never produce a state the engine would not.

The last line printed is a JSON object of variables the case prompt may use,
for example {"colonist": "B7"}.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
sys.path.insert(0, str(Path.cwd() / "scripts"))

import prepare_demo as pd  # noqa: E402  (reused for run_engine and the event helpers)
from engine import engine as eng  # noqa: E402  (pure grid math only)

GAME = Path("game")
LEDGER = GAME / "field" / "oxygen-ledger.md"
MEMORY = Path(".claude/agent-memory/navigator")


def run(*args):
    return subprocess_run([sys.executable, "scripts/prepare_demo.py", *args])


def subprocess_run(cmd):
    import subprocess
    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode != 0:
        sys.exit(f"fixture command failed: {' '.join(cmd)}\n{done.stdout}{done.stderr}")
    return done.stdout


def engine(*args, agent_type="script:mission-control", unit=None, log=False):
    return pd.run_engine(list(args), agent_type=agent_type, unit=unit, log=log)


def team():
    return json.loads(engine("intel"))


def set_ledger(sol, mode):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(f"# Oxygen ledger\nsol: {sol}\nmode: {mode}\nnote: scripted\n", encoding="utf-8")
    engine("apply-ledger")


def truth_view():
    """The one sanctioned look at the truth, as in prepare_demo.py. Agents never get it."""
    snap = json.loads(engine("map", "--view", "truth", "--json"))
    objective = snap["objectives"][snap["current_objective"] - 1]
    return snap["tiles"], objective["astronaut_at"], set(objective["zone"])


def safe_route(tiles, start, target):
    """True when `act move` reaches target without stopping on a rough tile."""
    result = pd.actual_entered(tiles, start, target, eng.MOVE_RANGE)
    return result is not None and not result[1]


# -- fixtures ---------------------------------------------------------------

def demo():
    """Sol 3, rover-1 ok next to the zone, rover-2 broken, oxygen 10."""
    run("--seed", "42")
    return {}


def fresh():
    """Sol 1, both rovers ok at base."""
    engine("init", "--force", "--seed", "42")
    return {}


def oxygen_low_after_normal():
    """Sol 2, oxygen 3, last sol was normal: conserve is the right call."""
    engine("init", "--force", "--seed", "42", "--oxygen-sols", "4")
    set_ledger(1, "normal")
    engine("resolve")
    return {}


def oxygen_low_after_conserve():
    """Sol 2, oxygen 3, last sol was conserve: the engine rejects a second conserve."""
    engine("init", "--force", "--seed", "42", "--oxygen-sols", "3")
    set_ledger(1, "conserve")
    engine("resolve")
    return {}


def colonist_in_reach():
    """Demo state, plus the colonist's tile, which rover-1 can reach this sol."""
    demo()
    tiles, colonist, _ = truth_view()
    start = team()["units"]["rover-1"]["at"]
    if not safe_route(tiles, start, colonist):
        sys.exit(f"fixture: colonist at {colonist} is not one safe move from {start}")
    return {"colonist": colonist}


def awaiting_orders():
    """A colonist was just rescued and the sol resolved: the human must decide."""
    colonist = colonist_in_reach()["colonist"]
    engine("act", "rover-1", "move", colonist)
    engine("act", "rover-1", "scan")
    engine("act", "rover-1", "rescue")
    set_ledger(3, "normal")
    engine("resolve")
    if team()["phase"] != "awaiting-orders":
        sys.exit("fixture: mission did not enter awaiting-orders")
    return {}


def navigator_known():
    """Demo state where the navigator already recorded every lesson the map offers."""
    demo()
    lessons = (
        "- Rough ground sits just off the direct line from base to the zone at B4 (objective 1): "
        "rover-2 broke down there, route through A4 or B5 instead.\n"
        "- The south-west corner near base is blocked: C1 is rough and C2 is rock (objective 1), "
        "so leave base along row A or B.\n"
    )
    MEMORY.mkdir(parents=True, exist_ok=True)
    (MEMORY / "MEMORY.md").write_text("# Navigator memory\n\n## Terrain lessons\n\n" + lessons, encoding="utf-8")
    (GAME / "intel").mkdir(parents=True, exist_ok=True)
    (GAME / "intel" / "lessons.md").write_text("# Lessons\n\n" + lessons, encoding="utf-8")
    return {}


def blocked_event(sol, agent_type, command, reason):
    event = pd._base_event(agent_type, None, "blocked")
    event.update({"sol": sol, "tool": "Bash", "command": command, "blocked": True,
                  "reason": reason, "source": "hook"})
    pd._append_event(event)


def log_sol3():
    """Sol 3 has been played and resolved by script; its dispatch is not written yet.

    The sol contains what the mission-log skill asks the dispatch to cover: a
    move and a scan, a note to the engineer, a repair, and one BLOCKED action.
    """
    demo()
    tiles, colonist, zone = truth_view()
    pd.STATE["sol"] = 3
    start = team()["units"]["rover-1"]["at"]
    target = next(c for c in sorted(zone) if c != colonist and tiles[c]["terrain"] == "plain"
                  and safe_route(tiles, start, c))
    pd.do_order("rover-1", f"enter the search zone at {target} and scan")
    pd.do_order("rover-2", "hold position until the engineer frees you")
    pd.do_note("mission-control", "engineer", "rover-2 broke down at B4, needs a repair", "script:mission-control")
    pd.run_engine(["act", "rover-1", "move", target], agent_type="script:rover", unit="rover-1")
    pd.run_engine(["act", "rover-1", "scan"], agent_type="script:rover", unit="rover-1")
    repair = GAME / "field" / "repair-rover-2.json"
    repair.write_text(json.dumps({"type": "repair", "unit": "rover-2", "sol": 3,
                                  "note": "freeing from rough terrain"}), encoding="utf-8")
    pd.append_script_event("script:engineer", None, "Write game/field/repair-rover-2.json", "wrote repair-rover-2.json")
    blocked_event(3, "life-support", "python engine/engine.py act rover-1 move C3",
                  "ROLE GUARD: life-support may not run 'act rover-1 move C3' (allowed: no engine commands)")
    set_ledger(3, "normal")
    pd.append_script_event("script:life-support", None, "Edit game/field/oxygen-ledger.md", "edited oxygen-ledger.md")
    pd.run_engine(["resolve"], agent_type="script:mission-control")
    return {"entered": target}


def log_sol4():
    """Sol 3 already has its dispatch; sol 4 has been played and needs one."""
    log_sol3()
    events = (GAME / "events.jsonl").read_text(encoding="utf-8").splitlines()
    seen = team()["astronaut"]["seen_at"]
    (GAME / "dispatches.md").write_text(
        "# Mission Dispatches\n\n## Sol 3\n\n"
        "Rover-1 rolls off the doorstep and into the search zone, and its first scan "
        f"{'sights the colonist at ' + seen if seen else 'finds no trace of the colonist'}. Rover-2 sits broken at B4; Mission Control notes the engineer, who files a "
        "repair so the rover can move next sol. Life support tries to drive rover-1 to C3 and is BLOCKED by "
        "the role guard, because it has no engine commands, then keeps oxygen in normal mode.\n\n"
        f"<!-- events:{len(events) - 1} -->\n", encoding="utf-8")
    tiles, colonist, zone = truth_view()
    pd.STATE["sol"] = 4
    state = team()
    start = state["units"]["rover-1"]["at"]
    explored = set(state["tiles"])
    target = next(c for c in sorted(zone) if c != colonist and c not in explored
                  and tiles[c]["terrain"] == "plain" and safe_route(tiles, start, c))
    pd.do_order("rover-1", f"push deeper into the zone to {target} and scan")
    pd.do_order("rover-2", "rejoin the search: move toward the zone and scan")
    pd.run_engine(["act", "rover-1", "move", target], agent_type="script:rover", unit="rover-1")
    pd.run_engine(["act", "rover-1", "scan"], agent_type="script:rover", unit="rover-1")
    pd.do_note("rover-2", "mission-control", "repaired and rolling again, leaving B4 by the plain tiles", "script:rover")
    pd.run_engine(["act", "rover-2", "move", "B5"], agent_type="script:rover", unit="rover-2")
    pd.run_engine(["act", "rover-2", "scan"], agent_type="script:rover", unit="rover-2")
    set_ledger(4, "normal")
    pd.run_engine(["resolve"], agent_type="script:mission-control")
    return {"entered": target}


FIXTURES = {f.__name__: f for f in (
    demo, fresh, oxygen_low_after_normal, oxygen_low_after_conserve, colonist_in_reach,
    awaiting_orders, navigator_known, log_sol3, log_sol4)}

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in FIXTURES:
        sys.exit("usage: python evals/fixtures.py <" + "|".join(FIXTURES) + ">")
    print(json.dumps(FIXTURES[sys.argv[1]]()))
