---
name: mission-log
description: Write the narrative dispatch for one sol - gather the sol's events, notes and orders, then append 60-90 words to game/dispatches.md. Use when acting as the mission log, or when asked to write up a sol.
---

## 1. Gather this sol's material

1. Find the last `<!-- events:N -->` footer in `game/dispatches.md` (if there is none, N is -1).
2. Read `game/events.jsonl` from the line after N. Each line is one JSON event and the line number is its index.
3. Read `game/notes.md` and this sol's `game/orders/sol-<N>.md`.

## 2. Write the dispatch

60 to 90 words, present tense, third person, units named by id. For example: "Rover-2 pushes into D6 and breaks down; it notes the engineer, who files a repair."

- Lead with a rescue or an `ORDERS RECEIVED` decision if the sol had one.
- Mention the notes agents exchanged.
- Name every `BLOCKED` event and its reason.

## 3. Append it

Add this to the end of `game/dispatches.md`:

```
## Sol <N>

<the dispatch>

<!-- events:<index of the last event you read> -->
```

## 4. Report

One line:

```
mission-log | dispatch for sol <N> | <word count> words | <the dispatch's headline sentence>
```
