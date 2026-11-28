#!/usr/bin/env python3
"""Per-unit wall time of `make verify-fast` (the `m5a-fast` job) across CI runs.

WHY. Issue #1730. On PR #1683 the 46-file pytest unit of `verify-fast` hit
tools/run_all.py's 600 s cap -- a NO-VERDICT, not a failure -- at `29905da9`,
100 minutes after passing in 450.0 s at `2d70924`. Two explanations were on
the table:

  (a) the runner the job landed on was slow: a whole-machine slowdown, in
      which every unit stretches by about the same factor, the two-second
      ones included; or
  (b) the batch grew between the two revisions, in which case only it
      stretches and the eleven siblings do not move.

Neither is testable from `gh run list`, whose createdAt -> updatedAt span
folds queue time, setup and all twelve units into one number. The
discriminating measurement is **per-unit wall time on the same job across
runs**: the summary table tools/run_all.py prints carries one `secs` per
unit, and the units are independent processes that share one machine. The
ratio between two of them is the test -- stable while both scale together
under (a); shifted at the revision that grew under (b).

The strongest single comparison is a same-SHA pair (a re-run): it holds
source, recipe and worker allocation fixed and varies only the runner. The
census also reports the across-run distribution of a reference unit
(`tools/test_run_case.py`: one file, the longest-running sibling, almost never
in a diff) as a canary for the runner fleet, and the focus unit's ratio to it.

WHAT THIS CANNOT SEE. It reads what the shipped recipe printed, so it measures
the **parallel** recipe (12 units on the runner's cores), never the focus unit
in isolation, and with `-q` there is no per-test duration to attribute time
within a unit. It cannot see *why* a runner was slow. `overlap_runs` (other
runs of the same workflow in flight during the job) is recorded as the proxy
the issue used; the report shows whether it predicts anything.

APPARATUS. `gh run view --job <id> --log` serves the **latest** attempt's log
for any job id of that run. On #1730 it returned attempt 2's green summary for
attempt 1's red job id, silently. This collector never uses it: it lists the
attempts and addresses each one as `gh run view <run> --attempt <n> --log`.

REFUSED IS AN OUTCOME. This queries a live API, so it can fail to answer:

    0  measured, and (--require-clean) the window has no NO-VERDICT
    1  measured, and the window contains a NO-VERDICT unit or a capped job
    2  REFUSED -- could not measure (no `gh`, API error, empty or short
       window, a focus slug that appears in no record, too many unreadable
       logs), or the `--snapshot` destination could not hold the record:
       refused before anything is collected where the path says so, else
       after the report with the gate's verdict withheld (#2098)

Network-dependent, so it is NOT part of `make verify`; its tests
(`tools/test_ci_verify_fast_timing.py`) are offline and parse fixture logs.

Usage:
    python tools/ci_verify_fast_timing.py --limit 100 --snapshot /tmp/m5a.json
    python tools/ci_verify_fast_timing.py --offline /tmp/m5a.json --list
    python tools/ci_verify_fast_timing.py --offline /tmp/m5a.json --require-clean --window 20
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
import pathlib
import re
import statistics
import subprocess
import sys
from dataclasses import asdict, dataclass

import refusal

DEFAULT_WORKFLOW = "rungs.yml"
DEFAULT_JOB = "m5a-fast"
GATE_STEP = "focused sound and selected-path gate"
BATCH = "test_filter_rate_chain"      # the 46-file unit: slug of its first path
REFERENCE = "test_run_case"           # one file, ~275-365 s, co-runs with BATCH
NO_VERDICT = "NO-VERDICT"
REFUSED = 2
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


class Refusal(refusal.Refused, Exception):
    """The measurement could not be taken. Not a failure -- an absence."""


# --------------------------------------------------------------------------
# pure helpers (everything below this line until `collect` is tested offline)
# --------------------------------------------------------------------------

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\s")
_ROW = re.compile(r"^(PASS|NO-VERDICT|LAUNCH-ERROR|FAIL(?:\([^)]*\))?)\s+(\d+(?:\.\d+)?)s\s+(.*)$")


@dataclass
class UnitTiming:
    """One row of the run_all.py summary table.

    `secs` is wall clock as printed. `censored` is True when the unit hit the
    per-command cap, in which case `secs` is the cap (600.0) and not the
    unit's duration: it is counted, never averaged. `slug` is the stem of the
    first .py path on the command line; `n_files` counts the .py paths named.
    """
    slug: str
    state: str            # PASS | NO-VERDICT | FAIL | LAUNCH-ERROR, as printed
    rc: str | None        # the parenthesised status for FAIL, e.g. "1"
    secs: float | None
    censored: bool
    n_files: int


@dataclass
class JobTiming:
    """One execution of the job: one attempt of one run.

    `units` is in recipe order and empty when `table` is False. `gate_secs`
    is the gate step's own wall time from the steps API, independent of the
    log. `overlap_runs` counts other runs of the workflow in flight, a lower
    bound that cannot see the runner's host. `error` is set when the log
    could not be read; such a record contributes no unit seconds anywhere.
    """
    run_id: int
    attempt: int
    event: str
    branch: str
    sha: str
    created_at: str
    conclusion: str
    runner_name: str
    started_at: str | None
    completed_at: str | None
    gate_secs: float | None
    units: list[UnitTiming]
    table: bool
    overlap_runs: int | None
    error: str | None
    job_id: int | None = None

    def unit(self, slug: str) -> UnitTiming | None:
        for u in self.units:
            if u.slug == slug:
                return u
        return None

    @property
    def measured(self) -> bool:
        return self.table and self.error is None


def slug(cmd: str) -> str:
    toks = cmd.split()
    for tok in toks:
        if tok.endswith(".py"):
            return tok.rsplit("/", 1)[-1][:-3]
    if len(toks) > 1:
        return toks[1]
    return toks[0] if toks else "?"


def n_files(cmd: str) -> int:
    return sum(1 for tok in cmd.split() if tok.endswith(".py"))


def parse_summary_line(message: str) -> UnitTiming | None:
    """One summary-table row, or None for every other line of the job log.

    A row is `<STATE> <secs>s <command>` after the log's own `job\\tstep\\tts`
    prefix has been removed. The tail lines (last output lines of a unit) and
    the `N/M passed` count are not rows and must not become ones: a tail line
    reading `400 passed in 275.60s` would otherwise be a second, fake timing.
    """
    m = _ROW.match(_ANSI.sub("", message).strip())
    if not m:
        return None
    tag, secs, cmd = m.groups()
    state = tag.split("(", 1)[0]
    rc = tag[5:-1] if tag.startswith("FAIL(") else None
    return UnitTiming(slug(cmd), state, rc, float(secs), state == NO_VERDICT, n_files(cmd))


def parse_job_log(lines, job: str = DEFAULT_JOB) -> list[UnitTiming]:
    """The run_all.py summary rows of one job's lines, in recipe order.

    `lines` may be a whole-run log with every job's lines interleaved; only
    lines whose first tab-field is exactly `job` are read. If the table was
    printed more than once the last printing wins, once per unit.
    """
    rows: dict[str, UnitTiming] = {}
    for line in lines:
        parts = _ANSI.sub("", line).rstrip("\r\n").split("\t", 2)
        if len(parts) < 3 or parts[0] != job:
            continue
        ts = _TIMESTAMP.match(parts[2])
        row = parse_summary_line(parts[2][ts.end():] if ts else parts[2])
        if row is not None:
            rows[row.slug] = row
    return list(rows.values())


def _parse_ts(value: str | None) -> _dt.datetime | None:
    if not value:
        return None
    return _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _span_seconds(start: str | None, end: str | None) -> float | None:
    a, b = _parse_ts(start), _parse_ts(end)
    if a is None or b is None:
        return None
    return (b - a).total_seconds()


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sx = sum((x - mx) ** 2 for x in xs)
    sy = sum((y - my) ** 2 for y in ys)
    if sx == 0 or sy == 0:
        return None                      # no variance on one side: not a 0.0 correlation
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(sx * sy)


def _ranks(v: list[float]) -> list[float]:
    order = sorted(range(len(v)), key=lambda i: v[i])
    ranks = [0.0] * len(v)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1      # ties share the average rank
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Rank correlation, or None when there is nothing to correlate."""
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    return _pearson(_ranks(xs), _ranks(ys))


