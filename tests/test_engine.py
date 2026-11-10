"""
Test plan for the Mars Search & Rescue engine (docs/SPEC.md S10 v3, S11 v3.1).

Run from the project root:
    python -m unittest -v

Each test chdirs into a fresh temporary directory, imports engine.engine,
and drives it exclusively through run([...]) under
contextlib.redirect_stdout - exactly how a real agent's `python
engine/engine.py ...` subprocess call is exercised, minus the subprocess.
Tests are allowed to read game/truth.json directly to find coordinates
(the engine itself is the only thing that is supposed to keep that file
secret from *agents*).

Grid is 10 columns x 8 rows (A-H, 1-10) as of v3.1. Missions now hold three
objectives (one colonist/search-zone each); only objectives[0] is open at
init, oxygen_sols defaults to 12 per colonist, and a rescue drops the
mission into phase "awaiting-orders" until continue-mission/rest/
abort-mission is called.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import engine  # noqa: E402


def run(argv):
    """Call engine.run(argv), capturing stdout. Returns (code, stdout_text)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = engine.run(argv)
    return code, buf.getvalue()


def read_truth():
    with open("game/truth.json", "r", encoding="utf-8") as f:
        return json.load(f)


def read_team():
    with open("game/team.json", "r", encoding="utf-8") as f:
        return json.load(f)


class EngineTestCase(unittest.TestCase):
    def setUp(self):
        self._old_cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self._tmp.cleanup()

    # -- small shared helpers -------------------------------------------------

    def init(self, seed=42, oxygen_sols=12, force=False):
        argv = ["init", "--seed", str(seed), "--oxygen-sols", str(oxygen_sols)]
        if force:
            argv.append("--force")
        code, out = run(argv)
        self.assertEqual(code, 0, out)
        return out

    def act(self, *args):
        return run(["act", *args])

    def find_coord(self, truth, predicate):
        for c in engine.all_coords():
            if predicate(c, truth["tiles"][c]):
                return c
        return None

    def teleport_to_astronaut(self, unit_id):
        """Test-only truth edit (agents may never do this): place `unit_id`
        directly on the current objective's astronaut tile, so rescue tests
        do not need to walk a rover there sol by sol."""
        truth = read_truth()
        obj = truth["objectives"][truth["current_objective"] - 1]
        truth["units"][unit_id]["at"] = obj["astronaut_at"]
        engine.save_truth(truth)
        return obj["astronaut_at"]


class TestSeedDeterminism(EngineTestCase):
    def test_seed_determinism(self):
        self.init(seed=7)
        truth1 = read_truth()
        run(["init", "--seed", "7", "--force"])
        truth2 = read_truth()
        self.assertEqual(truth1, truth2)

        run(["init", "--seed", "8", "--force"])
        truth3 = read_truth()
        self.assertNotEqual(truth1["tiles"], truth3["tiles"])


class TestInitFogAndZone(EngineTestCase):
    def test_init_fog_and_zone(self):
        self.init(seed=42)
        truth = read_truth()
        team = read_team()

        self.assertEqual(len(truth["explored"]), 9)
        expected_block = {f"{r}{c}" for r in "ABC" for c in (1, 2, 3)}
        self.assertEqual(set(truth["explored"]), expected_block)

        # Three objectives are generated up front, each its own known 3x3
        # zone at least 4 tiles from base; only objective 1 is open.
        self.assertEqual(len(truth["objectives"]), 3)
        self.assertEqual(truth["current_objective"], 1)
        self.assertEqual(truth["phase"], "active")
        statuses = [o["status"] for o in truth["objectives"]]
        self.assertEqual(statuses, ["open", "pending", "pending"])

        cur_zone = truth["objectives"][0]["zone"]
        self.assertEqual(len(cur_zone), 9)
        self.assertEqual(set(team["search_zone"]), set(cur_zone))
        for c in cur_zone:
            self.assertGreaterEqual(engine.dist("A1", c), 4)

        for obj in truth["objectives"]:
            self.assertIn(obj["astronaut_at"], obj["zone"])
            self.assertGreaterEqual(engine.dist("A1", obj["astronaut_at"]), 4)

        # Pending objectives 2 and 3 stay entirely out of team.json - only
        # the open zone is ever exposed (SPEC S11: "never leak pending
        # zones or astronaut tiles").
        pending_zones = set(truth["objectives"][1]["zone"]) | set(truth["objectives"][2]["zone"])
        self.assertFalse(pending_zones & set(team["search_zone"]))
        self.assertNotIn("objectives", team)

        # Astronaut position is secret until seen.
        self.assertEqual(team["astronaut"]["status"], "unknown")
        self.assertIsNone(team["astronaut"]["seen_at"])
        self.assertIsNone(team["astronaut"]["seen_sol"])

        self.assertEqual(team["objective"], {"current": 1, "total": 3, "rescued_count": 0})
        self.assertEqual(team["rescued"], [])
        self.assertEqual(team["phase"], "active")
        self.assertEqual(team["oxygen_sols"], 12)
        self.assertEqual(team["oxygen_max"], 12)

        base_tile = truth["tiles"]["A1"]
        self.assertEqual(base_tile["terrain"], "plain")

        code, out = run(["map"])
        self.assertEqual(code, 0)
        self.assertIn("?", out)
        self.assertIn("Z", out)  # unexplored search-zone tiles


