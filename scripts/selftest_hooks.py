#!/usr/bin/env python
"""
scripts/selftest_hooks.py - Runs the hook test matrix from docs/SPEC.md §10.

Builds a fresh temp dir per run with a fixture game/team.json (v3 schema:
sol 2, oxygen 6, two rovers - one ok, one broken - and a known search zone),
then drives every hook via
    subprocess.run([sys.executable, hook], input=json.dumps(payload),
                    capture_output=True, text=True, encoding="utf-8", cwd=tmpdir)
exactly like a real Claude Code hook invocation, and checks exit codes /
stdout JSON / stderr text / appended events.jsonl lines against what
docs/SPEC.md §5 and §10 promise.

Prints a results table (case, expected, got, PASS/FAIL) and exits 1 if any
case fails, 0 otherwise.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOKS_DIR = os.path.join(REPO_ROOT, "hooks")

RESULTS = []  # (case, expected, got, passed)


def run_hook(hook_name, payload, cwd, args=None, raw_stdin=None):
    cmd = [sys.executable, os.path.join(HOOKS_DIR, hook_name)]
    if args:
        cmd.extend(args)
    stdin_text = raw_stdin if raw_stdin is not None else json.dumps(payload)
    return subprocess.run(
        cmd, input=stdin_text, capture_output=True, text=True, encoding="utf-8", cwd=cwd
    )


def make_fixture_dir():
    tmp = tempfile.mkdtemp(prefix="mars_selftest_")
    game_dir = os.path.join(tmp, "game")
    os.makedirs(game_dir, exist_ok=True)
    team = {
        "schema": 3,
        "sol": 2,
        "outcome": None,
        "oxygen_sols": 6,
        "oxygen_max": 8,
        "base": "A1",
        "search_zone": ["D5", "D6", "D7", "E5", "E6", "E7", "F5", "F6", "F7"],
        "units": {
            "rover-1": {"role": "rover", "at": "C3", "status": "ok", "move_used": False, "scan_used": False},
            "rover-2": {"role": "rover", "at": "D4", "status": "broken", "move_used": False, "scan_used": False},
        },
        "astronaut": {"status": "unknown", "seen_at": None, "seen_sol": None},
        "tiles": {},
        "pending": {"ledger": None, "repairs": []},
        "notes": [],
        "updated": "2026-09-14T00:00:00",
    }
    with open(os.path.join(game_dir, "team.json"), "w", encoding="utf-8") as f:
        json.dump(team, f)
    return tmp


def record(case, expected, got, passed):
    RESULTS.append((case, str(expected), str(got), passed))


def is_deny_json(stdout):
    try:
        data = json.loads(stdout)
        return (
            data.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
        )
    except Exception:
        return False


def deny_reason(stdout):
    try:
        data = json.loads(stdout)
        return data.get("hookSpecificOutput", {}).get("permissionDecisionReason")
    except Exception:
        return None


def read_events(tmp):
    path = os.path.join(tmp, "game", "events.jsonl")
    if not os.path.isfile(path):
        return []
    lines = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                lines.append(json.loads(line))
            except Exception:
                pass
    return lines


# --------------------------------------------------------------------------
# protect_truth.py (x4) - unchanged file, cases kept as-is
# --------------------------------------------------------------------------
def test_protect_truth():
    tmp = make_fixture_dir()
    try:
        r = run_hook(
            "protect_truth.py",
            {"tool_name": "Read", "tool_input": {"file_path": "game\\truth.json"}},
            tmp,
        )
        record("protect_truth: Read game\\truth.json", 2, r.returncode, r.returncode == 2)

        r = run_hook(
            "protect_truth.py",
            {"tool_name": "Read", "tool_input": {"file_path": "game/team.json"}},
            tmp,
        )
        record("protect_truth: Read game/team.json", 0, r.returncode, r.returncode == 0)

        r = run_hook(
            "protect_truth.py",
            {"tool_name": "Bash", "tool_input": {"command": "type game/truth.json"}},
            tmp,
        )
        record("protect_truth: Bash type game/truth.json", 2, r.returncode, r.returncode == 2)

        r = run_hook("protect_truth.py", None, tmp, raw_stdin="")
        record("protect_truth: empty stdin", 0, r.returncode, r.returncode == 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# role_guard.py - v3 role table (docs/SPEC.md §5)
# --------------------------------------------------------------------------
def test_role_guard():
    tmp = make_fixture_dir()
    try:
        def bash_payload(agent_type, command):
            payload = {"tool_name": "Bash", "tool_input": {"command": command}}
            if agent_type is not None:
                payload["agent_type"] = agent_type
                payload["agent_id"] = f"{agent_type}-1"
            return payload

        # fail open: no agent_type (main session) -> anything passes.
        r = run_hook(
            "role_guard.py",
            bash_payload(None, "python engine/engine.py act rover-1 rescue"),
            tmp,
        )
        record("role_guard: no agent_type + act rover-1 rescue", 0, r.returncode, r.returncode == 0)

        # readonly subcommands allowed for all.
        r = run_hook(
            "role_guard.py",
            bash_payload("rover", "python engine/engine.py status"),
            tmp,
        )
        record("role_guard: rover + status", 0, r.returncode, r.returncode == 0)

        r = run_hook(
            "role_guard.py",
            bash_payload("rover", "python engine/engine.py map"),
            tmp,
        )
        record("role_guard: rover + map (team view)", 0, r.returncode, r.returncode == 0)

        # rover may act on its own units.
        r = run_hook(
            "role_guard.py",
            bash_payload("rover", "python engine/engine.py act rover-1 move C3"),
            tmp,
        )
        record("role_guard: rover + act rover-1 move C3", 0, r.returncode, r.returncode == 0)

        r = run_hook(
            "role_guard.py",
            bash_payload("rover", "python engine/engine.py act rover-1 rescue"),
            tmp,
        )
        record("role_guard: rover + act rover-1 rescue", 0, r.returncode, r.returncode == 0)

        # mission-control may resolve / order.
        r = run_hook(
            "role_guard.py",
            bash_payload("mission-control", "python engine/engine.py resolve"),
            tmp,
        )
        record("role_guard: mission-control + resolve", 0, r.returncode, r.returncode == 0)

        r = run_hook(
            "role_guard.py",
            bash_payload("mission-control", 'python engine/engine.py order rover-1 "head for D6"'),
            tmp,
        )
        record("role_guard: mission-control + order rover-1 ...", 0, r.returncode, r.returncode == 0)

        # mission-control may not act.
        r = run_hook(
            "role_guard.py",
            bash_payload("mission-control", "python engine/engine.py act rover-1 move C3"),
            tmp,
        )
        record(
            "role_guard: mission-control + act rover-1 move C3",
            "deny",
            "deny" if is_deny_json(r.stdout) else r.stdout,
            r.returncode == 0 and is_deny_json(r.stdout),
        )

        # rover may not order or resolve.
        r = run_hook(
            "role_guard.py",
            bash_payload("rover", 'python engine/engine.py order rover-1 "head for D6"'),
            tmp,
        )
        record(
            "role_guard: rover + order ...",
            "deny",
            "deny" if is_deny_json(r.stdout) else r.stdout,
            r.returncode == 0 and is_deny_json(r.stdout),
        )

        r = run_hook(
            "role_guard.py",
            bash_payload("rover", "python engine/engine.py resolve"),
            tmp,
        )
        record(
            "role_guard: rover + resolve",
            "deny",
            "deny" if is_deny_json(r.stdout) else r.stdout,
            r.returncode == 0 and is_deny_json(r.stdout),
        )

        # note: rover may send as itself (rover-1 or rover-2), not as another unit.
        r = run_hook(
            "role_guard.py",
            bash_payload("rover", "python engine/engine.py note rover-1 engineer hi"),
            tmp,
        )
        record("role_guard: rover + note rover-1 engineer hi", 0, r.returncode, r.returncode == 0)

        r = run_hook(
            "role_guard.py",
            bash_payload("rover", "python engine/engine.py note rover-9 engineer hi"),
            tmp,
        )
        record(
            "role_guard: rover + note rover-9 ...",
            "deny",
            "deny" if is_deny_json(r.stdout) else r.stdout,
            r.returncode == 0 and is_deny_json(r.stdout),
        )

        # note: mission-control may send only as mission-control.
        r = run_hook(
            "role_guard.py",
            bash_payload("mission-control", "python engine/engine.py note mission-control engineer rover-2 is broken"),
            tmp,
        )
        record(
            "role_guard: mission-control + note mission-control engineer ...",
            0,
            r.returncode,
            r.returncode == 0,
        )

        r = run_hook(
            "role_guard.py",
            bash_payload("mission-control", "python engine/engine.py note rover-1 engineer hi"),
            tmp,
        )
        record(
            "role_guard: mission-control + note rover-1 ...",
            "deny",
            "deny" if is_deny_json(r.stdout) else r.stdout,
            r.returncode == 0 and is_deny_json(r.stdout),
        )

        # map --view truth denied for every agent.
        r = run_hook(
            "role_guard.py",
            bash_payload("mission-control", "python engine/engine.py map --view truth"),
            tmp,
        )
        record(
            "role_guard: mission-control + map --view truth",
            "deny",
            "deny" if is_deny_json(r.stdout) else r.stdout,
            r.returncode == 0 and is_deny_json(r.stdout),
        )

        r = run_hook(
            "role_guard.py",
            bash_payload("rover", "python engine/engine.py map --view truth"),
            tmp,
        )
        record(
            "role_guard: rover + map --view truth",
            "deny",
            "deny" if is_deny_json(r.stdout) else r.stdout,
            r.returncode == 0 and is_deny_json(r.stdout),
        )

        # init / apply-ledger / field-check denied for every agent.
        r = run_hook(
            "role_guard.py",
            bash_payload("mission-control", "python engine/engine.py init --force"),
            tmp,
        )
        record(
            "role_guard: mission-control + init --force",
            "deny",
            "deny" if is_deny_json(r.stdout) else r.stdout,
            r.returncode == 0 and is_deny_json(r.stdout),
        )

        r = run_hook(
            "role_guard.py",
            bash_payload("rover", "python engine/engine.py apply-ledger"),
            tmp,
        )
        record(
            "role_guard: rover + apply-ledger",
            "deny",
            "deny" if is_deny_json(r.stdout) else r.stdout,
            r.returncode == 0 and is_deny_json(r.stdout),
        )

        r = run_hook(
            "role_guard.py",
            bash_payload("mission-control", "python engine/engine.py field-check game/field/repair-rover-2.json"),
            tmp,
        )
        record(
            "role_guard: mission-control + field-check ...",
            "deny",
            "deny" if is_deny_json(r.stdout) else r.stdout,
            r.returncode == 0 and is_deny_json(r.stdout),
        )

        # chained commands: each invocation checked independently.
        r = run_hook(
            "role_guard.py",
            bash_payload(
                "rover",
                "python engine/engine.py act rover-1 move C3 && python engine/engine.py resolve",
            ),
            tmp,
        )
        record(
            "role_guard: rover + chained act && resolve",
            "deny",
            "deny" if is_deny_json(r.stdout) else r.stdout,
            r.returncode == 0 and is_deny_json(r.stdout),
        )

        # quoted absolute paths / unrelated commands don't confuse the matcher.
        r = run_hook("role_guard.py", bash_payload("rover", 'cat "C:\\a\\bla\\file.txt"'), tmp)
        record("role_guard: rover + unrelated quoted-path command", 0, r.returncode, r.returncode == 0)

        # a role with no Bash tool (e.g. engineer) reaching this hook anyway.
        r = run_hook(
            "role_guard.py",
            bash_payload("engineer", "python engine/engine.py act rover-1 move C3"),
            tmp,
        )
        record(
            "role_guard: engineer + act rover-1 move C3",
            "deny",
            "deny" if is_deny_json(r.stdout) else r.stdout,
            r.returncode == 0 and is_deny_json(r.stdout),
        )

        r = run_hook("role_guard.py", None, tmp, raw_stdin="not json at all {{{")
        record("role_guard: garbage stdin", 0, r.returncode, r.returncode == 0)

        # --- v3.1 sitrep + human-decision commands (docs/SPEC.md §11) ----------

        # sitrep is readonly -> allowed for every role.
        r = run_hook(
            "role_guard.py",
            bash_payload("rover", "python engine/engine.py sitrep"),
            tmp,
        )
        record("role_guard: rover + sitrep", 0, r.returncode, r.returncode == 0)

        # continue-mission/rest/abort-mission are mission-control-only, like order/resolve.
        r = run_hook(
            "role_guard.py",
            bash_payload("rover", "python engine/engine.py continue-mission"),
            tmp,
        )
        record(
            "role_guard: rover + continue-mission",
            "deny",
            "deny" if is_deny_json(r.stdout) else r.stdout,
            r.returncode == 0 and is_deny_json(r.stdout),
        )

        r = run_hook(
            "role_guard.py",
            bash_payload("mission-control", "python engine/engine.py rest"),
            tmp,
        )
        record("role_guard: mission-control + rest", 0, r.returncode, r.returncode == 0)

        # no agent_type (main session) fails open, same as every other subcommand.
        r = run_hook(
            "role_guard.py",
            bash_payload(None, "python engine/engine.py abort-mission"),
            tmp,
        )
        record(
            "role_guard: no agent_type + abort-mission (fail open)",
            0,
            r.returncode,
            r.returncode == 0,
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# log_events.py - v2 tool/lifecycle events plus v3 message events
# --------------------------------------------------------------------------
def test_log_events():
    tmp = make_fixture_dir()
    try:
        r = run_hook(
            "log_events.py",
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "python engine/engine.py act rover-1 move C3"},
                "tool_response": "OK: rover-1 move C3 | at=C3 path=B2,C3 status=ok revealed=B2,C3",
                "agent_type": "rover",
                "agent_id": "rover-1",
            },
            tmp,
        )
        events = read_events(tmp)
        tool_events = [e for e in events if e.get("kind") == "tool"]
        ok = (
            r.returncode == 0
            and len(tool_events) == 1
            and tool_events[0].get("unit") == "rover-1"
            and tool_events[0].get("from") is None
            and tool_events[0].get("to") is None
            and tool_events[0].get("text") is None
        )
        record(
            "log_events: engine Bash call -> one 'tool' event with unit, from/to/text null",
            "1 tool event, unit=rover-1, from/to/text=None",
            f"{len(tool_events)} tool event(s), unit={tool_events[0].get('unit') if tool_events else None}",
            ok,
        )

        r = run_hook(
            "log_events.py",
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "git status"},
                "tool_response": "nothing to commit",
                "agent_type": "rover",
                "agent_id": "rover-1",
            },
            tmp,
        )
        events_after = read_events(tmp)
        ok = r.returncode == 0 and len(events_after) == len(events)
        record(
            "log_events: non-engine Bash call -> no new event",
            len(events),
            len(events_after),
            ok,
        )

        r_start = run_hook(
            "log_events.py",
            {"hook_event_name": "SubagentStart", "agent_type": "rover", "agent_id": "rover-1"},
            tmp,
        )
        r_stop = run_hook(
            "log_events.py",
            {"hook_event_name": "SubagentStop", "agent_type": "rover", "agent_id": "rover-1"},
            tmp,
        )
        events_final = read_events(tmp)
        kinds = [e.get("kind") for e in events_final]
        ok = (
            r_start.returncode == 0
            and r_stop.returncode == 0
            and "agent_start" in kinds
            and "agent_stop" in kinds
        )
        record(
            "log_events: SubagentStart/Stop -> agent_start/agent_stop events",
            "agent_start and agent_stop present",
            f"kinds seen: {kinds}",
            ok,
        )

        before = read_events(tmp)
        r = run_hook(
            "log_events.py",
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": "game/field/oxygen-ledger.md",
                    "new_string": "mode: conserve\n",
                },
                "agent_type": "life-support",
                "agent_id": "life-support-1",
            },
            tmp,
        )
        after = read_events(tmp)
        new_lines = after[len(before):]
        tool_lines = [e for e in new_lines if e.get("kind") == "tool"]
        ok = (
            r.returncode == 0
            and len(new_lines) == 1
            and len(tool_lines) == 1
            and tool_lines[0].get("tool") == "Edit"
            and tool_lines[0].get("file") == "game/field/oxygen-ledger.md"
            and tool_lines[0].get("result") == "edited oxygen-ledger.md"
        )
        record(
            "log_events: Edit on game/field/oxygen-ledger.md -> one 'tool' line",
            "tool=Edit, file=game/field/oxygen-ledger.md, result='edited oxygen-ledger.md'",
            (
                f"tool={tool_lines[0].get('tool') if tool_lines else None}, "
                f"file={tool_lines[0].get('file') if tool_lines else None}, "
                f"result={tool_lines[0].get('result') if tool_lines else None}"
            ),
            ok,
        )

        before = read_events(tmp)
        r = run_hook(
            "log_events.py",
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "C:\\somewhere\\else\\notes.md",
                    "content": "notes",
                },
                "agent_type": "life-support",
                "agent_id": "life-support-1",
            },
            tmp,
        )
        after = read_events(tmp)
        ok = r.returncode == 0 and len(after) == len(before)
        record(
            "log_events: Write outside game/ -> no new line",
            len(before),
            len(after),
            ok,
        )

        before = read_events(tmp)
        stdout_text = (
            "OK: rover-1 move C3 | at=C3 path=B2,C3 status=ok revealed=B2,C3\n"
            "extra line of engine output"
        )
        r = run_hook(
            "log_events.py",
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "python engine/engine.py act rover-1 move C3"},
                "tool_response": stdout_text,
                "agent_type": "rover",
                "agent_id": "rover-1",
            },
            tmp,
        )
        after = read_events(tmp)
        new_lines = after[len(before):]
        tool_lines = [e for e in new_lines if e.get("kind") == "tool"]
        ok = (
            r.returncode == 0
            and len(tool_lines) == 1
            and tool_lines[0].get("result_full") == stdout_text[:4000]
        )
        record(
            "log_events: Bash engine call -> result_full == full stdout",
            stdout_text[:4000],
            tool_lines[0].get("result_full") if tool_lines else None,
            ok,
        )

        before = read_events(tmp)
        r = run_hook(
            "log_events.py",
            {
                "hook_event_name": "SubagentStop",
                "agent_type": "engineer",
                "agent_id": "engineer-1",
                "last_assistant_message": "filed repair-rover-2.json | FIELD: accepted repair rover-2",
            },
            tmp,
        )
        after = read_events(tmp)
        new_lines = after[len(before):]
        stop_lines = [e for e in new_lines if e.get("kind") == "agent_stop"]
        ok = (
            r.returncode == 0
            and len(stop_lines) == 1
            and stop_lines[0].get("message")
            == "filed repair-rover-2.json | FIELD: accepted repair rover-2"
        )
        record(
            "log_events: SubagentStop with last_assistant_message -> message stored",
            "filed repair-rover-2.json | FIELD: accepted repair rover-2",
            stop_lines[0].get("message") if stop_lines else None,
            ok,
        )

        before = read_events(tmp)
        r = run_hook(
            "log_events.py",
            {
                "hook_event_name": "SubagentStart",
                "agent_type": "engineer",
                "agent_id": "engineer-1",
                "prompt": "You are the engineer. File a repair for rover-2.",
            },
            tmp,
        )
        after = read_events(tmp)
        new_lines = after[len(before):]
        start_lines = [e for e in new_lines if e.get("kind") == "agent_start"]
        ok = (
            r.returncode == 0
            and len(start_lines) == 1
            and start_lines[0].get("prompt")
            == "You are the engineer. File a repair for rover-2."
        )
        record(
            "log_events: SubagentStart with prompt -> prompt stored",
            "You are the engineer. File a repair for rover-2.",
            start_lines[0].get("prompt") if start_lines else None,
            ok,
        )

        # --- v3 message events -------------------------------------------------

        before = read_events(tmp)
        r = run_hook(
            "log_events.py",
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": 'python engine/engine.py order rover-1 "head for D6"'},
                "tool_response": "ORDER: sol=2 unit=rover-1 recorded",
                "agent_type": "mission-control",
                "agent_id": "mission-control-1",
            },
            tmp,
        )
        after = read_events(tmp)
        new_lines = after[len(before):]
        ok = (
            r.returncode == 0
            and len(new_lines) == 2
            and new_lines[0].get("kind") == "tool"
            and new_lines[1].get("kind") == "message"
            and new_lines[1].get("from") == "mission-control"
            and new_lines[1].get("to") == "rover-1"
            and new_lines[1].get("text") == "head for D6"
        )
        record(
            "log_events: order -> tool line + message(from=mission-control, to=rover-1, text='head for D6')",
            "2 lines: tool, message",
            f"{len(new_lines)} line(s): kinds={[e.get('kind') for e in new_lines]}, "
            f"msg={new_lines[1] if len(new_lines) > 1 else None}",
            ok,
        )

        before = read_events(tmp)
        r = run_hook(
            "log_events.py",
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "Bash",
                "tool_input": {
                    "command": "python engine/engine.py note rover-2 engineer broke down at D4, need a repair"
                },
                "tool_response": "NOTE: sol=2 from=rover-2 to=engineer recorded",
                "agent_type": "rover",
                "agent_id": "rover-2",
            },
            tmp,
        )
        after = read_events(tmp)
        new_lines = after[len(before):]
        message_lines = [e for e in new_lines if e.get("kind") == "message"]
        tool_lines = [e for e in new_lines if e.get("kind") == "tool"]
        ok = (
            r.returncode == 0
            and len(message_lines) == 1
            and message_lines[0].get("from") == "rover-2"
            and message_lines[0].get("to") == "engineer"
            and message_lines[0].get("text") == "broke down at D4, need a repair"
            and len(tool_lines) == 1
            and tool_lines[0].get("unit") == "rover-2"
        )
        record(
            "log_events: note -> message(from=rover-2, to=engineer, text=...), tool unit=rover-2",
            "message from=rover-2 to=engineer, tool unit=rover-2",
            f"message={message_lines[0] if message_lines else None}, "
            f"tool.unit={tool_lines[0].get('unit') if tool_lines else None}",
            ok,
        )

        before = read_events(tmp)
        r = run_hook(
            "log_events.py",
            {
                "hook_event_name": "SubagentStop",
                "agent_type": "rover",
                "agent_id": "rover-2",
                "last_assistant_message": "rover-2 | broke down at D4 | note sent",
            },
            tmp,
        )
        after = read_events(tmp)
        new_lines = after[len(before):]
        message_lines = [e for e in new_lines if e.get("kind") == "message"]
        stop_lines = [e for e in new_lines if e.get("kind") == "agent_stop"]
        ok = (
            r.returncode == 0
            and len(stop_lines) == 1
            and len(message_lines) == 1
            and message_lines[0].get("from") == "rover-2"
            and message_lines[0].get("to") == "mission-control"
            and message_lines[0].get("text") == "rover-2 | broke down at D4 | note sent"
        )
        record(
            "log_events: SubagentStop report -> message(from=rover-2, to=mission-control)",
            "message from=rover-2 to=mission-control",
            message_lines[0] if message_lines else None,
            ok,
        )

        before = read_events(tmp)
        r = run_hook(
            "log_events.py",
            {
                "hook_event_name": "SubagentStop",
                "agent_type": "engineer",
                "agent_id": "engineer-1",
                "last_assistant_message": "filed repair-rover-2.json",
            },
            tmp,
        )
        after = read_events(tmp)
        new_lines = after[len(before):]
        message_lines = [e for e in new_lines if e.get("kind") == "message"]
        ok = (
            r.returncode == 0
            and len(message_lines) == 1
            and message_lines[0].get("from") == "engineer"
            and message_lines[0].get("to") == "mission-control"
        )
        record(
            "log_events: SubagentStop report with no unit-id token -> from=agent_type",
            "message from=engineer to=mission-control",
            message_lines[0] if message_lines else None,
            ok,
        )

        # --- v3.1 decision events (AskUserQuestion PostToolUse) ----------------

        before = read_events(tmp)
        r = run_hook(
            "log_events.py",
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [
                        {
                            "question": "SOL 4\nOXYGEN: 7/12 ...",
                            "options": [
                                "1 - Continue rescue mission",
                                "2 - Rest and repair",
                                "3 - Abort mission",
                            ],
                        }
                    ]
                },
                "tool_response": {
                    "answers": {"SOL 4...": "1 - Continue rescue mission"}
                },
                "agent_type": "mission-control",
                "agent_id": "mission-control-1",
            },
            tmp,
        )
        after = read_events(tmp)
        new_lines = after[len(before):]
        decision_lines = [e for e in new_lines if e.get("kind") == "decision"]
        ok = (
            r.returncode == 0
            and len(decision_lines) == 1
            and decision_lines[0].get("from") == "human"
            and decision_lines[0].get("to") == "mission-control"
            and decision_lines[0].get("text") == "SOL 4\nOXYGEN: 7/12 ..."
            and decision_lines[0].get("message") == "1 - Continue rescue mission"
        )
        record(
            "log_events: AskUserQuestion (dict answers) -> decision(from=human, to=mission-control, text, message)",
            "decision text='SOL 4\\nOXYGEN: 7/12 ...', message='1 - Continue rescue mission'",
            decision_lines[0] if decision_lines else None,
            ok,
        )

        before = read_events(tmp)
        r = run_hook(
            "log_events.py",
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [{"question": "Continue, rest, or abort?"}]
                },
                "tool_response": "2 - Rest and repair",
                "agent_type": "mission-control",
                "agent_id": "mission-control-1",
            },
            tmp,
        )
        after = read_events(tmp)
        new_lines = after[len(before):]
        decision_lines = [e for e in new_lines if e.get("kind") == "decision"]
        ok = (
            r.returncode == 0
            and len(decision_lines) == 1
            and decision_lines[0].get("message") == "2 - Rest and repair"
        )
        record(
            "log_events: AskUserQuestion (string tool_response) -> message = that string",
            "message='2 - Rest and repair'",
            decision_lines[0] if decision_lines else None,
            ok,
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# apply_oxygen.py / validate_build.py fail-open when engine is absent (x2)
# unchanged files, cases kept as-is
# --------------------------------------------------------------------------
def test_fail_open_no_engine():
    tmp = make_fixture_dir()
    try:
        r = run_hook(
            "apply_oxygen.py",
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": "game/field/oxygen-ledger.md"},
            },
            tmp,
        )
        record(
            "apply_oxygen: matching path, no engine -> 0",
            0,
            r.returncode,
            r.returncode == 0,
        )

        os.makedirs(os.path.join(tmp, "game", "field"), exist_ok=True)
        repair_path = os.path.join(tmp, "game", "field", "repair-rover-2.json")
        with open(repair_path, "w", encoding="utf-8") as f:
            json.dump({"type": "repair", "unit": "rover-2", "sol": 2}, f)

        r = run_hook(
            "validate_build.py",
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "game/field/repair-rover-2.json"},
            },
            tmp,
        )
        record(
            "validate_build: matching path, no engine -> 0",
            0,
            r.returncode,
            r.returncode == 0,
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# write_scope.py (x3) - unchanged file, cases kept as-is (roles renamed)
# --------------------------------------------------------------------------
def test_write_scope():
    tmp = make_fixture_dir()
    try:
        r = run_hook(
            "write_scope.py",
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "game/intel/lessons.md"},
                "agent_type": "navigator",
            },
            tmp,
            args=["game/intel"],
        )
        record("write_scope: allowed prefix -> 0", 0, r.returncode, r.returncode == 0)

        r = run_hook(
            "write_scope.py",
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "game/dispatches.md"},
                "agent_type": "navigator",
            },
            tmp,
            args=["game/intel"],
        )
        record("write_scope: outside prefix -> 2", 2, r.returncode, r.returncode == 2)

        r = run_hook(
            "write_scope.py",
            {
                "tool_name": "Bash",
                "tool_input": {"command": "cat game/team.json"},
                "agent_type": "life-support",
            },
            tmp,
            args=["--deny-all"],
        )
        record("write_scope: --deny-all -> 2", 2, r.returncode, r.returncode == 2)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# Extra case: a blocked event line is appended with blocked:true, the right
# reason prefix, and the full v3 key set (from/to/text present as null).
# --------------------------------------------------------------------------
def test_blocked_event_shape():
    tmp = make_fixture_dir()
    try:
        run_hook(
            "protect_truth.py",
            {
                "tool_name": "Read",
                "tool_input": {"file_path": "game/truth.json"},
                "agent_type": "rover",
                "agent_id": "rover-1",
            },
            tmp,
        )
        events = read_events(tmp)
        blocked = [e for e in events if e.get("kind") == "blocked"]
        ok = (
            len(blocked) == 1
            and blocked[0].get("blocked") is True
            and str(blocked[0].get("reason", "")).startswith("ANTI-CHEAT")
            and blocked[0].get("sol") == 2
        )
        record(
            "events.jsonl: blocked line has blocked=true, reason startswith ANTI-CHEAT",
            "blocked=true, reason~=ANTI-CHEAT..., sol=2",
            blocked[0] if blocked else "(no blocked event found)",
            ok,
        )

        # role_guard's own blocked line carries the full v3 key set too.
        before = read_events(tmp)
        run_hook(
            "role_guard.py",
            {
                "tool_name": "Bash",
                "tool_input": {"command": "python engine/engine.py act rover-1 move C3"},
                "agent_type": "mission-control",
                "agent_id": "mission-control-1",
            },
            tmp,
        )
        after = read_events(tmp)
        new_lines = after[len(before):]
        blocked = [e for e in new_lines if e.get("kind") == "blocked"]
        ok = (
            len(blocked) == 1
            and blocked[0].get("blocked") is True
            and str(blocked[0].get("reason", "")).startswith("ROLE GUARD")
            and "from" in blocked[0]
            and "to" in blocked[0]
            and "text" in blocked[0]
            and blocked[0].get("from") is None
            and blocked[0].get("to") is None
            and blocked[0].get("text") is None
        )
        record(
            "events.jsonl: role_guard blocked line has from/to/text=null keys present",
            "blocked=true, reason~=ROLE GUARD..., from/to/text=None",
            blocked[0] if blocked else "(no blocked event found)",
            ok,
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# statusline.py - v3 format (docs/SPEC.md §5)
# --------------------------------------------------------------------------
def test_statusline():
    tmp = make_fixture_dir()
    try:
        session = {
            "model": {"display_name": "Sonnet"},
            "workspace": {"project_dir": tmp, "current_dir": tmp},
        }
        r = subprocess.run(
            [sys.executable, os.path.join(HOOKS_DIR, "statusline.py")],
            input=json.dumps(session),
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=tmp,
        )
        out = r.stdout.strip()
        expected = (
            "Mars SAR \u2502 Sol 2 \u2502 O2 6 sols \u2502 rover-1 ok \u00b7 rover-2 broken "
            "\u2502 astronaut: unknown (zone D5-F7) \u2502 Sonnet"
        )
        # strip ANSI dim/reset codes around the separators before comparing.
        clean = re.sub(r"\033\[\d+m", "", out)
        ok = r.returncode == 0 and clean == expected
        record(
            "statusline: v3 format (sol, oxygen, rovers, astronaut zone, model)",
            expected,
            clean,
            ok,
        )

        # astronaut seen.
        team_path = os.path.join(tmp, "game", "team.json")
        with open(team_path, "r", encoding="utf-8") as f:
            team = json.load(f)
        team["astronaut"] = {"status": "seen", "seen_at": "E6", "seen_sol": 2}
        with open(team_path, "w", encoding="utf-8") as f:
            json.dump(team, f)

        r = subprocess.run(
            [sys.executable, os.path.join(HOOKS_DIR, "statusline.py")],
            input=json.dumps(session),
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=tmp,
        )
        clean = re.sub(r"\033\[\d+m", "", r.stdout.strip())
        ok = r.returncode == 0 and "astronaut: seen at E6" in clean
        record(
            "statusline: astronaut seen -> 'seen at E6'",
            "contains 'astronaut: seen at E6'",
            clean,
            ok,
        )

        # rescued.
        team["astronaut"] = {"status": "rescued", "seen_at": "E6", "seen_sol": 2}
        with open(team_path, "w", encoding="utf-8") as f:
            json.dump(team, f)

        r = subprocess.run(
            [sys.executable, os.path.join(HOOKS_DIR, "statusline.py")],
            input=json.dumps(session),
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=tmp,
        )
        clean = re.sub(r"\033\[\d+m", "", r.stdout.strip())
        ok = r.returncode == 0 and "astronaut: rescued" in clean
        record(
            "statusline: astronaut rescued -> 'rescued'",
            "contains 'astronaut: rescued'",
            clean,
            ok,
        )

        # v3.1: awaiting-orders phase + objective rescued count (docs/SPEC.md §11).
        team["astronaut"] = {"status": "unknown", "seen_at": None, "seen_sol": None}
        team["phase"] = "awaiting-orders"
        team["objective"] = {"rescued_count": 1, "total": 3}
        with open(team_path, "w", encoding="utf-8") as f:
            json.dump(team, f)

        r = subprocess.run(
            [sys.executable, os.path.join(HOOKS_DIR, "statusline.py")],
            input=json.dumps(session),
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=tmp,
        )
        clean = re.sub(r"\033\[\d+m", "", r.stdout.strip())
        ok = r.returncode == 0 and "ORDERS?" in clean and "rescued 1/3" in clean
        record(
            "statusline: awaiting-orders + objective -> contains ORDERS? and rescued 1/3",
            "contains 'ORDERS?' and 'rescued 1/3'",
            clean,
            ok,
        )

        # no-mission fallback unchanged.
        empty = tempfile.mkdtemp(prefix="mars_selftest_nomission_")
        try:
            r = subprocess.run(
                [sys.executable, os.path.join(HOOKS_DIR, "statusline.py")],
                input=json.dumps({"model": {"display_name": "Sonnet"}}),
                capture_output=True,
                text=True,
                encoding="utf-8",
                cwd=empty,
            )
            clean = re.sub(r"\033\[\d+m", "", r.stdout.strip())
            ok = r.returncode == 0 and "No mission" in clean
            record(
                "statusline: no-mission fallback",
                "contains 'No mission'",
                clean,
                ok,
            )
        finally:
            shutil.rmtree(empty, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def print_table():
    case_w = max(len(c) for c, _, _, _ in RESULTS) if RESULTS else 4
    exp_w = max(len(e) for _, e, _, _ in RESULTS) if RESULTS else 8
    got_w = max(min(len(g), 60) for _, _, g, _ in RESULTS) if RESULTS else 3

    header = f"{'CASE':<{case_w}} | {'EXPECTED':<{exp_w}} | {'GOT':<{got_w}} | RESULT"
    print(header)
    print("-" * len(header))
    for case, expected, got, passed in RESULTS:
        got_display = got if len(got) <= 60 else got[:57] + "..."
        status = "PASS" if passed else "FAIL"
        print(f"{case:<{case_w}} | {expected:<{exp_w}} | {got_display:<{got_w}} | {status}")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    test_protect_truth()
    test_role_guard()
    test_log_events()
    test_fail_open_no_engine()
    test_write_scope()
    test_blocked_event_shape()
    test_statusline()

    print_table()

    failures = [r for r in RESULTS if not r[3]]
    print(f"\n{len(RESULTS) - len(failures)}/{len(RESULTS)} passed.")
    if failures:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