def _completed_values(records: list[JobTiming], which: str) -> list[float]:
    return [u.secs for r in records if (u := r.unit(which)) is not None and not u.censored]


def same_sha_groups(records: list[JobTiming], focus: str = BATCH,
                    reference: str = REFERENCE) -> list[dict]:
    """Re-runs: two or more executions of one commit, so only the runner differs.

    A group needs two *completed* reference values; a censored one is a cap,
    not a duration, and cannot be compared. Listed oldest attempt first.
    """
    by_sha: dict[str, list[JobTiming]] = {}
    for r in records:
        by_sha.setdefault(r.sha, []).append(r)
    out: list[dict] = []
    for sha, rs in by_sha.items():
        rs = sorted(rs, key=lambda r: r.attempt)
        refs = [u.secs for r in rs if (u := r.unit(reference)) is not None and not u.censored]
        if len(refs) < 2:
            continue
        foc = [u for r in rs if (u := r.unit(focus)) is not None]
        fvals = sorted(u.secs for u in foc if not u.censored)
        out.append({
            "sha": sha,
            "n": len(rs),
            "attempts": [r.attempt for r in rs],
            "run_ids": [r.run_id for r in rs],
            "conclusions": [r.conclusion for r in rs],
            "reference": {"values": refs, "spread": max(refs) / min(refs)},
            "focus": {"n": len(fvals), "n_censored": len(foc) - len(fvals),
                      "values": fvals, "censored_at": sorted(u.secs for u in foc if u.censored)},
        })
    return out


