# run-sol

The orchestration skill: one sol from intel to report, run by the main session.

What to look at:

- **The description carries the trigger.** "Run the next sol", "advance the mission" and "play a round" all match it, so nobody has to type `/run-sol`.
- **Steps are commands and tables.** The spawn table and the decision table are easier for the model to follow than the same content as prose.
- **The human decision is a step, not a warning.** Step 6 ends the turn after asking, and the engine also refuses to continue until a human answers. The skill asks; the engine enforces.
- **One sentence of "why".** The agent-type rule explains what goes wrong (`role_guard.py` blocks the wrong type), because that is the mistake a model would otherwise make.

Try it: start `claude` in the repo and say `Mission Control, run the next sol`.
