#!/usr/bin/env python3
"""One table row per hosted run, every cell parsed from that run's record.

Issue #1947 measured ``pytest tools/`` on 1, 2, auto and 8 pytest-xdist
workers, three runs each, on GitHub-hosted ``ubuntu-24.04`` runners (the
workflow is reproduced in ``docs/ci-tools-xdist-measurement-2026-11-28.md``).
Each run uploaded four files, and this reads exactly those:

``env.txt``      the runner: cpus, memory, package versions, the commit
``pytest.log``   pytest's own output, inside ``tools/run_with_rss.py`` inside
                 ``tools/mem_peak.py`` -- their ``[rss]`` / ``[mem]`` lines
                 bracket pytest's summary line
``mem.json``     ``tools/mem_peak.py``'s record: the peak of the SUM of VmRSS
                 over the whole process tree
``union.json``   ``tools/check_collection_union.py --json``: did every test
                 run exactly once, with the baseline's outcomes

Why a tool and not a hand. The table is the document's numbers, and a number
typed from a log is a claim: #1816's measurement recorded seven figures that
were wrong before they were right, each by hand. So each cell is parsed here,
each parser is pinned by ``tools/test_xdist_run_table.py`` to a line of the
exact shape its producer writes, and a record missing a load-bearing line is
REFUSED (exit 2) -- never shown as a blank, because a blank reads as zero and
zero is a number.

Two wall times appear per run on purpose. ``job wall`` is the Actions API's
``started_at`` to ``completed_at`` for the whole job (checkout, pip, apt, the
run, the collection, the upload), the same measure the #1816 table used, so
the two documents compare like with like. ``pytest's own`` is the ``in <t>s``
of pytest's summary line. The difference between them is overhead that no
worker count can remove.

Usage::

    python tools/xdist_run_table.py jobs.json run-dir [run-dir ...]
    python tools/xdist_run_table.py run-dir [run-dir ...]        # no job wall

``jobs.json`` is ``gh api repos/<owner>/<repo>/actions/runs/<id>/jobs``. A
run directory is one ``actions/download-artifact`` directory, named
``xdist-<candidate>-r<repeat>``.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import statistics
import sys
from dataclasses import dataclass, field

import refusal

PASS, REFUSED = "PASS", "REFUSED"
CANDIDATES = ("n1", "n2", "nauto", "n8")
SUMMARY = re.compile(r"^((?:\d+ (?:passed|failed|skipped|xfailed|xpassed|errors?|warnings?),? ?)+)"
                     r" in ([\d.]+)s")
RSS = re.compile(r"^\[rss\] peak RSS (\d+) MB\s+user ([\d.]+)s\s+sys ([\d.]+)s\s+wall ([\d.]+)s")
MEM = re.compile(r"^\[mem\] peak tree RSS (\d+) MB at ([\d.]+)s\s+samples (\d+)\s+"
                 r"max processes (\d+)\s+wall ([\d.]+)s")
COUNT = re.compile(r"(\d+) (passed|failed|skipped|xfailed|xpassed|errors?)")


class Refusal(refusal.Refused, Exception):
    pass


def read_env(text: str) -> dict:
    """The workflow's ``key value`` lines, each value up to the end of its
    line; ``meminfo`` and ``cpu_count`` lines are split into numeric fields."""
    env = {}
    for line in text.splitlines():
        key, _, value = line.partition(" ")
        env[key] = value.strip()
        for name, n in re.findall(r"(MemTotal|MemAvailable):\s+(\d+) kB", line):
            env[f"{name}_mb"] = int(n) // 1024
        for name, n in re.findall(r"\b(cpu_count|affinity) (\d+)", line):
            env[name] = int(n)
    return env


def read_pytest_log(text: str) -> dict:
    """pytest's summary line and the two wrappers' final lines."""
    out = {}
    for line in text.splitlines():
        m = SUMMARY.match(line.strip())
        if m:
            out.update({w: int(n) for n, w in COUNT.findall(m.group(1))})
            out["pytest_s"] = float(m.group(2))
            out["summary_line"] = line.strip()
        m = RSS.match(line.strip())
        if m:
            out["max_single_rss_mb"] = int(m.group(1))
            out["user_s"], out["sys_s"] = float(m.group(2)), float(m.group(3))
            out["step_wall_s"] = float(m.group(4))
        m = MEM.match(line.strip())
        if m:
            out["mem_peak_mb"] = int(m.group(1))
        m = re.match(r"^\[(mem|rss)\] exit (\d+)", line)
        if m:
            out[f"{m.group(1)}_exit"] = int(m.group(2))
        m = re.match(r"^\[(mem|rss)\] TERMINATED BY (\w+)", line)
        if m:
            out[f"{m.group(1)}_signal"] = m.group(2)
    if "pytest_s" not in out:
        raise Refusal(f"no pytest summary line ('<n> passed ... in <t>s') in the log")
    if "step_wall_s" not in out:
        raise Refusal("no '[rss] peak RSS' line in the log: run_with_rss.py did not finish")
    if "mem_peak_mb" not in out:
        raise Refusal("no '[mem] peak tree RSS' line in the log: mem_peak.py did not finish")
    out["cpu_s"] = out["user_s"] + out["sys_s"]
    out.setdefault("passed", 0)
    for k in ("failed", "skipped", "xfailed", "xpassed", "error", "errors"):
        out.setdefault(k, 0)
    out["error"] = out["error"] + out["errors"]
    return out


@dataclass
class Run:
    label: str = ""
    repeat: int = 0
    dir: str = ""
    head: str = ""
    cpus: int | None = None
    mem_total_mb: int | None = None
    runner: str = ""
    conclusion: str = ""
    job_wall_min: float | None = None
    step_wall_api_min: float | None = None
    step_wall_s: float = 0.0
    pytest_s: float = 0.0
    cpu_s: float = 0.0
    peak_tree_rss_mb: int = 0
    max_processes: int = 0
    counts: dict = field(default_factory=dict)          # the union check's tally
    summary_counts: dict = field(default_factory=dict)   # pytest's own line
    summary_line: str = ""
    verdict: str = ""                                   # the check's overall verdict
    expected: int = 0                                   # what the collection lists
    outcome_diffs: int = 0
    union_holes: int = 0
    exits: dict = field(default_factory=dict)

    @property
    def cpu_over_wall(self) -> float:
        return self.cpu_s / self.step_wall_s if self.step_wall_s else 0.0

    @property
    def key(self) -> tuple:
        return (CANDIDATES.index(self.label) if self.label in CANDIDATES else 99,
                self.label, self.repeat)


def _t(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def read_run_dir(path) -> Run:
    path = pathlib.Path(path)
    m = re.fullmatch(r"xdist-(.+)-r(\d+)", path.name)
    if not m:
        raise Refusal(f"{path.name}: not named xdist-<candidate>-r<repeat>")
    run = Run(label=m.group(1), repeat=int(m.group(2)), dir=path.name)
    try:
        env = read_env((path / "env.txt").read_text())
        log = read_pytest_log((path / "pytest.log").read_text(errors="replace"))
        mem = json.loads((path / "mem.json").read_text())
    except OSError as exc:
        raise Refusal(f"{path.name}: {exc.strerror}: {exc.filename}") from exc
    except json.JSONDecodeError as exc:
        raise Refusal(f"{path.name}: mem.json is not JSON: {exc}") from exc
    run.head, run.cpus, run.mem_total_mb = env.get("head"), env.get("cpu_count"), env.get("MemTotal_mb")
    for k in ("step_wall_s", "pytest_s", "cpu_s", "summary_line", "user_s", "sys_s",
              "max_single_rss_mb", "passed", "failed", "skipped", "xfailed", "xpassed",
              "error", "mem_exit", "rss_exit"):
        if k in log:
            setattr(run, k, log[k])
    run.summary_counts = {k: log[k] for k in ("passed", "failed", "skipped", "xfailed", "xpassed", "error")}
    run.peak_tree_rss_mb = int(mem["peak_tree_rss_mb"])
    run.max_processes = int(mem["max_processes"])
    if run.peak_tree_rss_mb != log["mem_peak_mb"]:
        raise Refusal(f"{path.name}: mem.json says peak {run.peak_tree_rss_mb} MB and the "
                      f"log's [mem] line says {log['mem_peak_mb']}: two records, one run")
    union = path / "union.json"
    if union.exists():
        u = json.loads(union.read_text())
        run.verdict = u["verdict"]
        run.expected = int(u.get("expected", 0))
        run.counts = u.get("counts", {})
        run.outcome_diffs = len(u.get("outcome_diffs", []))
        run.union_holes = sum(len(u.get(k, [])) for k in ("missing", "extra", "duplicate"))
    else:
        run.verdict = "(not checked)"
    return run


def read_jobs(path) -> dict:
    """``{'tools-n2-r1': job}`` from the Actions API's jobs document."""
    return {j["name"]: j for j in json.loads(pathlib.Path(path).read_text())["jobs"]}