def _runner_bucket(name: str) -> str:
    """`GitHub Actions 100052299` -> `GitHub Actions`; anything else verbatim."""
    low = name.lower()
    return "GitHub Actions" if low.startswith("github actions") else name


def summarise(records: list[JobTiming], focus: str = BATCH, reference: str = REFERENCE) -> dict:
    """Per-unit distributions, the focus/reference ratio, and the re-run pairs.

    Censoring is the whole discipline here. A NO-VERDICT unit's `secs` is the
    cap it ran into, so it is counted (`n_censored`, `censored_at`) and kept
    out of every min/median/max; a job with no table (canceled before the
    summary printed) or with a read error contributes no unit seconds at all.
    """
    measured = [r for r in records if r.measured]
    units: dict[str, dict] = {}
    for r in measured:
        for u in r.units:
            d = units.setdefault(u.slug, {"n": 0, "n_censored": 0, "values": [], "censored_at": []})
            d["n"] += 1
            if u.censored:
                d["n_censored"] += 1
                d["censored_at"].append(u.secs)
            else:
                d["values"].append(u.secs)
    for d in units.values():
        vals = sorted(d.pop("values"))
        d["censored_at"] = sorted(set(d["censored_at"]))
        if vals:
            d.update(min=vals[0], median=statistics.median(vals), max=vals[-1])

    ratio = {"n": 0, "values": [], "n_censored_lb": 0, "censored_lb": []}
    pairs: list[tuple[float, float]] = []
    for r in measured:
        f, ref = r.unit(focus), r.unit(reference)
        if ref is None or ref.censored or f is None:
            continue
        if f.censored:
            ratio["n_censored_lb"] += 1
            ratio["censored_lb"].append(f.secs / ref.secs)      # the true ratio is at least this
        else:
            ratio["n"] += 1
            ratio["values"].append(f.secs / ref.secs)
            pairs.append((f.secs, ref.secs))
    vals = sorted(ratio.pop("values"))
    ratio["censored_lb"] = sorted(ratio["censored_lb"])
    if vals:
        ratio.update(min=vals[0], median=statistics.median(vals), max=vals[-1])

    gates = sorted(r.gate_secs for r in records if r.gate_secs is not None)
    overlaps = [(r.overlap_runs, r.unit(reference).secs) for r in measured
                if r.overlap_runs is not None and r.unit(reference) is not None and not r.unit(reference).censored]
    return {
        "focus": focus, "reference": reference,
        "total": len(records), "measured": len(measured),
        "no_table_cancelled": sum(1 for r in records if not r.table and r.conclusion == "cancelled" and r.error is None),
        "skipped": sum(1 for r in records if r.conclusion == "skipped"),
        "no_table_other": [(r.run_id, r.attempt, r.conclusion) for r in records
                           if not r.table and r.conclusion not in ("cancelled", "skipped") and r.error is None],
        "errors": [(r.run_id, r.attempt, r.error) for r in records if r.error],
        "no_verdict_units": [(r.run_id, r.attempt, u.slug, r.sha, r.branch)
                             for r in measured for u in r.units if u.state == NO_VERDICT],
        "fail_units": [(r.run_id, r.attempt, u.slug, u.rc) for r in measured for u in r.units if u.state == "FAIL"],
        "units": units,
        "ratio": ratio,
        "gate_secs": ({"n": len(gates), "min": gates[0], "median": statistics.median(gates), "max": gates[-1]}
                      if gates else None),
        "same_sha": same_sha_groups(measured, focus, reference),
        "spearman_log_focus_vs_log_reference": (spearman([math.log(a) for a, _ in pairs], [math.log(b) for _, b in pairs])
                                                if len(pairs) >= 3 else None),
        "spearman_overlap_vs_reference": (spearman([float(a) for a, _ in overlaps], [b for _, b in overlaps])
                                          if len(overlaps) >= 3 else None),
        "runners": sorted({_runner_bucket(r.runner_name) for r in records if r.runner_name}),
    }


