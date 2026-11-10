---
name: rover-ops
description: One sol of work for a single rover - check status, move toward the search zone, scan, rescue the colonist if standing on their tile, and report one line. Use when acting as rover-1 or rover-2.
---

`<you>` is the unit id from your prompt. You get one `move` and one `scan` per sol.

Run each command below exactly as written, one command per Bash call. You are already in the project root: adding `cd` or chaining commands makes the call fall outside the permission rule and it is denied.

## 1. Check yourself and the map

```
python engine/engine.py unit <you>
python engine/engine.py map
```

If `status: broken`, do not move or scan. Ask for a repair and go to step 4:

```
python engine/engine.py note <you> engineer "broke down at <coord>, need a repair"
```

## 2. Move

Follow your orders. With no specific order, head for the nearest unexplored search-zone tile (`Z` on the map). Pick the destination from `legal_moves` in the `unit` output, and prefer a route without `~` (rough) tiles: entering one can break you down.

```
python engine/engine.py act <you> move <coord>
```

## 3. Scan

```
python engine/engine.py act <you> scan
```

- Colonist on your own tile: `python engine/engine.py act <you> rescue`
- Colonist on another tile: `python engine/engine.py note <you> mission-control "astronaut seen at <coord>"`

## 4. Report

One line:

```
<you> | <actions taken> | <OK/ERROR lines> | <one observation>
```

Lead with a rescue or a breakdown if either happened.