class TestMoveRules(EngineTestCase):
    def test_move_rules(self):
        self.init(seed=42)
        truth = read_truth()

        rock = self.find_coord(truth, lambda c, t: t["terrain"] == "rock")
        self.assertIsNotNone(rock, "seed 42 has no rock tile")
        code, out = self.act("rover-1", "move", rock)
        self.assertEqual(code, 1)
        self.assertIn("unreachable", out)

        far = self.find_coord(
            truth, lambda c, t: t["terrain"] in ("plain", "rough") and engine.dist("A1", c) >= 3
        )
        self.assertIsNotNone(far)
        code, out = self.act("rover-1", "move", far)
        self.assertEqual(code, 1)
        self.assertIn("unreachable", out)

        near = self.find_coord(
            truth, lambda c, t: t["terrain"] == "plain" and 0 < engine.dist("A1", c) <= 2
        )
        self.assertIsNotNone(near)
        code, out = self.act("rover-1", "move", near)
        self.assertEqual(code, 0, out)
        self.assertIn("status=ok", out)

        # Second move this sol -> slot error.
        code, out = self.act("rover-1", "move", "A1")
        self.assertEqual(code, 1)
        self.assertIn("already used its move slot", out)


class TestBreakdownAndRepair(EngineTestCase):
    def test_breakdown_and_repair(self):
        self.init(seed=42)
        truth = read_truth()

        # Find a rough tile reachable from base within move range, so we can
        # deterministically drive rover-1 into a breakdown.
        target = None
        for c in engine.all_coords():
            if truth["tiles"][c]["terrain"] != "rough":
                continue
            if engine._bfs_path(truth, "A1", c, engine.MOVE_RANGE) is not None:
                target = c
                break
        self.assertIsNotNone(target, "seed 42 has no rover-reachable rough tile")

        code, out = self.act("rover-1", "move", target)
        self.assertEqual(code, 0, out)
        self.assertIn("status=broken", out)
        self.assertIn("note=entered rough terrain", out)

        # The rover may have halted at a *different* rough tile than the one
        # requested, if the path crossed one first - that is correct engine
        # behaviour (SPEC S1: "path stops at the first rough tile entered").
        truth = read_truth()
        stopped_at = truth["units"]["rover-1"]["at"]
        self.assertEqual(truth["units"]["rover-1"]["status"], "broken")
        self.assertEqual(truth["tiles"][stopped_at]["terrain"], "rough")

        # A broken rover cannot move or scan.
        code, out = self.act("rover-1", "move", "A1")
        self.assertEqual(code, 1)
        self.assertIn("is broken at", out)
        code, out = self.act("rover-1", "scan")
        self.assertEqual(code, 1)
        self.assertIn("is broken at", out)

        # Engineer files a repair for the current sol.
        sol = truth["sol"]
        repair = {"type": "repair", "unit": "rover-1", "sol": sol, "note": "test"}
        os.makedirs("game/field", exist_ok=True)
        path = "game/field/repair-rover-1.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(repair, f)

        code, out = run(["field-check", path])
        self.assertEqual(code, 0, out)
        self.assertIn("accepted repair rover-1", out)

        code, out = run(["resolve"])
        self.assertEqual(code, 0, out)
        truth = read_truth()
        self.assertEqual(truth["units"]["rover-1"]["status"], "ok")
        self.assertFalse(os.path.exists(path))
        applied = list(Path("game/field/applied").glob("*repair-rover-1.json"))
        self.assertEqual(len(applied), 1)
        self.assertNotIn("REJECTED", applied[0].name)

        # Second resolve must not re-apply anything and must not error.
        code, out = run(["resolve"])
        self.assertEqual(code, 0, out)
        truth2 = read_truth()
        self.assertEqual(truth2["units"]["rover-1"]["status"], "ok")


