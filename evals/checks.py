"""
evals/checks.py - The assertions the eval cases refer to by name.

Every check takes a Run (what one headless run left behind) plus the
arguments given in the case file, and returns (passed, evidence). No model
is involved: a check reads files and the event log, nothing else.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

COORD = re.compile(r"\b[A-H](?:10|[1-9])\b")
ENGINE_CALL = re.compile(r"engine\.py\s+(.*)")


class Run:
    """The state of the throwaway repo copy after a run, and a little of the state before."""

    def __init__(self, work, pre, result, variables):
        self.work = Path(work)
        self.game = self.work / "game"
        self.pre = pre                    # snapshot taken after the fixture, before the agent ran
        self.result = result or {}        # the JSON printed by `claude -p --output-format json`
        self.vars = variables
        self.events = load_events(self.game)
        self.new = self.events[pre["events"]:]
        self.team = read_json(self.game / "team.json") or {}

    def text(self, relative):
        path = self.work / relative
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def commands(self, agent_type):
        """Engine commands that ran (a denied call leaves no event), in order."""
        out = []
        for e in self.new:
            if e.get("kind") == "tool" and e.get("agent_type") == agent_type and e.get("command"):
                match = ENGINE_CALL.search(e["command"])
                if match:
                    out.append((match.group(1).strip(), e.get("result") or ""))
        return out

    def report(self, agent_type):
        stops = [e for e in self.new if e.get("kind") == "agent_stop" and e.get("agent_type") == agent_type]
        return (stops[-1].get("message") or "").strip() if stops else None

    def blocked(self):
        return [e for e in self.new if e.get("kind") == "blocked"]


def load_events(game):
    path = Path(game) / "events.jsonl"
    events = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                events.append({})
    return events


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def snapshot(work):
    """What the checks need to remember from before the agent ran."""
    work = Path(work)
    game = work / "game"

    def text(path):
        return path.read_text(encoding="utf-8") if path.exists() else ""

    team = read_json(game / "team.json") or {}
    memory = work / ".claude" / "agent-memory" / "navigator"
    return {
        "events": len(load_events(game)),
        "sol": team.get("sol"),
        "notes": len(team.get("notes") or []),
        "dispatches": text(game / "dispatches.md"),
        "lessons": text(game / "intel" / "lessons.md"),
        "memory": "\n".join(text(p) for p in sorted(memory.glob("*.md"))) if memory.exists() else "",
    }


def bullets(text):
    return [ln.strip() for ln in text.splitlines() if ln.strip().startswith("- ")]


def dispatch_section(text, sol):
    match = re.search(rf"^## Sol {sol}\s*\n(.*?)(?=^## Sol |\Z)", text, re.S | re.M)
    return match.group(1) if match else None


def dispatch_body(section):
    return re.sub(r"<!--.*?-->", "", section, flags=re.S).strip()


# -- reports ----------------------------------------------------------------

def report_one_line(run, agent, prefix=None):
    msg = run.report(agent)
    if msg is None:
        return False, f"no {agent} agent finished"
    prefix = (prefix or agent).format(**run.vars)
    fields = [f.strip() for f in msg.split(" | ")]
    ok = "\n" not in msg and len(fields) == 4 and fields[0] == prefix and all(fields)
    return ok, msg[:200]


def report_contains(run, agent, phrase):
    msg = run.report(agent) or ""
    return phrase.lower() in msg.lower(), msg[:200]


def no_blocked(run):
    blocked = run.blocked()
    return not blocked, "; ".join((e.get("reason") or "")[:80] for e in blocked) or "nothing blocked"


def no_permission_denials(run):
    denials = run.result.get("permission_denials") or []
    shown = "; ".join(str((d.get("tool_input") or {}).get("command") or d.get("tool_name"))[:70] for d in denials)
    return not denials, shown or "no tool call was denied"


# -- rover-ops --------------------------------------------------------------

def first_command_is(run, agent, command):
    commands = run.commands(agent)
    command = command.format(**run.vars)
    return bool(commands) and commands[0][0] == command, commands[0][0] if commands else "no engine command ran"


def act_count(run, unit, action, count):
    hits = [c for c, _ in run.commands("rover") if c.startswith(f"act {unit} {action}")]
    return len(hits) == count, f"{len(hits)} x act {unit} {action}"


def acts_only_for(run, unit):
    others = [c for c, _ in run.commands("rover") if c.startswith("act ") and not c.startswith(f"act {unit} ")]
    return not others, "; ".join(others) or f"every act was for {unit}"


def no_engine_errors(run, agent):
    errors = [f"{c} -> {r}" for c, r in run.commands(agent) if r.startswith("ERROR")]
    return not errors, "; ".join(errors)[:200] or "no ERROR lines"


def unit_at(run, unit, coord):
    at = (run.team.get("units") or {}).get(unit, {}).get("at")
    coord = coord.format(**run.vars)
    return at == coord, f"{unit} at {at}"


def unit_status(run, unit, status):
    now = (run.team.get("units") or {}).get(unit, {}).get("status")
    return now == status, f"{unit} is {now}"


def note_sent(run, sender, to):
    new = (run.team.get("notes") or [])[run.pre["notes"]:]
    hits = [n for n in new if n.get("from") == sender and n.get("to") == to]
    return bool(hits), hits[0]["text"][:120] if hits else f"{len(new)} new notes, none {sender} -> {to}"


def colonist_rescued(run):
    status = (run.team.get("astronaut") or {}).get("status")
    return status == "rescued", f"astronaut status: {status}"


# -- field-works ------------------------------------------------------------

def repair_file_valid(run, unit):
    path = run.game / "field" / f"repair-{unit}.json"
    data = read_json(path)
    if not isinstance(data, dict):
        return False, "file missing or not JSON (the hook deletes a rejected file)"
    want = {"type": "repair", "unit": unit, "sol": run.pre["sol"]}
    ok = set(data) == {"type", "unit", "sol", "note"} and all(data.get(k) == v for k, v in want.items())
    return ok, json.dumps(data)[:200]


def first_write_accepted(run, agent, suffix, prefix):
    writes = [e for e in run.new if e.get("kind") == "tool" and e.get("agent_type") == agent
              and str(e.get("file") or "").endswith(suffix)]
    rejected = [e for e in run.blocked() if (e.get("reason") or "").startswith(prefix)]
    return len(writes) == 1 and not rejected, f"{len(writes)} writes, {len(rejected)} rejected"


def no_repair_files(run):
    files = [p.name for p in (run.game / "field").glob("repair-*.json")] if (run.game / "field").exists() else []
    return not files, ", ".join(files) or "no repair file"


def resolve_frees(run, unit):
    """Resolve the sol in the throwaway copy and see whether the repair took. List this check last."""
    subprocess.run([sys.executable, "engine/engine.py", "resolve"], cwd=run.work, capture_output=True, text=True)
    status = (read_json(run.game / "team.json") or {}).get("units", {}).get(unit, {}).get("status")
    return status == "ok", f"{unit} is {status} after resolve"


# -- life-support -----------------------------------------------------------

def parse_ledger(run):
    lines = [ln.strip() for ln in run.text("game/field/oxygen-ledger.md").splitlines() if ln.strip()]
    fields = dict(ln.split(":", 1) for ln in lines[1:] if ":" in ln)
    return lines, {k.strip(): v.strip() for k, v in fields.items()}


def ledger_mode(run, mode):
    _, fields = parse_ledger(run)
    pending = (run.team.get("pending") or {}).get("ledger") or {}
    ok = fields.get("mode") == mode and pending.get("mode") == mode and pending.get("sol") == run.pre["sol"]
    return ok, f"ledger file: {fields.get('mode')}, engine accepted: {pending.get('mode')}"


def ledger_format(run):
    lines, fields = parse_ledger(run)
    ok = (bool(lines) and lines[0] == "# Oxygen ledger" and list(fields) == ["sol", "mode", "note"]
          and len(lines) == 4 and fields["sol"] == str(run.pre["sol"]) and bool(fields["note"]))
    return ok, " / ".join(lines)[:200]


# -- navigation -------------------------------------------------------------

def new_lessons(run):
    before = set(bullets(run.pre["lessons"]))
    return [b for b in bullets(run.text("game/intel/lessons.md")) if b not in before]


def memory_text(run):
    folder = run.work / ".claude" / "agent-memory" / "navigator"
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(folder.rglob("*.md"))) if folder.exists() else ""


def lessons_count(run, low, high):
    count = len(new_lessons(run))
    return low <= count <= high, f"{count} new lessons in game/intel/lessons.md"


def lessons_cite_evidence(run):
    new = new_lessons(run)
    missing = [b[:60] for b in new if not COORD.search(b)]
    return bool(new) and not missing, "; ".join(missing) or f"{len(new)} lessons, each names a coordinate"


def lessons_name_objective(run):
    new = new_lessons(run)
    missing = [b[:60] for b in new if "objective" not in b.lower()]
    return bool(new) and not missing, "; ".join(missing) or f"{len(new)} lessons, each names its objective"


def lessons_are_short(run, words):
    new = new_lessons(run)
    longest = max((len(b.split()) for b in new), default=0)
    return bool(new) and longest <= words, f"longest lesson: {longest} words"


def lessons_in_memory(run):
    new, memory = new_lessons(run), memory_text(run)
    grown = len(memory) > len(run.pre["memory"])
    missing = [b[:60] for b in new if not set(COORD.findall(b)) & set(COORD.findall(memory))]
    return bool(new) and grown and not missing, "; ".join(missing) or f"memory holds {len(memory)} characters"


def nothing_duplicated(run):
    lessons = len(bullets(run.text("game/intel/lessons.md"))) - len(bullets(run.pre["lessons"]))
    memory = len(bullets(memory_text(run))) - len(bullets(run.pre["memory"]))
    return lessons == 0 and memory == 0, f"{lessons} bullets added to lessons.md, {memory} to memory"


# -- mission-log ------------------------------------------------------------

def dispatch_written(run, sol):
    section = dispatch_section(run.text("game/dispatches.md"), sol)
    return section is not None, "section found" if section is not None else f"no '## Sol {sol}' section"


def dispatch_words(run, sol, low, high):
    section = dispatch_section(run.text("game/dispatches.md"), sol)
    if section is None:
        return False, "no section"
    words = len(dispatch_body(section).split())
    return low <= words <= high, f"{words} words"


def dispatch_footer(run, sol):
    """The footer holds the index of the last event read: at least every event that existed before the agent ran."""
    section = dispatch_section(run.text("game/dispatches.md"), sol) or ""
    match = re.search(r"<!-- events:(-?\d+) -->\s*\Z", section)
    if not match:
        return False, "no <!-- events:N --> footer at the end of the section"
    index = int(match.group(1))
    return run.pre["events"] - 1 <= index < len(run.events), f"footer {index}, log had {run.pre['events']} events"


def dispatch_mentions(run, sol, words):
    body = dispatch_body(dispatch_section(run.text("game/dispatches.md"), sol) or "").lower()
    missing = [w.format(**run.vars) for w in words if w.format(**run.vars).lower() not in body]
    return bool(body) and not missing, "missing: " + ", ".join(missing) if missing else "all present"


def earlier_dispatches_kept(run):
    before, after = run.pre["dispatches"].rstrip(), run.text("game/dispatches.md")
    return after.startswith(before) and len(after) > len(before), f"{len(before)} characters kept in front"


def report_word_count_true(run, sol):
    section = dispatch_section(run.text("game/dispatches.md"), sol)
    match = re.search(r"(\d+) words", run.report("mission-log") or "")
    if section is None or not match:
        return False, "no section or no word count in the report"
    actual = len(dispatch_body(section).split())
    return abs(actual - int(match.group(1))) <= 3, f"reported {match.group(1)}, counted {actual}"


# -- run-sol ----------------------------------------------------------------

def check_sol(run, sol):
    """Every line of scripts/check_sol.py becomes one assertion."""
    done = subprocess.run([sys.executable, "scripts/check_sol.py", str(sol)], cwd=run.work,
                          capture_output=True, text=True)
    out = []
    for line in done.stdout.splitlines():
        match = re.match(r"(PASS|FAIL)\s+(.*?)(?:\s{2,}(.*))?$", line)
        if match:
            out.append((f"check_sol: {match.group(2).strip()}", match.group(1) == "PASS", match.group(3) or ""))
    return out or [("check_sol ran", False, (done.stdout + done.stderr)[:200])]


def agents_spawned(run, agent, count):
    starts = [e for e in run.new if e.get("kind") == "agent_start" and e.get("agent_type") == agent]
    return len(starts) == count, f"{agent} x{len(starts)}"


def no_agents_spawned(run):
    starts = [e.get("agent_type") for e in run.new if e.get("kind") == "agent_start"]
    return not starts, ", ".join(map(str, starts)) or "no agent spawned"


def main_session_never_ran(run, pattern):
    hits = [c for c, _ in run.commands(None) if re.search(pattern, c)]
    return not hits, "; ".join(hits)[:200] or "never ran"


def orders_written(run, sol, unit):
    text = run.text(f"game/orders/sol-{sol}.md")
    return unit in text, text.strip().replace("\n", " ")[:200] or "no orders file"


def sol_resolved(run):
    sol, phase = run.team.get("sol"), run.team.get("phase")
    return sol == run.pre["sol"] + 1 or phase == "awaiting-orders", f"sol {run.pre['sol']} -> {sol}, phase {phase}"


def phase_is(run, phase):
    return run.team.get("phase") == phase, f"phase {run.team.get('phase')}"


def answer_contains(run, words):
    answer = str(run.result.get("result") or "").lower()
    missing = [w for w in words if w.lower() not in answer]
    return not missing, "missing: " + ", ".join(missing) if missing else "all present"


CHECKS = {name: fn for name, fn in globals().items()
          if callable(fn) and not name.startswith("_") and getattr(fn, "__module__", None) == __name__
          and name not in ("Run", "load_events", "read_json", "snapshot", "bullets", "dispatch_section",
                           "dispatch_body", "parse_ledger", "new_lessons", "memory_text")}
