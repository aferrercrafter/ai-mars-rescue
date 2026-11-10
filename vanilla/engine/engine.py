"""
Mars Search & Rescue - ground-truth engine (v3.1, objectives + sitrep).

This is the single source of truth ("referee") for a cooperative rescue
mission played by a Claude Code multi-agent training demo. It is a
stdlib-only Python 3 CLI: every agent (two rovers, engineer, life support,
navigator, mission control, ...) shells out to it to look at the world and
to act. All authoritative state lives in game/truth.json; a fog-of-war view
is derived into game/team.json on every mutating command, which is the only
state file the agents are allowed to read.

Grid: 10 columns x 8 rows. Rows A-H (top to bottom), columns 1-10.
Coordinates such as "C4" are case-insensitive on input, uppercase on output.
Distance is Chebyshev (8-directional): dist(A1, B2) == 1.

v3 dropped beacon, storms, blackout, battery, bridges, ridges/craters, the
drone and EVA roles, carry/recharge actions, and the `tile` command, leaving
three mechanics: fog of war, rover breakdown on rough terrain, and the
oxygen ledger. Notes (`note`/`notes`) are the one inter-agent channel.

v3.1 adds three colonists, each their own objective with its own 3x3 zone
and its own oxygen clock (see `truth["objectives"]`). Only one objective is
"open" (current) at a time; rescuing its colonist freezes its clock and
drops the mission into `phase: awaiting-orders`, where every mutating
command except `sitrep`/`continue-mission`/`rest`/`abort-mission` is
refused until a human (via `sitrep`'s three options) says what happens
next. The mission-level `outcome` is therefore no longer rescued/lost (that
is now per-objective `status`); it is null until `abort-mission` sets it to
`aborted` or `complete`.

Usage:
    python engine/engine.py init [--seed N] [--force] [--oxygen-sols N]
    python engine/engine.py status
    python engine/engine.py map [--view team|truth] [--json]
    python engine/engine.py unit <id>
    python engine/engine.py intel
    python engine/engine.py act <unit> <action> [arg]
    python engine/engine.py order <unit|all> <text...>
    python engine/engine.py note <from> <to> <text...>
    python engine/engine.py notes [--for <name>]
    python engine/engine.py apply-ledger
    python engine/engine.py field-check <path>
    python engine/engine.py resolve
    python engine/engine.py sitrep
    python engine/engine.py continue-mission
    python engine/engine.py rest
    python engine/engine.py abort-mission
    python engine/engine.py events [--since N] [--json]
    python engine/engine.py replay

Full contracts: docs/SPEC.md (v3 S1-S10, v3.1 S11).
"""

import argparse
import json
import os
import random
import re
import sys
import tempfile
from collections import deque
from datetime import datetime
from pathlib import Path

# --------------------------------------------------------------------------
# Grid constants
# --------------------------------------------------------------------------

ROWS = "ABCDEFGH"          # row A..H, top -> bottom (8 rows)
GRID_COLS = 10
GRID_ROWS = len(ROWS)
BASE = "A1"
COORD_RE = re.compile(r"^[A-H](10|[1-9])$")

GAME_DIR = Path("game")
TRUTH_FILE = GAME_DIR / "truth.json"
TEAM_FILE = GAME_DIR / "team.json"
EVENTS_FILE = GAME_DIR / "events.jsonl"
DISPATCHES_FILE = GAME_DIR / "dispatches.md"
NOTES_FILE = GAME_DIR / "notes.md"
ORDERS_DIR = GAME_DIR / "orders"
FIELD_DIR = GAME_DIR / "field"
FIELD_APPLIED_DIR = FIELD_DIR / "applied"
INTEL_DIR = GAME_DIR / "intel"
SNAPSHOTS_DIR = GAME_DIR / "snapshots"
LEDGER_FILE = FIELD_DIR / "oxygen-ledger.md"

# Init explores the 3x3 block A1-C3 (SPEC S1) - 9 tiles, always in-bounds
# since the grid is at least 3x3 in the top-left corner.
INITIAL_EXPLORED_BLOCK = 3

# Terrain weights (percent). A single weighted pick per tile keeps the RNG
# stream deterministic from sub_seed alone (order: all_coords(), row-major).
TERRAIN_WEIGHTS = [("plain", 65), ("rough", 25), ("rock", 10)]

# "Base is plain; no rock in A1-B2" (SPEC S1): the 2x2 corner block around
# base is kept passable. Rolled-then-overridden so the RNG stream consumed
# is identical whether or not the override fires (same trick v1 used for
# blackout tiles) - not pinned exactly by SPEC beyond "no rock here".
NO_ROCK_BLOCK = {"A1", "A2", "B1", "B2"}

UNIT_IDS = ["rover-1", "rover-2"]
MOVE_RANGE = 2
SCAN_RADIUS = 1
ACTIONS = ["move", "scan", "rescue"]

ZONE_SIZE = 3
ZONE_MIN_DIST = 4  # every zone tile must be >= this far (Chebyshev) from base
                    # AND (v3.1) from every tile of every other zone
NUM_OBJECTIVES = 3  # three colonists, one objective/zone each (SPEC S11)

# The one message shown for act/order/note/resolve while the mission is
# paused for a human decision (SPEC S11's exact wording).
AWAITING_ORDERS_ERROR = (
    "team is waiting for orders; run sitrep and choose continue-mission, rest, or abort-mission"
)


# --------------------------------------------------------------------------
# Grid helpers
# --------------------------------------------------------------------------

def all_coords():
    return [f"{r}{c}" for r in ROWS for c in range(1, GRID_COLS + 1)]


def normalize_coord(raw):
    """Return an uppercase 'A1'..'H10' coordinate, or None if invalid."""
    if not raw:
        return None
    s = raw.strip().upper()
    if not COORD_RE.match(s):
        return None
    return s


def coord_rc(coord):
    return ROWS.index(coord[0]), int(coord[1:]) - 1


def dist(a, b):
    ra, ca = coord_rc(a)
    rb, cb = coord_rc(b)
    return max(abs(ra - rb), abs(ca - cb))


def neighbors(coord):
    r, c = coord_rc(coord)
    out = []
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr == 0 and dc == 0:
                continue
            nr, nc = r + dr, c + dc
            if 0 <= nr < GRID_ROWS and 0 <= nc < GRID_COLS:
                out.append(f"{ROWS[nr]}{nc + 1}")
    return sorted(out)


def passable(tile):
    """Rovers cross plain and rough; rock is impassable (SPEC S1)."""
    return tile["terrain"] in ("plain", "rough")


