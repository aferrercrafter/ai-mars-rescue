#!/usr/bin/env python
"""
log_events.py - Audit-trail hook for PostToolUse(Bash, Edit, Write),
SubagentStart, SubagentStop.

Purpose (teaching material):
  The Mars Rescue UI (ui/server.py + index.html) is powered entirely by
  game/events.jsonl - it never talks to Claude Code directly. This hook is
  the writer: every time a subagent runs an engine.py command via Bash,
  every time a subagent edits/writes a file under game/ or
  .claude/agent-memory/, and every time a subagent starts or stops, we
  append one JSON line.

  Claude Code pipes different shapes on stdin depending on hook_event_name:
    PostToolUse (Bash): {"hook_event_name": "PostToolUse", "tool_name": "Bash",
                  "tool_input": {"command": "..."},
                  "tool_response": "<stdout>" | {"stdout": "...", ...},
                  "agent_type": "rover", "agent_id": "rover-1"}
    PostToolUse (Edit/Write): {"hook_event_name": "PostToolUse",
                  "tool_name": "Edit" | "Write",
                  "tool_input": {"file_path": "...", "new_string": "..."}
                               | {"file_path": "...", "content": "..."},
                  "agent_type": "...", "agent_id": "..."}
    SubagentStart: {"hook_event_name": "SubagentStart",
                  "tool_input": {"prompt": "..."} | "prompt": "..." | "description": "...",
                  "agent_type": "...", "agent_id": "..."}
    SubagentStop: {"hook_event_name": "SubagentStop",
                  "last_assistant_message": "..." | {...} | [...],
                  "agent_type": "...", "agent_id": "..."}

  We only care about Bash calls that actually touched the engine - a plain
  `git status` or `ls` produces no event - and about Edit/Write calls that
  touch game/ or .claude/agent-memory/ (anything else is skipped). This
  hook can never block anything (PostToolUse / lifecycle hooks run after
  the fact), so it always exits 0, wrapped in one broad try/except. Every
  event line carries the full schema; fields that don't apply are null.

  v3 adds `kind:"message"` rows (docs/SPEC.md §4) - the data behind the
  UI's Interactions view - for three sources, always in addition to (never
  instead of) the normal event for that call:
    (a) `engine.py order <unit> <text...>` -> from=mission-control, to=<unit>
    (b) `engine.py note <from> <to> <text...>` -> from/to as given
    (c) SubagentStop with a non-empty last_assistant_message -> from=<the
        unit id if the report's first token looks like one, else
        agent_type>, to=mission-control, text=first line of the report
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

UNIT_AFTER_ACT_RE = re.compile(r"\bact\s+(\S+)")
ORDER_RE = re.compile(r"engine\.py\s+order\s+(\S+)\s+(.*?)\s*(?:&&|\|\||;|$)")
NOTE_RE = re.compile(r"engine\.py\s+note\s+(\S+)\s+(\S+)\s+(.*?)\s*(?:&&|\|\||;|$)")

# A unit id looks like "rover-1" - a role word, a dash, then digits. Used to
# recognise a `note`'s <from> as a unit (rather than a role name like
# "mission-control" or "engineer"), and to recognise a subagent report that
# opens with its own unit id ("rover-2 | broke down at D4 | ...").
UNIT_ID_RE = re.compile(r"^[A-Za-z]+-\d+$")


def read_sol(cwd):
    try:
        with open(os.path.join(cwd, "game", "team.json"), "r", encoding="utf-8") as f:
            return json.load(f).get("sol")
    except Exception:
        return None


def append_event(cwd, event):
    try:
        game_dir = os.path.join(cwd, "game")
        os.makedirs(game_dir, exist_ok=True)
        with open(os.path.join(game_dir, "events.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
    except Exception:
        pass


def _response_text(tool_response):
    """tool_response may be a plain string or a dict with a 'stdout' key."""
    if isinstance(tool_response, dict):
        return tool_response.get("stdout", "") or ""
    elif isinstance(tool_response, str):
        return tool_response
    return ""


def first_result_line(tool_response):
    text = _response_text(tool_response)
    first_line = text.splitlines()[0] if text.splitlines() else ""
    return first_line[:200]


def full_result_text(tool_response):
    text = _response_text(tool_response)
    return text[:4000]


def resolve_file_path(cwd, path):
    """Normalise to forward slashes; make relative to cwd when absolute and under it."""
    if not path:
        return None
    try:
        if os.path.isabs(path):
            try:
                rel = os.path.relpath(path, cwd).replace("\\", "/")
            except Exception:
                rel = None
            if rel is not None and not rel.startswith(".."):
                return rel
        return path.replace("\\", "/")
    except Exception:
        return None


def edit_write_content(tool_input):
    """new_string (Edit) or content (Write), truncated to 1500 chars, else None."""
    try:
        val = tool_input.get("new_string")
        if val is None:
            val = tool_input.get("content")
        if val is None:
            return None
        if not isinstance(val, str):
            try:
                val = json.dumps(val)
            except Exception:
                val = str(val)
        return val[:1500]
    except Exception:
        return None


def extract_prompt(payload):
    """First present of tool_input.prompt, payload.prompt, payload.description."""
    try:
        tool_input = payload.get("tool_input")
        if isinstance(tool_input, dict):
            val = tool_input.get("prompt")
            if val is not None:
                return str(val)[:1500]
        val = payload.get("prompt")
        if val is not None:
            return str(val)[:1500]
        val = payload.get("description")
        if val is not None:
            return str(val)[:1500]
    except Exception:
        pass
    return None


def report_line(message):
    """Return the one line of an agent's final message worth showing.

    The skills ask for `<unit> | <actions> | <result> | <observation>`, but a
    model sometimes wraps it in markdown. Prefer the first line that looks
    like that format; otherwise the last non-empty line (usually the summary);
    otherwise the first line.
    """
    lines = [ln.strip().strip("*").strip() for ln in message.splitlines() if ln.strip()]
    if not lines:
        return message.strip()
    for ln in lines:
        if " | " in ln and not ln.startswith("#"):
            return ln
    for ln in reversed(lines):
        if not ln.startswith("#") and len(ln) > 12:
            return ln
    return lines[0]


def extract_message(payload):
    """last_assistant_message, json-dumped if dict/list, truncated to 4000 chars."""
    try:
        val = payload.get("last_assistant_message")
        if val is None:
            return None
        if isinstance(val, (dict, list)):
            val = json.dumps(val)
        elif not isinstance(val, str):
            val = str(val)
        return val[:4000]
    except Exception:
        return None


def strip_quotes(text):
    """Drop one layer of matching surrounding quotes, if present."""
    text = (text or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    return text


def extract_unit(command):
    """Unit for a tool event's 'unit' field: the acted-on unit for `act`, or
    the sending unit for `note` when its <from> looks like a unit id."""
    match = UNIT_AFTER_ACT_RE.search(command)
    if match:
        return match.group(1)
    match = NOTE_RE.search(command)
    if match:
        frm = match.group(1)
        if UNIT_ID_RE.match(frm):
            return frm
    return None


def base_event(cwd, kind, agent_type, agent_id):
    return {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "sol": read_sol(cwd),
        "kind": kind,
        "agent_type": agent_type,
        "agent_id": agent_id,
        "unit": None,
        "tool": None,
        "command": None,
        "file": None,
        "result": None,
        "result_full": None,
        "prompt": None,
        "message": None,
        "blocked": False,
        "reason": None,
        "source": "hook",
        "from": None,
        "to": None,
        "text": None,
    }


def message_event(cwd, agent_type, agent_id, frm, to, text):
    event = base_event(cwd, "message", agent_type, agent_id)
    event["from"] = frm
    event["to"] = to
    event["text"] = text
    return event


def question_text(tool_input):
    """First question's `question` field (<=300 chars); else any string field
    found in tool_input, as a fallback for a differently-shaped payload."""
    try:
        questions = (tool_input or {}).get("questions")
        if isinstance(questions, list) and questions:
            first = questions[0]
            if isinstance(first, dict):
                val = first.get("question")
                if isinstance(val, str) and val:
                    return val[:300]
        for val in (tool_input or {}).values():
            if isinstance(val, str) and val.strip():
                return val[:300]
    except Exception:
        pass
    return None


def answer_message(tool_response):
    """The chosen answer(s), inspecting tool_response defensively: a plain
    string as-is; a dict tried in order as `answers` (dict of question->answer,
    values joined with '; '), `answer`, `result`, else json-dumped. <=300 chars."""
    try:
        if isinstance(tool_response, str):
            return tool_response[:300]
        if isinstance(tool_response, dict):
            answers = tool_response.get("answers")
            if isinstance(answers, dict) and answers:
                return "; ".join(str(v) for v in answers.values())[:300]
            val = tool_response.get("answer")
            if val is not None:
                return str(val)[:300]
            val = tool_response.get("result")
            if val is not None:
                return str(val)[:300]
            return json.dumps(tool_response)[:300]
    except Exception:
        pass
    return None


def maybe_append_order_or_note_message(cwd, agent_type, agent_id, command):
    match = ORDER_RE.search(command)
    if match:
        unit = match.group(1)
        text = strip_quotes(match.group(2))
        append_event(cwd, message_event(cwd, agent_type, agent_id, "mission-control", unit, text))
        return

    match = NOTE_RE.search(command)
    if match:
        frm = match.group(1)
        to = match.group(2)
        text = strip_quotes(match.group(3))
        append_event(cwd, message_event(cwd, agent_type, agent_id, frm, to, text))


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    try:
        hook_event_name = payload.get("hook_event_name")
        agent_type = payload.get("agent_type")
        agent_id = payload.get("agent_id")
        cwd = payload.get("cwd") or os.getcwd()

        if hook_event_name == "SubagentStart":
            event = base_event(cwd, "agent_start", agent_type, agent_id)
            event["prompt"] = extract_prompt(payload)
            append_event(cwd, event)
        elif hook_event_name == "SubagentStop":
            event = base_event(cwd, "agent_stop", agent_type, agent_id)
            message = extract_message(payload)
            event["message"] = message
            append_event(cwd, event)
            if message:
                first_line = report_line(message)
                first_line = first_line[:300]
                first_token = first_line.split()[0] if first_line.split() else ""
                frm = first_token if UNIT_ID_RE.match(first_token) else agent_type
                append_event(
                    cwd,
                    message_event(cwd, agent_type, agent_id, frm, "mission-control", first_line),
                )
        elif hook_event_name == "PostToolUse":
            tool_name = payload.get("tool_name")
            tool_input = payload.get("tool_input", {}) or {}
            if tool_name == "Bash":
                command = tool_input.get("command", "") or ""
                if "engine.py" in command:
                    event = base_event(cwd, "tool", agent_type, agent_id)
                    event["unit"] = extract_unit(command)
                    event["tool"] = tool_name
                    event["command"] = command
                    event["result"] = first_result_line(payload.get("tool_response"))
                    event["result_full"] = full_result_text(payload.get("tool_response"))
                    append_event(cwd, event)
                    maybe_append_order_or_note_message(cwd, agent_type, agent_id, command)
                # Non-engine Bash calls: nothing to log.
            elif tool_name in ("Edit", "Write"):
                file_path = resolve_file_path(cwd, tool_input.get("file_path"))
                if file_path and (
                    file_path.startswith("game/")
                    or file_path.startswith(".claude/agent-memory/")
                ):
                    event = base_event(cwd, "tool", agent_type, agent_id)
                    basename = file_path.rsplit("/", 1)[-1]
                    event["tool"] = tool_name
                    event["file"] = file_path
                    event["result"] = (
                        "edited " if tool_name == "Edit" else "wrote "
                    ) + basename
                    event["result_full"] = edit_write_content(tool_input)
                    append_event(cwd, event)
                # Files outside game/ or .claude/agent-memory/: nothing to log.
            elif tool_name == "AskUserQuestion":
                event = base_event(cwd, "decision", agent_type, agent_id)
                event["from"] = "human"
                event["to"] = "mission-control"
                event["text"] = question_text(tool_input)
                event["message"] = answer_message(payload.get("tool_response"))
                event["tool"] = "AskUserQuestion"
                append_event(cwd, event)
    except Exception:
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
