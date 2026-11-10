# Mars Search & Rescue — the stage (v3.1)

A single-page, offline console for the mission. It is the **stage** for a live
session, not a dashboard: it carries both the running mission *and* the
explanation of what is happening, so the instructor never has to leave the
screen to explain a concept.

v3 is the **reduced** display, cut to fit a 15-minute demo slot in front of a
room of technical non-developers: five roles instead of nine, three mechanics
instead of eight, one grouped row per role per sol instead of a raw event
stream, an **Interactions** view that shows the notes channel, and a **Roles**
tab that behaves like a slide deck.

**v3.1** adds the objectives and the human decision (`docs/SPEC.md` §11): a
**10×8** grid (rows `A`–`H`, columns `1`–`10`), **three colonists** in three
hidden 3×3 zones with only the current one revealed, a per-colonist 12-sol
oxygen clock, a `RESCUED k/3` counter and a phase pill in the top bar, a green
flag on every rescued tile, and — when the engine puts the mission in
`phase: awaiting-orders` — a **sitrep terminal panel** in place of the lineup,
plus amber **decision** rows and `ORDERS RECEIVED` banners in the feed. The
decision is the one line on this page that no model wrote.

It is a harness, not a model. `ui/server.py` only reads files and serves them
to `ui/index.html`. No LLM calls, no writes, Python stdlib only, everything
inline — no npm, no CDN, works with the network unplugged.

## Run

Against the real mission (after `python scripts/prepare_demo.py --seed 42`):

```
python ui/server.py
```

Defaults to `--port 8765 --root game`. Then open http://localhost:8765.

Against the bundled fixtures — no mission, no engine, no hooks required:

```
python ui/server.py --root ui/fixtures --port 8765          # sol 2, active
python ui/server.py --root ui/fixtures-orders --port 8765   # sol 4, awaiting orders
```

Two canned missions, so both halves of §11 can be shown offline: `ui/fixtures/`
is a live sol 2 with objective 1 open, and `ui/fixtures-orders/` is the same map
two sols later — colonist 1 aboard, one human decision already taken, the team
waiting for the next order with the **sitrep terminal** on screen.

`--root X` is the directory that **contains** `team.json` (not `X/game`). So
`--root .` reads `./team.json`, and the default `--root game` reads
`./game/team.json`.

### The light theme (`ui/light/`)

The same page, restyled for a projected room of non-developers: paper
background, one sans-serif, sentence case, fewer colours, hotkey badges only
in presenter mode. It started as a generated copy of `ui/index.html` and is
now its own source file (`ui/light/index.html`, edit it directly). Same
server, same API, same mission; run it next to the dark page on its own port:

```
python ui/server.py --port 8767 --index ui/light/index.html
```

`--index` only changes the HTML served at `/`; `--root` works as before.

Where the light page differs from the dark one, beyond colour:

- **No Roles tab.** The lineup became **the team**: a spread pile of six
  cards (five roles and you) at the left of the side column, the chosen
  card slid out with its tool belt, and the role's *static* description at
  the right — purpose, model, tools, what it does → what runs, guards,
  cannot, and the presenter's "point at" line. What a role *did* this sol
  stays in the feed. **Compare all** opens the old summary grid in a wide
  drawer. Tabs are now 1 Mission, 2 Rules & hooks, 3 Memory.
- **Fold buttons** (▾) on the team and the feed panels; the state is
  remembered per browser in `localStorage`.
- Arrow keys only drive the sol replay; cards are chosen by clicking.

### Two roots, on purpose

`--root` swaps the **mission** only. The harness tabs always read the real
project files, resolved from the location of `server.py` (the parent of `ui/`),
never from the cwd and never from `--root`:

| From `--root` (mission state) | From the project dir (harness content) |
|---|---|
| `team.json`, `events.jsonl`, `snapshots/`, `truth.json` | `.claude/agents/*.md` |
| `notes.md`, `intel/`, `orders/`, `dispatches.md`, `field/` | `.claude/skills/*/SKILL.md` |
| | `hooks/*.py`, `.claude/settings.json` |
| | `.claude/agent-memory/**`, `CLAUDE.md`, `ui/roles.json` |

So `--root ui/fixtures` gives you a canned sol 2 with two agents mid-flight
while the Roles, Rules & hooks and Memory tabs still show the *real* agent
files, the *real* hook sources and the *real* settings. `ui/fixtures/` and
`ui/fixtures-orders/` deliberately contain no `.claude/` of their own — they do
not need one.

