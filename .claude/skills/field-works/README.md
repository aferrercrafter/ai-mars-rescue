# field-works

The engineer's procedure. Loaded into the `engineer` agent by `skills: [field-works]`.

What to look at:

- **An agent with no shell.** The engineer has `tools: Read, Write`. It acts by writing a file, and the engine picks the file up on `resolve`.
- **The exact JSON is in the skill.** Earlier versions described the file in words and the first write often missed the `type` key. Showing the literal example fixed that.
- **A hook validates the output.** `hooks/validate_build.py` runs after every Write, deletes a bad repair file and tells the agent why. The skill only has to say what to do when that happens.