# --------------------------------------------------------------------------
# Deterministic map + search-zone generation (SPEC S1)
# --------------------------------------------------------------------------

def _weighted_pick(rng, weights):
    total = sum(w for _, w in weights)
    x = rng.uniform(0, total)
    cum = 0
    for value, w in weights:
        cum += w
        if x <= cum:
            return value
    return weights[-1][0]


def generate_tiles(sub_seed):
    rng = random.Random(sub_seed)
    tiles = {}
    for coord in all_coords():
        if coord == BASE:
            tiles[coord] = {"terrain": "plain"}
            continue
        terrain = _weighted_pick(rng, TERRAIN_WEIGHTS)
        if coord in NO_ROCK_BLOCK and terrain == "rock":
            terrain = "plain"
        tiles[coord] = {"terrain": terrain}
    return rng, tiles


def bfs_reachable(tiles, start=BASE):
    """All coords reachable from start through passable tiles (ignores
    move-range limits; used for the generator's connectivity checks)."""
    seen = {start}
    q = deque([start])
    while q:
        cur = q.popleft()
        for n in neighbors(cur):
            if n not in seen and passable(tiles[n]):
                seen.add(n)
                q.append(n)
    return seen


def zone_coords(r0, c0):
    """The 9 coords of a 3x3 block whose top-left corner is (r0, c0), in
    row-major order (matches SPEC's example: ["D5","D6","D7","E5",...])."""
    return [
        f"{ROWS[r0 + dr]}{c0 + dc + 1}"
        for dr in range(ZONE_SIZE)
        for dc in range(ZONE_SIZE)
    ]


def zone_candidates():
    """Every in-bounds 3x3 block whose tiles are all >= ZONE_MIN_DIST from
    base. Fixed given the grid size - independent of seed."""
    out = []
    for r0 in range(0, GRID_ROWS - ZONE_SIZE + 1):
        for c0 in range(0, GRID_COLS - ZONE_SIZE + 1):
            coords = zone_coords(r0, c0)
            if all(dist(BASE, c) >= ZONE_MIN_DIST for c in coords):
                out.append((r0, c0))
    return out


_ZONE_CANDIDATES = zone_candidates()


def _zones_far_enough(block_a, block_b, min_dist=ZONE_MIN_DIST):
    """True if the two zones are at least min_dist (Chebyshev) apart (SPEC
    S11: "at least 4 tiles from each other").

    Measured corner-to-corner between the blocks' (r0, c0) anchors, which by
    Chebyshev's translation invariance is identical to center-to-center.
    Deviation note: the more literal "every tile of A is >= min_dist from
    every tile of B" (the same rule already used for "4 tiles from base",
    where base is a single point) is infeasible here - three 3x3 zones each
    already required to sit >= 4 from base leaves no way to also keep every
    pair of them >= 4 apart tile-for-tile on an 8x10 grid (verified: zero
    valid triples). The corner/center metric is the closest reading that
    still yields real spacing between zones and is actually satisfiable."""
    r0a, c0a = block_a
    r0b, c0b = block_b
    return max(abs(r0a - r0b), abs(c0a - c0b)) >= min_dist


def _pick_zone_blocks(rng, count=NUM_OBJECTIVES):
    """Pick `count` non-overlapping 3x3 blocks from _ZONE_CANDIDATES, each
    pairwise >= ZONE_MIN_DIST from every other picked block, consuming rng
    deterministically. Returns None if the candidate pool runs out."""
    remaining = list(_ZONE_CANDIDATES)
    chosen = []
    for _ in range(count):
        if not remaining:
            return None
        block = rng.choice(remaining)
        chosen.append(block)
        remaining = [b for b in remaining if b != block and _zones_far_enough(b, block)]
        # keep only candidates that were already far from *every* previously
        # chosen block too (remaining was built from the previous filter).
    return chosen


def generate_mission(seed, max_attempts=4000):
    """Generate tiles + three non-overlapping search zones (one per
    colonist/objective) + an astronaut position per zone, retrying with
    sub-seeds until every non-rock zone tile of every zone is rover-reachable
    from base (SPEC S1/S11: "a rover path from A1 to every zone tile exists"
    - read as every zone tile the astronaut could actually be standing on,
    since a rock tile can never be a valid destination for anyone).

    Raises ValueError (message has no "ERROR:" prefix; cmd_init adds it)
    when no attempt within max_attempts succeeds, per SPEC S11's exact
    wording: "ERROR: seed N cannot place three reachable zones"."""
    if not _ZONE_CANDIDATES:
        raise ValueError(f"seed {seed} cannot place three reachable zones")
    for attempt in range(max_attempts):
        sub_seed = seed * 1000 + attempt
        rng, tiles = generate_tiles(sub_seed)
        blocks = _pick_zone_blocks(rng)
        if blocks is None:
            continue
        rover_reach = bfs_reachable(tiles)
        objectives = []
        ok = True
        for obj_id, block in enumerate(blocks, start=1):
            zone = zone_coords(*block)
            passable_zone = [c for c in zone if passable(tiles[c])]
            if not passable_zone or not all(c in rover_reach for c in passable_zone):
                ok = False
                break
            astronaut_at = rng.choice(passable_zone)
            objectives.append({
                "id": obj_id,
                "zone": zone,
                "astronaut_at": astronaut_at,
                "status": "pending",
                "seen_at": None,
                "seen_sol": None,
            })
        if not ok:
            continue
        return tiles, objectives, sub_seed
    raise ValueError(f"seed {seed} cannot place three reachable zones")


def legal_moves_for(truth, uid):
    u = truth["units"][uid]
    start = u["at"]
    seen = {start: 0}
    q = deque([start])
    reachable = []
    while q:
        cur = q.popleft()
        d = seen[cur]
        if d >= MOVE_RANGE:
            continue
        for n in neighbors(cur):
            if n not in seen and passable(truth["tiles"][n]):
                seen[n] = d + 1
                q.append(n)
                reachable.append(n)
    return sorted(reachable)


def _bfs_path(truth, start, target, limit):
    """Shortest path (by tile count) from start to target through passable
    tiles, at most `limit` steps. Returns the list of coords entered
    (excluding start), or None if unreachable within limit."""
    if start == target:
        return []
    parent = {start: None}
    depth = {start: 0}
    q = deque([start])
    while q:
        cur = q.popleft()
        if depth[cur] >= limit:
            continue
        for n in neighbors(cur):
            if n in parent:
                continue
            if not passable(truth["tiles"][n]):
                continue
            parent[n] = cur
            depth[n] = depth[cur] + 1
            if n == target:
                path = []
                node = n
                while node != start:
                    path.append(node)
                    node = parent[node]
                path.reverse()
                return path
            q.append(n)
    return None