`GET /api/sitrep` is the one endpoint that reads neither: it shells out to the
project's `engine.py sitrep`, and only when `--root` **is** the project's own
`game/` directory. Under a fixture root the block is synthesized from that
fixture's `team.json` in exactly the same format, so the panel reads the same
whichever half produced it (`source` in the payload says which).

## The four tabs

| Tab | Key | What it is for |
|---|---|---|
| **Mission** | `1` | the live mission: 10×8 terrain map, the lineup of five roles (or the sitrep terminal while the team waits for orders), the grouped feed, the Interactions view, the replay slider |
| **Roles** | `2` | one slide per role — purpose, model, tools, what it does → what runs, guards, cannot — then a comparison grid of all five, plus **you** as a sixth column |
| **Rules & hooks** | `3` | the soft-vs-hard story: three CLAUDE.md quotes on the left, the six hooks in teaching order with live counts on the right |
| **Memory** | `4` | state vs knowledge: engine state on the left (v3.1 adds `Colonists rescued k/3` with the tiles, the current objective and its phase, `of 80` explored tiles, and `--/12` oxygen when no clock runs), the Navigator's memory and `intel/lessons.md` on the right |

(The v2 Concepts tab is gone; its content lives in the slides and in the Beats
drawer.)

### Sol glossary

A **sol** is one Martian day, and in this mission it is one round:

1. Mission Control reads the intel and the notes (`engine.py intel`,
   `engine.py notes --for mission-control`, `game/intel/lessons.md`).
2. It records an order per rover (`engine.py order <unit> "…"`), and a note to
   the engineer when a rover is broken.
3. It dispatches one wave of subagents in parallel — rover-1, rover-2, the
   engineer (only when a rover is broken) and life support — then the
   navigator, then the mission log.
4. Each rover acts through the engine: at most one `move` and one `scan`, plus
   `rescue` when it is standing on the colonist's tile.
5. Mission Control resolves (`engine.py resolve`): repair files and the oxygen
   ledger are applied, oxygen ticks down, slots reset, a snapshot is written.
6. **v3.1** — if that sol ended in a rescue, the engine is now in
   `phase: awaiting-orders` and refuses `act`, `order`, `note` and `resolve`.
   Mission Control runs `engine.py sitrep`, stops its own loop and asks the
   human with `AskUserQuestion`; the answer becomes
   `engine.py continue-mission | rest | abort-mission`.

The same sentence is on the "?" tooltip beside the sol number in the top bar,
and the objectives sentence is on the "?" beside `RESCUED`. The prepared
mission starts at **sol 2**; `ui/fixtures/` sits at **sol 2, mid-sol** and
`ui/fixtures-orders/` at **sol 4, waiting for orders**.

## Endpoints

All JSON (except `GET /`), all `Cache-Control: no-store`. The server never
crashes on a missing or malformed file — it degrades to an empty payload.

### Mission state — read from `--root`

| Endpoint | Behaviour |
|---|---|
| `GET /` | serves `ui/index.html` (resolved next to `server.py`) |
| `GET /api/state` | `<root>/team.json` verbatim, or `{"sol":0,"missing":true}` |
| `GET /api/events?since=N` | `{"next": M, "events":[{"index": i, …}]}` from 0-based line `N`; malformed lines skipped but still counted |
| `GET /api/snapshots` | `{"sols":[…]}` — every `sol-N.json` under `<root>/snapshots/` |
| `GET /api/snapshot?sol=N` | that snapshot, or `404 {"error":"no snapshot"}` |
| `GET /api/truth` | `<root>/truth.json`, for the instructor-only "show truth" toggle |
| `GET /api/notes` | **new in v3** — `{path,text,missing}` for `<root>/notes.md`, the notes channel between agents |
| `GET /api/intel` | `{"root":…,"files":[{path,name,content}]}` — `intel/*.md`, `orders/*.md`, `dispatches.md`, `field/oxygen-ledger.md` when present |
| `GET /api/sitrep` | **new in v3.1** — `{text, waiting, source, options}`. Runs `python engine/engine.py sitrep` in the project root (cwd = project dir, 5 s timeout, 1 s cache) when `--root` is that project's `game/`; otherwise, or if the call fails, synthesizes the §11 block from `<root>/team.json`. `waiting` is true when the block contains `TEAM WAITING FOR ORDERS`; `source` is `"engine"` or `"team.json"` |

### Harness content — read from the project dir

