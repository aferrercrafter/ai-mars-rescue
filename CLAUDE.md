# Mars Search & Rescue

A multi-agent rescue mission on a 10x8 grid (rows A-H, columns 1-10). Three colonists are hidden, one objective at a time, each inside a known 3x3 search zone. This session is Mission Control. Two rovers, an engineer, life support, a navigator and a mission log are subagents that act through the engine CLI or through files under `game/`.

## Rules

- **All mission actions go through `python engine/engine.py ...`**, run from the project root. The engine is the only source of truth.
- **"Run the next sol" or "run sol N" means: follow the `run-sol` skill in this session.** Spawn the `mission-control` agent only when the user asks for "the nested variant".
- **Never read `game/truth.json`.** Use `engine.py status`, `map`, `unit` or `intel`.
- **After a rescue, only the human decides.** The mission enters `phase: awaiting-orders`; ask the human to choose continue, rest or abort, and never choose for them.
- **Notes are the only channel between agents:** `engine.py note <from> <to> "<text>"`.
- **The units are `rover-1` and `rover-2`.** No other unit ids exist.
- **`game/` is runtime state**, gitignored. `python scripts/reset.py` deletes it; `python scripts/prepare_demo.py --seed 42` builds a ready-to-play mission.

## Where things are

- Engine: `engine/engine.py` (tests in `tests/`)
- Agents: `.claude/agents/`
- Skills: `.claude/skills/<name>/SKILL.md`
- Hooks: `hooks/`, wired in `.claude/settings.json` and in agent frontmatter
- Mission display: `ui/server.py`
- Design and contracts: `docs/DESIGN.md`, `docs/SPEC.md`

## Changing the project

- After editing the engine, copy it to `vanilla/engine/engine.py`; a test fails if the two differ.
- Run `python -m unittest` and `python scripts/selftest_hooks.py` before committing.