# --------------------------------------------------------------------------
# collection (network)
# --------------------------------------------------------------------------

def _gh(args: list[str], cwd: pathlib.Path) -> str:
    try:
        proc = subprocess.run(["gh", *args], capture_output=True, text=True, cwd=cwd)
    except FileNotFoundError as exc:                       # no gh on PATH
        raise Refusal(f"`gh` is not installed or not on PATH: {exc}") from exc
    if proc.returncode != 0:
        tail = proc.stderr.strip().splitlines()
        raise Refusal(f"`gh {' '.join(args[:3])}` exited {proc.returncode}: "
                      f"{tail[-1] if tail else ''}")
    return proc.stdout


def _step_secs(job: dict, name: str) -> float | None:
    for step in job.get("steps") or []:
        if step.get("name") == name:
            return _span_seconds(step.get("started_at"), step.get("completed_at"))
    return None


def _overlap(run: dict, job: dict, all_runs: list[dict]) -> int | None:
    """Other runs of this workflow in flight while this job ran (a lower
    bound: only runs inside the fetched window are counted)."""
    s, e = _parse_ts(job.get("started_at")), _parse_ts(job.get("completed_at"))
    if s is None or e is None:
        return None
    n = 0
    for other in all_runs:
        if other.get("id") == run.get("id"):
            continue
        c, u = _parse_ts(other.get("created_at")), _parse_ts(other.get("updated_at"))
        if c is not None and u is not None and c <= e and u >= s:
            n += 1
    return n


def _collect_job(repo: str, run: dict, attempt: int, job_name: str, cwd: pathlib.Path,
                 all_runs: list[dict]) -> JobTiming | None:
    rid = run["id"]
    payload = json.loads(_gh(["api", f"repos/{repo}/actions/runs/{rid}/attempts/{attempt}/jobs"], cwd))
    hits = [j for j in payload.get("jobs") or [] if j.get("name") == job_name]
    if not hits:
        return None                                      # the job is not in this attempt at all
    j = hits[0]
    rec = JobTiming(
        run_id=rid, attempt=attempt, event=run.get("event", "?"), branch=run.get("head_branch", "?"),
        sha=(run.get("head_sha") or "")[:8], created_at=run.get("created_at", ""),
        conclusion=j.get("conclusion") or "?", runner_name=j.get("runner_name") or "",
        started_at=j.get("started_at"), completed_at=j.get("completed_at"),
        gate_secs=_step_secs(j, GATE_STEP), units=[], table=False,
        overlap_runs=_overlap(run, j, all_runs), error=None, job_id=j.get("id"))
    if not j.get("started_at"):
        return rec                                      # never started (skipped by the scope filter)
    try:
        log = _gh(["run", "view", str(rid), "--attempt", str(attempt), "--log"], cwd)
    except Refusal as exc:
        rec.error = str(exc)
        return rec
    rec.units = parse_job_log(log.splitlines(), job_name)
    rec.table = bool(rec.units)
    if not rec.table and rec.conclusion not in ("cancelled", "skipped", "timed_out"):
        rec.error = (f"job finished {rec.conclusion} but no run_all.py summary table "
                     f"is in its log -- its units are unknown, not zero")
    return rec


