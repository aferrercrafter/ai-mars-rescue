# Mars Search & Rescue — SPEC v3 (frozen contracts)

Status: approved 2026-09-14 (v3 = the reduced demo: five roles, three mechanics, interactions via notes). v1 is in git history. Conventions unchanged: engine is the only writer of game truth; `ERROR:` lines on **stdout** with exit 1; atomic writes; hooks fail open on malformed stdin; paths normalised with `replace("\\", "/")`; commands run from the project root.

## §1 Grid and mechanics (only three)

| Item | Contract |
|---|---|
| Grid | 8 columns × 6 rows. Rows `A`–`F` top→bottom, columns `1`–`8`. Coord regex `^[A-F][1-8]$`. `A1` top-left = base. Chebyshev distance. |
| Terrain | `plain` 65 %, `rough` 25 %, `rock` 10 %. Rovers cross plain and rough; rock is impassable. Base is plain; no rock in A1–B2. |
| Fog | `truth.json` has every tile; `team.json.tiles` only explored tiles. Init explores the 3×3 block A1–C3. Moving reveals tiles entered; `scan` reveals radius 1 (terrain) and sights the astronaut if within radius 1. |
| Search zone | A 3×3 block of tiles, at least 4 tiles from base, containing the astronaut (uniform among its plain/rough tiles). The zone is **known** from the start: `team.json.search_zone = ["D5","D6","D7","E5",…]`. The astronaut's exact tile is secret until sighted. Generator retries (sub-seed `seed*1000+attempt`) until a rover path from A1 to every zone tile exists. |
| Breakdown | A rover that **enters** a `rough` tile stops there and becomes `status: broken`. A broken rover cannot move or scan. The engineer files `game/field/repair-<unit>.json`; `resolve` applies it → `ok`. Rough tiles are visible once explored (`~` on the map), so the second breakdown is a choice, the first is a surprise. |
| Oxygen | Starts at `--oxygen-sols` (default 8). Each `resolve` subtracts 1 in `mode: normal`, 0 in `mode: conserve`; conserve cannot be used two sols in a row (`ERROR: ledger: conserve mode used last sol; astronaut must breathe`). Reaches 0 before rescue → `outcome: lost`. Rescue freezes it. |

No beacon, no storms, no blackout, no battery, no bridges, no ridges/craters.

## §2 Units

| Unit ids | role (`agent_type`) | move range | actions |
|---|---|---|---|
| `rover-1`, `rover-2` | `rover` | 2 (BFS through plain/rough; path stops at the first rough tile entered) | `move <coord>`, `scan`, `rescue` |

One `move` slot and one `scan` slot per unit per sol; `rescue` uses no slot and only works when the rover stands on the astronaut's tile (`ERROR: no astronaut at <coord>`). `resolve` resets slots.

## §3 Engine CLI — `python engine/engine.py <cmd>`

Exit codes 0/1/2 as before. `run(argv) -> int`, `main()`. `act` success line: `OK: <unit> <action> [<arg>] | k=v …`.