class TestScanRevealsAndSights(EngineTestCase):
    def test_scan_reveals_and_sights(self):
        self.init(seed=42)
        astro = self.teleport_to_astronaut("rover-2")

        code, out = self.act("rover-2", "scan")
        self.assertEqual(code, 0, out)
        self.assertIn(f"astronaut={astro}", out)

        truth = read_truth()
        obj = truth["objectives"][0]
        self.assertEqual(obj["status"], "open")
        self.assertEqual(obj["seen_at"], astro)
        self.assertEqual(obj["seen_sol"], truth["sol"])

        team = read_team()
        self.assertEqual(team["astronaut"]["status"], "seen")
        self.assertEqual(team["astronaut"]["seen_at"], astro)

        for n in engine.neighbors(astro) + [astro]:
            self.assertIn(n, truth["explored"])

        # One scan slot per sol.
        code, out = self.act("rover-2", "scan")
        self.assertEqual(code, 1)
        self.assertIn("already scanned", out)


class TestRescueAndWin(EngineTestCase):
    def test_rescue_and_win(self):
        self.init(seed=42, oxygen_sols=30)
        truth = read_truth()
        astro = truth["objectives"][0]["astronaut_at"]

        code, out = self.act("rover-1", "rescue")
        self.assertEqual(code, 1)
        self.assertIn("no astronaut at", out)

        # Walk rover-1 to the astronaut over as many sols as it takes,
        # filing a same-sol repair if it happens to break down en route.
        for _ in range(60):
            truth = read_truth()
            u = truth["units"]["rover-1"]
            if u["at"] == astro:
                break
            if u["status"] == "broken":
                repair = {"type": "repair", "unit": "rover-1", "sol": truth["sol"], "note": "t"}
                os.makedirs("game/field", exist_ok=True)
                with open("game/field/repair-rover-1.json", "w", encoding="utf-8") as f:
                    json.dump(repair, f)
            else:
                path = engine._bfs_path(truth, u["at"], astro, engine.MOVE_RANGE)
                if path:
                    target = path[-1]
                else:
                    reach = engine.legal_moves_for(truth, "rover-1")
                    target = min(reach, key=lambda c: engine.dist(c, astro)) if reach else u["at"]
                if target != u["at"]:
                    code, out = self.act("rover-1", "move", target)
                    self.assertEqual(code, 0, out)
            run(["resolve"])
        truth = read_truth()
        self.assertEqual(truth["units"]["rover-1"]["at"], astro)

        code, out = self.act("rover-1", "rescue")
        self.assertEqual(code, 0, out)
        truth = read_truth()
        self.assertEqual(truth["objectives"][0]["status"], "rescued")
        self.assertEqual(truth["phase"], "awaiting-orders")
        self.assertIsNone(truth["outcome"])  # mission-level outcome is untouched by a rescue
        oxygen_at_rescue = truth["oxygen_sols"]

        # act/order/note are refused while awaiting orders.
        code, out = self.act("rover-2", "scan")
        self.assertEqual(code, 1)
        self.assertIn("team is waiting for orders", out)
        code, out = run(["order", "rover-1", "hold"])
        self.assertEqual(code, 1)
        self.assertIn("team is waiting for orders", out)
        code, out = run(["note", "rover-1", "mission-control", "rescued"])
        self.assertEqual(code, 1)
        self.assertIn("team is waiting for orders", out)

        # resolve still runs once, for the sol the rescue happened on -
        # oxygen is frozen (must not drop further).
        code, out = run(["resolve"])
        self.assertEqual(code, 0, out)
        self.assertIn("RESCUED: OBJECTIVE 1 OF 3 -> TEAM WAITING FOR ORDERS", out)
        truth = read_truth()
        self.assertEqual(truth["oxygen_sols"], oxygen_at_rescue)
        self.assertEqual(truth["phase"], "awaiting-orders")

        # A further resolve refuses: awaiting orders.
        code, out = run(["resolve"])
        self.assertEqual(code, 1)
        self.assertIn("team is waiting for orders", out)


