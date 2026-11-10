---
name: life-support
description: Oxygen ledger procedure - choose normal or conserve mode for the sol and record it in game/field/oxygen-ledger.md. Use when acting as life support, or when the oxygen mode for a sol has to be set.
---

## 1. Read state

Read `game/team.json`: `sol`, `oxygen_sols`, and `pending.ledger` (the mode used last sol).

## 2. Choose the mode

- `conserve` when `oxygen_sols` is 3 or less and last sol was not `conserve`.
- `normal` otherwise.

The engine rejects `conserve` two sols in a row.

## 3. Edit the ledger

Edit `game/field/oxygen-ledger.md` and keep this exact format:

```
# Oxygen ledger
sol: <current sol>
mode: normal | conserve
note: <why, free text>
```

## 4. If the ledger is rejected

A hook applies the ledger as soon as you save it. No message means it was accepted. `LEDGER REJECTED: <reason>` means it was not: fix the one thing the reason names and save again, once.

## 5. Report

One line:

```
life-support | <mode chosen> | <accepted/rejected> | <oxygen forecast>
```
