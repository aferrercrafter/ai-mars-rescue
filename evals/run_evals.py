#!/usr/bin/env python
"""
evals/run_evals.py - Does each skill make its agent better at its job?

For every case in evals/cases/<skill>.json this runs the real agent headless
(`claude -p`) several times in two configurations:

    with_skill      the repo as it is
    without_skill   the same repo with that one skill removed

and grades what each run left behind with the checks in evals/checks.py.
Every run happens in a throwaway copy of the repo, so your own game/, agent
memory and agent files are never touched, and runs can go in parallel.

    python evals/run_evals.py --plan                  # list the runs, spend nothing
    python evals/run_evals.py life-support            # one skill
    python evals/run_evals.py                         # all six
    python evals/run_evals.py --summary               # table from the stored results
    python evals/run_evals.py run-sol --trigger       # does the description trigger?

These runs cost money. --plan shows how many there are before you start.
Raw output goes to evals/results/, which is not in git.
"""
import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))
import checks  # noqa: E402

CASES = ROOT / "evals" / "cases"
RESULTS = ROOT / "evals" / "results"
CONFIGS = ["with_skill", "without_skill"]
RELAY = (
    'Spawn the `{agent}` agent (subagent_type "{agent}") with exactly this prompt and nothing else:\n\n'
    "{prompt}\n\n"
    "Wait for it to finish, then reply with its final message verbatim. "
    "Do not do any of its work yourself and do not run any other tool."
)


def load_cases(skills):
    out = []
    for path in sorted(CASES.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if not skills or data["skill_name"] in skills:
            out.append(data)
    return out


def copy_repo(dest):
    """Copy everything git knows about (tracked, or new and not ignored) into dest."""
    listed = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT,
                            capture_output=True, text=True, check=True).stdout.splitlines()
    for rel in listed:
        if rel.startswith("evals/results/") or not (ROOT / rel).is_file():
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, target)
    shutil.copyfile(dest / ".claude" / "settings.local.json.example", dest / ".claude" / "settings.local.json")


def remove_skill(work, skill, agent):
    """The baseline: the skill folder is gone, and the agent neither preloads nor mentions it.

    The sentence pointing at the skill goes too. Left in, the agent looks for a skill that
    is not there and gives up, which says nothing about how it does without one.
    """
    shutil.rmtree(work / ".claude" / "skills" / skill)
    if agent:
        path = work / ".claude" / "agents" / f"{agent}.md"
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        path.write_text("".join(ln for ln in lines if not ln.startswith(("skills:", "Follow the `"))),
                        encoding="utf-8")


