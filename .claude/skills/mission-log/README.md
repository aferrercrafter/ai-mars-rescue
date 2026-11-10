# mission-log

The dispatch-writing procedure. Loaded into the `mission-log` agent by `skills: [mission-log]`.

What to look at:

- **A style guide as a skill.** Length, tense, person and what to lead with are stated as checkable rules, with one example sentence.
- **A bookmark in the output.** The `<!-- events:N -->` footer tells the next run where to start reading, so the agent needs no memory.
- **Append only.** `hooks/write_scope.py` limits the agent to `game/dispatches.md`.
