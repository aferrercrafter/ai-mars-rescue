---
name: field-works
description: Repair procedure for broken rovers - find them in the mission state and write one game/field/repair-<unit>.json per rover. Use when acting as the engineer, or when a rover is reported broken and needs a repair filed.
---

## 1. Find broken rovers

Read `game/notes.md` and `game/team.json`. Note the current `sol` and every unit with `status: broken`.

## 2. Write one repair file per broken rover

Write `game/field/repair-<unit>.json` with exactly these four keys:

```json
{"type": "repair", "unit": "rover-2", "sol": 6, "note": "freeing from rough terrain"}
```

`sol` must equal the current sol.

## 3. If a file is rejected

A validation hook checks every file you write. On a problem it deletes the file and answers `FIELD REJECTED: <reason>`. Fix the one thing the reason names and write the file again, once.

## 4. Report

One line:

```
engineer | <files written, or "no repairs needed"> | <accepted/rejected> | <one observation>
```
