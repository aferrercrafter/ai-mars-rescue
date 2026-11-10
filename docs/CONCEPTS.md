# Concepts, and where each one shows up

The **harness** is everything around the model: `CLAUDE.md`, hooks, skills, agents, settings, the engine. The model is the same in the main folder and in `vanilla/`; only the harness differs.

## Tools

- `Bash` is how every engine call happens.
- `Read` is how the main session studies `game/intel/lessons.md` and `game/dispatches.md`.
- `Agent` spawns the roles.
- `Skill` is how the main session pulls `run-sol` into context when asked to run a sol.

## Permissions

The first `python engine/engine.py ...` call prompts for approval. Choosing "don't ask again" writes an allow rule to `.claude/settings.local.json`; `.claude/settings.local.json.example` shows the result.

## Rules versus hooks

A rule in `CLAUDE.md` is a request to the model. A hook is enforced by Claude Code before or after the tool call.

- `hooks/protect_truth.py` blocks every tool from touching `game/truth.json`, for subagents and for the main session.
- `hooks/role_guard.py` reads `agent_type` from the tool call and denies engine commands outside that role's table. A life-support agent trying `act rover-1 move` is blocked. The main session has no `agent_type` and passes through.

`vanilla/` has the same rule in its `CLAUDE.md` and no hook, so the rule can be talked around.

## Skills versus agents

- A skill (`.claude/skills/<name>/SKILL.md`) is a procedure loaded into a context: the same model, with borrowed instructions.
- An agent (`.claude/agents/<name>.md`) is a worker with its own fresh context window, its own `tools:` list and often its own `model:`.

Here each role is an agent, and its procedure is a skill preloaded through the agent's `skills:` line. The agent file says who the role is and what it may never do; the skill says how the job is done.

## Agents at scale

One sol spawns four or five subagents in one wave (two rovers, life support, the engineer when a rover is broken), then the navigator. All of them are visible in `/tasks`. None of their work lands in the main session's context; each returns a one-line report.

## Interactions

Agents never call each other. They leave notes through the engine (`engine.py note <from> <to> "..."`), logged in `game/notes.md`. The display's Interactions filter is built from these notes.

## Memory: state versus knowledge

- **State** is explored tiles, positions and oxygen. It lives in `game/team.json`, is deterministic and replayable, and is never held in a model's memory.
- **Knowledge** is what was learned: the navigator's lessons. It lives in the navigator's `memory: project` store (`.claude/agent-memory/navigator/`), which survives `/clear` and restarts. It is gitignored so every clone starts with an empty memory.

## Status line and SessionStart

`hooks/statusline.py` runs as a plain process, not as a model tool call, and prints the mission summary at the bottom of the terminal. The `SessionStart` hook prints `engine.py status` when a session starts, resumes or compacts, so the mission state is re-read from disk and never depends on the conversation.