class TestOxygenAndLedger(EngineTestCase):
    def test_oxygen_countdown_and_loss(self):
        self.init(seed=42, oxygen_sols=2)
        code, out = run(["resolve"])
        self.assertEqual(code, 0, out)
        truth = read_truth()
        self.assertEqual(truth["oxygen_sols"], 1)
        self.assertIsNone(truth["outcome"])
        self.assertEqual(truth["phase"], "active")

        code, out = run(["resolve"])
        self.assertEqual(code, 0, out)
        truth = read_truth()
        self.assertEqual(truth["oxygen_sols"], 0)
        self.assertEqual(truth["objectives"][0]["status"], "lost")
        self.assertEqual(truth["phase"], "awaiting-orders")
        self.assertIsNone(truth["outcome"])  # the mission continues (SPEC S11)
        self.assertIn("LOST", out)

        team = read_team()
        self.assertEqual(team["objective"]["rescued_count"], 0)
        code, out = run(["sitrep"])
        self.assertIn("COLONISTS RESCUED: 0/3", out)

        # Further resolve/act refuse: the team is waiting for orders, not
        # "mission over" - a lost colonist does not end the mission.
        code, out = run(["resolve"])
        self.assertEqual(code, 1)
        self.assertIn("team is waiting for orders", out)

        code, out = self.act("rover-1", "scan")
        self.assertEqual(code, 1)
        self.assertIn("team is waiting for orders", out)

    def test_conserve_twice_rejected(self):
        self.init(seed=42, oxygen_sols=12)
        truth = read_truth()
        sol = truth["sol"]
        os.makedirs("game/field", exist_ok=True)
        with open("game/field/oxygen-ledger.md", "w", encoding="utf-8") as f:
            f.write(f"# Oxygen ledger\nsol: {sol}\nmode: conserve\n")
        code, out = run(["apply-ledger"])
        self.assertEqual(code, 0, out)
        self.assertIn("mode=conserve", out)
        code, out = run(["resolve"])
        self.assertEqual(code, 0, out)

        truth = read_truth()
        sol2 = truth["sol"]
        with open("game/field/oxygen-ledger.md", "w", encoding="utf-8") as f:
            f.write(f"# Oxygen ledger\nsol: {sol2}\nmode: conserve\n")
        code, out = run(["apply-ledger"])
        self.assertEqual(code, 1)
        self.assertIn("conserve mode used last sol", out)


class TestNotes(EngineTestCase):
    def test_notes(self):
        self.init(seed=42)
        code, out = run(["note", "rover-2", "engineer", "broke", "down", "at", "D4"])
        self.assertEqual(code, 0, out)
        self.assertIn("NOTE:", out)
        self.assertIn("from=rover-2", out)
        self.assertIn("to=engineer", out)

        team = read_team()
        self.assertEqual(len(team["notes"]), 1)
        self.assertEqual(team["notes"][0]["from"], "rover-2")
        self.assertEqual(team["notes"][0]["to"], "engineer")
        self.assertEqual(team["notes"][0]["text"], "broke down at D4")

        notes_text = Path("game/notes.md").read_text(encoding="utf-8")
        self.assertIn("# Mission notes", notes_text)
        self.assertIn("rover-2", notes_text)

        code, out = run(["note", "mission-control", "rover-1", "advance", "north"])
        self.assertEqual(code, 0, out)

        code, out = run(["notes", "--for", "engineer"])
        self.assertEqual(code, 0, out)
        self.assertIn("rover-2 -> engineer", out)
        self.assertNotIn("mission-control -> rover-1", out)

        code, out = run(["notes"])
        self.assertEqual(code, 0, out)
        self.assertIn("rover-2 -> engineer", out)
        self.assertIn("mission-control -> rover-1", out)

        run(["note", "rover-1", "mission-control", "saw", "nothing"])
        code, out = run(["status"])
        self.assertEqual(code, 0, out)
        self.assertIn("Notes: 1 unread for mission-control", out)