def claude(prompt, work, model, budget, timeout, extra=()):
    """One headless run. Only this repo's settings are loaded, so the result does not depend on
    the plugins, hooks or MCP servers of whoever runs the evals."""
    cmd = [shutil.which("claude") or "claude", "-p", "--model", model, "--permission-mode", "acceptEdits",
           "--setting-sources", "project,local", "--strict-mcp-config", "--no-session-persistence",
           "--max-budget-usd", str(budget), *extra]
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
    return subprocess.run(cmd, input=prompt, cwd=work, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def one_run(suite, case, config, index, args):
    skill, agent = suite["skill_name"], suite["agent"]
    out = RESULTS / skill / f"eval-{case['name']}" / config / f"run-{index}"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    work = Path(tempfile.mkdtemp(prefix="mars-eval-"))
    try:
        copy_repo(work)
        if config == "without_skill":
            remove_skill(work, skill, agent)
        fixture = subprocess.run([sys.executable, "evals/fixtures.py", case["fixture"]], cwd=work,
                                 capture_output=True, text=True)
        if fixture.returncode != 0:
            raise RuntimeError(f"fixture {case['fixture']} failed: {fixture.stderr or fixture.stdout}")
        variables = json.loads(fixture.stdout.strip().splitlines()[-1])
        pre = checks.snapshot(work)

        prompt = case["prompt"].format(**variables)
        if agent:
            prompt, model, budget, timeout = RELAY.format(agent=agent, prompt=prompt), args.relay_model, 1, 420
        else:
            model, budget, timeout = args.model, 4, 1200
        started = time.time()
        try:
            done = claude(prompt, work, model, budget, timeout, ["--output-format", "json"])
            raw = done.stdout
        except subprocess.TimeoutExpired:
            raw = json.dumps({"result": "TIMEOUT", "is_error": True})
        seconds = round(time.time() - started, 1)
        try:
            result = json.loads(raw)
        except ValueError:
            result = {"result": raw[-2000:], "is_error": True}
        if isinstance(result, list):  # some versions print the whole event array
            result = next((r for r in reversed(result) if r.get("type") == "result"), {})

        run = checks.Run(work, pre, result, variables)
        (out / "outputs").mkdir()
        (out / "outputs" / "claude.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        keep_outputs(work, out / "outputs")

        expectations = []
        for a in case["assertions"]:
            try:
                verdict = checks.CHECKS[a["check"]](run, **a.get("args", {}))
            except Exception as exc:  # a check that crashes is a failed check, not a failed batch
                verdict = (False, f"check raised {type(exc).__name__}: {exc}")
            if isinstance(verdict, list):
                expectations += [{"text": t, "passed": bool(p), "evidence": e} for t, p, e in verdict]
            else:
                expectations.append({"text": a["text"], "passed": bool(verdict[0]), "evidence": verdict[1]})
        passed = sum(e["passed"] for e in expectations)
        grading = {
            "expectations": expectations,
            "summary": {"passed": passed, "failed": len(expectations) - passed, "total": len(expectations),
                        "pass_rate": round(passed / len(expectations), 4)},
            "timing": {"total_duration_seconds": seconds},
            "cost_usd": result.get("total_cost_usd"),
        }
        (out / "grading.json").write_text(json.dumps(grading, indent=2), encoding="utf-8")
        (out / "timing.json").write_text(json.dumps({"total_duration_seconds": seconds,
                                                     "duration_ms": int(seconds * 1000)}), encoding="utf-8")
        print(f"{skill:13} {case['name']:30} {config:13} run-{index}  {passed}/{len(expectations)}"
              f"  {seconds:6.0f}s  ${result.get('total_cost_usd') or 0:.3f}", flush=True)
        return grading
    except Exception as exc:
        print(f"{skill:13} {case['name']:30} {config:13} run-{index}  FAILED TO RUN: {exc}", flush=True)
        return None
    finally:
        shutil.rmtree(work, ignore_errors=True)


def keep_outputs(work, dest):
    """Keep what a person would want to read afterwards. Never the truth file."""
    for rel in ("game", ".claude/agent-memory"):
        if (work / rel).exists():
            shutil.copytree(work / rel, dest / rel, ignore=shutil.ignore_patterns("truth.json", "snapshots"))


# -- summary ----------------------------------------------------------------

def collect():
    table = {}
    for path in sorted(RESULTS.glob("*/eval-*/*/run-*/grading.json")):
        skill, case, config = path.parts[-5], path.parts[-4][5:], path.parts[-3]
        table.setdefault(skill, {}).setdefault(case, {}).setdefault(config, []).append(
            json.loads(path.read_text(encoding="utf-8")))
    return table


def rate(gradings):
    rates = [g["summary"]["pass_rate"] for g in gradings]
    if not rates:
        return "-"
    spread = f" ±{statistics.stdev(rates) * 100:.0f}" if len(rates) > 1 else ""
    return f"{statistics.mean(rates) * 100:.0f}%{spread} (n={len(rates)})"


def summary():
    table = collect()
    lines = ["| Skill | Case | With skill | Without skill |", "|---|---|---|---|"]
    for skill, cases in table.items():
        for case, configs in cases.items():
            lines.append(f"| `{skill}` | {case} | {rate(configs.get('with_skill', []))} "
                         f"| {rate(configs.get('without_skill', []))} |")
        merged = {c: [g for cfg in cases.values() for g in cfg.get(c, [])] for c in CONFIGS}
        lines.append(f"| `{skill}` | **all cases** | **{rate(merged['with_skill'])}** "
                     f"| **{rate(merged['without_skill'])}** |")
    lines += ["", "Assertions that failed at least once:", ""]
    for skill, cases in table.items():
        for case, configs in cases.items():
            for config in CONFIGS:
                runs = configs.get(config, [])
                fails = {}
                for g in runs:
                    for e in g["expectations"]:
                        if not e["passed"]:
                            fails.setdefault(e["text"], []).append(e["evidence"])
                for text, evidence in fails.items():
                    lines.append(f"- `{skill}` / {case} / {config}: {text} - failed {len(evidence)} of {len(runs)}"
                                 f" (e.g. {str(evidence[0])[:110]})")
    cost = sum(g.get("cost_usd") or 0 for cases in table.values() for cfgs in cases.values()
               for runs in cfgs.values() for g in runs)
    lines += ["", f"Total cost of the stored runs: ${cost:.2f}"]
    print("\n".join(lines))


# -- description triggering -------------------------------------------------

def trigger_once(query, index, args):
    """Start a session with the query and stop it at the first tool call: was it Skill(run-sol)?"""
    work = Path(tempfile.mkdtemp(prefix="mars-trig-"))
    try:
        copy_repo(work)
        subprocess.run([sys.executable, "evals/fixtures.py", "demo"], cwd=work, capture_output=True, text=True)
        cmd = [shutil.which("claude") or "claude", "-p", "--model", args.model, "--setting-sources", "project,local",
               "--strict-mcp-config", "--no-session-persistence", "--max-budget-usd", "1",
               "--output-format", "stream-json", "--verbose"]
        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
        proc = subprocess.Popen(cmd, cwd=work, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace")
        proc.stdin.write(query)
        proc.stdin.close()
        first = None
        for line in proc.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") == "assistant":
                for block in event.get("message", {}).get("content", []):
                    if block.get("type") == "tool_use" and first is None:
                        first = block
            if first or event.get("type") == "result":
                break
        proc.kill()
        proc.wait()
        used = bool(first) and first.get("name") == "Skill" and "run-sol" in json.dumps(first.get("input"))
        return used, (first or {}).get("name") or "answered without a tool"
    finally:
        shutil.rmtree(work, ignore_errors=True)


def trigger(skill, args):
    queries = json.loads((ROOT / "evals" / "trigger" / f"{skill}.json").read_text(encoding="utf-8"))
    jobs = [(q, i) for q in queries for i in range(args.runs)]
    print(f"{len(jobs)} runs ({len(queries)} phrasings x {args.runs}), each stopped at its first tool call.")
    if args.plan:
        return
    with ThreadPoolExecutor(args.jobs) as pool:
        outcomes = list(pool.map(lambda job: trigger_once(job[0]["query"], job[1], args), jobs))
    rows, correct = [], 0
    for q in queries:
        mine = [o for (jq, _), o in zip(jobs, outcomes) if jq is q]
        hits = sum(1 for used, _ in mine if used)
        ok = sum(1 for used, _ in mine if used == q["should_trigger"])
        correct += ok
        rows.append({"query": q["query"], "should_trigger": q["should_trigger"], "triggered": hits,
                     "runs": len(mine), "first_tools": sorted({tool for _, tool in mine})})
        print(f"{'ok ' if ok == len(mine) else 'BAD'} {hits}/{len(mine)} triggered "
              f"(want {'yes' if q['should_trigger'] else 'no '})  {q['query'][:70]}")
    print(f"\n{correct}/{len(jobs)} runs behaved as wanted.")
    out = RESULTS / skill
    out.mkdir(parents=True, exist_ok=True)
    (out / "trigger.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")


def main():
    sys.stdout.reconfigure(encoding="utf-8")  # the table uses a plus-minus sign
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("skills", nargs="*", help="skill names; default is all six")
    ap.add_argument("--runs", type=int, default=3, help="runs per case and configuration (default 3)")
    ap.add_argument("--case", help="only the case with this name")
    ap.add_argument("--config", choices=CONFIGS, help="only this configuration")
    ap.add_argument("--jobs", type=int, default=4, help="runs in parallel (default 4)")
    ap.add_argument("--model", default="sonnet", help="model of the main session for run-sol (default sonnet)")
    ap.add_argument("--relay-model", default="haiku",
                    help="model of the session that only spawns a role agent (default haiku)")
    ap.add_argument("--plan", action="store_true", help="list the runs and stop")
    ap.add_argument("--summary", action="store_true", help="print the table from evals/results/")
    ap.add_argument("--trigger", action="store_true", help="test the description of the named skill instead")
    args = ap.parse_args()

    if args.summary:
        return summary()
    if args.trigger:
        return trigger(args.skills[0] if args.skills else "run-sol", args)

    jobs = [(suite, case, config, i)
            for suite in load_cases(args.skills) for case in suite["evals"]
            if not args.case or case["name"] == args.case
            for config in CONFIGS if not args.config or config == args.config
            for i in range(1, args.runs + 1)]
    print(f"{len(jobs)} runs.")
    if args.plan:
        for suite, case, config, i in jobs:
            print(f"  {suite['skill_name']:13} {case['name']:30} {config:13} run-{i}")
        return
    with ThreadPoolExecutor(args.jobs) as pool:
        list(pool.map(lambda job: one_run(*job, args), jobs))
    print()
    summary()


if __name__ == "__main__":
    main()
