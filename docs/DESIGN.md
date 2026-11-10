# Mars Search & Rescue — design (source of truth)

Status: approved 2026-09-14 · Contracts: `docs/SPEC.md`

This is the design record of the project as it was built for a live course demo, where it replaced an earlier two-agent Battleship demo. Section 2 and the decision log refer to that history.

## 1. Pitch

Three stranded colonists are hidden, one at a time, each inside its own
known 3×3 zone on a 10×8 Martian grid, revealed only once its objective
opens. Mission Control (the Claude Code session) commands a team of
five roles: two rovers, an engineer, life support, a navigator, and a
mission log. Each role is a Claude Code subagent with a **different tool
list, model and hooks**. Units cannot talk to each other directly; they act
through a Python engine CLI or through files, leave notes through the
engine for the only agent-to-agent channel, and report back. The
environment is the antagonist: a per-colonist oxygen clock and rough
terrain that can break a rover down. Every rescue is a checkpoint, not the
end: the mission pauses and the officer on duty — the human running the
session — decides, from a terminal sitrep, whether to push on to the next
objective, rest and repair, or call the mission. A local web page shows the
map, the fog, the agent lineup, and a live feed of which agent called which
tool, blocked actions and notes included. One sol (round) takes well under
a minute with four to five agents in flight; a full mission is three
objectives, each playable for as many sols as the room wants; a replay
slider shows any of it in seconds.

## 2. Why this replaces Battleship