def _attach_jobs(run: Run, jobs: dict | None) -> None:
    if jobs is None:
        return
    job = jobs.get(f"tools-{run.label}-r{run.repeat}")
    if job is None or not job.get("completed_at") or not job.get("started_at"):
        return
    run.runner = job.get("runner_name", "")
    run.conclusion = job.get("conclusion") or ""
    run.job_wall_min = (_t(job["completed_at"]) - _t(job["started_at"])).total_seconds() / 60
    steps = [s for s in job.get("steps", [])
             if str(s.get("name", "")).startswith("every tools/ test")
             and s.get("started_at") and s.get("completed_at")]
    if steps:
        run.step_wall_api_min = (_t(steps[0]["completed_at"]) - _t(steps[0]["started_at"])).total_seconds() / 60


@dataclass
class Table:
    runs: list[Run] = field(default_factory=list)

    def render(self) -> str:
        return render(self.runs)

    def render_summary(self) -> str:
        return render_summary(self.runs)


def build_table(run_dirs, jobs=None) -> Table:
    if jobs is not None and not isinstance(jobs, dict):
        jobs = read_jobs(jobs)
    runs = []
    for d in run_dirs:
        try:
            runs.append(read_run_dir(d))
        except Refusal as exc:
            raise Refusal(f"record {pathlib.Path(d).name}: {exc}") from exc
    runs.sort(key=lambda r: r.key)
    for r in runs:
        _attach_jobs(r, jobs)
    return Table(runs)


