# Hooks

A hook is a command Claude Code runs at a fixed point in its loop. Claude Code passes the event as JSON on stdin; the hook answers with an exit code (2 blocks and sends stderr back to the model) or with JSON on stdout. A rule in `CLAUDE.md` is a request to the model. A hook runs every time, whatever the model decides.

| Script | Event | Wired in | What it does |
|---|---|---|---|
| `protect_truth.py` | PreToolUse, all file and shell tools | `.claude/settings.json` | Blocks any access to `game/truth.json`, for every agent and the main session |
| `role_guard.py` | PreToolUse, Bash | `.claude/settings.json` | Reads `agent_type` and denies engine commands outside that role's table |
| `write_scope.py` | PreToolUse, Write/Edit (or Bash) | each agent file's `hooks:` | Denies writes outside the paths given as arguments; `--deny-all` denies everything |
| `apply_oxygen.py` | PostToolUse, Edit/Write | `.claude/settings.json` | When the oxygen ledger is saved, applies it through the engine and reports a rejection |
| `validate_build.py` | PostToolUse, Write | `.claude/settings.json` | Validates repair files; deletes a bad one and reports why |
| `log_events.py` | PostToolUse, SubagentStart, SubagentStop | `.claude/settings.json` | Appends every tool call and agent start/stop to `game/events.jsonl` for the display |
| `statusline.py` | status line | `.claude/settings.json` | Prints the mission summary at the bottom of the terminal |

Things worth copying:

- **Fail open on bad input.** Every hook exits 0 if stdin is not the JSON it expects, so a hook bug never blocks unrelated work.
- **Two ways to say no.** `protect_truth.py` uses exit code 2 and stderr. `role_guard.py` prints a `permissionDecision: deny` JSON with a reason.
- **One script, many scopes.** `write_scope.py` takes its allowed paths as arguments, so each agent file supplies its own.
- **No shell in the wiring.** The commands use `"command": "python", "args": ["${CLAUDE_PROJECT_DIR}/hooks/..."]`, which works the same on Windows, macOS and Linux and does not depend on the current directory.

One thing to know about hooks in agent files: Claude Code runs them only after you have accepted the workspace trust dialog for this folder in an interactive session. Until then the agent still runs and its `hooks:` block is silently skipped. Hooks in `.claude/settings.json` are not affected.

Test them without Claude Code:

```
python scripts/selftest_hooks.py
```
