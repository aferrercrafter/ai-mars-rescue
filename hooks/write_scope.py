#!/usr/bin/env python
"""
write_scope.py <prefix...> | write_scope.py --deny-all

Purpose (teaching material):
  Several roles (navigator, mission-log) get broad
  Read/Write/Edit tools but should only ever write inside one folder (or
  one file). Rather than a bespoke hook per role, this one script takes its
  allowed prefixes as argv, straight from the agent file's own frontmatter:

      hooks:
        PreToolUse:
          - matcher: "Write|Edit"
            hooks: [{type: command, command: python,
                     args: ["${CLAUDE_PROJECT_DIR}/hooks/write_scope.py", "game/intel"]}]

  This is the "hooks can live inside an agent file" teaching point from
  docs/DESIGN.md §4 - the hook script is shared, but each agent supplies its
  own scope via arguments.

  A special case: life-support has no Bash tool at all, but its agent file
  still wires `write_scope.py --deny-all` to PreToolUse(Bash) as a
  belt-and-suspenders guard ("an agent with no shell") - --deny-all denies
  every call this hook instance ever sees, regardless of path.
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


def normalize_relative(raw_path, cwd):
    """Absolute paths are made relative to cwd first, then slashes normalized."""
    if raw_path and os.path.isabs(raw_path):
        try:
            raw_path = os.path.relpath(raw_path, cwd)
        except Exception:
            pass
    return (raw_path or "").replace("\\", "/")


def append_event(cwd, agent_type, tool_name, command_or_path, reason):
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
            "agent_id": None,
            "unit": None,
            "tool": tool_name,
            "command": command_or_path,
            "result": None,
            "blocked": True,
            "reason": reason,
            "source": "hook",
        }

        game_dir = os.path.join(cwd, "game")
        os.makedirs(game_dir, exist_ok=True)
        with open(os.path.join(game_dir, "events.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
    except Exception:
        pass


def main():
    prefixes = sys.argv[1:]
    deny_all = prefixes == ["--deny-all"]

    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    try:
        tool_input = payload.get("tool_input", {}) or {}
        agent_type = payload.get("agent_type")
        tool_name = payload.get("tool_name")
        cwd = payload.get("cwd") or os.getcwd()
        file_path = tool_input.get("file_path", "") or ""
        command = tool_input.get("command", "") or ""

        if deny_all:
            reason = f"WRITE SCOPE: {agent_type} may only write under (nothing; no shell access)."
            print(reason, file=sys.stderr)
            append_event(cwd, agent_type, tool_name, command or file_path, reason)
            sys.exit(2)

        normalized = normalize_relative(file_path, cwd)
        allowed = any(
            normalized.startswith(prefix.replace("\\", "/")) for prefix in prefixes
        )

        if not allowed:
            prefixes_str = ", ".join(prefixes) if prefixes else "(nothing)"
            reason = f"WRITE SCOPE: {agent_type} may only write under {prefixes_str}."
            print(reason, file=sys.stderr)
            append_event(cwd, agent_type, tool_name, file_path, reason)
            sys.exit(2)
    except Exception:
        sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()
