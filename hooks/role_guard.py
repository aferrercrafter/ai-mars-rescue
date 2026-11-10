#!/usr/bin/env python
"""
role_guard.py - Per-role guardrail PreToolUse hook (matcher: Bash).

Purpose (teaching material):
  Five roles share one engine CLI, but only two of them ever touch Bash:
  Mission Control (orders, resolving the sol, reading everything) and the
  rovers (acting, and sending notes as themselves). This hook reads
  `agent_type` off the subagent that is about to run a Bash command and
  denies subcommands outside its lane - "rover-1, resolve the sol" or
  "rover-1, send a note as rover-9" becomes a red BLOCKED line instead of a
  silent cheat.

  `agent_type` is absent for the main session (Mission Control acting
  directly, not as a nested subagent) - that case always passes through,
  matching the access matrix in docs/SPEC.md §4 ("main session: anything but
  truth.json"). Every other agent_type (engineer, life-support, navigator,
  mission-log, ...) has no Bash tool at all per docs/SPEC.md §6, but if one
  ever reached this hook it would fall through to the same deny path as an
  unlisted subcommand.

  A command can chain multiple engine.py calls with `&&`
  (`python engine/engine.py act rover-1 move C3 && python engine/engine.py
  resolve`); every invocation in the chain is checked independently.

  Decision channel (alternative to exit 2, used here because we want a
  structured, model-visible reason rather than just stderr text):
      stdout JSON: {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                     "permissionDecision": "deny",
                     "permissionDecisionReason": "..."}}
      exit 0
  Malformed stdin, or a command with no engine.py invocation at all, always
  allows (fail open / not our concern).

docs/SPEC.md §5 table (v3.1 adds sitrep + the human-decision commands, §11):

  | Subcommand                                                     | mission-control | rover | others |
  |------------------------------------------------------------------|:---:|:---:|:---:|
  | status, map (team), unit, intel, notes, events, replay, sitrep | yes | yes | yes |
  | order, resolve                                                  | yes | no  | no  |
  | continue-mission, rest, abort-mission                           | yes | no  | no  |
  | note <from> ...                                                 | yes, from=mission-control | yes, from=rover-1|rover-2 | no |
  | act rover-N move|scan|rescue                                    | no  | yes | no  |
  | init, apply-ledger, field-check, map --view truth               | no  | no  | no  |
"""
import datetime
import json
import os
import re
import sys

try:  # Windows consoles default to cp1252; hook payloads are UTF-8 JSON
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# Matches each `engine.py <subcmd> <args...>` occurrence; args stop at a
# shell chaining operator (&&, ||, ;) so multiple invocations in one command
# are matched separately instead of one match swallowing the rest.
INVOCATION_RE = re.compile(r"engine\.py\s+(\w[\w-]*)([^&|;]*)")

READONLY_SUBCMDS = {"status", "map", "unit", "intel", "notes", "events", "replay", "sitrep"}
MC_ONLY_SUBCMDS = {"order", "resolve", "continue-mission", "rest", "abort-mission"}
ROVER_ACTIONS = {"move", "scan", "rescue"}

# The only two rover unit ids that may act as `<from>` on a `note` (docs/SPEC.md
# §2). `act` only needs the looser `rover-` prefix check below - the engine
# itself rejects an unknown unit id.
ROVER_NOTE_UNITS = ("rover-1", "rover-2")


def allowed_summary(agent_type):
    """Human-readable 'allowed' list for the deny reason."""
    base = "status, map, unit, intel, notes, events, replay, sitrep"
    if agent_type == "mission-control":
        return (
            base
            + ", order, resolve, note mission-control ..., "
            + "continue-mission, rest, abort-mission"
        )
    if agent_type == "rover":
        return base + ", note rover-1|rover-2 ..., act rover-N move|scan|rescue"
    return base