def _f(v, spec=".1f", blank="") -> str:
    return blank if v is None else format(v, spec)


HEAD = ("| candidate | repeat | runner | job wall (min) | pytest step (min, API) | "
        "pytest's own (s) | CPU user+sys (s) | CPU / wall | peak tree RSS (MB) | "
        "max processes | pytest's summary line | collection union |")


def render(runs: list[Run]) -> str:
    lines = [HEAD, "|" + "---|" * 12]
    for r in runs:
        step = _f(r.step_wall_api_min, ".1f", "(no step record)")
        if r.union_holes:
            union = f"UNION BROKEN: {r.union_holes} not run exactly once"
        else:
            union = f"{r.expected} ran once; " + (
                f"{r.outcome_diffs} outcome differences vs baseline"
                if r.outcome_diffs else "outcomes identical to baseline")
        lines.append(
            f"| {r.label} | {r.repeat} | {r.runner} | {_f(r.job_wall_min, '.1f', '(no jobs record)')} | "
            f"{step} | {r.pytest_s:.2f} | {r.cpu_s:.0f} | {r.cpu_over_wall:.2f} | "
            f"{r.peak_tree_rss_mb} | {r.max_processes} | {r.summary_line} | {union} |")
    return "\n".join(lines)


def render_summary(runs: list[Run]) -> str:
    """Per candidate: n, and min / median / max of the whole-job wall time."""
    lines = ["| candidate | runs | job wall min | job wall median | job wall max | "
             "pytest's own median (s) | CPU / wall median | peak tree RSS max (MB) |",
             "|---|---|---|---|---|---|---|---|"]
    for label in CANDIDATES:
        group = [r for r in runs if r.label == label]
        if not group:
            continue
        walls = [r.job_wall_min for r in group if r.job_wall_min is not None]
        if walls:
            wall = f"{min(walls):.1f} | {statistics.median(walls):.1f} | {max(walls):.1f}"
        else:
            wall = "(no jobs record) |  | "
        lines.append(f"| {label} | {len(group)} | {wall} | "
                     f"{statistics.median(r.pytest_s for r in group):.0f} | "
                     f"{statistics.median(r.cpu_over_wall for r in group):.2f} | "
                     f"{max(r.peak_tree_rss_mb for r in group)} |")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    jobs = None
    dirs = list(argv)
    if argv and argv[0].endswith(".json"):
        jobs = read_jobs(argv[0])
        dirs = argv[1:]
    if not dirs:
        print(__doc__, file=sys.stderr)
        print("REFUSED: no run directories given", file=sys.stderr)
        return 2
    try:
        table = build_table(dirs, jobs)
    except (Refusal, OSError, KeyError, json.JSONDecodeError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    print(table.render())
    print()
    print(table.render_summary())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
