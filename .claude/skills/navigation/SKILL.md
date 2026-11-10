---
name: navigation
description: Turn explored terrain into short reusable lessons and save them to the navigator's memory and game/intel/lessons.md. Use when acting as the navigator after a rover wave, or when asked which terrain or routes to avoid.
---

## 1. Compare the map with what you already know

Read `tiles` in `game/team.json` and your memory files under `.claude/agent-memory/navigator/`. Look for terrain worth a rule that is not recorded yet: clusters of rough tiles, dead ends, routes that reached the colonist or wasted a sol.

## 2. Write 1 to 3 lessons

One bullet each: a general rule, its evidence coordinates, and the objective it came from.

```
- Rough tiles cluster around D6-E7 (objective 1): route rovers through D5.
- Row F of the zone was a dead end (objective 1).
```

Write rules, not a list of tiles. If nothing new is worth a rule, write nothing.

## 3. Save them

Add the new lessons to your memory, and append the same bullets to `game/intel/lessons.md` so roles without memory can read them.

## 4. Report

One line:

```
navigator | <number of new lessons> | <files updated> | <the most useful new lesson>
```
