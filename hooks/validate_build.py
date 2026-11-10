#!/usr/bin/env python
"""
validate_build.py - Deterministic validation hook, PostToolUse(Write).

Purpose (teaching material):
  The Engineer's only tools are Read/Write; it expresses repairs and bridges
  by writing game/field/repair-<unit>.json or game/field/bridge-<coord>.json.
  This hook is what keeps that channel honest: a bad filename gets deleted
  outright, and a well-named file still has to pass
  `python engine/engine.py field-check <path>` (schema + rules: is the unit
  actually stuck, is the bridge tile actually an explored crater, etc.)
  before it's allowed to survive on disk. `resolve` never has to defend
  itself against malformed field files - this hook already threw them away.

  The engine may not exist yet while hooks are being built in parallel, so
  a missing engine/engine.py must fail OPEN (exit 0) rather than error -
  we simply can't validate yet, and we leave the file alone.
"""
import json
import os
import re
import subprocess
import sys

try:  # Windows consoles default to cp1252; hook payloads are UTF-8 JSON
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

FIELD_FILE_RE = re.compile(r"(?:^|/)game/field/([^/]+\.json)$")
NAME_RE = re.compile(r"^(repair-[a-z]+-\d+|bridge-[A-H]\d{1,2})\.json$")


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
    match = FIELD_FILE_RE.search(normalized)
    if not match:
        # Not a top-level game/field/*.json write (e.g. field/applied/... or
        # an unrelated file) - nothing for this hook to do.
        sys.exit(0)

    filename = match.group(1)

    def delete_file():
        try:
            abs_path = file_path if os.path.isabs(file_path) else os.path.join(cwd, file_path)
            if os.path.isfile(abs_path):
                os.remove(abs_path)
        except Exception:
            pass

    if not NAME_RE.match(filename):
        delete_file()
        reason = "FIELD REJECTED: filename must be repair-<unit>.json or bridge-<coord>.json"
        append_rejection_event(cwd, agent_type, agent_id, tool_name or "Write", file_path, reason)
        print(reason, file=sys.stderr)
        sys.exit(2)

    engine_path = os.path.join(cwd, "engine", "engine.py")
    if not os.path.isfile(engine_path):
        # Engine not built yet - fail open, leave the file for later validation.
        sys.exit(0)

    try:
        result = subprocess.run(
            [sys.executable, engine_path, "field-check", file_path],
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

    delete_file()
    reason = f"FIELD REJECTED: {error_line}"
    append_rejection_event(cwd, agent_type, agent_id, tool_name or "Write", file_path, reason)
    print(reason, file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
