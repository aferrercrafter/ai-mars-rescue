# life-support

The oxygen ledger procedure. Loaded into the `life-support` agent by `skills: [life-support]`.

What to look at:

- **A decision rule the model can apply.** Step 2 is a threshold and one exception, not a discussion of trade-offs.
- **A file as the interface.** Life support has `tools: Read, Edit` and edits one file. `hooks/apply_oxygen.py` runs after the edit and applies the ledger through the engine.
- **Two layers of protection.** The agent has no Bash tool, and its agent file also wires `write_scope.py --deny-all` on Bash in case that ever changes.