class TestOrdersAndIntel(EngineTestCase):
    def test_orders_and_intel(self):
        self.init(seed=42)
        code, out = run(["order", "rover-1", "scout", "the", "zone"])
        self.assertEqual(code, 0, out)
        self.assertIn("ORDER:", out)
        truth = read_truth()
        order_path = Path(f"game/orders/sol-{truth['sol']}.md")
        self.assertTrue(order_path.exists())
        self.assertIn("rover-1", order_path.read_text(encoding="utf-8"))

        code, out = run(["intel"])
        self.assertEqual(code, 0, out)
        team = read_team()
        printed = json.loads(out)
        # 'updated' is regenerated on every call; compare everything else.
        printed.pop("updated", None)
        team.pop("updated", None)
        self.assertEqual(printed, team)

        self.assertEqual(printed["astronaut"]["status"], "unknown")
        self.assertIsNone(printed["astronaut"]["seen_at"])


class TestIllegalActions(EngineTestCase):
    def test_illegal_actions(self):
        self.init(seed=42)

        code, out = self.act("rover-9", "move", "A2")
        self.assertEqual(code, 1)
        self.assertIn("Unknown unit", out)

        code, out = self.act("rover-1", "move", "Z99")
        self.assertEqual(code, 1)
        self.assertIn("Invalid coordinate", out)

        code, out = self.act("rover-1", "recharge")
        self.assertEqual(code, 1)
        self.assertIn("cannot recharge", out)

        code, out = self.act("rover-1", "rescue")
        self.assertEqual(code, 1)
        self.assertIn("no astronaut at", out)

        code, out = run(["init", "--seed", "1"])
        self.assertEqual(code, 1)
        self.assertIn("already in progress", out)


# --------------------------------------------------------------------------
# v3.1 (SPEC S11): objectives, sitrep, continue-mission / rest / abort-mission
# --------------------------------------------------------------------------

class TestSitrepExactText(EngineTestCase):
    def test_sitrep_before_and_after_rescue(self):
        self.init(seed=42, oxygen_sols=12)

        code, out = run(["sitrep"])
        self.assertEqual(code, 0, out)
        expected_before = (
            "SOL 1\n"
            "OXYGEN: 12/12\n"
            "ZONE EXPLORED: 0%\n"
            "ROVERS ONLINE: 2/2\n"
            "ROVERS DAMAGED: 0/2\n"
            "COLONISTS RESCUED: 0/3\n"
            "\n"
            "TEAM ACTIVE: OBJECTIVE 1 OF 3\n"
            "1 - CONTINUE RESCUE MISSION\n"
            "2 - REST AND REPAIR\n"
            "3 - ABORT MISSION\n"
        )
        self.assertEqual(out, expected_before)

        self.teleport_to_astronaut("rover-1")
        code, out = self.act("rover-1", "rescue")
        self.assertEqual(code, 0, out)

        code, out = run(["sitrep"])
        self.assertEqual(code, 0, out)
        expected_after = (
            "SOL 1\n"
            "OXYGEN: --/12\n"
            "ZONE EXPLORED: --\n"
            "ROVERS ONLINE: 2/2\n"
            "ROVERS DAMAGED: 0/2\n"
            "COLONISTS RESCUED: 1/3\n"
            "\n"
            "TEAM WAITING FOR ORDERS:\n"
            "1 - CONTINUE RESCUE MISSION\n"
            "2 - REST AND REPAIR\n"
            "3 - ABORT MISSION\n"
        )
        self.assertEqual(out, expected_after)


