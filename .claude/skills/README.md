# Skills

Each folder here is one skill: a `SKILL.md` that Claude loads, and a `README.md` for people.

- `SKILL.md` holds instructions only. Its `description` says what the skill does and when to use it; the body is the procedure.
- `README.md` holds the explanation: why the skill exists and what to look at. Nothing links to it from `SKILL.md`, so Claude never loads it and it costs no context.

| Skill | Used by | How it gets loaded |
|---|---|---|
| `run-sol` | the main session | Claude picks it from its description when you ask to run a sol |
| `rover-ops` | `rover` agent | preloaded by `skills:` in the agent file |
| `field-works` | `engineer` agent | preloaded |
| `life-support` | `life-support` agent | preloaded |
| `navigation` | `navigator` agent | preloaded |
| `mission-log` | `mission-log` agent | preloaded |

The role agents have short `tools:` lists with no `Skill` tool, so they cannot fetch a skill themselves. The `skills:` line in each agent file injects the full skill text when the agent starts.
