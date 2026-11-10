# Results

Full run on 2026-11-10, Claude Code 2.1.322. Three runs per case and configuration. Role agents ran on their own models (haiku; sonnet for `mission-log`), spawned by a haiku session. `run-sol` ran in a sonnet main session.

Each figure is the share of assertions that passed, averaged over the runs, with the standard deviation between runs.

| Skill | Case | With skill | Without skill |
|---|---|---|---|
| `rover-ops` | ordered-move-and-scan | 100% ±0 | 56% ±19 |
| `rover-ops` | broken-rover-asks-for-repair | 100% ±0 | 67% ±16 |
| `rover-ops` | rescue-on-own-tile | 100% ±0 | 19% ±8 |
| `rover-ops` | **all cases** | **100%** | **47%** |
| `field-works` | broken-rover-gets-repair | 100% ±0 | 80% ±0 |
| `field-works` | no-broken-rover | 100% ±0 | 50% ±0 |
| `field-works` | **all cases** | **100%** | **65%** |
| `life-support` | plenty-of-oxygen | 100% ±0 | 50% ±0 |
| `life-support` | low-oxygen-conserve | 100% ±0 | 8% ±14 |
| `life-support` | low-oxygen-after-conserve | 100% ±0 | 50% ±43 |
| `life-support` | **all cases** | **100%** | **36%** |
| `navigation` | first-lessons | 100% ±0 | 24% ±8 |
| `navigation` | nothing-new | 100% ±0 | 33% ±14 |
| `navigation` | **all cases** | **100%** | **29%** |
| `mission-log` | sol-with-repair-and-block | 100% ±0 | 63% ±6 |
| `mission-log` | second-dispatch-keeps-first | 100% ±0 | 54% ±7 |
| `mission-log` | **all cases** | **100%** | **59%** |
| `run-sol` | full-sol | 100% ±0 | 72% ±38 |
| `run-sol` | colonist-already-rescued | 100% ±0 | 100% ±0 |
| `run-sol` | **all cases** | **100%** | **86%** |

84 graded runs cost $2.31 in total: about $0.004 for a role-agent run and $0.22 for a full sol.

## What the skills add

- **`rover-ops`**: without it no rover ever rescued the colonist it was standing on (0 of 3), and rovers went looking for the engine's commands with `--help` and chained shell calls that the permission rule or the role guard refused. A broken rover asked the engineer for help in 1 run of 3.
- **`field-works`**: the engineer writes a valid repair file either way. The skill adds the one-line report and the "no repairs needed" answer.
- **`life-support`**: without it the agent never chose `conserve` at oxygen 3 (0 of 3). It left the ledger alone or wrote `normal`.
- **`navigation`**: without it the navigator wrote 5 tile-by-tile notes with no objective (2 runs) or no bullets at all (1 run), and added duplicates when it already knew the terrain (2 of 3).
- **`mission-log`**: without it the dispatch was too short in 4 of 6 runs (36 to 49 words), had no events footer on a first dispatch, and the earlier section was rewritten in 2 of 3 runs.
- **`run-sol`**: without it the result depends on the run. One session ended after 13 seconds without spawning anyone, one ran the sol without spawning `rover` agents or the navigator, one happened to do everything.

## Does the `run-sol` description trigger?

16 phrasings in `trigger/run-sol.json`, three runs each, stopped at the first tool call: **48 of 48 as wanted**. All eight "play a sol" phrasings loaded the skill first, including ones that never say "sol" ("ok next round please, same plan as before"). None of the eight near misses did (show the map, why did rover-2 break down, run the tests, explain the skill without running it).

`CLAUDE.md` also tells the main session to follow `run-sol` when asked to run a sol, so this measures the description and that rule together.

## What was changed because of the evals

One skill was edited. `life-support` told the agent that `pending.ledger` in `game/team.json` is the mode used last sol. The engine clears that field when a sol resolves, so it is always empty when life support starts. The agents passed anyway, because they found last sol's mode in the ledger file they were about to edit. The skill now points at the ledger file. Re-run after the change: 9 of 9 runs at 100%.

No other skill failed an assertion, so no other skill was changed.

## Assertions that tell you nothing here

These passed in every run of both configurations, so they do not measure the skill. They are kept as guard rails.

- `run-sol` / colonist-already-rescued: all four. `CLAUDE.md` already says only the human decides after a rescue, and that rule is enough.
- `run-sol` / full-sol: the main session never runs `act` or `init`; no action blocked by a hook.
- `field-works`: the repair file is valid, accepted first time, and frees the rover.
- `rover-ops`: exactly one move; the rover ends on the ordered tile; a broken rover does not try to move or scan.
- "No hook blocked or rejected anything" for `field-works`, `navigation` and `mission-log`.

## Limits

- Three runs per cell shows gross variance, not small differences. With every with-skill cell at 100%, these cases cannot tell a good skill from a slightly better one; harder cases would be needed for that.
- All cases use seed 42.
- The runs happen in untrusted copies, so the `write_scope.py` hooks declared in the agent files did not run during the evals. They were checked separately in the trusted project folder: an agent with the same `hooks:` block as `mission-log.md` was refused a write to `game/hook-test.md` with `WRITE SCOPE: ... may only write under game/dispatches.md`.
- The post-rescue case runs headless, where `AskUserQuestion` does not exist. The interactive question itself is not tested.