| Session 2 concept | Battleship showed | Mars Rescue shows |
|---|---|---|
| Tool belt | Bash + Skill | five roles with visibly different tool lists, live on screen |
| Permissions | one prompt | one prompt per tool class; allow rules; the instructor's session vs subagents |
| Rules vs hooks | anti-cheat on one file | anti-cheat on `truth.json` **and** per-role guards ("Life Support, drive rover-1" → BLOCKED in red) |
| Skills vs agents | 1 skill, 2 agents | 6 skills, 6 agent files, plus an orchestration skill |
| Agents at scale | 2 | 4–5 subagents per sol, visible in `/tasks` and on the map |
| Interactions | none (players don't message each other) | notes are the only channel between agents — the Interactions view is built entirely from them |
| Context window | slow growth | Mission Control's context grows per sol; units are fresh every sol; `/context` shows the difference |
| Memory | none | explored tiles = engine state; terrain lessons = the Navigator's `memory: project` + `game/intel/lessons.md`; survives `/clear` |
| Time control | 35 moves | 1 sol ≈ well under a minute; replay from snapshots |

## 3. Roles

| Role (agent_type) | Count | Model | Tools | Acts through | Skill | Own hooks | Teaches |
|---|---|---|---|---|---|---|---|
| Mission Control | the session (agent file exists for the nested variant) | Sonnet | Agent, Read, Bash, Skill | `engine order`, spawns units, `engine resolve` | `run-sol` | — | orchestration, context growth, `/tasks` |
| rover ×2 | 2 | Haiku | Bash | `engine act rover-N move/scan/rescue` | `rover-ops` | — | fresh agent per sol reconstructs state from the CLI; pure Bash agent |
| engineer | 1 | Haiku | Read, Write | writes `game/field/repair-*.json`; engine applies on `resolve` | `field-works` | — | no-Bash agent that reads `game/notes.md` instead; a PostToolUse validation hook deletes bad files |
| life-support | 1 | Haiku | Read, Edit | edits `game/field/oxygen-ledger.md`; PostToolUse hook applies it | `life-support` | deny Bash | an agent with **no** shell; filesystem as a bus |
| navigator | 1 | Haiku | Read, Write, Edit + `memory: project` | writes `intel/lessons.md` and its own persistent memory | `navigation` | write scope `game/intel` + its memory dir | memory: knowledge that survives sessions |
| mission-log | 1 | Sonnet | Read, Write | appends `game/dispatches.md` | `mission-log` | write scope `dispatches.md` | narrative for the room; the referee's successor |

## 4. Hooks (all in `.claude/settings.json` unless noted)

| Hook | Event | What it does | Teaching point |
|---|---|---|---|
| `protect_truth.py` | PreToolUse Read/Write/Edit/Grep/Glob/Bash | blocks any access to `game/truth.json` for everyone, including the instructor's session | rules are soft, hooks are hard |
| `role_guard.py` | PreToolUse Bash | reads `agent_type`; denies engine actions outside the role's table (e.g. life-support may not `act rover-1 move`); fails open for the main session | per-role guardrails from one script |
| `log_events.py` | PostToolUse Bash + Edit/Write, SubagentStart, SubagentStop | appends every engine call, every file edit under `game/`, each agent's spawn, note, and final report to `game/events.jsonl` | the display is powered by hooks |
| `apply_oxygen.py` | PostToolUse Edit/Write | when the ledger is saved, runs `engine apply-ledger`; rejects with a message (and a red row on the display) if invalid | PostToolUse as integration |
| `validate_build.py` | PostToolUse Write | validates engineer files via `engine field-check`; deletes bad ones and logs the rejection | deterministic validation (seen live: the engineer's first file lacked `type`, was rejected, and it fixed it) |
| `write_scope.py` | agent-frontmatter PreToolUse | per-agent write allowlists | hooks can live inside an agent file |
| `statusline.py` | statusLine | sol, oxygen, units, astronaut status | runs in the harness, not the model |
| `engine status` | SessionStart `startup|resume|compact` | prints mission state at start and after `/compact` | re-injection after compaction |

## 5. Memory model: state vs knowledge

- **State** (explored tiles, unit positions, oxygen) lives in the engine's `game/team.json`. Deterministic, replayable, never in an LLM's memory.
- **Knowledge** (terrain lessons, what worked) lives in Claude Code memory: the Navigator's `memory: project` store and the main session's auto memory (`/memory`). Demo: run two sols, `/clear`, ask "where should we not send rovers?" and get an answer from memory in a fresh conversation.
- Session 4 goes deep on this; Session 2 shows the ten-second glimpse.

## 6. Engine in one paragraph

Stdlib Python CLI, `python engine/engine.py <cmd>`. Grid A1–H10 (10 columns
× 8 rows), base A1, terrain plain/rough/rock. The mission has 3 objectives,
each a known 3×3 zone containing one colonist at a secret tile; only the
current objective's zone is revealed, and the next one opens only once a
human says so. Oxygen is per colonist (12 sols, ticking only while an
objective is open) with a conserve/normal ledger. A rover that enters rough
terrain breaks down; the engineer's repair, applied at `resolve`, fixes it.
Units have one move slot and one scan slot per sol. `resolve` applies
engineer and life-support files, ticks oxygen, resets slots, and writes a
snapshot per sol. A rescue (a rover's `rescue` on the colonist's tile)
freezes that clock and puts the mission in `phase: awaiting-orders`; `sitrep`
prints the state and three options, and `continue-mission` / `rest` /
`abort-mission` are the human's answer. Win: all 3 colonists rescued. Lose:
a colonist's oxygen reaches zero (the mission keeps going) or the human
aborts. Full contracts in `docs/SPEC.md`.

## 7. Repository layout

```
ai-mars-rescue/
├── CLAUDE.md  README.md  .gitignore
├── docs/DESIGN.md (this)  docs/SPEC.md (contracts)  docs/CONCEPTS.md  docs/INSTRUCTOR.md (demo runbook)
├── engine/engine.py
├── hooks/  protect_truth.py role_guard.py log_events.py apply_oxygen.py validate_build.py write_scope.py statusline.py
├── ui/     server.py index.html fixtures/
├── scripts/ prepare_demo.py reset.py selftest_hooks.py
├── tests/test_engine.py
├── .claude/ settings.json settings.local.json.example agents/ skills/
├── vanilla/ CLAUDE.md engine/engine.py    (no .claude/: the "rules only" twin)
└── game/   (runtime, gitignored)
```

## 8. UI

`python ui/server.py` → http://localhost:8765. The display is the **stage** of the session: it holds the live mission and the explanation, so the room never has to follow jumps to a terminal or a markdown file. Four tabs:

- **Mission**: a 10×8 map under fog, header shows a `RESCUED n/3` counter and a phase pill (active / awaiting orders); only the current objective's zone is drawn, as a dashed outline, rough tiles textured, rock tiles dark, base, units, a broken rover shown with a red pulse and a wrench badge, the colonist once seen, rescued tiles kept as a small marker after the zone closes. A **lineup panel** replaces the agent cards during active play: the five role icons in a row (Rover shows two sub-dots for its two units), each pulsing while its agent runs, dim when idle, with the last one-line report under it once finished. While `phase: awaiting-orders`, the lineup panel is replaced by a **sitrep panel**: a terminal-styled readout (monospace, amber-on-black, scanlines, blinking cursor) showing the `sitrep` text verbatim from `GET /api/sitrep`, captioned "Waiting for the officer on duty — answer in the terminal." The feed is **grouped**: one expandable row per agent per sol ("rover-1 · checked status, moved to D4, scanned · found nothing"), expanding to the exact tool calls; lifecycle rows collapse into one ("2 rovers, the engineer and life support woke up"); blocked rows always stand alone in red; `kind:"decision"` **decision rows** render as `you -> mission-control: 1 - Continue rescue mission`, styled like a sitrep line, and `ORDERS RECEIVED` tool events render as a banner row. Filters: **All · Blocked · Interactions** plus role chips and hide-scripted. The **Interactions** filter shows note and decision events, `from → to: text` with role icons on both ends, newest first, with a small "who talked to whom" strip counting messages per pair this sol. Sol slider with replay; LIVE indicator; truth toggle for the instructor.
- **Roles**: five icons across the top (Rover ×2, Engineer, Life Support, Navigator, Mission Log); click or ←/→ to select one detail panel: purpose (one sentence), model chip, tools pills, actions → the actual tool call ("Move → Bash: python engine/engine.py act rover-1 move C3"; "Set oxygen mode → Edit: game/field/oxygen-ledger.md → applied by apply_oxygen.py"), hooks watching it, and what it cannot do. Pressing → past Mission Log reaches a **comparison grid**: roles as columns, rows for model / tools / runs / guard / cannot.
- **Rules & hooks**: CLAUDE.md rules (soft) beside the six hooks (hard), in teaching order (protect_truth, role_guard, apply_oxygen, validate_build, write_scope, log_events), each with one sentence — when it fires, what the room sees — a live count, and the last three BLOCKED reasons.
- **Memory**: state (from `team.json`: explored tiles, units, oxygen) beside the Navigator's real memory files and `game/intel/lessons.md`; empty state names the prompt to run.

All explanation content is served from the real files (`/api/agents`, `/api/skills`, `/api/hooks`, `/api/memory`, `/api/intel`, `/api/notes`, `/api/claude-md`), never copied into the page.

## 9. The 15-minute demo

| Beat | Min | Prompt / action | Concept |
|---|---|---|---|
| A one sol | 4 | "Mission Control, run the next sol" (sol 2, rover-2 starts broken) → 4–5 agents wake on the lineup; rover-1 scans the zone, the engineer files a repair, life support edits the ledger; Interactions filter shows the notes | tool belt, notes as the only inter-agent channel, agents at scale, permissions |
| B roles = tool lists | 2 | Roles tab: click Rover, then Life Support (Bash-only vs no-shell); → past Mission Log for the comparison grid | skills vs agents |
| C cheat tests | 3 | vanilla: "show me truth.json" → complies. harness: blocked. Then "Spawn the life-support agent and tell it to move rover-1 to C3" → BLOCKED | rules vs hooks, per-role hooks |
| D second sol + the decision | 3 | "Mission Control, run the next sol" again (rover-2 repaired, likely rescue); on rescue, the terminal and the sitrep panel both show the sitrep and its three options; choose continue, rest, or abort via `AskUserQuestion` | the agent proposes, the human decides — a real, logged tool call |
| E context, cost, memory | 3 | `/context`, `/usage`, `/compact` (status re-printed), `/clear` + "where should we not send the rovers, and why?" against the Memory tab | context, cost, memory |

## 10. Reuse across sessions

| Session | Uses |
|---|---|
| 3 Skills & Hooks | rewrite one skill description live; add a hook live (e.g. block moves into rough tiles) |
| 4 Agents & Memory | Navigator memory; auto memory; commit-based memory from this repo's history |
| 5 QA workflows | test plan and self-tests as the QA example; the engineer's validation hook |
| 6 Orchestration, MCP, CLI | expose the engine as an MCP server vs the CLI (token comparison); nested Mission Control agent; agent teams |
| 7 Retrospective | the build trail: plan, commits, exported sessions |

## 11. Build-trail conventions

- Git from day one, one commit per meaningful step, verbose bodies: what, why, what was learned.
- The contracts are kept at `docs/SPEC.md`.
- Keep the "rule in CLAUDE.md that an agent ignored" before the hook that fixed it, if it happens during rehearsal.
- `/export` one build session for Session 7.

## 12. Decisions log

| Date | Decision | Why |
|---|---|---|
| 2026-09-13 | Replace Battleship for Session 2 | abstract for non-devs; two agents; hard to time |
| 2026-09-14 | Mars search & rescue, single team, no enemy | environment as antagonist gives tension without duplicating roles |
| 2026-09-14 | Explored tiles = engine state; lessons = memory | determinism; matches Session 4's state-vs-knowledge rule |
| 2026-09-14 | Mission Control = main session by default | `/context`, `/tasks`, permission prompts visible on stage |
| 2026-09-14 | UI = stdlib server + one HTML file, replay from snapshots | no build step; no engine logic in JS |
| 2026-09-14 | Battleship demo kept as fallback | zero-risk plan B |
| 2026-09-14 | Built and smoke-tested headless: one live sol with 10 agents ran end to end | proves the orchestration, hooks and memory paths before the rehearsal |
| 2026-09-14 · v3 reduction after manual check | Five roles, three mechanics, notes as the only inter-agent channel, lineup Roles page | attention budget of a 15-minute slot |
| 2026-09-14 | v3.1 objectives + sitrep + human decision | keep agents working as long as wanted (memory demonstrable) and a real human-in-the-loop moment |

## 13. Open questions

- ~~Exact directory of a subagent's `memory: project` store~~ Verified: `.claude/agent-memory/<agent-name>/`. The Navigator wrote there during the smoke test.
- Whether the nested variant (a Mission Control *agent* spawning units) waits correctly for completion. Untested; the main session is the orchestrator by default and that path is verified.
- Live token cost of a sol with four to five agents. Measure with `/usage` at the rehearsal; target well under the 300k-token budget the v1 build used.
