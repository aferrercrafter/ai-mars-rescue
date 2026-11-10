#!/usr/bin/env python
"""
apply_oxygen.py - Integration hook, PostToolUse(Edit|Write).

Purpose (teaching material):
  Life Support has no Bash tool at all (Read, Edit only - "an agent with no
  shell; filesystem as a bus", per docs/DESIGN.md §3). It expresses its
  decision by editing game/field/oxygen-ledger.md. This hook is the other
  half of that bus: whenever that specific file is saved, we call
  `python engine/engine.py apply-ledger` on its behalf and surface a
  validation error back to the model if the ledger was malformed.

  This is a PostToolUse hook, so the Edit/Write has already happened - we
  cannot undo the file write, only reject the *ledger* by telling the model
  to fix it and save again (exit 2 sends stderr back to the model, same as
  a PreToolUse block).

  The engine may not exist yet while hooks are being built in parallel, so
  a missing engine/engine.py must fail OPEN (exit 0) rather than error.
"""
import json
import os
import subprocess
import sys

try:  # Windows consoles default to cp1252; hook payloads are UTF-8 JSON
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def append_rejection_event(cwd, agent_type, agent_id, tool_name, file_path, reason):
    """Append a `blocked` event so the mission display can show this rejection.

    Same shape as the events written by log_events.py / write_scope.py; the
    display maps the reason prefix back to this hook. Never raises.
    """
    try:
        import datetime
        game_dir = os.path.join(cwd, "game")
        os.makedirs(game_dir, exist_ok=True)
        sol = None
        try:
            with open(os.path.join(game_dir, "team.json"), encoding="utf-8") as f:
                sol = json.load(f).get("sol")
        except Exception:
            pass
        event = {
            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            "sol": sol, "kind": "blocked",
            "agent_type": agent_type or None, "agent_id": agent_id or None,
            "unit": None, "tool": tool_name, "command": None,
            "result": None, "blocked": True, "reason": reason, "source": "hook",
            "file": file_path.replace("\\", "/") if file_path else None,
            "result_full": None, "prompt": None, "message": None,
        }
        with open(os.path.join(game_dir, "events.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
    except Exception:
        pass



def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    try:
        file_path = (payload.get("tool_input", {}) or {}).get("file_path", "") or ""
        cwd = payload.get("cwd") or os.getcwd()
        agent_type = payload.get("agent_type") or ""
        agent_id = payload.get("agent_id") or ""
        tool_name = payload.get("tool_name") or ""
    except Exception:
        sys.exit(0)

    normalized = file_path.replace("\\", "/")
    if not normalized.endswith("game/field/oxygen-ledger.md"):
        sys.exit(0)

    engine_path = os.path.join(cwd, "engine", "engine.py")
    if not os.path.isfile(engine_path):
        # Engine not built yet (or this project has no engine) - fail open.
        sys.exit(0)

    try:
        result = subprocess.run(
            [sys.executable, engine_path, "apply-ledger"],
            capture_output=True,
            text=True,
            cwd=cwd,
        )
    except Exception:
        sys.exit(0)

    if result.returncode == 0:
        sys.exit(0)

    error_line = ""
    for line in (result.stdout or "").splitlines():
        if line.startswith("ERROR:"):
            error_line = line
            break
    if not error_line:
        lines = (result.stdout or "").splitlines()
        error_line = lines[0] if lines else "(no engine output)"

    reason = f"LEDGER REJECTED: {error_line}. Fix the ledger and save again."
    append_rejection_event(cwd, agent_type, agent_id, tool_name or "Edit", file_path, reason)
    print(reason, file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
