#!/usr/bin/env python
"""
protect_truth.py - "Anti-cheat" PreToolUse hook (matcher: Read|Write|Edit|Grep|Glob|Bash).

Purpose (teaching material):
  Claude Code hooks are plain shell commands wired up in .claude/settings.json.
  Before certain tools run, Claude Code pipes a JSON object describing the
  pending tool call to this script's stdin, e.g.:
      {"tool_name": "Read", "tool_input": {"file_path": "game/truth.json"},
       "agent_type": "rover", "agent_id": "rover-1"}

  game/truth.json holds the ENTIRE map (including the astronaut's real
  position) and is engine-managed. Letting any agent - including the main
  session - read or touch it directly would let it cheat by seeing through
  the fog. This hook blocks that for every tool that could reach the file:
  Read/Write/Edit (file_path), Grep (pattern/path), Glob (pattern/path) and
  Bash (command, e.g. `type game/truth.json` or `cat game/truth.json`).

  Exit code contract (set by Claude Code, not us to invent):
    exit 0 -> allow the tool call to proceed
    exit 2 -> BLOCK the tool call; stderr text is shown back to the model
  Any other exit code is a non-blocking error, so on any doubt (malformed
  stdin, missing keys) we fail OPEN (exit 0) rather than break the session.
"""
import datetime
import json
import os
import sys

try:  # Windows consoles default to cp1252; hook payloads are UTF-8 JSON
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

REASON = "ANTI-CHEAT"
MESSAGE = (
    "ANTI-CHEAT: game/truth.json holds the full map and the astronaut's "
    "position. Use python engine/engine.py (status/map/intel/tile) instead."
)


def append_event(cwd, agent_type, agent_id, tool_name, command):
    """Best-effort append to game/events.jsonl - never let logging break the hook."""
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
            "unit": None,
            "tool": tool_name,
            "command": command,
            "result": None,
            "blocked": True,
            "reason": REASON,
            "source": "hook",
        }

        game_dir = os.path.join(cwd, "game")
        os.makedirs(game_dir, exist_ok=True)
        with open(os.path.join(game_dir, "events.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
    except Exception:
        pass


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        # Malformed/missing/empty stdin: fail open, never crash the session.
        sys.exit(0)

    try:
        tool_input = payload.get("tool_input", {}) or {}
        agent_type = payload.get("agent_type")
        agent_id = payload.get("agent_id")
        tool_name = payload.get("tool_name")
        cwd = payload.get("cwd") or os.getcwd()

        blocked = False

        # Read/Write/Edit use file_path; Grep/Glob use pattern and/or path.
        for key in ("file_path", "path", "pattern"):
            value = tool_input.get(key)
            if value:
                normalized = str(value).replace("\\", "/")
                if "game/truth.json" in normalized or normalized.endswith("/truth.json"):
                    blocked = True
                    break

        # Bash: looser substring check on the whole command.
        command = tool_input.get("command")
        if not blocked and command:
            normalized_command = str(command).replace("\\", "/")
            # the display server (ui/server.py) serves the same file at /api/truth
            if "truth.json" in normalized_command or "api/truth" in normalized_command:
                blocked = True

        if blocked:
            print(MESSAGE, file=sys.stderr)
            append_event(cwd, agent_type, agent_id, tool_name, command)
            sys.exit(2)
    except Exception:
        # Never let an unexpected shape crash the session - fail open.
        sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()
