"""Mars Search & Rescue - UI server (v3.1, the "stage" server).

Serves ui/index.html and a read-only JSON API. Two families of endpoints:

  MISSION state, from --root (default "game"): team.json, events.jsonl,
  snapshots/, truth.json, intel/, orders/, dispatches.md, field/, notes.md,
  and /api/sitrep (SPEC §11).

  HARNESS content, always from the project directory (the parent of ui/,
  resolved from this script's location, NOT from the cwd and NOT from
  --root): .claude/agents/*.md, .claude/skills/*/SKILL.md, hooks/*.py,
  .claude/settings.json, .claude/agent-memory/**, CLAUDE.md, ui/roles.json.

The split is deliberate: `--root ui/fixtures` swaps the *mission* for a
canned one while the harness tabs (Roles, Rules & hooks, Memory) keep
showing the real agent files, the real hooks and the real settings.

The server never writes anything - the engine (engine/engine.py) is the
only writer of game state. This process is a harness, not a model: it does
not call any LLM and it never mutates the files it reads. The one
subprocess it ever starts is the read-only `engine.py sitrep` behind
/api/sitrep, and only when --root is the project's own game/ directory. Every response
carries Cache-Control: no-store, and every handler degrades to an empty
payload rather than raising on a missing or malformed file.

Usage:
    python ui/server.py [--port 8765] [--root game]

--root is the directory that CONTAINS team.json (default "game", i.e. this
repo's real runtime state). For the offline fixture demo:
    python ui/server.py --root ui/fixtures --port 8765
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
INDEX_HTML = SCRIPT_DIR / "index.html"
ROLES_JSON = SCRIPT_DIR / "roles.json"

# The project directory is the parent of ui/ - resolved from the script
# location so the harness endpoints work no matter what cwd or --root is.
PROJECT_DIR = SCRIPT_DIR.parent

ROOT: Path = Path("game")  # overwritten by main() from --root

MAX_TEXT_BYTES = 400_000  # per-file guard so one huge file can't wedge a tab


def _read_json(path: Path):
    """Return (data, error). error is None on success."""
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f), None
    except FileNotFoundError:
        return None, "missing"
    except (json.JSONDecodeError, OSError) as exc:
        return None, str(exc)


def _read_text(path: Path):
    """Return the file's text, or None if it cannot be read.

    Tries UTF-8 first, then cp1252: some of the hook sources were written on
    Windows with a cp1252 section sign in their docstrings, and decoding those
    bytes as UTF-8 would put a replacement glyph on the Rules & hooks tab.
    """
    try:
        data = path.read_bytes()[:MAX_TEXT_BYTES]
    except (OSError, ValueError):
        return None
    for enc in ("utf-8", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def _rel(path: Path, base: Path) -> str:
    """Forward-slashed path relative to base (falls back to the name)."""
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except (ValueError, OSError):
        return path.name


def _read_events_since(path: Path, since: int):
    """Read events.jsonl starting at 0-based line index `since`.

    Malformed lines are skipped (but still counted for indexing purposes).
    Returns (events, next_index).
    """
    events = []
    next_index = since
    try:
        with path.open("r", encoding="utf-8") as f:
            for index, line in enumerate(f):
                if index < since:
                    continue
                line = line.strip()
                next_index = index + 1
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                obj["index"] = index
                events.append(obj)
    except FileNotFoundError:
        return [], since
    except OSError:
        return [], since
    return events, next_index


# ---------------------------------------------------------------------------
# frontmatter
# ---------------------------------------------------------------------------

def parse_frontmatter(text: str):
    """Split a markdown file into (frontmatter dict, body).

    Deliberately simple - not a YAML parser. Agent and skill files in this
    repo use one `key: value` per line at indent 0; a value that continues
    on indented lines (the `hooks:` flow mapping could, in principle) is
    appended verbatim, newlines included, so the caller gets the raw YAML
    text back for display.
    """
    fm = {}
    if not text:
        return fm, ""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return fm, text
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return fm, text
    key = None
    for raw in lines[1:end]:
        if not raw.strip():
            continue
        if raw[:1] in (" ", "\t") and key:
            fm[key] = fm[key] + "\n" + raw.rstrip()
            continue
        if ":" in raw:
            key, _, value = raw.partition(":")
            key = key.strip()
            fm[key] = value.strip()
        elif key:
            fm[key] = fm[key] + "\n" + raw.rstrip()
    body = "\n".join(lines[end + 1:]).lstrip("\n")
    return fm, body


def _split_tools(value):
    if not value:
        return []
    return [t.strip() for t in value.split(",") if t.strip()]


def _hook_summary(source: str) -> str:
    """One readable description for a hook: its docstring's title line, plus
    the start of its "Purpose (teaching material)" block when it has one.

    The hooks in this repo open with a one-line title ("role_guard.py - Per-role
    guardrail PreToolUse hook (matcher: Bash).") and then a Purpose block that
    is the actual teaching text. Showing only the first paragraph would leave
    the Rules & hooks tab with seven near-identical one-liners.
    """
    doc = _module_docstring(source)
    if not doc:
        return ""
    paragraphs = [p for p in doc.strip().split("\n\n") if p.strip()]
    if not paragraphs:
        return ""
    title = " ".join(paragraphs[0].split())
    rest = ""
    for para in paragraphs[1:]:
        flat = " ".join(para.split())
        # drop the "Purpose (teaching material):" label itself
        flat = flat.split(":", 1)[1].strip() if flat.lower().startswith("purpose") else flat
        if flat:
            rest = flat
            break
    out = title if not rest else title + " " + rest
    if len(out) > 460:
        out = out[:459].rsplit(" ", 1)[0] + "…"
    return out


def _module_docstring(source: str) -> str:
    """Module docstring without importing or executing the module."""
    if not source:
        return ""
    try:
        import ast

        mod = ast.parse(source)
        return ast.get_docstring(mod) or ""
    except (SyntaxError, ValueError):
        return ""


# ---------------------------------------------------------------------------
# harness collectors
# ---------------------------------------------------------------------------

def collect_agents():
    """[{name, description, tools, model, memory, maxTurns, hooks, body}]"""
    out = []
    agent_dir = PROJECT_DIR / ".claude" / "agents"
    if not agent_dir.is_dir():
        return out
    for path in sorted(agent_dir.glob("*.md")):
        text = _read_text(path)
        if text is None:
            continue
        fm, body = parse_frontmatter(text)
        out.append({
            "name": fm.get("name") or path.stem,
            "file": _rel(path, PROJECT_DIR),
            "description": fm.get("description", ""),
            "tools": _split_tools(fm.get("tools", "")),
            "model": fm.get("model", ""),
            "memory": fm.get("memory") or None,
            "maxTurns": fm.get("maxTurns") or None,
            "hooks": fm.get("hooks") or None,
            "body": body,
        })
    return out


def collect_skills():
    """[{name, description, body}] from .claude/skills/*/SKILL.md"""
    out = []
    skill_dir = PROJECT_DIR / ".claude" / "skills"
    if not skill_dir.is_dir():
        return out
    for path in sorted(skill_dir.glob("*/SKILL.md")):
        text = _read_text(path)
        if text is None:
            continue
        fm, body = parse_frontmatter(text)
        out.append({
            "name": fm.get("name") or path.parent.name,
            "file": _rel(path, PROJECT_DIR),
            "description": fm.get("description", ""),
            "body": body,
        })
    return out


def collect_hooks():
    """{settings: <.claude/settings.json>, files: {name: {source, summary}}}"""
    settings, err = _read_json(PROJECT_DIR / ".claude" / "settings.json")
    files = {}
    hook_dir = PROJECT_DIR / "hooks"
    if hook_dir.is_dir():
        for path in sorted(hook_dir.glob("*.py")):
            source = _read_text(path)
            if source is None:
                continue
            files[path.name] = {
                "source": source,
                "summary": _hook_summary(source),
            }
    return {
        "settings": settings if err is None else None,
        "settings_error": err,
        "files": files,
    }


def collect_memory():
    """{files:[{path, content}]} for everything under .claude/agent-memory/."""
    out = []
    mem_dir = PROJECT_DIR / ".claude" / "agent-memory"
    if not mem_dir.is_dir():
        return {"dir": ".claude/agent-memory", "files": out}
    for path in sorted(mem_dir.rglob("*")):
        if not path.is_file():
            continue
        content = _read_text(path)
        if content is None:
            continue
        out.append({"path": _rel(path, PROJECT_DIR), "content": content})
    return {"dir": ".claude/agent-memory", "files": out}


def collect_intel():
    """Mission files a human would read: intel, orders, dispatches, ledger.

    These come from --root (they are mission state, not harness content), so
    fixture mode shows the fixture's files if it has any.
    """
    out = []
    candidates = []
    for sub in ("intel", "orders"):
        d = ROOT / sub
        if d.is_dir():
            candidates.extend(sorted(d.glob("*.md")))
    for single in (ROOT / "dispatches.md", ROOT / "field" / "oxygen-ledger.md"):
        if single.is_file():
            candidates.append(single)
    for path in candidates:
        content = _read_text(path)
        if content is None:
            continue
        out.append({"path": str(path).replace("\\", "/"),
                    "name": path.name,
                    "content": content})
    return {"root": str(ROOT).replace("\\", "/"), "files": out}


# ---------------------------------------------------------------------------
# sitrep (SPEC §11): the ship's-computer block the human answers
# ---------------------------------------------------------------------------

SITREP_TTL = 1.0           # seconds; one subprocess per second at most
_sitrep_cache: dict = {"at": 0.0, "payload": None}

OPTION_LINES = [
    "1 - CONTINUE RESCUE MISSION",
    "2 - REST AND REPAIR",
    "3 - ABORT MISSION",
]


def _sitrep_is_block(text: str) -> bool:
    """True when `text` looks like the engine's sitrep block and not an error."""
    if not text:
        return False
    head = text.lstrip()
    if head.startswith("ERROR:") or head.startswith("usage"):
        return False
    return head.upper().startswith("SOL ")


def _sitrep_from_team(team) -> str:
    """Synthesize the §11 block from team.json, for fixture roots and for the
    case where `engine.py sitrep` is unavailable (an older engine, no mission).

    Same shape, same order, same uppercase wording as the engine's own block -
    the terminal panel must read identically whichever half produced it.
    """
    team = team if isinstance(team, dict) else {}
    units = team.get("units") or {}
    tiles = team.get("tiles") or {}
    zone = [str(c) for c in (team.get("search_zone") or [])]
    obj = team.get("objective") or {}
    phase = team.get("phase") or "active"

    oxy = team.get("oxygen_sols")
    oxy_max = team.get("oxygen_max") or 12
    oxy_txt = "--" if oxy is None else str(oxy)

    if zone:
        explored = sum(1 for c in zone if c in tiles)
        pct = round(explored * 100.0 / (len(zone) or 9))
        zone_txt = f"{pct}%"
    else:
        zone_txt = "--"

    total_units = len(units) or 2
    online = sum(1 for u in units.values() if (u or {}).get("status") == "ok")
    damaged = sum(1 for u in units.values() if (u or {}).get("status") == "broken")

    total_obj = obj.get("total") or 3
    done = obj.get("rescued_count")
    if done is None:
        done = len(team.get("rescued") or [])
    current = obj.get("current")

    lines = [
        f"SOL {team.get('sol', '--')}",
        f"OXYGEN: {oxy_txt}/{oxy_max}",
        f"ZONE EXPLORED: {zone_txt}",
        f"ROVERS ONLINE: {online}/{total_units}",
        f"ROVERS DAMAGED: {damaged}/{total_units}",
        f"COLONISTS RESCUED: {done}/{total_obj}",
        "",
    ]
    if phase == "awaiting-orders":
        lines.append("TEAM WAITING FOR ORDERS:")
    elif current:
        lines.append(f"TEAM ACTIVE: OBJECTIVE {current} OF {total_obj}")
    else:
        lines.append(f"TEAM ACTIVE: OBJECTIVE -- OF {total_obj}")
    lines.extend(OPTION_LINES)
    return "\n".join(lines)


def _engine_sitrep():
    """`python engine/engine.py sitrep` from the project root, or None.

    Only attempted when --root is the project's own game/ directory: with a
    fixture root the engine would answer about a different mission than the one
    on screen, so the fixture's team.json is synthesized instead.
    """
    try:
        same_mission = ROOT.resolve() == (PROJECT_DIR / "game").resolve()
    except OSError:
        same_mission = False
    if not same_mission or not (PROJECT_DIR / "engine" / "engine.py").is_file():
        return None
    try:
        proc = subprocess.run(
            [sys.executable, "engine/engine.py", "sitrep"],
            cwd=str(PROJECT_DIR),
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = (proc.stdout or b"").decode("utf-8", "replace").replace("\r\n", "\n").strip("\n")
    if proc.returncode != 0 or not _sitrep_is_block(text):
        return None
    return text


def collect_sitrep():
    """{text, waiting, source} - cached for SITREP_TTL seconds."""
    now = time.monotonic()
    cached = _sitrep_cache.get("payload")
    if cached is not None and now - _sitrep_cache.get("at", 0.0) < SITREP_TTL:
        return cached

    text = _engine_sitrep()
    source = "engine"
    if text is None:
        team, err = _read_json(ROOT / "team.json")
        text = _sitrep_from_team(team if err is None else {})
        source = "team.json"

    payload = {
        "text": text,
        "waiting": "TEAM WAITING FOR ORDERS" in text.upper(),
        "source": source,
        "options": OPTION_LINES,
    }
    _sitrep_cache["payload"] = payload
    _sitrep_cache["at"] = now
    return payload


class Handler(BaseHTTPRequestHandler):
    server_version = "MarsRescueUI/3.1"

    # ---- helpers -------------------------------------------------
    def _send_json(self, obj, status: int = HTTPStatus.OK):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def _send_file(self, path: Path, content_type: str):
        try:
            body = path.read_bytes()
        except OSError:
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def log_message(self, fmt, *args):  # noqa: A003 - stdlib signature
        sys.stderr.write("%s - %s\n" % (self.client_address[0], fmt % args))

    # ---- routing ---------------------------------------------------
    def do_GET(self):  # noqa: N802 - stdlib signature
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)

            if path == "/":
                self._send_file(INDEX_HTML, "text/html; charset=utf-8")
                return

            # ---------- mission state (from --root) ----------
            if path == "/api/state":
                data, err = _read_json(ROOT / "team.json")
                if err is not None:
                    self._send_json({"sol": 0, "missing": True})
                    return
                self._send_json(data)
                return

            if path == "/api/events":
                since_raw = query.get("since", ["0"])[0]
                try:
                    since = max(0, int(since_raw))
                except ValueError:
                    since = 0
                events, next_index = _read_events_since(ROOT / "events.jsonl", since)
                self._send_json({"next": next_index, "events": events})
                return

            if path == "/api/snapshots":
                snap_dir = ROOT / "snapshots"
                sols = []
                if snap_dir.is_dir():
                    for p in snap_dir.glob("sol-*.json"):
                        name = p.stem  # "sol-3"
                        try:
                            sols.append(int(name.split("-", 1)[1]))
                        except (IndexError, ValueError):
                            continue
                sols.sort()
                self._send_json({"sols": sols})
                return

            if path == "/api/snapshot":
                sol_raw = query.get("sol", [None])[0]
                if sol_raw is None:
                    self._send_json({"error": "missing ?sol=N"}, HTTPStatus.BAD_REQUEST)
                    return
                try:
                    sol = int(sol_raw)
                except ValueError:
                    self._send_json({"error": "no snapshot"}, HTTPStatus.NOT_FOUND)
                    return
                data, err = _read_json(ROOT / "snapshots" / f"sol-{sol}.json")
                if err is not None:
                    self._send_json({"error": "no snapshot"}, HTTPStatus.NOT_FOUND)
                    return
                self._send_json(data)
                return

            if path == "/api/truth":
                data, err = _read_json(ROOT / "truth.json")
                if err is not None:
                    self._send_json({"error": "no truth"}, HTTPStatus.NOT_FOUND)
                    return
                self._send_json(data)
                return

            if path == "/api/intel":
                self._send_json(collect_intel())
                return

            if path == "/api/sitrep":
                # SPEC §11: the block the officer on duty answers. Runs
                # `engine.py sitrep` in the project root (1 s cache) and falls
                # back to synthesizing it from <root>/team.json.
                self._send_json(collect_sitrep())
                return

            if path == "/api/notes":
                # game/notes.md - the only channel between agents (SPEC §3 `note`).
                # Mission state, so it comes from --root like team.json does.
                text = _read_text(ROOT / "notes.md")
                self._send_json({
                    "path": str(ROOT / "notes.md").replace("\\", "/"),
                    "text": text if text is not None else "",
                    "missing": text is None,
                })
                return

            # ---------- harness content (from PROJECT_DIR) ----------
            if path == "/api/agents":
                self._send_json(collect_agents())
                return

            if path == "/api/skills":
                self._send_json(collect_skills())
                return

            if path == "/api/hooks":
                self._send_json(collect_hooks())
                return

            if path == "/api/memory":
                self._send_json(collect_memory())
                return

            if path == "/api/roles":
                data, err = _read_json(ROLES_JSON)
                if err is not None:
                    self._send_json({"roles": [], "error": err})
                    return
                self._send_json(data)
                return

            if path == "/api/claude-md":
                text = _read_text(PROJECT_DIR / "CLAUDE.md")
                self._send_json({
                    "path": "CLAUDE.md",
                    "text": text if text is not None else "",
                    "missing": text is None,
                })
                return

            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:  # never crash on a bad request
            try:
                self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            except Exception:
                pass


def run(argv=None) -> int:
    global ROOT, INDEX_HTML
    parser = argparse.ArgumentParser(description="Mars Search & Rescue UI server")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--root", default="game", help="directory containing team.json etc.")
    parser.add_argument("--host", default="127.0.0.1",
                        help="address to listen on (default 127.0.0.1, this machine only; the page can "
                             "show the hidden map, so open it to a network deliberately)")
    parser.add_argument("--index", default=None,
                        help="HTML page to serve at / (default ui/index.html; ui/light/index.html is the light theme)")
    args = parser.parse_args(argv)

    ROOT = Path(args.root)
    if args.index:
        INDEX_HTML = Path(args.index)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(
        f"Mars Search & Rescue UI: http://localhost:{args.port}"
        f"  (mission root={ROOT}, harness={PROJECT_DIR})",
        file=sys.stderr,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def main():
    sys.exit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
