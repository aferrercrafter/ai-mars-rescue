---
name: run-sol
description: Run one full sol of the Mars rescue as Mission Control - read intel, give orders, spawn the unit agents, resolve, and hand any post-rescue decision to the human. Use when asked to run the next sol, run sol N, advance the mission, or play a round (a sol is one Martian day, one round of orders).
---

Run the engine commands exactly as written, from the project root, without `cd`.

## 1. Check the mission

```
python engine/engine.py status
python engine/engine.py sitrep
```

- `No mission`: tell the user to run `python scripts/prepare_demo.py --seed 42` or `python engine/engine.py init`, and stop. Do not run `init` yourself.
- `TEAM WAITING FOR ORDERS`: the sol is already resolved. Go straight to step 6 with this sitrep.

## 2. Read intel

```
python engine/engine.py intel
python engine/engine.py notes --for mission-control
```

Read `game/intel/lessons.md` if it exists.

## 3. Give orders

For each rover that is not broken, pick the unexplored search-zone tile it should head for:

```
python engine/engine.py order <unit> "<text>"
```

For each rover that `status` shows as `broken`:

```
python engine/engine.py note mission-control engineer "<unit> broke down at <coord>, needs a repair"
```

## 4. Spawn the wave

Spawn these in one message, each with `run_in_background: true`, and wait for all of them to finish:

| Unit | `subagent_type` | When |
|---|---|---|
| rover-1 | `rover` | always |
| rover-2 | `rover` | always |
| engineer | `engineer` | only if a rover is broken |
| life support | `life-support` | always |

Use these agent types exactly. A hook reads the agent type and blocks engine actions from any other type, so a rover spawned as `general-purpose` loses its sol.

Rover prompt:

```
You are <unit-id>. Orders: <the order from step 3>.
```

Then spawn `navigator` in the background and wait for it.

## 5. Resolve

```
python engine/engine.py resolve
python engine/engine.py sitrep
```

Announce the `RESOLVED:` line. If the sitrep prints `TEAM WAITING FOR ORDERS`, go to step 6. Otherwise go to step 7.

## 6. The human decides

A colonist was rescued, and only the human chooses what happens next. Ask with `AskUserQuestion`:

- `question`: the sitrep block, verbatim, line breaks kept
- `header`: `Orders`
- `options` (single select), mapped to the command to run:

| Option | Command |
|---|---|
| `1 - Continue rescue mission` | `python engine/engine.py continue-mission` |
| `2 - Rest and repair` | `python engine/engine.py rest` |
| `3 - Abort mission` | `python engine/engine.py abort-mission` |

Run the chosen command, print its `ORDERS RECEIVED:` line, and end the turn. Do not start another sol.

If `AskUserQuestion` is not available, print the sitrep and end the turn without choosing.

## 7. Log and report

Spawn `mission-log` in the background. Report to the user: the `RESOLVED:` line, one sentence per unit, and your intent for the next sol.

## Rules

- Never run `engine.py act` yourself. Only rover agents act.
- Run this skill in the main session. Spawn the `mission-control` agent only when the user asks for "the nested variant", and then let it run the whole sol.