| Endpoint | Behaviour |
|---|---|
| `GET /api/agents` | `[{name,file,description,tools:[…],model,memory,maxTurns,hooks,body}]` from `.claude/agents/*.md` |
| `GET /api/skills` | `[{name,file,description,body}]` from `.claude/skills/*/SKILL.md` |
| `GET /api/hooks` | `{settings: <.claude/settings.json>, files: {"protect_truth.py": {source, summary}, …}}` for every `hooks/*.py` |
| `GET /api/memory` | `{dir, files:[{path,content}]}` for everything under `.claude/agent-memory/` — an empty list when the directory is absent |
| `GET /api/claude-md` | `{path,text,missing}` for the project `CLAUDE.md` |
| `GET /api/roles` | `ui/roles.json` verbatim |

## Presenting from it

- **Keys**: `1`–`4` switch tabs, `←` / `→` step the sol slider (`→` past the
  last sol returns to live) — and on the **Roles** tab the same two keys move
  between role slides, `→` past Mission Log opening the comparison grid. `L`
  live, `C` toggles the exact tool-call line inside expanded feed rows, `B`
  opens the beats checklist, `P` toggles presenter mode, `Esc` closes the
  drawer. Keys are ignored while typing in an input.
- **Presenter mode** (`P`, or the button next to LIVE): an amber "Point at"
  strip under the tabs with a one-line hint per tab, the per-role "Point at"
  line on the Roles tab, the **Beats** button, the replay sentence under the
  sol slider, and the instructor-only "show truth" checkbox on the timeline.
  Off by default so the room never sees the truth toggle.
- **Beats** (`B`): the current sitrep block at the top of the drawer (live from
  `/api/sitrep`, so the three answers are always in front of you), then the
  beats — A run the next sol (lineup, then Interactions), B the Roles lineup,
  C the two cheat tests, D a second sol then replay, **D2 the rescue hands
  control back** (sitrep terminal, the decision row, the ORDERS banner, the
  green flag and `RESCUED 1/3`), E `/context` `/usage` `/compact` `/clear` and
  the Memory tab — with timing, the tab and key, and the exact prompts in
  copyable boxes.
- **Orientation line** under the tabs: one sentence saying what the page is,
  dismissible with its ×. It comes back on reload, which is what you want for
  a fresh room. Turning presenter mode on hides it and shows the "Point at"
  line in its place (two strips would push the timeline off a 900 px screen);
  turning presenter mode off brings it back, unless you had dismissed it
  yourself.
- **Glossary**: dotted words (agent, subagent, tool, hook, skill, context
  window, sol, note, harness, engine, model, Claude Code — and in v3.1
  objective, sitrep, decision) show a definition on hover or focus. The text lives in one `GLOSSARY` map in `index.html`; any
  element with `data-term="key"` gets it.

## Mission tab, in detail

### The map

- **Top bar**: title, sol with the "?" glossary tooltip, oxygen bar (green →
  amber → red) reading `--/12` when no colonist clock is running, `RESCUED k/3`
  with one pip per colonist and its own "?" tooltip, the **phase pill**
  (`ACTIVE · OBJECTIVE 2 OF 3` in green, `WAITING FOR ORDERS` in amber with a
  pulsing dot, `MISSION CLOSED` when the mission is over), the outcome banner
  (RESCUED / ALL COLONISTS RESCUED / LOST / MISSION ABORTED), a LIVE / replay
  indicator that pulses while polling, then Beats and Presenter. One row at
  1600 px.
- **10×8 grid**, rows `A`–`H` top to bottom, columns `1`–`10`. Tiles are 76 px
  (canvas 792×634), which is §11's floor and still leaves ~700 px for the side
  column at 1600×900. The bigger grid cost 20 px of height, paid for by a
  one-line legend, a tighter top bar and moving the replay hint into presenter
  mode — the whole Mission tab measures ~854 px at 1600×900, so the feed is
  above the fold with no page scroll.
- **Terrain** is drawn procedurally, deterministically seeded from each tile's
  coordinate — no image files. `plain`: regolith speckle and a few pebbles.
  `rough`: bright and dark rubble strokes plus angular chips — the tile that
  stops a rover and breaks it, and it has to be recognisable from the back of
  the room. `rock`: near-black, three or four boulders with lit tops,
  impassable. Unexplored tiles are dark fog with a faint grid and a vignette.
  Each tile is rendered once into an offscreen canvas, keyed by coordinate plus
  its flags, and blitted after that.