def collect(repo: str, workflow: str, job_name: str, limit: int, cwd: pathlib.Path,
            max_error_frac: float = 0.2) -> list[JobTiming]:
    """Every execution of `job_name` in the last `limit` runs of `workflow`,
    one record per attempt, newest run first. A job whose log cannot be read
    is kept with `error` set; if too many are, the window is refused rather
    than reported with a hole in it."""
    runs = json.loads(_gh(["api", f"repos/{repo}/actions/workflows/{workflow}/runs?per_page={limit}"], cwd))
    runs = runs.get("workflow_runs") or []
    if not runs:
        raise Refusal(f"no runs of {workflow} were returned for {repo}")
    completed = [r for r in runs if r.get("status") == "completed"]
    records: list[JobTiming] = []
    for run in completed:
        for attempt in range(1, int(run.get("run_attempt") or 1) + 1):
            rec = _collect_job(repo, run, attempt, job_name, cwd, completed)
            if rec is not None:
                records.append(rec)
    if not records:
        raise Refusal(f"no `{job_name}` job ran in the last {len(completed)} completed runs of {workflow}")
    errors = [r for r in records if r.error]
    if errors and len(errors) > max_error_frac * len(records):
        raise Refusal(f"{len(errors)} of {len(records)} `{job_name}` logs could not be read "
                      f"(more than {max_error_frac:.0%}); first: {errors[0].error}")
    return records


def _load(path: pathlib.Path) -> list[JobTiming]:
    try:
        raw = json.loads(path.read_text())
        if not isinstance(raw, list):
            raise ValueError("snapshot is not a list of records")
        return [JobTiming(**{**r, "units": [UnitTiming(**u) for u in r.get("units", [])]}) for r in raw]
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise Refusal(f"could not read snapshot {path}: {exc}") from exc


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------

def _fmt(v, nd=1, width=6):
    return f"{v:{width}.{nd}f}" if v is not None else " " * (width - 1) + "-"


def unclean(records: list[JobTiming]) -> list[str]:
    """Everything in `records` that is a lost verdict: a NO-VERDICT unit, or a
    job that ended at the job cap before its summary printed. FAIL is not
    listed -- an assertion is a result, not a missing one."""
    out = []
    for r in records:
        for u in r.units:
            if u.state == NO_VERDICT:
                out.append(f"run {r.run_id} attempt {r.attempt} {r.sha} {r.branch}: NO-VERDICT {u.slug} at {u.secs:.1f}s")
        if not r.table and r.conclusion in ("cancelled", "timed_out"):
            out.append(f"run {r.run_id} attempt {r.attempt} {r.sha} {r.branch}: job {r.conclusion} before any summary")
    return out


