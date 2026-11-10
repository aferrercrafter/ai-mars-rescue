# AI Mars Rescue

A small multi-agent project for learning how Claude Code is extended: project memory, skills, subagents, hooks and settings, all working together on one mission.

Three colonists are stranded on a 10x8 Martian grid. Your Claude Code session is Mission Control. It commands two rovers, an engineer, life support, a navigator and a mission log, each a subagent with its own tools, model and guardrails. A Python engine holds the mission state; a local web page shows the map and a live feed of what every agent does.

## What you need

- [Claude Code](https://code.claude.com/docs) installed and logged in
- Python 3.10 or newer, available as `python` on your PATH (no packages to install)

On macOS or Linux, if you only have `python3`, either point `python` at it (for example with a virtual environment: `python3 -m venv .venv && source .venv/bin/activate`) or replace `python` with `python3` in `.claude/settings.json` and in the `hooks:` lines of `.claude/agents/*.md`.

## Quick start

```
git clone https://github.com/aferrercrafter/ai-mars-rescue.git
cd ai-mars-rescue
python scripts/prepare_demo.py --seed 42     # builds a mission, fast-forwarded to sol 3
python ui/server.py                          # mission display on http://localhost:8765
```

In a second terminal, from the same folder:

```
claude
```

Then ask:

```
Mission Control, run the next sol
```

The first time you start `claude` here it asks whether you trust the folder. Accept it: the hooks written inside the agent files (`.claude/agents/*.md`) are skipped until you do, and they are also skipped in headless `claude -p` runs of a folder you have never trusted.

Claude Code asks permission the first time it runs the engine. To skip those prompts, copy `.claude/settings.local.json.example` to `.claude/settings.local.json`.

Each "run the next sol" plays one round: orders, a wave of agents, a resolve. When a colonist is rescued the mission stops and asks you what to do next.

To start over: `python scripts/reset.py`, then `prepare_demo.py` again.

## What to study

| Concept | Where it lives | Read |
|---|---|---|
| Project memory | `CLAUDE.md` | the file itself: short rules, no explanation |
| Skills | `.claude/skills/` | [.claude/skills/README.md](.claude/skills/README.md), then each skill's `SKILL.md` and `README.md` |
| Subagents | `.claude/agents/` | compare `rover.md` (`tools: Bash`) with `life-support.md` (`tools: Read, Edit`) |
| Hooks | `hooks/`, `.claude/settings.json` | [hooks/README.md](hooks/README.md) |
| Permissions | `.claude/settings.local.json.example` | the allow rules that silence the prompts |
| Agent memory | `.claude/agent-memory/navigator/` (created at runtime) | `.claude/skills/navigation/README.md` |

A longer walk through the same ideas is in [docs/CONCEPTS.md](docs/CONCEPTS.md).

## Things to try

1. **Rules versus hooks.** `vanilla/` is the same game with only a `CLAUDE.md` and no `.claude/` folder. Start `claude` in `vanilla/` (after `python engine/engine.py init --seed 42`) and ask it to read `game/truth.json`. Then ask the same thing in the main folder, where `hooks/protect_truth.py` blocks it.
2. **Per-role guardrails.** Ask: `Spawn the life-support agent and tell it to move rover-1 to C3.` Watch the BLOCKED line in the display.
3. **Memory.** Play two sols, run `/clear`, then ask: `Where should we NOT send the rovers, and why?`
4. **Context.** Run `/context` after a sol. The subagents' work is not in your session's context; only their one-line reports are.

## Repository layout

```
CLAUDE.md            project memory for the main session
.claude/
  settings.json      hooks and status line
  agents/            six subagents
  skills/            six skills, each with SKILL.md (for Claude) and README.md (for you)
hooks/               the hook scripts
engine/engine.py     mission rules and state; the only thing that changes the game
scripts/             prepare_demo.py, reset.py, selftest_hooks.py
tests/               engine tests
ui/                  mission display (server.py + two single-file pages)
vanilla/             the same game without the harness
docs/                DESIGN.md, SPEC.md, CONCEPTS.md, INSTRUCTOR.md
game/                runtime state, created when you start a mission (gitignored)
```

## Tests

```
python -m unittest                  # engine rules
python scripts/selftest_hooks.py    # every hook, without Claude Code
```

To check how the agents did after a real sol (for example after you change a skill):

```
python scripts/check_sol.py 3       # the sol number you just ran
```

It reads `game/` and checks that both rovers acted, reports follow the one-line format, no hook blocked anything, the ledger and lessons were written, and the dispatch is 60 to 90 words.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Licence

MIT. See [LICENSE](LICENSE).
