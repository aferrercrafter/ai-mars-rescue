# rover-ops

The rover's procedure for one sol. Loaded into the `rover` agent by `skills: [rover-ops]` in `.claude/agents/rover.md`.

What to look at:

- **Skill versus agent.** The agent file says who the rover is and what it must never do. This skill says how to do the job. Neither repeats the other.
- **One skill, two units.** Both rovers load the same skill; the unit id comes from the spawn prompt (`<you>`).
- **The report format is part of the skill.** `hooks/log_events.py` picks the line containing ` | ` out of the agent's final message for the mission display.

Backed by `hooks/role_guard.py`, which blocks a rover from acting for the other rover or from running Mission Control commands, whatever the skill says.