| Command | Args | stdout on success |
|---|---|---|
| `init` | `[--seed N] [--force] [--oxygen-sols N]` | `Mission initialised: seed=42 base=A1 grid=8x6 oxygen=8 sols=1. Search zone D5-F7; astronaut position is secret; 9 tiles explored around base.` Creates `game/{orders,field,field/applied,intel,snapshots}`, empty `events.jsonl` (if absent), `notes.md` header, `dispatches.md` header, `snapshots/sol-1.json`; clears stale snapshots. |
| `status` | — | `Sol: 2` / `Outcome: in progress|RESCUED|LOST` / `Oxygen: 7 sols` / `Astronaut: unknown (search zone D5-F7)|seen at E6 (sol 2)|rescued` / `rover-1 @C3 ok|broken` / `rover-2 @D4 broken` / `Pending: repair rover-2, ledger conserve` / `Notes: 2 unread for mission-control` |
| `map` | `[--view team|truth] [--json]` | ASCII grid; `.` plain `~` rough `#` rock `?` unexplored `B` base `R1`/`R2` rover (`!` suffix when broken, e.g. `R2!`) `Z` search-zone tile (unexplored zone tiles show `Z`, explored ones show their terrain with a `z` overlay in the legend line) `*` astronaut (truth view or once seen). Legend line last. `--view truth` denied to every agent by role_guard. |
| `unit` | `<id>` | `rover-1: role=rover at=C3 status=ok move_used=no scan_used=no legal_moves=B2,B3,C4,D3,…` |
| `intel` | — | `team.json` verbatim (JSON, indent 2). |
| `act` | `<unit> move <coord>` / `<unit> scan` / `<unit> rescue` | `OK: rover-1 move D4 \| at=D4 path=C4,D4 status=ok revealed=C4,D4` · broken: `OK: rover-2 move E5 \| at=D4 path=D4 status=broken revealed=D4 note=entered rough terrain` · `OK: rover-1 scan \| at=D4 revealed=C3,C4,C5,D3,D5,E3,E4,E5 astronaut=none|E5` · `OK: rover-1 rescue \| at=E5 outcome=rescued oxygen=6` |
| `order` | `<unit|all> <text…>` | `ORDER: sol=2 unit=rover-1 recorded` → appends to `game/orders/sol-N.md` |
| `note` | `<from> <to> <text…>` (`to` = unit id, role name, `mission-control`, or `all`) | `NOTE: sol=2 from=rover-2 to=engineer recorded` → appends `- sol 2 - **rover-2 -> engineer**: text` to `game/notes.md` and to `team.json.notes[]` (`{sol, from, to, text}`). This is the **only** channel between agents; the display's Interactions view is built from it. |
| `notes` | `[--for <name>]` | prints the notes (all, or those addressed to `<name>`/`all`) one per line: `sol 2 - rover-2 -> engineer: broke down at D4, need a repair` |
| `apply-ledger` | — | `LEDGER: accepted sol=2 mode=conserve` (parses `game/field/oxygen-ledger.md`: `sol:` and `mode:` keys only) |
| `field-check` | `<path>` | `FIELD: accepted repair rover-2` (rules: unit exists and is broken; `sol` == current; only `repair-<unit>.json` names) |
| `resolve` | — | `RESOLVED: sol=2 -> 3 \| oxygen=6 outcome=none` then indented `applied: repair rover-2`, `ledger: mode=normal (oxygen -1)`, `units: rover-1 D4 ok; rover-2 D4 ok`, `RESCUED`/`LOST: oxygen exhausted`. Applies repairs and ledger, ticks oxygen, resets slots, sol += 1, writes `snapshots/sol-<newsol>.json`. |
| `events` | `[--since N] [--json]` | as v1 |
| `replay` | — | `sol 2: oxygen=7 explored=17 units=rover-1@C3,rover-2@D4! astronaut=unknown notes=1 events=22` |

Error strings keep the v1 wording where the concept survives (`Unknown unit`, `Invalid coordinate 'X'. Use A1-F8.`, `cannot <action>`, slot errors, `is broken at <coord>; needs an engineer repair`, `unreachable`, `no astronaut at`, `mission over`, ledger and field errors). Removed: everything about battery, storm, beacon, blackout, bridge, drone, eva, carry, recharge.

## §4 State files

**`truth.json`**: `{schema:3, seed, sol, outcome, outcome_settled, oxygen_sols, oxygen_max, base, search_zone:[…], astronaut:{at, status}, tiles:{"A1":{terrain}}, explored:[…], units:{id:{role, at, status, move_used, scan_used}}, pending:{ledger}, last_ledger_mode, notes:[{sol,from,to,text}], applied_log:[…]}`.

**`team.json`** (derived; readable by all): `{schema:3, sol, outcome, oxygen_sols, oxygen_max, base, search_zone, units, tiles:{explored only}, astronaut:{status, seen_at, seen_sol}, pending:{ledger, repairs:[…]}, notes:[…], updated}`.

