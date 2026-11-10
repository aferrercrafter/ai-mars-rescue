# Contributing

1. Fork the repository and create a branch for your change.
2. Make the change. Keep to the conventions below.
3. Run the checks:
   ```
   python -m unittest
   python scripts/selftest_hooks.py
   ```
4. Open a pull request that says what you changed and how you tested it.

## Conventions

- **Skills.** `SKILL.md` contains instructions only: a `description` that says what the skill does and when to use it, then numbered steps. Explanation for people goes in the `README.md` next to it, which `SKILL.md` does not link.
- **Agents.** An agent file states the role, its tools and its hard rules. The procedure lives in a skill listed under `skills:`.
- **Hooks.** Standard library only. Exit 0 on input you do not understand. Add a case to `scripts/selftest_hooks.py` for every new behaviour.
- **Engine.** After editing `engine/engine.py`, copy it to `vanilla/engine/engine.py`.
- **Never commit `game/`, `.claude/agent-memory/` or `.claude/settings.local.json`.** They are gitignored runtime or personal files.