def render(records: list[JobTiming], summary: dict, *, list_records: bool = False) -> str:
    focus, reference = summary["focus"], summary["reference"]
    L = [f"{summary['total']} `{DEFAULT_JOB}` execution(s): {summary['measured']} with a run_all.py table, "
         f"{summary['no_table_cancelled']} cancelled before one, {summary['skipped']} skipped by the scope filter, "
         f"{len(summary['errors'])} unreadable"]
    if records:
        stamps = sorted(r.created_at for r in records if r.created_at)
        L.append(f"window: {stamps[0]} -> {stamps[-1]}   runners: {', '.join(summary['runners']) or '?'}")
    g = summary["gate_secs"]
    if g:
        L.append(f"gate step wall time (API): n={g['n']} min {g['min']:.0f}s / median {g['median']:.0f}s / max {g['max']:.0f}s")
    L += ["", "per-unit wall clock, completed executions only (a NO-VERDICT's secs is the cap, not a duration):",
          f"  {'unit':32s} {'n':>3s} {'cens':>4s} {'min':>7s} {'median':>7s} {'max':>7s}  cap"]
    for slug_ in sorted(summary["units"], key=lambda s: -(summary["units"][s].get("median") or 0)):
        d = summary["units"][slug_]
        if d["n"] == 0:
            continue
        mark = "  <- focus" if slug_ == focus else ("  <- reference" if slug_ == reference else "")
        L.append(f"  {slug_:32s} {d['n']:3d} {d['n_censored']:4d} {_fmt(d.get('min'))} {_fmt(d.get('median'))} "
                 f"{_fmt(d.get('max'))}  {','.join(f'{c:.0f}' for c in d['censored_at']) or '-'}{mark}")
    r = summary["ratio"]
    L += ["", f"ratio {focus}/{reference} (same job, same machine):"]
    if r["n"]:
        L.append(f"  completed: n={r['n']} min {r['min']:.3f} / median {r['median']:.3f} / max {r['max']:.3f}")
    else:
        L.append("  completed: n=0")
    if r["n_censored_lb"]:
        lbs = r["censored_lb"]
        L.append(f"  censored (lower bounds, focus hit the cap): n={r['n_censored_lb']} min {lbs[0]:.3f} / max {lbs[-1]:.3f}")
    rho = summary["spearman_log_focus_vs_log_reference"]
    L.append(f"  spearman(log focus, log reference) = {rho:.3f}" if rho is not None else
             "  spearman(log focus, log reference) = - (fewer than 3 completed pairs)")
    L += ["", "same-SHA re-runs (source and recipe fixed; only the runner differs):"]
    groups = summary["same_sha"]
    if not groups:
        L.append("  none")
    for grp in groups:
        ref_v, fo = grp["reference"], grp["focus"]
        line = (f"  {grp['sha']} attempts {grp['attempts']} {grp['conclusions']}: "
                f"{reference} {' -> '.join(f'{v:.1f}' for v in ref_v['values'])}s (x{ref_v['spread']:.2f}); "
                f"{focus} ")
        line += " -> ".join(f"{v:.1f}" for v in fo["values"]) + "s" if fo["n"] else ""
        if fo["n_censored"]:
            line += (" + " if fo["n"] else "") + f">={fo['censored_at'][0]:.0f}s censored x{fo['n_censored']}"
        L.append(line)
    ov = summary["spearman_overlap_vs_reference"]
    L += ["", f"concurrency proxy: spearman(overlap_runs, {reference} secs) = "
          + (f"{ov:.3f}" if ov is not None else "- (fewer than 3 points)") +
          "   [a count of other workflow runs in flight; it does not see the runner's host]"]
    L += ["", f"NO-VERDICT units: {len(summary['no_verdict_units'])}"]
    for run_id, att, slug_, sha, branch in summary["no_verdict_units"]:
        L.append(f"  run {run_id} attempt {att} {sha} {branch}: {slug_}")
    L.append(f"FAIL units (assertions -- results, not lost verdicts): {len(summary['fail_units'])}")
    for run_id, att, slug_, rc in summary["fail_units"]:
        L.append(f"  run {run_id} attempt {att} {slug_} rc={rc}")
    if summary["no_table_other"]:
        L.append(f"jobs with no table and no error (unexpected): {summary['no_table_other']}")
    if summary["errors"]:
        L.append("unreadable logs:")
        for run_id, att, msg in summary["errors"]:
            L.append(f"  run {run_id} attempt {att}: {msg}")
    if list_records:
        L += ["", f"{'run':>12s} att {'sha':8s} {'event':12s} {'branch':28s} {'concl':9s} "
                 f"{'ref_s':>6s} {'foc_s':>6s} {'ratio':>6s} {'gate_s':>6s} ovl  created"]
        for rec in records:
            f, ref = rec.unit(focus), rec.unit(reference)
            fs = f"{f.secs:6.1f}" if f else "     -"
            rs = f"{ref.secs:6.1f}" if ref else "     -"
            ra = f"{f.secs / ref.secs:6.3f}" if f and ref and not f.censored and not ref.censored else (
                f">={f.secs / ref.secs:5.3f}" if f and ref and f.censored and not ref.censored else "     -")
            L.append(f"{rec.run_id:12d} {rec.attempt:3d} {rec.sha:8s} {rec.event:12s} {rec.branch[:28]:28s} "
                     f"{rec.conclusion:9s} {rs} {fs} {ra} {_fmt(rec.gate_secs, 0):>6s} "
                     f"{_fmt(rec.overlap_runs, 0, 3):>3s}  {rec.created_at}")
    return "\n".join(L)