# --------------------------------------------------------------------------
# State persistence
# --------------------------------------------------------------------------

def load_truth():
    """Return the parsed truth dict, or None if no mission has been started.

    Returns a string starting with 'ERROR:' instead if the file exists but
    is unreadable/corrupt, so callers can print it verbatim and return 1.
    """
    if not TRUTH_FILE.exists():
        return None
    try:
        with open(TRUTH_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return f"ERROR: game/truth.json is missing or corrupt ({exc}). Run init --force."


def atomic_write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def save_truth(truth):
    atomic_write_json(TRUTH_FILE, truth)


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# Objective helpers (SPEC S11)
# --------------------------------------------------------------------------

def current_objective(truth):
    """Return the dict for truth["current_objective"], or None. None only
    happens before any mission exists in principle - init always opens
    objective 1 immediately, so in practice this is never None once a
    mission has been started."""
    cur = truth.get("current_objective")
    if cur is None:
        return None
    return truth["objectives"][cur - 1]


def objective_clock_running(truth):
    """True while an objective's oxygen clock is actually ticking: the
    mission is in the active phase and the current objective is still
    open (not yet rescued/lost)."""
    obj = current_objective(truth)
    return truth["phase"] == "active" and obj is not None and obj["status"] == "open"


def require_not_awaiting(truth):
    """Returns an error code (already printed) if the mission is paused for
    a human decision, else None. SPEC S11: act/order/note/resolve are
    refused with this exact message while phase == awaiting-orders."""
    if truth["phase"] == "awaiting-orders":
        return error(AWAITING_ORDERS_ERROR)
    return None


# --------------------------------------------------------------------------
# team.json derivation (fog of war) - SPEC S4, extended by S11
# --------------------------------------------------------------------------

def derive_team(truth):
    explored = set(truth["explored"])
    tiles = {coord: {"terrain": truth["tiles"][coord]["terrain"]} for coord in sorted(explored)}

    cur_obj = current_objective(truth)
    is_open = objective_clock_running(truth)

    # Never leak pending zones or astronaut tiles (SPEC S11): only the
    # currently-open objective's zone/oxygen are ever exposed to team.json.
    search_zone = list(cur_obj["zone"]) if is_open else []
    oxygen_sols = truth["oxygen_sols"] if is_open else None

    rescued_tiles = [o["astronaut_at"] for o in truth["objectives"] if o["status"] == "rescued"]

    if cur_obj is None:
        astro_status, seen_at, seen_sol = "none", None, None
    elif cur_obj["status"] == "rescued":
        astro_status, seen_at, seen_sol = "rescued", cur_obj["seen_at"], cur_obj["seen_sol"]
    elif cur_obj["status"] == "lost":
        astro_status, seen_at, seen_sol = "lost", cur_obj["seen_at"], cur_obj["seen_sol"]
    elif cur_obj["seen_at"] is not None:
        astro_status, seen_at, seen_sol = "seen", cur_obj["seen_at"], cur_obj["seen_sol"]
    else:
        astro_status, seen_at, seen_sol = "unknown", None, None

    field_repairs = []
    if FIELD_DIR.exists():
        for p in sorted(FIELD_DIR.glob("*.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if data.get("type") == "repair" and data.get("unit"):
                field_repairs.append(data["unit"])

    team = {
        "schema": 4,
        "sol": truth["sol"],
        "outcome": truth["outcome"],
        "phase": truth["phase"],
        "oxygen_sols": oxygen_sols,
        "oxygen_max": truth["oxygen_max"],
        "base": truth["base"],
        "search_zone": search_zone,
        "rescued": rescued_tiles,
        "objective": {
            "current": truth["current_objective"],
            "total": len(truth["objectives"]),
            "rescued_count": len(rescued_tiles),
        },
        "units": truth["units"],
        "tiles": tiles,
        "astronaut": {
            "status": astro_status,
            "seen_at": seen_at,
            "seen_sol": seen_sol,
        },
        "pending": {
            "ledger": truth["pending"]["ledger"],
            "repairs": field_repairs,
        },
        "notes": truth["notes"],
        "updated": now_iso(),
    }
    return team


def save_team(truth):
    team = derive_team(truth)
    atomic_write_json(TEAM_FILE, team)
    return team


# --------------------------------------------------------------------------
# Small output helpers
# --------------------------------------------------------------------------

def error(msg):
    print(f"ERROR: {msg}")
    return 1


def kv(pairs):
    """Format an OK-line's trailing key=value list (lists comma-joined, no
    spaces inside values)."""
    parts = []
    for k, v in pairs:
        if isinstance(v, (list, tuple)):
            v = ",".join(str(x) for x in v) if v else "-"
        parts.append(f"{k}={v}")
    return " ".join(parts)


TRUTH_LOAD_ERROR_NO_MISSION = "No mission. Start one with: python engine/engine.py init"


def require_truth():
    """Load truth.json for a mutating/reading command. Returns (truth, None)
    or (None, error_code) where error_code has already been printed."""
    truth = load_truth()
    if truth is None:
        return None, error(TRUTH_LOAD_ERROR_NO_MISSION)
    if isinstance(truth, str):
        print(truth)
        return None, 1
    return truth, None


def require_active_mission(truth):
    """Returns an error code (already printed) if the mission is over,
    else None."""
    if truth["outcome"] is not None:
        return error(f"mission over (outcome={truth['outcome']})")
    return None


# --------------------------------------------------------------------------
# init
# --------------------------------------------------------------------------

def cmd_init(args):
    existing = load_truth()
    if isinstance(existing, dict) and existing.get("outcome") is None and not args.force:
        return error("A mission is already in progress. Use --force to start over.")

    seed = args.seed if args.seed is not None else random.randrange(1_000_000)
    try:
        tiles, objectives, sub_seed = generate_mission(seed)
    except ValueError as exc:
        return error(str(exc))

    explored = {
        f"{ROWS[r]}{c + 1}"
        for r in range(INITIAL_EXPLORED_BLOCK)
        for c in range(INITIAL_EXPLORED_BLOCK)
    }

    units = {
        uid: {"role": "rover", "at": BASE, "status": "ok", "move_used": False, "scan_used": False}
        for uid in UNIT_IDS
    }

    objectives[0]["status"] = "open"  # SPEC S11: "init opens objective 1 immediately"

    truth = {
        "schema": 4,
        "seed": seed,
        "sol": 1,
        "outcome": None,          # null | aborted | complete (mission-level; SPEC S11)
        "objective_settled": False,  # engine bookkeeping (renamed from v1/v3's
                                      # outcome_settled): becomes True once a
                                      # resolve has settled the current
                                      # awaiting-orders objective (see
                                      # cmd_resolve); a further resolve while
                                      # still awaiting orders then refuses.
        "phase": "active",        # active | awaiting-orders
        "oxygen_sols": args.oxygen_sols,
        "oxygen_max": args.oxygen_sols,
        "base": BASE,
        "current_objective": 1,
        "objectives": objectives,
        "tiles": tiles,
        "explored": sorted(explored),
        "units": units,
        "pending": {"ledger": None},
        "last_ledger_mode": None,
        "notes": [],
        "applied_log": [],
    }

    for d in (ORDERS_DIR, FIELD_DIR, FIELD_APPLIED_DIR, INTEL_DIR, SNAPSHOTS_DIR):
        d.mkdir(parents=True, exist_ok=True)
    # A new mission starts with a clean log as well as a clean timeline: the
    # snapshots below are wiped, so leaving the previous mission's rows in
    # events.jsonl would leave the UI feed showing sols this mission never had.
    EVENTS_FILE.write_text("", encoding="utf-8")
    NOTES_FILE.write_text("# Mission notes\n\n", encoding="utf-8")
    DISPATCHES_FILE.write_text("# Mission Dispatches\n\n", encoding="utf-8")

    for stale in SNAPSHOTS_DIR.glob("sol-*.json"):  # a new mission starts with a clean timeline
        stale.unlink()

    save_truth(truth)
    team = save_team(truth)
    atomic_write_json(SNAPSHOTS_DIR / f"sol-{team['sol']}.json", team)  # named by its sol (sol 1)

    zone = objectives[0]["zone"]
    zone_label = f"{zone[0]}-{zone[-1]}"
    print(
        f"Mission initialised: seed={seed} base={BASE} grid={GRID_COLS}x{GRID_ROWS} "
        f"oxygen={truth['oxygen_sols']} sols=1. Search zone {zone_label}; astronaut position is secret; "
        f"{len(explored)} tiles explored around base."
    )
    return 0


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------

def cmd_status(args):
    truth = load_truth()
    if truth is None:
        print("No mission. Start one with: python engine/engine.py init --seed 42")
        return 0
    if isinstance(truth, str):
        print(truth)
        return 1

    lines = [f"Sol: {truth['sol']}"]
    outcome = truth["outcome"]
    outcome_disp = {None: "in progress", "aborted": "ABORTED", "complete": "COMPLETE"}.get(
        outcome, outcome
    )
    lines.append(f"Outcome: {outcome_disp}")

    phase_disp = "active" if truth["phase"] == "active" else "awaiting orders"
    lines.append(f"Phase: {phase_disp}")

    cur = current_objective(truth)
    total = len(truth["objectives"])
    if cur is not None:
        zone_label = f"{cur['zone'][0]}-{cur['zone'][-1]}"
        lines.append(f"Objective: {truth['current_objective']} of {total} (zone {zone_label})")

    is_open = objective_clock_running(truth)
    lines.append(f"Oxygen: {truth['oxygen_sols']} sols" if is_open else "Oxygen: -- sols")

    if cur is None:
        astro_disp = "none"
    elif cur["status"] == "rescued":
        astro_disp = "rescued"
    elif cur["status"] == "lost":
        astro_disp = "lost"
    elif cur["seen_at"] is not None:
        astro_disp = f"seen at {cur['seen_at']} (sol {cur['seen_sol']})"
    else:
        zone_label = f"{cur['zone'][0]}-{cur['zone'][-1]}"
        astro_disp = f"unknown (search zone {zone_label})"
    lines.append(f"Astronaut: {astro_disp}")

    for uid in UNIT_IDS:
        u = truth["units"][uid]
        lines.append(f"{uid} @{u['at']} {u['status']}")

    team = derive_team(truth)
    pending_bits = []
    for unit in team["pending"]["repairs"]:
        pending_bits.append(f"repair {unit}")
    if team["pending"]["ledger"]:
        pending_bits.append(f"ledger {team['pending']['ledger']['mode']}")
    if pending_bits:
        lines.append("Pending: " + ", ".join(pending_bits))

    # "Unread" is tracked the simple way: a note counts as unread for
    # mission-control while it was recorded on the *current* sol (resolve
    # advances the sol, which naturally "clears" the count) rather than via
    # a separate notes_read_sol cursor.
    unread = sum(
        1 for n in truth["notes"]
        if n["sol"] == truth["sol"] and n["to"] in ("mission-control", "all")
    )
    lines.append(f"Notes: {unread} unread for mission-control")

    print("\n".join(lines))
    return 0


# --------------------------------------------------------------------------
# map / unit / intel
# --------------------------------------------------------------------------

TERRAIN_GLYPH = {"plain": ".", "rough": "~", "rock": "#"}


def render_map(truth, view):
    """ASCII grid. view='team' respects fog; view='truth' shows everything.
    Glyph priority: unit(s) > astronaut > unexplored (zone tiles show 'Z',
    others '?') > base > terrain.

    SPEC S11: only the *current* objective's zone/astronaut ever show up
    here (pending zones and other objectives' astronauts stay secret); this
    matters only for view='team' since view='truth' already marks every
    tile explored regardless of zone."""
    explored = set(truth["explored"])
    cur = current_objective(truth)
    zone = set(cur["zone"]) if (cur is not None and truth["phase"] == "active") else set()
    units_at = {}
    for uid in UNIT_IDS:
        units_at.setdefault(truth["units"][uid]["at"], []).append(uid)

    astro_coord = None
    if cur is not None:
        if view == "truth":
            astro_coord = cur["astronaut_at"]
        elif cur["seen_at"] is not None:
            astro_coord = cur["seen_at"]

    header = "    " + "  ".join(str(c) for c in range(1, GRID_COLS + 1))
    lines = [header]
    for r, row_letter in enumerate(ROWS):
        cells = []
        for c in range(1, GRID_COLS + 1):
            coord = f"{row_letter}{c}"
            is_explored = view == "truth" or coord in explored
            if coord in units_at:
                parts = []
                for uid in units_at[coord]:
                    n = uid.split("-")[1]
                    suffix = "!" if truth["units"][uid]["status"] == "broken" else ""
                    parts.append(f"R{n}{suffix}")
                glyph = "".join(parts)
            elif astro_coord == coord:
                glyph = "*"
            elif not is_explored:
                glyph = "Z" if coord in zone else "?"
            elif coord == BASE:
                glyph = "B"
            else:
                glyph = TERRAIN_GLYPH[truth["tiles"][coord]["terrain"]]
            cells.append(glyph)
        lines.append(f"{row_letter} | " + "  ".join(cells))
    lines.append(
        "Legend: . plain  ~ rough  # rock  ? unexplored  B base  R1/R2 rover "
        "(! broken)  Z current objective's search zone (unexplored; explored zone tiles show "
        "their terrain)  * astronaut"
    )
    return "\n".join(lines)


def cmd_map(args):
    truth, err = require_truth()
    if err is not None:
        return err
    if args.view == "truth":
        # SPEC S3: role_guard denies this to every agent; the engine itself
        # does not enforce roles (that is hooks' job), it just serves it.
        if args.json:
            print(json.dumps(truth, indent=2))
        else:
            print(render_map(truth, "truth"))
        return 0

    team = save_team(truth)
    if args.json:
        print(json.dumps(team, indent=2))
    else:
        print(render_map(truth, "team"))
    return 0


def cmd_unit(args):
    truth, err = require_truth()
    if err is not None:
        return err
    uid = args.unit
    if uid not in UNIT_IDS:
        return error(f"Unknown unit '{args.unit}'. Units: " + ", ".join(UNIT_IDS))

    u = truth["units"][uid]
    moves = legal_moves_for(truth, uid)
    moves_str = ",".join(moves) if moves else "-"
    print(
        f"{uid}: role=rover at={u['at']} status={u['status']} "
        f"move_used={'yes' if u['move_used'] else 'no'} scan_used={'yes' if u['scan_used'] else 'no'} "
        f"legal_moves={moves_str}"
    )
    return 0


def cmd_intel(args):
    truth, err = require_truth()
    if err is not None:
        return err
    team = save_team(truth)
    print(json.dumps(team, indent=2))
    return 0


# --------------------------------------------------------------------------
# act
# --------------------------------------------------------------------------

def act_move(truth, unit_id, arg):
    u = truth["units"][unit_id]
    if u["status"] == "broken":
        return error(f"{unit_id} is broken at {u['at']}; needs an engineer repair")
    if u["move_used"]:
        return error(f"{unit_id} already used its move slot this sol")

    coord = normalize_coord(arg)
    if coord is None:
        return error(f"Invalid coordinate '{arg}'. Use A1-H10.")

    path = _bfs_path(truth, u["at"], coord, MOVE_RANGE)
    if path is None:
        return error(f"{coord} unreachable for {unit_id} within range {MOVE_RANGE} (terrain or path)")

    before_explored = set(truth["explored"])
    entered = []
    broke_down = False
    for c in path:
        entered.append(c)
        if truth["tiles"][c]["terrain"] == "rough":
            broke_down = True
            break

    truth["explored"] = sorted(before_explored | set(entered))
    revealed = sorted(set(entered) - before_explored)

    u["at"] = entered[-1]
    u["move_used"] = True
    if broke_down:
        u["status"] = "broken"

    pairs = [("at", u["at"]), ("path", entered), ("status", u["status"]), ("revealed", revealed)]
    if broke_down:
        pairs.append(("note", "entered rough terrain"))
    print(f"OK: {unit_id} move {coord} | " + kv(pairs))
    return 0


def act_scan(truth, unit_id):
    u = truth["units"][unit_id]
    if u["status"] == "broken":
        return error(f"{unit_id} is broken at {u['at']}; needs an engineer repair")
    if u["scan_used"]:
        return error(f"{unit_id} already scanned this sol")

    # cmd_act already refuses act while phase == awaiting-orders, so an
    # objective is guaranteed to be open here.
    obj = current_objective(truth)

    at = u["at"]
    before_explored = set(truth["explored"])
    in_radius = {c for c in all_coords() if dist(at, c) <= SCAN_RADIUS}
    truth["explored"] = sorted(before_explored | in_radius)
    revealed = sorted(in_radius - before_explored)

    astronaut_at = obj["astronaut_at"]
    seen_now = astronaut_at in in_radius
    if seen_now and obj["seen_at"] is None:
        obj["seen_at"] = astronaut_at
        obj["seen_sol"] = truth["sol"]

    u["scan_used"] = True
    astro_field = astronaut_at if seen_now else "none"
    print(
        f"OK: {unit_id} scan | " + kv([("at", at), ("revealed", revealed), ("astronaut", astro_field)])
    )
    return 0


def act_rescue(truth, unit_id):
    u = truth["units"][unit_id]
    obj = current_objective(truth)
    if obj is None or u["at"] != obj["astronaut_at"]:
        return error(f"no astronaut at {u['at']}")

    if obj["seen_at"] is None:
        obj["seen_at"] = u["at"]
        obj["seen_sol"] = truth["sol"]
    obj["status"] = "rescued"
    # SPEC S11: rescue freezes this objective's clock and pauses the
    # mission for a human decision; objective_settled is already False here
    # (it can only become True by a resolve while awaiting-orders, and we
    # were in the active phase up to this call), so the next `resolve`
    # still runs once to let this sol tick forward.
    truth["phase"] = "awaiting-orders"
    print(f"OK: {unit_id} rescue | " + kv([("at", u["at"]), ("outcome", "rescued"), ("oxygen", truth["oxygen_sols"])]))
    return 0


def cmd_act(args):
    truth, err = require_truth()
    if err is not None:
        return err
    blocked = require_active_mission(truth)
    if blocked is not None:
        return blocked
    blocked = require_not_awaiting(truth)
    if blocked is not None:
        return blocked

    unit_id = args.unit
    if unit_id not in UNIT_IDS:
        return error(f"Unknown unit '{args.unit}'. Units: " + ", ".join(UNIT_IDS))

    action = args.action
    if action not in ACTIONS:
        return error(f"{unit_id} cannot {action} (role rover allows: {', '.join(ACTIONS)})")

    if action == "move":
        rc = act_move(truth, unit_id, args.arg)
    elif action == "scan":
        rc = act_scan(truth, unit_id)
    else:
        rc = act_rescue(truth, unit_id)

    if rc == 0:
        save_truth(truth)
        save_team(truth)
    return rc


# --------------------------------------------------------------------------
# order
# --------------------------------------------------------------------------

def cmd_order(args):
    truth, err = require_truth()
    if err is not None:
        return err
    blocked = require_active_mission(truth)
    if blocked is not None:
        return blocked
    blocked = require_not_awaiting(truth)
    if blocked is not None:
        return blocked

    sol = truth["sol"]
    path = ORDERS_DIR / f"sol-{sol}.md"
    ORDERS_DIR.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(f"# Orders - Sol {sol}\n\n", encoding="utf-8")
    text = " ".join(args.text)
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"- **{args.unit}**: {text}\n")
    print(f"ORDER: sol={sol} unit={args.unit} recorded")
    return 0


# --------------------------------------------------------------------------
# note / notes
# --------------------------------------------------------------------------

def cmd_note(args):
    truth, err = require_truth()
    if err is not None:
        return err
    blocked = require_active_mission(truth)
    if blocked is not None:
        return blocked
    blocked = require_not_awaiting(truth)
    if blocked is not None:
        return blocked

    sol = truth["sol"]
    frm, to = args.from_, args.to
    text = " ".join(args.text)

    truth["notes"].append({"sol": sol, "from": frm, "to": to, "text": text})
    save_truth(truth)
    save_team(truth)

    if not NOTES_FILE.exists():
        NOTES_FILE.write_text("# Mission notes\n\n", encoding="utf-8")
    with open(NOTES_FILE, "a", encoding="utf-8") as f:
        f.write(f"- sol {sol} - **{frm} -> {to}**: {text}\n")

    print(f"NOTE: sol={sol} from={frm} to={to} recorded")
    return 0


def cmd_notes(args):
    truth, err = require_truth()
    if err is not None:
        return err

    for n in truth["notes"]:
        if args.for_ and n["to"] not in (args.for_, "all"):
            continue
        print(f"sol {n['sol']} - {n['from']} -> {n['to']}: {n['text']}")
    return 0


# --------------------------------------------------------------------------
# apply-ledger
# --------------------------------------------------------------------------

LEDGER_KEYS = {"sol", "mode", "note"}


def parse_ledger_file():
    """Parse the 'key: value' lines of oxygen-ledger.md. Returns
    (dict_or_None, error_message_or_None)."""
    data = {}
    for line in LEDGER_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key not in LEDGER_KEYS:
            return None, f"ledger: unknown key '{key}'"
        data[key] = value
    return data, None


def cmd_apply_ledger(args):
    truth, err = require_truth()
    if err is not None:
        return err
    blocked = require_active_mission(truth)
    if blocked is not None:
        return blocked

    if not LEDGER_FILE.exists():
        return error("ledger: no game/field/oxygen-ledger.md found")

    data, parse_err = parse_ledger_file()
    if parse_err is not None:
        return error(parse_err)

    try:
        ledger_sol = int(data.get("sol", ""))
    except ValueError:
        return error("ledger: unknown key 'sol'")
    if ledger_sol != truth["sol"]:
        return error(f"ledger sol mismatch (ledger says {ledger_sol}, mission is at {truth['sol']})")

    mode = data.get("mode", "normal").lower()
    if mode == "conserve" and truth.get("last_ledger_mode") == "conserve":
        return error("ledger: conserve mode used last sol; astronaut must breathe")

    truth["pending"]["ledger"] = {"sol": ledger_sol, "mode": mode}
    save_truth(truth)
    save_team(truth)
    print(f"LEDGER: accepted sol={ledger_sol} mode={mode}")
    return 0


# --------------------------------------------------------------------------
# field-check
# --------------------------------------------------------------------------

def cmd_field_check(args):
    truth, err = require_truth()
    if err is not None:
        return err

    p = Path(args.path)
    if not p.exists():
        return error("field: bad json")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return error("field: bad json")

    if not isinstance(data, dict) or "type" not in data:
        return error("field: missing key 'type'")
    if data["type"] != "repair":
        return error("field: unknown type")

    unit = data.get("unit")
    if unit not in UNIT_IDS or truth["units"][unit]["status"] != "broken":
        return error(f"field: unit {unit} is not broken")
    if data.get("sol") != truth["sol"]:
        return error("field: sol mismatch")
    if p.name != f"repair-{unit}.json":
        return error(f"field: filename must be repair-{unit}.json")

    print(f"FIELD: accepted repair {unit}")
    return 0


# --------------------------------------------------------------------------
# resolve
# --------------------------------------------------------------------------

def _apply_field_files(truth, sol):
    """Apply every game/field/*.json repair file, moving it into
    field/applied/ (or field/applied/ with a REJECTED prefix if invalid).
    Returns a list of human-readable 'applied: ...' lines."""
    FIELD_APPLIED_DIR.mkdir(parents=True, exist_ok=True)
    lines = []
    if not FIELD_DIR.exists():
        return lines
    for p in sorted(FIELD_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None

        valid = False
        unit = None
        if isinstance(data, dict) and data.get("type") == "repair":
            unit = data.get("unit")
            valid = (
                unit in UNIT_IDS
                and truth["units"][unit]["status"] == "broken"
                and data.get("sol") == sol
                and p.name == f"repair-{unit}.json"
            )

        if valid:
            truth["units"][unit]["status"] = "ok"
            truth["applied_log"].append({"sol": sol, "type": "repair", "unit": unit})
            lines.append(f"applied: repair {unit}")
            dest = FIELD_APPLIED_DIR / f"sol-{sol}-{p.name}"
        else:
            dest = FIELD_APPLIED_DIR / f"sol-{sol}-REJECTED-{p.name}"
        os.replace(p, dest)
    return lines


def cmd_resolve(args):
    truth, err = require_truth()
    if err is not None:
        return err
    blocked = require_active_mission(truth)
    if blocked is not None:
        return blocked

    # SPEC S11: resolve is refused while awaiting orders, EXCEPT once - the
    # very resolve that ends the sol a rescue (or an oxygen-loss) happened
    # on must still be allowed to run so that sol ticks forward. We reuse
    # (rename) the v1/v3 outcome_settled flag as objective_settled: it is
    # False for the sol the objective closed on (this resolve is allowed
    # and, at the bottom, flips it True) and True afterwards (every further
    # resolve is refused until continue-mission/rest opens a new sol).
    if truth["phase"] == "awaiting-orders" and truth.get("objective_settled"):
        return error(AWAITING_ORDERS_ERROR)

    old_sol = truth["sol"]
    lines = []

    lines.extend(_apply_field_files(truth, old_sol))

    ledger = truth["pending"]["ledger"]
    mode = ledger["mode"] if ledger else "normal"
    oxygen_cost = 0 if mode == "conserve" else 1

    clock_running = objective_clock_running(truth)
    applied_cost = oxygen_cost if clock_running else 0
    if clock_running:
        truth["oxygen_sols"] = max(0, truth["oxygen_sols"] - oxygen_cost)
    lines.append(f"ledger: mode={mode} (oxygen -{applied_cost})")
    truth["last_ledger_mode"] = mode
    truth["pending"]["ledger"] = None

    obj = current_objective(truth)
    if clock_running and truth["oxygen_sols"] <= 0:
        obj["status"] = "lost"
        truth["phase"] = "awaiting-orders"
        lines.append("LOST: oxygen exhausted")

    for u in truth["units"].values():
        u["move_used"] = False
        u["scan_used"] = False

    unit_bits = "; ".join(
        f"{uid} {truth['units'][uid]['at']} {truth['units'][uid]['status']}" for uid in UNIT_IDS
    )
    lines.append(f"units: {unit_bits}")

    if truth["phase"] == "awaiting-orders" and obj is not None and obj["status"] == "rescued":
        lines.append(
            f"RESCUED: OBJECTIVE {truth['current_objective']} OF {len(truth['objectives'])} "
            "-> TEAM WAITING FOR ORDERS"
        )

    new_sol = old_sol + 1
    truth["sol"] = new_sol
    if truth["phase"] == "awaiting-orders":
        truth["objective_settled"] = True

    save_truth(truth)
    team = save_team(truth)
    atomic_write_json(SNAPSHOTS_DIR / f"sol-{new_sol}.json", team)

    header = (
        f"RESOLVED: sol={old_sol} -> {new_sol} | oxygen={truth['oxygen_sols']} "
        f"outcome={truth['outcome'] or 'none'}"
    )
    print(header)
    for line in lines:
        print(f"    {line}")
    return 0


# --------------------------------------------------------------------------
# sitrep / continue-mission / rest / abort-mission (SPEC S11)
# --------------------------------------------------------------------------

def cmd_sitrep(args):
    truth, err = require_truth()
    if err is not None:
        return err

    cur = current_objective(truth)
    is_open = objective_clock_running(truth)
    total = len(truth["objectives"])

    lines = [f"SOL {truth['sol']}"]
    if is_open:
        lines.append(f"OXYGEN: {truth['oxygen_sols']}/{truth['oxygen_max']}")
        explored = set(truth["explored"])
        pct = round(100 * sum(1 for c in cur["zone"] if c in explored) / len(cur["zone"]))
        lines.append(f"ZONE EXPLORED: {pct}%")
    else:
        lines.append(f"OXYGEN: --/{truth['oxygen_max']}")
        lines.append("ZONE EXPLORED: --")

    online = sum(1 for uid in UNIT_IDS if truth["units"][uid]["status"] == "ok")
    damaged = sum(1 for uid in UNIT_IDS if truth["units"][uid]["status"] == "broken")
    lines.append(f"ROVERS ONLINE: {online}/{len(UNIT_IDS)}")
    lines.append(f"ROVERS DAMAGED: {damaged}/{len(UNIT_IDS)}")

    rescued_count = sum(1 for o in truth["objectives"] if o["status"] == "rescued")
    lines.append(f"COLONISTS RESCUED: {rescued_count}/{total}")

    lines.append("")
    if truth["phase"] == "awaiting-orders":
        lines.append("TEAM WAITING FOR ORDERS:")
    else:
        lines.append(f"TEAM ACTIVE: OBJECTIVE {truth['current_objective']} OF {total}")
    lines.append("1 - CONTINUE RESCUE MISSION")
    lines.append("2 - REST AND REPAIR")
    lines.append("3 - ABORT MISSION")

    print("\n".join(lines))
    return 0


def cmd_continue_mission(args):
    truth, err = require_truth()
    if err is not None:
        return err
    blocked = require_active_mission(truth)
    if blocked is not None:
        return blocked
    # Not in SPEC S11's literal text, but guards against a mis-issued
    # continue-mission opening a second objective on top of one that is
    # still open (only reachable while awaiting orders in the intended
    # flow) - see the deviation note in the build report.
    if truth["phase"] != "awaiting-orders":
        return error("continue-mission is only available while the team is waiting for orders; run sitrep")

    pending = [o for o in truth["objectives"] if o["status"] == "pending"]
    if not pending:
        return error("no colonists left to rescue; abort-mission to close the mission")

    nxt = pending[0]
    nxt["status"] = "open"
    truth["current_objective"] = nxt["id"]
    truth["oxygen_sols"] = truth["oxygen_max"]
    truth["phase"] = "active"
    truth["objective_settled"] = False

    save_truth(truth)
    save_team(truth)

    zone_label = f"{nxt['zone'][0]}-{nxt['zone'][-1]}"
    print(
        f"ORDERS RECEIVED: CONTINUE RESCUE MISSION -> OBJECTIVE {nxt['id']} OF {len(truth['objectives'])}, "
        f"ZONE {zone_label}, OXYGEN {truth['oxygen_max']} SOLS"
    )
    return 0


def cmd_rest(args):
    truth, err = require_truth()
    if err is not None:
        return err
    blocked = require_active_mission(truth)
    if blocked is not None:
        return blocked
    if truth["phase"] == "active":
        return error("rest is only possible while the team is waiting for orders")

    for u in truth["units"].values():
        u["status"] = "ok"
        u["move_used"] = False
        u["scan_used"] = False

    old_sol = truth["sol"]
    new_sol = old_sol + 1
    truth["sol"] = new_sol
    # No clock is running while awaiting orders, so no oxygen tick here.

    save_truth(truth)
    team = save_team(truth)
    atomic_write_json(SNAPSHOTS_DIR / f"sol-{new_sol}.json", team)

    online = sum(1 for u in truth["units"].values() if u["status"] == "ok")
    print(f"ORDERS RECEIVED: REST AND REPAIR -> SOL {new_sol}, ROVERS ONLINE {online}/{len(UNIT_IDS)}")
    return 0


def cmd_abort_mission(args):
    truth, err = require_truth()
    if err is not None:
        return err
    blocked = require_active_mission(truth)
    if blocked is not None:
        return blocked

    total = len(truth["objectives"])
    rescued_count = sum(1 for o in truth["objectives"] if o["status"] == "rescued")
    truth["outcome"] = "complete" if rescued_count == total else "aborted"
    truth["phase"] = "awaiting-orders"

    save_truth(truth)
    save_team(truth)

    print(f"ORDERS RECEIVED: ABORT MISSION -> MISSION CLOSED, COLONISTS RESCUED {rescued_count}/{total}")
    return 0


# --------------------------------------------------------------------------
# events / replay
# --------------------------------------------------------------------------

def _read_events():
    events = []
    if not EVENTS_FILE.exists():
        return events
    for line in EVENTS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _subcommand_from_command(command):
    """Return the part of a shell command after ``engine.py`` (any path
    form). Subagents call the engine as ``python engine/engine.py ...``,
    sometimes with a quoted absolute path. Strip everything up to and
    including engine.py plus a trailing quote."""
    if not command:
        return ""
    stripped = re.sub(r'^.*?engine\.py["\']?\s*', "", command.strip(), count=1, flags=re.S)
    return stripped.strip() or command.strip()


def cmd_events(args):
    events = _read_events()
    if args.json:
        for i, ev in enumerate(events):
            if i < args.since:
                continue
            out = dict(ev)
            out["index"] = i
            print(json.dumps(out))
        return 0

    for i, ev in enumerate(events):
        if i < args.since:
            continue
        kind = ev.get("kind", "")
        disp_kind = "BLOCKED" if kind == "blocked" else kind
        agent_type = ev.get("agent_type") or "-"
        unit = ev.get("unit") or "-"
        sol = ev.get("sol")
        sol_str = f"sol{sol}" if sol is not None else "sol?"
        sub = _subcommand_from_command(ev.get("command", ""))
        if kind == "blocked" and ev.get("reason"):
            tail = f"-> {ev['reason']}"
        elif kind == "message":
            tail = f"-> {ev.get('from')} -> {ev.get('to')}: {ev.get('text')}"
        else:
            tail = f"-> {ev.get('result', '')}"
        print(f"#{i} {sol_str} {disp_kind} {agent_type}/{unit} {sub} {tail}")
    return 0


def cmd_replay(args):
    if not SNAPSHOTS_DIR.exists():
        print("No snapshots yet.")
        return 0
    events = _read_events()

    def snapshot_sol(p):
        m = re.match(r"sol-(\d+)\.json$", p.name)
        return int(m.group(1)) if m else -1

    snaps = sorted(SNAPSHOTS_DIR.glob("sol-*.json"), key=snapshot_sol)
    for p in snaps:
        try:
            snap = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        # Label by the snapshot's filename; init writes sol-1.json and every
        # resolve writes sol-<newsol>.json, so names and contents agree.
        sol = snapshot_sol(p)
        explored = len(snap.get("tiles", {}))
        units_str = ",".join(
            f"{uid}@{snap['units'][uid]['at']}" + ("!" if snap["units"][uid]["status"] == "broken" else "")
            for uid in UNIT_IDS if uid in snap.get("units", {})
        )
        astro_status = snap.get("astronaut", {}).get("status", "unknown")
        notes_count = len(snap.get("notes", []))
        events_count = sum(1 for ev in events if ev.get("sol") is not None and ev.get("sol") <= sol)
        oxygen_disp = snap.get("oxygen_sols")
        oxygen_disp = oxygen_disp if oxygen_disp is not None else "--"
        objective = snap.get("objective", {})
        rescued_count = objective.get("rescued_count", 0)
        rescued_total = objective.get("total", NUM_OBJECTIVES)
        print(
            f"sol {sol}: oxygen={oxygen_disp} explored={explored} units={units_str} "
            f"astronaut={astro_status} notes={notes_count} events={events_count} "
            f"rescued={rescued_count}/{rescued_total}"
        )
    return 0


# --------------------------------------------------------------------------
# CLI wiring
# --------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(prog="engine.py", description="Mars Search & Rescue ground-truth engine.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="Start a new mission.")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--force", action="store_true")
    p.add_argument("--oxygen-sols", type=int, default=12, dest="oxygen_sols")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("status", help="Print mission status.")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("map", help="Render the map.")
    p.add_argument("--view", choices=["team", "truth"], default="team")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_map)

    p = sub.add_parser("unit", help="Describe one unit.")
    p.add_argument("unit")
    p.set_defaults(func=cmd_unit)

    p = sub.add_parser("intel", help="Print team.json.")
    p.set_defaults(func=cmd_intel)

    p = sub.add_parser("act", help="Perform a unit action.")
    p.add_argument("unit")
    p.add_argument("action")
    p.add_argument("arg", nargs="?", default=None)
    p.set_defaults(func=cmd_act)

    p = sub.add_parser("order", help="Record an order.")
    p.add_argument("unit")
    p.add_argument("text", nargs="+")
    p.set_defaults(func=cmd_order)

    p = sub.add_parser("note", help="Record a note between agents.")
    p.add_argument("from_", metavar="from")
    p.add_argument("to")
    p.add_argument("text", nargs="+")
    p.set_defaults(func=cmd_note)

    p = sub.add_parser("notes", help="Print recorded notes.")
    p.add_argument("--for", dest="for_", default=None)
    p.set_defaults(func=cmd_notes)

    p = sub.add_parser("apply-ledger", help="Apply the oxygen ledger.")
    p.set_defaults(func=cmd_apply_ledger)

    p = sub.add_parser("field-check", help="Validate a field/*.json file.")
    p.add_argument("path")
    p.set_defaults(func=cmd_field_check)

    p = sub.add_parser("resolve", help="Resolve the sol.")
    p.set_defaults(func=cmd_resolve)

    p = sub.add_parser("sitrep", help="Print the situation report block.")
    p.set_defaults(func=cmd_sitrep)

    p = sub.add_parser("continue-mission", help="Open the next pending objective.")
    p.set_defaults(func=cmd_continue_mission)

    p = sub.add_parser("rest", help="Rest and repair while awaiting orders.")
    p.set_defaults(func=cmd_rest)

    p = sub.add_parser("abort-mission", help="Close out the mission.")
    p.set_defaults(func=cmd_abort_mission)

    p = sub.add_parser("events", help="Print events.jsonl.")
    p.add_argument("--since", type=int, default=0)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_events)

    p = sub.add_parser("replay", help="Summarise every snapshot.")
    p.set_defaults(func=cmd_replay)

    return parser


def run(argv):
    """Parse argv and dispatch. Never calls sys.exit; returns the exit code
    so tests can call this directly under contextlib.redirect_stdout."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    return args.func(args)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
