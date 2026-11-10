# Skill evals

`scripts/check_sol.py` tells you whether one sol went well. This folder answers a different question: **does each skill make its agent better than the same agent without it?**

Every case is run several times in two configurations and graded by code, with no model involved in the grading:

| Configuration | What changes |
|---|---|
| `with_skill` | nothing; the repo as it is |
| `without_skill` | the skill's folder is deleted. For a role agent, its `skills:` line and the sentence that points at the skill are removed too. For `run-sol`, the main session simply cannot find the skill. |

## What is here

| File | Role |
|---|---|
| `cases/<skill>.json` | The cases: a starting state (`fixture`), the prompt, and the assertions |
| `fixtures.py` | Builds each starting state through the engine CLI |
| `checks.py` | One small function per assertion; it reads `game/` and the event log |
| `run_evals.py` | Runs the cases headless, grades them, prints the table |
| `trigger/run-sol.json` | Phrasings that should, and should not, load `run-sol` |
| `RESULTS.md` | What the last full run showed, and what was changed because of it |
| `results/` | Raw output of your own runs (not in git) |

## Run them

From the project root. You need the `claude` CLI logged in; the runs cost money (the table in `RESULTS.md` gives the totals for a full run).

```
python evals/run_evals.py --plan              # list the runs and stop; costs nothing
python evals/run_evals.py life-support        # one skill: 3 cases x 2 configurations x 3 runs
python evals/run_evals.py                     # all six skills
python evals/run_evals.py --summary           # print the table again from evals/results/
python evals/run_evals.py run-sol --trigger   # does the description load the skill?
```

Useful options: `--runs 5`, `--case low-oxygen-conserve`, `--config with_skill`, `--jobs 2`, `--model opus`.

## How one run works

1. The repo is copied to a temporary folder, so your `game/`, your agent memory and your agent files are never touched and runs can go in parallel.
2. For `without_skill`, the skill is removed from the copy.
3. `fixtures.py` builds the starting state, for example "sol 2, oxygen 3, last sol was conserve".
4. `claude -p` runs with only this project's settings (`--setting-sources project,local`), so your personal plugins and hooks do not change the result. For a role skill, a small session spawns the real agent with the case prompt; for `run-sol` the prompt goes to the main session.
5. `checks.py` grades what was left behind and writes `grading.json` next to the raw output.

A copy is not a folder you have trusted, so the hooks written inside the agent files do not run there (see `hooks/README.md`). The hooks in `.claude/settings.json` do, and those are the ones the checks rely on.

## Adding a case

Add an entry to `cases/<skill>.json`. Each assertion names a function in `checks.py` and its arguments:

```json
{"text": "Exactly one scan", "check": "act_count", "args": {"unit": "rover-1", "action": "scan", "count": 1}}
```

Write the assertion so that it can fail. An assertion that passes with and without the skill tells you nothing about the skill; `RESULTS.md` lists which ones turned out that way.

The layout of `results/` (`eval-<case>/<configuration>/run-<n>/grading.json`) is the one the `skill-creator` skill uses, so its `aggregate_benchmark` script and viewer work on `evals/results/<skill>`.