def check_invocation(agent_type, subcmd, args):
    """Returns None if allowed, else a deny reason string."""
    invocation_text = subcmd + (" " + " ".join(args) if args else "")

    if subcmd == "map":
        if "--view" in args:
            idx = args.index("--view")
            view = args[idx + 1] if idx + 1 < len(args) else None
            if view == "truth":
                return (
                    f"ROLE GUARD: {agent_type} may not run 'map --view truth' "
                    f"(allowed: {allowed_summary(agent_type)})"
                )
        return None

    if subcmd in READONLY_SUBCMDS:
        return None

    if subcmd in MC_ONLY_SUBCMDS:
        if agent_type == "mission-control":
            return None
        return (
            f"ROLE GUARD: {agent_type} may not run '{invocation_text}' "
            f"(allowed: {allowed_summary(agent_type)})"
        )

    if subcmd == "note":
        frm = args[0] if args else None
        if agent_type == "mission-control":
            if frm == "mission-control":
                return None
            return (
                "ROLE GUARD: mission-control may only send notes as "
                "mission-control"
            )
        if agent_type == "rover":
            if frm in ROVER_NOTE_UNITS:
                return None
            return "ROLE GUARD: rover may only send notes as rover-1 or rover-2"
        return (
            f"ROLE GUARD: {agent_type} may not run '{invocation_text}' "
            f"(allowed: {allowed_summary(agent_type)})"
        )

    if subcmd == "act":
        unit = args[0] if args else None
        action = args[1] if len(args) > 1 else None

        if agent_type != "rover":
            return (
                f"ROLE GUARD: {agent_type} may not run '{invocation_text}' "
                f"(allowed: {allowed_summary(agent_type)})"
            )

        if not unit or not unit.startswith("rover-"):
            return "ROLE GUARD: rover may only command rover-N units"

        if action not in ROVER_ACTIONS:
            return (
                f"ROLE GUARD: {agent_type} may not run '{invocation_text}' "
                f"(allowed: {allowed_summary(agent_type)})"
            )

        return None

    # Unlisted subcommand (init, apply-ledger, field-check, unknown, ...):
    # denied for every agent_type.
    return (
        f"ROLE GUARD: {agent_type} may not run '{invocation_text}' "
        f"(allowed: {allowed_summary(agent_type)})"
    )


def append_event(cwd, agent_type, agent_id, command, unit, reason):
    try:
        sol = None
        team_path = os.path.join(cwd, "game", "team.json")
        try:
            with open(team_path, "r", encoding="utf-8") as f:
                sol = json.load(f).get("sol")
        except Exception:
            sol = None

        event = {
            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            "sol": sol,
            "kind": "blocked",
            "agent_type": agent_type,
            "agent_id": agent_id,
            "unit": unit,
            "tool": "Bash",
            "command": command,
            "result": None,
            "result_full": None,
            "blocked": True,
            "reason": reason,
            "source": "hook",
            "file": None,
            "prompt": None,
            "message": None,
            "from": None,
            "to": None,
            "text": None,
        }

        game_dir = os.path.join(cwd, "game")
        os.makedirs(game_dir, exist_ok=True)
        with open(os.path.join(game_dir, "events.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
    except Exception:
        pass


def deny(reason):
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    print(json.dumps(output))
    sys.exit(0)


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    try:
        agent_type = payload.get("agent_type")
        agent_id = payload.get("agent_id")
        command = (payload.get("tool_input", {}) or {}).get("command", "") or ""
        cwd = payload.get("cwd") or os.getcwd()

        if not agent_type:
            # Main session (not a subagent) - not subject to per-role limits.
            sys.exit(0)

        for match in INVOCATION_RE.finditer(command):
            subcmd = match.group(1)
            args = match.group(2).split()
            unit = args[0] if subcmd == "act" and args else None

            reason = check_invocation(agent_type, subcmd, args)
            if reason:
                append_event(cwd, agent_type, agent_id, command, unit, reason)
                deny(reason)
    except Exception:
        sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()