class TestContinueMission(EngineTestCase):
    def test_continue_mission_opens_next_objective(self):
        self.init(seed=42, oxygen_sols=12)
        self.teleport_to_astronaut("rover-1")
        code, out = self.act("rover-1", "rescue")
        self.assertEqual(code, 0, out)
        run(["resolve"])  # the one allowed resolve for the rescue sol

        truth = read_truth()
        next_zone = truth["objectives"][1]["zone"]
        zone_label = f"{next_zone[0]}-{next_zone[-1]}"

        code, out = run(["continue-mission"])
        self.assertEqual(code, 0, out)
        self.assertEqual(
            out.strip(),
            f"ORDERS RECEIVED: CONTINUE RESCUE MISSION -> OBJECTIVE 2 OF 3, "
            f"ZONE {zone_label}, OXYGEN 12 SOLS",
        )

        truth = read_truth()
        self.assertEqual(truth["current_objective"], 2)
        self.assertEqual(truth["phase"], "active")
        self.assertEqual(truth["oxygen_sols"], 12)
        self.assertEqual(truth["objectives"][1]["status"], "open")

        team = read_team()
        self.assertEqual(set(team["search_zone"]), set(next_zone))
        self.assertEqual(team["oxygen_sols"], 12)
        self.assertEqual(team["objective"]["current"], 2)

        # act/order/resolve work normally again now that the team is active.
        code, out = self.act("rover-1", "scan")
        self.assertEqual(code, 0, out)

    def test_continue_mission_with_nothing_pending_errors(self):
        self.init(seed=42, oxygen_sols=12)
        for _ in range(3):
            self.teleport_to_astronaut("rover-1")
            code, out = self.act("rover-1", "rescue")
            self.assertEqual(code, 0, out)
            run(["resolve"])
            truth = read_truth()
            if any(o["status"] == "pending" for o in truth["objectives"]):
                code, out = run(["continue-mission"])
                self.assertEqual(code, 0, out)

        code, out = run(["continue-mission"])
        self.assertEqual(code, 1)
        self.assertIn("no colonists left to rescue; abort-mission to close the mission", out)


class TestRest(EngineTestCase):
    def test_rest_repairs_rovers_and_advances_sol(self):
        self.init(seed=42, oxygen_sols=12)

        # Refused while active (a clock is running).
        code, out = run(["rest"])
        self.assertEqual(code, 1)
        self.assertIn("rest is only possible while the team is waiting for orders", out)

        truth = read_truth()
        truth["units"]["rover-2"]["status"] = "broken"
        engine.save_truth(truth)

        self.teleport_to_astronaut("rover-1")
        code, out = self.act("rover-1", "rescue")
        self.assertEqual(code, 0, out)
        run(["resolve"])

        truth = read_truth()
        old_sol = truth["sol"]
        self.assertEqual(truth["phase"], "awaiting-orders")

        code, out = run(["rest"])
        self.assertEqual(code, 0, out)
        self.assertEqual(
            out.strip(), f"ORDERS RECEIVED: REST AND REPAIR -> SOL {old_sol + 1}, ROVERS ONLINE 2/2"
        )

        truth = read_truth()
        self.assertEqual(truth["sol"], old_sol + 1)
        self.assertEqual(truth["units"]["rover-2"]["status"], "ok")
        self.assertFalse(truth["units"]["rover-1"]["move_used"])
        self.assertFalse(truth["units"]["rover-2"]["move_used"])
        self.assertEqual(truth["phase"], "awaiting-orders")  # rest does not decide anything
        self.assertEqual(truth["oxygen_sols"], truth["oxygen_sols"])  # no clock running, unchanged
        self.assertTrue(Path(f"game/snapshots/sol-{old_sol + 1}.json").exists())


class TestAbortMission(EngineTestCase):
    def test_abort_mission_closes_and_refuses_further_mutation(self):
        self.init(seed=42, oxygen_sols=12)

        code, out = run(["abort-mission"])
        self.assertEqual(code, 0, out)
        self.assertEqual(
            out.strip(), "ORDERS RECEIVED: ABORT MISSION -> MISSION CLOSED, COLONISTS RESCUED 0/3"
        )

        truth = read_truth()
        self.assertEqual(truth["outcome"], "aborted")

        code, out = run(["status"])
        self.assertIn("Outcome: ABORTED", out)

        for args in (["act", "rover-1", "scan"], ["resolve"], ["abort-mission"], ["rest"]):
            code, out = run(args)
            self.assertEqual(code, 1, out)
            self.assertIn("mission over", out)

    def test_abort_mission_after_all_rescued_is_complete(self):
        self.init(seed=42, oxygen_sols=12)
        for i in range(3):
            self.teleport_to_astronaut("rover-1")
            code, out = self.act("rover-1", "rescue")
            self.assertEqual(code, 0, out)
            run(["resolve"])
            if i < 2:
                run(["continue-mission"])

        code, out = run(["abort-mission"])
        self.assertEqual(code, 0, out)
        self.assertIn("COLONISTS RESCUED 3/3", out)
        truth = read_truth()
        self.assertEqual(truth["outcome"], "complete")


if __name__ == "__main__":
    unittest.main()