- **Search zone**: only the **open** one — the 3×3 block from
  `team.json.search_zone` — drawn as a dashed ochre outline (per-tile edges, so
  any zone shape works) with a `SEARCH ZONE` label at its top-left corner. The
  other two objectives are not in team data at all until the human orders
  `continue-mission`, so there is nothing to draw and nothing to leak. While
  the team waits for orders `search_zone` is empty and no outline is drawn.
- **Rescued tiles** (`team.json.rescued`) keep a small green planted flag with
  a tick, bottom-left of the tile so a rover token never covers it, for the
  rest of the mission. The pink "still out there" colonist icon is suppressed
  on a rescued tile.
- **Base** is a planted flag with a `BASE` caption on A1.
- **Rovers** carry the rover icon and their id on a pill under the token. A
  broken rover gets a red pulsing ring, a red outline and a **wrench badge** —
  the visual shorthand for "waiting for the engineer". Tokens fan out when two
  rovers share a tile and animate between tiles on a move.
- **The colonist** appears only once `team.json.astronaut.seen_at` is set, as
  the waving-astronaut icon in a pink ring. The instructor's truth toggle shows
  every colonist still out there — `truth.json.objectives[].astronaut_at` for
  each objective that is not `rescued`, not just the open one.
- **Timeline**: the sol slider (labelled with its first and last sol) loads
  that snapshot and filters the feed to `sol ≤ N`; LIVE returns to 1 s polling.

### The lineup (replaces v2's agent cards)

Five role tiles in a row — Rover, Engineer, Life Support, Navigator, Mission
Log — always the same five, in the same order, so the room learns the shape:

- **pulsing ring** while an agent of that role is running (an `agent_start`
  with no `agent_stop` yet);
- **dim** when that role has not run this sol at all;
- **two sub-dots** under Rover, one per unit: pulsing while that rover's
  subagent runs, solid when it has reported, **red** when the rover is broken;
- the **last one-line report** under the icon once the agent has finished, from
  the `agent_stop` message (full text on hover);
- clicking a tile filters the feed to that role; clicking it again clears.

A sentence under the row names who is running right now.

### The sitrep terminal (new in v3.1)

While `team.json.phase == "awaiting-orders"` the lineup panel is **replaced**,
in the same slot, by a panel styled like a 1979 ship's computer: black inset,
amber monospace on faint horizontal scanlines (a CSS repeating gradient with a
slow phosphor breath), a header line `MISSION COMPUTER · SITREP`, and a
blinking block cursor after the last line. Its body is the text from
`GET /api/sitrep` **verbatim** — the same nine lines the instructor is being
asked to answer in the terminal — with the phase line and the three numbered
options brightened, nothing reworded:

```
SOL 4
OXYGEN: --/12
ZONE EXPLORED: --
ROVERS ONLINE: 2/2
ROVERS DAMAGED: 0/2
COLONISTS RESCUED: 1/3

TEAM WAITING FOR ORDERS:
1 - CONTINUE RESCUE MISSION
2 - REST AND REPAIR
3 - ABORT MISSION
```

Under it, one caption: *Waiting for the officer on duty — answer in the
terminal.* When the phase goes back to `active` the lineup comes back. The
block is polled every 2 s on its own clock (the server caches it for 1 s), and
the same block is at the top of the **Beats** drawer (`B`) as the presenter's
reminder of the only three answers.

Why the swap and not a sixth panel: while the team waits for orders nothing is
running, so five idle rings have nothing to say, and the panel that matters has
to be the one at eye height.

### The grouped feed

One expandable row per `(agent_id, sol)` instead of one row per tool call:

```
S2 ▸ rover-1   checked itself · looked at the map · moved to D5 · scanned ·
               found the colonist at E6 · sent a note to mission-control    6
```

- The **label** is the unit for a rover, the role otherwise, `mission control`
  for the main session, and the unit or role for a scripted row (which also
  carries a grey `SCRIPTED` tag).
- The **summary** is the humanised verbs joined with `·`. Repeats collapse
  (`scanned · found nothing ×2`), orders aggregate in place (`gave 2 orders`),
  and a failed engine call reads as an attempt (`could not move to E5`).
- **Expanding** a row lists the individual calls — tool, plain sentence, first
  line of output, and the exact call in monospace (`C` hides that last line).
  Clicking one opens the drawer.
- **Lifecycle rows** collapse per sol and per wave into one line: "rover-1,
  rover-2, the engineer and life support woke up — fresh context, one job
  each", then "… reported back". A gap of more than 25 s between spawns starts
  a new wave, so wave 2 ("the navigator and the mission log woke up") is its
  own row.