def _record_path(name) -> None:
    """Check the `--snapshot` destination BEFORE anything is collected, or
    Refusal (#2098).

    A missing parent directory is created here, as `fpga/selected_preset.py`
    creates `--out`'s. A destination that is a directory, or whose parent
    cannot be made, is refused now -- a collection walks the live API, and
    none of it should run for a record that cannot be written -- rather than
    by `OSError` from the write afterwards, which exited 1 with a traceback:
    the status `--require-clean` uses for a measured NO-VERDICT.

    What this cannot see -- a parent that exists but is not writable, a disk
    that fills -- is left to the guard around the write.
    """
    if not name:
        return
    out = pathlib.Path(name)
    try:
        is_dir = out.is_dir()
        if not is_dir:
            out.parent.mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError) as exc:
        raise Refusal(f"--snapshot {str(name)!r}: no record can be written there: {exc}") from exc
    if is_dir:
        raise Refusal(f"--snapshot {str(name)!r} names an existing directory; "
                      "name the record file")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default="2AMLogic/gf180-parasynth")
    ap.add_argument("--workflow", default=DEFAULT_WORKFLOW)
    ap.add_argument("--job", default=DEFAULT_JOB)
    ap.add_argument("--limit", type=int, default=100,
                    help="how many recent runs to pull, every attempt of each (default 100)")
    ap.add_argument("--focus", default=BATCH, help="the unit under suspicion, by its slug")
    ap.add_argument("--reference", default=REFERENCE,
                    help="the sibling unit it is compared against, by its slug")
    ap.add_argument("--window", type=int, default=40,
                    help="how many most-recent executions --require-clean inspects")
    ap.add_argument("--require-clean", action="store_true",
                    help="exit 1 if the window contains a NO-VERDICT unit or a capped job")
    ap.add_argument("--max-error-frac", type=float, default=0.2,
                    help="refuse the window if more than this share of logs is unreadable")
    ap.add_argument("--snapshot", type=pathlib.Path,
                    help="also write the collected records here (a missing parent directory is created)")
    ap.add_argument("--offline", type=pathlib.Path, help="analyse a previously written snapshot, no network")
    ap.add_argument("--list", action="store_true", help="print every execution as a row")
    a = ap.parse_args(argv)

    unwritten = None
    try:
        if a.snapshot:
            _record_path(a.snapshot)
        if a.require_clean and a.window <= 0:
            raise Refusal("--window must be positive to score any executions")
        if a.offline:
            records = _load(a.offline)
        else:
            records = collect(a.repo, a.workflow, a.job, a.limit, REPO_ROOT, a.max_error_frac)
        if not records:
            raise Refusal("no records: an empty window is not a clean one")
        if a.snapshot:
            try:
                a.snapshot.write_text(json.dumps([asdict(r) for r in records], indent=1) + "\n")
            except OSError as exc:
                # Held, not raised: the report is printed with or without the
                # record, and the refusal comes after it (#2098). The write
                # stays here, before the checks below, so a run that is
                # refused later still leaves the records it collected.
                unwritten = exc
        summary = summarise(records, a.focus, a.reference)
        if a.focus not in summary["units"]:
            raise Refusal(f"focus unit {a.focus!r} appears in no measured record; the recipe's "
                          f"unit slugs are {sorted(summary['units'])}")
        if a.require_clean and len(records) < a.window:
            raise Refusal(f"asked for a clean window of {a.window} executions but only {len(records)} "
                          f"are available -- raise --limit. A short window is not a clean window.")
    except Refusal as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        if unwritten is not None:
            print(f"REFUSED: --snapshot {a.snapshot} not written as well: {unwritten}",
                  file=sys.stderr)
        return REFUSED

    print(render(records, summary, list_records=a.list))
    if unwritten is not None:
        # The report is on stdout above; its snapshot is the evidence, and a
        # window whose record is gone is neither clean nor unclean (#2098).
        print(f"REFUSED: report printed above; --snapshot {a.snapshot} not written: "
              f"{unwritten}", file=sys.stderr)
        return REFUSED
    if a.require_clean:
        bad = unclean(records[:a.window])
        if bad:
            print(f"\nFAIL: {len(bad)} lost verdict(s) in the last {a.window} executions:", file=sys.stderr)
            for line in bad:
                print(f"  {line}", file=sys.stderr)
            return 1
        print(f"\nPASS: no NO-VERDICT in the last {a.window} executions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