**`events.jsonl`** (index = line number): `{ts, sol, kind: tool|blocked|agent_start|agent_stop|message, agent_type, agent_id, unit, tool, command, result, result_full, blocked, reason, source: hook|script, file, prompt, message, from, to, text}`. `kind:"message"` rows are written by `log_events.py` for: `engine.py order <unit> …` (from `mission-control`, to `<unit>`), `engine.py note <from> <to> …` (from/to as given), and `SubagentStop` with a report (from the agent's unit or role, to `mission-control`, text = first line of `message`). Every other key stays null when not applicable.

`game/field/oxygen-ledger.md`: `# Oxygen ledger` / `sol: 2` / `mode: normal | conserve` / `note: …`. `game/field/repair-<unit>.json`: `{"type":"repair","unit":"rover-2","sol":2,"note":"…"}`. `game/notes.md`, `game/orders/sol-N.md`, `game/intel/lessons.md` (Navigator), `game/dispatches.md`, `game/snapshots/sol-N.json`.

| Role | Reads | Writes | Enforced by |
|---|---|---|---|
| main session (Mission Control) | anything but truth.json | orders/notes via engine | protect_truth |
| rover | engine output only (Bash) | notes via `engine note rover-N …` | tools: Bash; role_guard |
| engineer | team.json, notes (`engine notes --for engineer` is **not** available: no Bash → reads `game/notes.md`) | `field/repair-*.json` | tools Read/Write; validate_build |
| life-support | team.json, ledger | `field/oxygen-ledger.md` | tools Read/Edit; apply_oxygen; frontmatter hook denies Bash |
| navigator | team.json, notes.md, own memory | `intel/lessons.md`, `.claude/agent-memory/navigator/` | write_scope |
| mission-log | team.json, events.jsonl, notes.md, orders/ | `dispatches.md` | write_scope |

## §5 Hooks (unchanged files, updated tables)

`role_guard.py` table (subcommand → allowed `agent_type`; unlisted = denied for every agent):

| Subcommand | mission-control | rover | others (no Bash tool anyway) |
|---|---|---|---|
| `status`, `map` (team), `unit`, `intel`, `notes`, `events`, `replay` | yes | yes | yes |
| `order`, `resolve` | yes | no | no |
| `note <from> …` | yes (from = mission-control) | yes, only when `<from>` is its own unit prefix `rover-` | no |
| `act rover-N move|scan|rescue` | no | yes | no |
| `init`, `apply-ledger`, `field-check`, `map --view truth` | no | no | no |

`log_events.py`: as v2 plus the `message` events above. `apply_oxygen.py`, `validate_build.py`, `write_scope.py`, `protect_truth.py`: unchanged behaviour. `statusline.py`: `Mars SAR │ Sol 2 │ O2 7 sols │ rover-1 ok · rover-2 broken │ astronaut: unknown (zone D5-F7) │ Sonnet`. `settings.json`: unchanged.

## §6 Agents (`.claude/agents/`): exactly these six files

| name | tools | model | memory | maxTurns | frontmatter hooks | one-line purpose |
|---|---|---|---|---|---|---|
| `mission-control` | Agent, Read, Bash, Skill | sonnet | project | 40 | — | nested variant only; the main session is the default orchestrator |
| `rover` | Bash | haiku | — | 8 | — | drive, scan the search zone, rescue; leave a note when broken |
| `engineer` | Read, Write | haiku | — | 6 | — | read `game/notes.md` + team.json, file a repair for each broken rover |
| `life-support` | Read, Edit | haiku | — | 6 | PreToolUse Bash → `write_scope.py --deny-all` | set `mode` in the ledger (conserve when oxygen ≤ 3 and not used last sol) |
| `navigator` | Read, Write, Edit | haiku | project | 8 | PreToolUse Write\|Edit → `write_scope.py game/intel .claude/agent-memory/navigator` | write lessons (rough tiles to avoid, what worked) to memory and `intel/lessons.md` |
| `mission-log` | Read, Write | sonnet | — | 8 | PreToolUse Write\|Edit → `write_scope.py game/dispatches.md` | 60–90 word dispatch per sol naming units, notes and BLOCKED incidents |

Deleted: drone, eva, science-officer, cartographer (and their skills recon, eva-ops, science, cartography).

## §7 Skills (`.claude/skills/`): exactly these six

| name | steps |
|---|---|
| `run-sol` | 0 status (no mission → tell the user to run `python scripts/prepare_demo.py --seed 42`). 1 `intel`, `notes --for mission-control`, read `game/intel/lessons.md`. 2 `order` per rover (and a `note mission-control engineer …` when a rover is broken). 3 ONE wave, one message, `run_in_background: true`: rover-1, rover-2, engineer (only if a rover is broken), life-support. Wait for all. 4 spawn navigator; wait. 5 `resolve`; announce RESCUED/LOST. 6 spawn mission-log in background; report the RESOLVED line + one sentence per unit + intent. Never call `act`. |
| `rover-ops` | `unit <you>`, `map`; if `status=broken`: `note <you> engineer "broke down at <coord>, need a repair"` and stop. Else move toward the nearest unexplored search-zone tile avoiding `~` tiles when possible, `scan`; if the scan shows `astronaut=<your tile>` → `rescue`; if the astronaut is seen elsewhere → `note <you> mission-control "astronaut seen at <coord>"`. Report one line. |
| `field-works` | read `game/notes.md` and team.json; for each broken rover write `game/field/repair-<unit>.json` with the current sol; on `FIELD REJECTED` fix once; report. |
| `life-support` | read team.json; `mode: conserve` when oxygen ≤ 3 and `pending.ledger`/last mode allows, else `normal`; edit the ledger; confirm `LEDGER: accepted`; report. |
| `navigation` | diff explored tiles vs memory; write 1–3 lessons with coordinates (rough tiles, dead ends, what found the astronaut) to memory and `intel/lessons.md`; report. |
| `mission-log` | Read `game/events.jsonl` (lines after the footer index), `game/notes.md`, orders → append `## Sol N` (60–90 words) + footer `<!-- events:<index> -->`; report headline. |

## §8 UI v3

Tabs: **Mission · Roles · Rules & hooks · Memory** (Concepts removed). Presenter mode, keys, Beats drawer kept.

**Mission**: 8×6 map (tiles ≥ 96 px), search zone drawn as a dashed outline with a "search zone" label, rough tiles with a visible texture, rock tiles dark, broken rover with a red pulse and a wrench badge, astronaut once seen. **Lineup panel** replaces the agent cards: the five role icons in a row (Rover ×2 shows two sub-dots), each pulsing while its agent runs, dim when idle, with the last one-line report under it when finished. **Feed, grouped**: one expandable row per agent per sol ("rover-1 · checked status, moved to D4, scanned · found nothing" → expands to the exact tool calls with the drawer per call); lifecycle rows collapse into one ("2 rovers, the engineer and life support woke up"); blocked rows always stand alone in red. Filters: **All · Blocked · Interactions** + role chips + hide scripted. **Interactions view** (the Interactions filter): only `message` events, rendered as `from → to: text` with role icons on both ends, newest first; a small "who talked to whom" strip at the top counts messages per pair this sol. Drawer unchanged (exact tool call, full output, report, hook).

**Roles** = proposal 2: five icons across the top (Rover ×2, Engineer, Life Support, Navigator, Mission Log); click or ←/→ to select; one detail panel with exactly: purpose (one sentence), model chip, tools pills, actions → what runs (max 3 rows, exact commands from §3/§4), guarded by (hook pills), cannot (2–3 items). Pressing → past Mission Log shows the **comparison grid** (roles as columns; rows For / Model / Tools / Runs / Guard / Cannot) as the summary slide. No counters, no file toggles.

**Rules & hooks**: left "Rules (soft)" with three CLAUDE.md quotes; right the six hooks in teaching order (protect_truth, role_guard, apply_oxygen, validate_build, write_scope, log_events) each with one sentence "when it fires / what the room sees" and a live count; the last three BLOCKED reasons. **Memory**: state (from team.json: explored tiles, units, oxygen) beside the Navigator's memory files and `intel/lessons.md`; empty state names the prompt to run.

`ui/roles.json`: five roles with the four blocks. Server endpoints unchanged (`/api/notes` added: `game/notes.md` text).

## §9 `scripts/prepare_demo.py`

`python scripts/prepare_demo.py [--seed 42] [--sols 1] [--oxygen-sols 8]`. Plays K sols headless through the CLI: both rovers advance toward the search zone and scan; on the last sol rover-2 is driven into a rough tile so it starts the live demo **broken**, and the script records a note `rover-2 → engineer` via `engine note`. Guarantees at exit: `outcome=null`, astronaut unseen, rover-2 broken, ≥ 2 zone tiles still unexplored, oxygen ≥ 6, sol = K+1, and the astronaut reachable by rover-1 within 2 live sols. Script events use `source:"script"`, `agent_type:"script:<role>"`, and `kind:"message"` for the note and orders. Prints status, map, `Ready: sol 2, rover-2 broken at D4, astronaut somewhere in D5-F7, oxygen 7.`

## §10 Tests

`tests/test_engine.py` (unittest): determinism, init fog + zone known + astronaut in zone ≥ 4 from base, move rules (range, rock impassable, second move slot error), breakdown on rough + repair applied at resolve + second resolve idempotent, scan reveals radius 1 and sights astronaut, rescue → outcome rescued + oxygen frozen + `mission over`, oxygen loss with `--oxygen-sols 2`, conserve twice rejected, notes recorded in team.json and printed by `notes --for engineer`, orders + intel, illegal actions (unknown unit, bad coord, `rescue` off-tile, `init` without `--force`).

`scripts/selftest_hooks.py`: v2 cases updated to the new role table (rover may `note rover-1 …`, may not `note rover-9 …` nor `order`; mission-control may `note mission-control engineer …`, may not `act`), plus log_events `message` cases for `order`, `note`, and SubagentStop-with-report.

## §11 v3.1 — Objectives, sitrep and the human decision (approved 2026-09-14)

**Objectives.** A mission has **3 colonists**, each in its own 3x3 zone on a **10x8 grid** (rows `A`-`H`, columns `1`-`10`; coord regex `^[A-H](10|[1-9])$`; base A1). Zones do not overlap and are at least 4 tiles from base and from each other. Only the **current** objective's zone is revealed in `team.json.search_zone`; the others are absent from team data until opened. `truth.json.objectives = [{id:1, zone:[...], astronaut_at, status: pending|open|rescued}, ...]`, `truth.current_objective` (1-based, or null when none open).

**Oxygen is per colonist.** `oxygen_max` = 12 (default `--oxygen-sols 12`). The clock starts when the objective is opened (`oxygen_sols = 12`) and ticks at `resolve` as before (conserve rule unchanged). At 0 the colonist is lost (`objective.status = lost`) and the team waits for orders; the mission is not over.

**Rescue** (`act rover-N rescue` on the colonist's tile) sets the objective to `rescued`, marks the tile (`team.json.rescued = ["E6", ...]`), freezes that clock and puts the mission in **`phase: awaiting-orders`** (`team.json.phase`, default `active`). While awaiting orders, `act`, `order`, `note` and `resolve` are refused: `ERROR: team is waiting for orders; run sitrep and choose continue-mission, rest, or abort-mission`.

**New commands**

| Command | stdout |
|---|---|
| `sitrep` | Exactly this block (uppercase, one line each, no trailing spaces):<br>`SOL 4`<br>`OXYGEN: 7/12` (current colonist; `--/12` when no objective is open)<br>`ZONE EXPLORED: 30%` (explored tiles of the current zone / 9, rounded; `--` when none open)<br>`ROVERS ONLINE: 1/2`<br>`ROVERS DAMAGED: 1/2`<br>`COLONISTS RESCUED: 1/3`<br>(blank line)<br>`TEAM WAITING FOR ORDERS:` (only when `phase = awaiting-orders`; otherwise `TEAM ACTIVE: OBJECTIVE 2 OF 3` )<br>`1 - CONTINUE RESCUE MISSION`<br>`2 - REST AND REPAIR`<br>`3 - ABORT MISSION` |
| `continue-mission` | opens the next pending objective: reveals its zone, starts its oxygen clock, `phase = active`; prints `ORDERS RECEIVED: CONTINUE RESCUE MISSION -> OBJECTIVE 2 OF 3, ZONE F7-H9, OXYGEN 12 SOLS`. With no pending objective: `ERROR: no colonists left to rescue; abort-mission to close the mission`. |
| `rest` | one sol passes: every rover becomes `ok` where it stands, slots reset, sol += 1, no oxygen tick (no clock is running), snapshot written; phase stays `awaiting-orders`; prints `ORDERS RECEIVED: REST AND REPAIR -> SOL 5, ROVERS ONLINE 2/2`. Refused while a clock is running (`phase = active`): `ERROR: rest is only possible while the team is waiting for orders`. |
| `abort-mission` | `outcome = aborted` (or `complete` when all 3 rescued); prints `ORDERS RECEIVED: ABORT MISSION -> MISSION CLOSED, COLONISTS RESCUED 1/3`; every mutating command afterwards → `ERROR: mission over (outcome=aborted)`. |

`init` opens objective 1 immediately (phase active) so sol 1 plays as in v3. `status` gains `Objective: 1 of 3 (zone D5-F7)` and `Phase: active|awaiting orders`. `replay` lines add `rescued=1/3`. `resolve` when the current colonist is rescued this sol prints `RESCUED: OBJECTIVE 1 OF 3 -> TEAM WAITING FOR ORDERS` and sets the phase (the rescue itself already set it; resolve still runs to tick the sol). Mission Control decides **after** resolve.

**Decision logging.** `log_events.py` also handles `PostToolUse` for tool `AskUserQuestion` (settings matcher `AskUserQuestion`): appends `kind:"decision"` with `text` = the question text (first question's `question` field, <= 300 chars), `message` = the chosen answer(s) joined by `; ` (from `tool_response` — inspect defensively: it may be a string, or a dict with `answers`/`answer`), `from:"human"`, `to:"mission-control"`, `source:"hook"`. The engine calls `continue-mission|rest|abort-mission` are logged as normal tool events; the display labels them `ORDERS RECEIVED`.

**`run-sol` addition.** Step 5b: after `resolve`, run `sitrep`; if it prints `TEAM WAITING FOR ORDERS`, STOP the loop and ask the human with the AskUserQuestion tool: question = the sitrep block verbatim (as one string, line breaks kept), header `Orders`, options exactly `1 - Continue rescue mission`, `2 - Rest and repair`, `3 - Abort mission`. Map the answer to `continue-mission`, `rest`, or `abort-mission`, run it, print its `ORDERS RECEIVED` line, and end the turn (do not start the next sol on your own). If the tool is unavailable (headless), print the sitrep and end the turn.

**Display.** Header: `RESCUED 1/3` counter and phase pill. Map: rescued tiles keep a small marker; only the open zone is drawn. **Sitrep panel**: when `phase = awaiting-orders`, the lineup panel is replaced by a terminal-styled panel (monospace, amber-on-black, scanlines, blinking block cursor) showing the `sitrep` text verbatim from a new endpoint `GET /api/sitrep` (server runs `python engine/engine.py sitrep` in the project root, cached 1 s; `{text, waiting: bool}`), with the caption "Waiting for the officer on duty — answer in the terminal." **Decision row**: `kind:"decision"` events render in the feed and in Interactions as `you -> mission-control: 1 - Continue rescue mission`, styled like a sitrep line; `ORDERS RECEIVED` tool events render as a banner row. `prepare_demo.py` unchanged in behaviour (sol 2, objective 1 open, rover-2 broken); `--sols` still supported.