- **Blocked rows never group**: standalone, red, a `BLOCKED` badge, the attempt
  phrased as an attempt ("tries to make rover-1 move C3 — refused") and the
  hook's reason underneath verbatim.
- **Decision rows never group either** (v3.1): a `kind:"decision"` event is one
  amber monospace row, `you → mission-control : 2 - Rest and repair`, with a
  keyboard-key glyph for the key that was pressed and a `decision` tag. The
  question — the sitrep block, verbatim — is in the drawer, under *The question
  Claude Code asked*, in the same terminal styling as the panel. `from` is
  `"human"` in the log and reads `you` on screen.
- **`ORDERS RECEIVED` rows** are a full-width banner in the terminal palette:
  the engine's own line (`ORDERS RECEIVED: REST AND REPAIR -> SOL 4, ROVERS
  ONLINE 2/2`) with an `ORDERS` tag, detected either from the result line or
  from the subcommand (`continue-mission`, `rest`, `abort-mission`). Clicking
  it opens the usual drawer with the full engine output.
- **Humanised v3.1 verbs**: `sitrep` → "prints the sitrep", `continue-mission`
  → "orders: continue the rescue mission", `rest` → "orders: rest and repair",
  `abort-mission` → "orders: abort the mission", `rescue` → "rescued the
  colonist".
- **Filters**: an `All · Blocked · Interactions` segmented control, a chip per
  role plus Mission Control, `hide scripted`, and the `tool calls` toggle.
- Empty states say what to type in the terminal next.

### The Interactions view

The `Interactions` filter turns the feed into the message log:
`kind:"message"` events **and** the human's `kind:"decision"` rows, newest
first, each rendered as

```
S2  rover-1 → mission-control   colonist seen at E6             note
```

with the role icon on both ends and a `note` / `order` / `report` tag for how
it travelled. Above it, a **who-talked-to-whom strip** counts messages per pair
for the sol on screen (top six pairs, then "+N more pairs"), and one line
explains the whole mechanism:

> Agents cannot talk to each other directly; every message here went through
> the engine's notes file or an order, and Mission Control reads them.

The decision row sits in the same list, amber, `you → mission-control` — the
one line in the view that did not come out of a model.

### The detail drawer — "what did that actually call?"

Clicking any call (or any blocked or message row) opens a drawer on the right:

1. **When** — timestamp, sol, event index.
2. **Who** — icon, actor label, role, `agent_id`.
3. **In plain language** — the same sentence as the row.
4. **The message** (message rows) — from → to, the text, and the engine command
   it travelled on. For a **decision** row instead: *The question Claude Code
   asked — the sitrep, verbatim* in the terminal styling, then *The answer the
   human gave* with its key glyph.
5. **The tool call Claude Code made** — the translation moment, formatted as
   `Bash · python engine/engine.py act rover-1 move D5`, or
   `Edit · game/field/oxygen-ledger.md`, or
   `Agent · subagent_type: rover, run_in_background: true`.
6. **Blocked** — the reason, in red, when the call was denied.
7. **Full output** — `result_full`, monospace and scrollable.
8. **The spawn prompt** (`agent_start`) or **its final report** (`agent_stop`).
9. **Hooks that saw this call, in order**, and what each one did.
10. **The subagent that made it** — role, model, running/finished, elapsed.

| The call | Hooks named in the drawer |
|---|---|
| any `Bash` engine call | `protect_truth.py` → `role_guard.py` → `log_events.py` |
| `Edit game/field/oxygen-ledger.md` | `protect_truth.py` → `apply_oxygen.py` → `log_events.py` |
| `Write game/field/*.json` | `protect_truth.py` → `validate_build.py` → `log_events.py` |
| `Write game/intel/…`, `dispatches.md`, a memory file | `write_scope.py` (from the agent's own frontmatter) → `log_events.py` |
| blocked, reason starts `ANTI-CHEAT` | `protect_truth.py` — refused |
| blocked, reason starts `ROLE GUARD` | `role_guard.py` — refused |
| blocked, reason starts `WRITE SCOPE` | `write_scope.py` — refused |
| blocked, reason starts `LEDGER REJECTED` | `apply_oxygen.py` — refused |
| blocked, reason starts `FIELD REJECTED` | `validate_build.py` — refused |
| `kind:"decision"` (v3.1) | `log_events.py` — `PostToolUse(AskUserQuestion)`; the tool call reads `AskUserQuestion · header: Orders · options: …` |

Escape or the scrim closes the drawer.

## Roles tab — one slide per role

The same five-icon lineup sits across the top, static. Click an icon, or use
`←` / `→`, and the panel below shows exactly one role:

- **purpose** — one sentence;
- **model** chip (top right) and **tools** pills;
- **what it does → what runs** — at most three rows, each the literal command
  (`Bash · python engine/engine.py act rover-1 move D5`,
  `Write · game/field/repair-rover-2.json`) with the hook chain under it;
- **guarded by** — hook pills;
- **cannot** — two or three things the role cannot do, each with the reason in
  muted text (the tool list, a hook, or the engine itself);
- **point at** — a presenter-only line, shown in presenter mode.

`→` past Mission Log (or the **Compare all five** button) shows the
**comparison grid**: roles as columns, `For / Model / Tools / Runs / Guard /
Cannot` as rows. That is the summary slide — leave it up while you talk. `←`
goes back to Mission Log.

**v3.1**: the grid has a sixth, last column — **You**, the officer on duty
(`roles.json` entry `human`, `compare_only: true`). It is not an agent, has no
agent file, is not in the lineup and gets no slide of its own; its `Model` cell
reads *no model* and its `Runs` cell is the `AskUserQuestion` options and the
three engine commands they map to. The Rover slide gained a fourth **cannot**:
*decide what happens after a rescue — the human does (AskUserQuestion)*.

No counters, no agent-file or skill toggles, no docstrings: v2 had all of them
and the room read none of them.

## `ui/roles.json`

Authored teaching content for the Roles tab — one entry per role:

```
{role, title, icon, colour, model, units?, purpose,
 tools: […],
 actions: [{action, runs, handled_by}],   // max 3 used
 guards: […],
 cannot: [{what, why}],
 point?, compare_only?}
```

The sixth entry, `human`, carries `compare_only: true`: the display renders it
only as the last column of the comparison grid (the flag documents the intent;
the display keys off `ROLE_ORDER`, which has five names in it).

The commands in `runs` are the exact ones from `docs/SPEC.md` §3 and the file
names the exact ones from §4. At render time it is **merged with
`/api/agents`**, and the live frontmatter wins for `tools` and `model` — so
editing an agent file mid-session changes the Roles tab, and `roles.json` never
goes stale about a tool list. When an agent file is missing the slide still
renders from `roles.json` and says so next to the file name.

## Event schema

`events.jsonl`, one JSON object per line, index = line number:

```
{ts, sol, kind: "tool"|"blocked"|"agent_start"|"agent_stop"|"message"|"decision",
 agent_type, agent_id, unit, tool, command, result, result_full,
 blocked, reason, source: "hook"|"script", file, prompt, message,
 from, to, text}
```

`kind:"decision"` is new in v3.1 (§11): written by `log_events.py` on
`PostToolUse` for the `AskUserQuestion` tool, with `text` = the question (the
sitrep block), `message` = the chosen answer, `from: "human"`,
`to: "mission-control"`, `source: "hook"`, and no `command`. The display gives
it its own row kind, treats it as an interaction, and colours it amber; the
key glyph is parsed from the answer (`1`/`2`/`3`, else the words
continue/rest/abort, else `↵`).

Main-session events have `agent_type: null` (the UI labels them
"mission control"). Script events have `agent_type` like `"script:rover"`.
`kind:"message"` rows are new in v3 and power the Interactions view: they are
written for `engine.py order …` (from `mission-control`), `engine.py note
<from> <to> …`, and a `SubagentStop` that carried a report (from the unit or
role, to `mission-control`, `command` null). The UI degrades cleanly when a key
is absent — the drawer says "Not recorded for this event" instead of guessing.

Two shapes are tolerated, because `log_events.py` and the fixtures differ:
a message row that **carries** the engine command (one row per order or note),
and a message row logged **beside** the `tool` row that produced it (`command`
empty). Either way each message appears exactly once in Interactions and the
action is counted exactly once in the grouped summary, and the actor is taken
from `from` / from the command itself — so `order rover-1 …` is credited to
Mission Control and not to the rover it names, whatever `agent_type` and `unit`
the logging hook happened to write.

## Fixture mode

`ui/fixtures/` and `ui/fixtures-orders/` are hand-built, believable states so
the whole page — both halves of §11 included — can be demonstrated with no
engine, no hooks and no network. Both are generated together, so they share one
10×8 terrain map (55 plain, 17 rough, 8 rock; no rock in A1–B2, every zone tile
reachable from base through plain/rough), the same three zones and the same
sol-1 history. They are two *independent* canned missions, not two views of
one: the colonist of objective 1 sits at E6 in the first and at D5 in the
second.

Shared shape:

- Three objectives in `truth.json.objectives`: **1** `D5-F7`, **2** `E8-G10`,
  **3** `A8-C10`, each `{id, zone, astronaut_at, status}`, plus
  `current_objective`, `phase` and `rescued`. `team.json` reveals **only** the
  open zone in `search_zone` and never mentions the other two.
- `team.json` (schema 3) carries the v3.1 keys the display reads:
  `phase`, `objective: {current, total, rescued_count}`, `rescued: [...]`,
  `oxygen_max: 12`, and `oxygen_sols: null` when no clock is running.
- `snapshots/sol-N.json` are team-shaped, so the replay slider walks the
  objective counters and the phase backwards as well as the map.
- `notes.md`, `intel/lessons.md`, `orders/sol-2.md`, `dispatches.md`,
  `field/oxygen-ledger.md`, `field/repair-rover-2.json` for the Memory tab.

### `ui/fixtures/` — sol 2, active, mid-sol

- Objective **1 of 3** open, zone `D5-F7` with 4 of its 9 tiles explored; 21 of
  80 tiles explored; oxygen **11/12**; `rescued: []`, `rescued_count: 0`,
  `phase: "active"`.
- `rover-1` at D5 (move and scan used) has **sighted the colonist at E6**;
  `rover-2` is **broken at D4**; a repair is pending; four notes.
- `events.jsonl`, 48 lines: 10 grey `source:"script"` sol-1 lines (init with
  `grid=10x8 oxygen=12`, two orders and rover-2's note as `kind:"message"`,
  the `RESOLVED` block); then the live sol-2 wave — six `agent_start`s in two
  waves with spawn prompts, `agent_stop` reports for four of them (so the
  lineup shows three roles reported and **two still pulsing**), real engine
  output including the full 10×8 ASCII `map`, `Read`/`Write`/`Edit` rows, 12
  `kind:"message"` rows, and **two** blocked lines (`ANTI-CHEAT` on
  `truth.json`, `ROLE GUARD` on life support reaching for `act rover-1 move
  C3`) plus one engine refusal that is *not* a hook denial (the broken rover
  trying to move).
- The sitrep synthesized for this root reads `TEAM ACTIVE: OBJECTIVE 1 OF 3`,
  so the terminal panel stays hidden and the lineup is on screen.

### `ui/fixtures-orders/` — sol 4, waiting for orders

- `phase: "awaiting-orders"`, `objective: {current: null, total: 3,
  rescued_count: 1}`, `rescued: ["D5"]`, `search_zone: []`,
  `oxygen_sols: null` (no clock), both rovers `ok`, four snapshots (sols 1–4).
- Story in `events.jsonl`, 54 lines: the same sol 1; then sol 2 where rover-1
  moves to D5, scans, finds the colonist **on its own tile** and runs
  `act rover-1 rescue` (`objective=1 outcome=rescued phase=awaiting-orders`);
  `resolve` prints `RESCUED: OBJECTIVE 1 OF 3 -> TEAM WAITING FOR ORDERS`;
  then at sol 3 `engine.py sitrep`, a **`kind:"decision"`** row
  (`from:"human"`, `message:"2 - Rest and repair"`, `text` = the sol-3 sitrep
  block verbatim) and `engine.py rest` printing
  `ORDERS RECEIVED: REST AND REPAIR -> SOL 4, ROVERS ONLINE 2/2`; then the
  mission log's sol-3 dispatch and a fresh `sitrep` at sol 4 — the display is
  caught at the moment the room is being asked again.
- So this root shows, offline: the sitrep terminal in the lineup's slot, the
  amber decision row (in the feed *and* in Interactions), the `ORDERS
  RECEIVED` banner, `RESCUED 1/3` with one pip lit, the amber phase pill,
  `--/12` oxygen and the green flag on D5.

Regenerating: both roots are written by one throwaway generator (kept out of
the repo on purpose — the fixtures are the artefact, not the script). Editing
them by hand is fine; `team.json`, `truth.json` and the snapshots must agree
about `phase`, `objective` and `rescued`, and the sitrep panel is synthesized
from `team.json`, so it follows any hand edit automatically.

## Notes / deviations from `docs/SPEC.md` §8 and §11

- **Canvas 792×634** (76 px tiles plus margins for row letters and column
  numbers) — §11's floor, and the largest that still leaves the grouped feed on
  screen at 1600×900 without a page scroll. Base font 15 px; the smallest
  labels are 12 px. Fitting 10×8 inside 900 px cost: the top bar's type and
  gaps came down a notch (it still has to be one row with two new readouts),
  the legend's labels were shortened to hold one line, and the timeline's
  replay sentence now shows **only in presenter mode** (`P`) — it is presenter
  material, and it was the cheapest whole row to buy back. Measured at
  1600×900: 854 px of content. The side column is pinned by JS to the height of
  the map column (`syncSideCol`) so the feed scrolls inside its own panel and
  the BLOCKED rows are on screen when they land.
- **Rover tokens shrank** with the tiles (radius 21, id pill 13 px) so a token
  plus its label stays inside one 76 px tile.
- **`/api/sitrep` only shells out to the engine when `--root` is the project's
  own `game/`.** §11 says the server runs `engine.py sitrep`; under a fixture
  root that would answer about a *different* mission than the one on screen, so
  the block is synthesized from the fixture's `team.json` in the same format
  instead (`source` tells you which). The synthesized block prints the three
  options under both phase lines, exactly as the §11 table lists them.
- **The header pill wording** is `ACTIVE · OBJECTIVE 2 OF 3` /
  `WAITING FOR ORDERS` as specified, plus `MISSION CLOSED` for
  `outcome: aborted` and `MISSION OVER` for any other terminal outcome — §11
  does not say what the pill shows once the mission is closed.
- **The sitrep panel takes the lineup's slot rather than sitting beside it**
  (§11: "the lineup panel is replaced by"), and it is not pixel-identical in
  height — the block is eleven monospace lines, so the panel is about 1.6× the
  lineup's height and the feed panel below it shrinks by that much. The feed
  keeps its own scroll.
- **No `MU-TH-UR` anywhere**: the panel header is `MISSION COMPUTER · SITREP`.
- **The human is a column, not a role**: `roles.json` has a sixth `human`
  entry, but `ROLE_ORDER` (lineup, chips, slides) still has five names, so the
  human appears only in the comparison grid, in decision rows and in
  Interactions. `from:"human"` renders as `you`.
- **Decisions count as interactions.** §11 says decision rows render in the
  feed and in Interactions; the pair strip above Interactions therefore counts
  `you → mission-control` too, but only for the sol on screen.
- **Lifecycle rows split by wave**, not only by sol: a spawn gap over 25 s
  starts a new row. §8 says "collapse into one"; one row per sol merged both
  waves into a six-name sentence that read worse than the two it replaced.
- **The Interactions strip shows the top six pairs** for the sol and then
  "+N more pairs" — eight pairs at full width pushed the messages themselves
  below the fold.
- **The role chip row includes Mission Control** as a sixth chip. The main
  session's rows group like everyone else's, so it needs a filter too; it is
  not one of the five lineup icons.
- **Interactions lists every sol's messages** (newest first) while the pair
  strip counts only the sol on screen, so the history is still one click away.
- `ui/fixtures/truth.json` is not in the spec's file list but is required for
  the "show truth" toggle to have anything to overlay in fixture mode.
- The Roles, Rules & hooks and Memory tabs, `ui/roles.json`, and the
  `/api/agents`, `/api/skills`, `/api/hooks`, `/api/memory`, `/api/roles`,
  `/api/intel` and `/api/claude-md` endpoints are additions beyond §8 — they
  are what turns the page from a dashboard into the stage.
- `roles.json` gives **Mission Log** `Read · game/events.jsonl` rather than
  `Bash · engine.py events --since N`: SPEC §6 gives that agent `Read, Write`
  and no Bash, while §7's skill step says to run `events --since`. The display
  follows the tool list, which is the thing the room is being shown.
- Panels that contain a `<details>` toggle (Rules & hooks, Memory) are only
  rebuilt when the underlying files actually change; a naive per-second rebuild
  would slam shut whatever the instructor had just opened. The same guard
  applies to the lineup and the feed (including which rows are expanded), which
  keeps the projected screen from flickering.
- `server.py` reads text as UTF-8 and falls back to cp1252: a couple of the
  hook docstrings were saved on Windows with a cp1252 section sign, and
  decoding those as UTF-8 would put a replacement glyph on screen.
- `hooks/statusline.py` is not listed on the Rules & hooks tab at all in v3 —
  §8 asks for six hooks in teaching order, and the status line is not a tool
  hook. It is still worth mentioning out loud.
