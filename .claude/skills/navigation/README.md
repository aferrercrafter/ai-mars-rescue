# navigation

The navigator's procedure. Loaded into the `navigator` agent by `skills: [navigation]`.

What to look at:

- **State versus knowledge.** Explored tiles are engine state in `game/team.json`. Lessons drawn from them are knowledge, kept in the agent's `memory: project` store under `.claude/agent-memory/navigator/`.
- **Memory survives the conversation.** After `/clear`, ask "Where should we not send the rovers, and why?" and the answer comes from the navigator's memory files.
- **A good example and a named failure.** The skill shows what a lesson looks like and says "rules, not a list of tiles", which is the common mistake.

Backed by `hooks/write_scope.py`, wired in the agent file with the two directories the navigator may write to.
