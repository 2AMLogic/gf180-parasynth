#!/usr/bin/env python3
"""Per-step wall time for a GitHub Actions workflow, grouped by runner class.

WHY. Issue #264 reported that `moog-acceptance.yml`'s `acceptance` job was
being cancelled at its 20-minute cap on ~20% of runs, and offered two
hypotheses that run-level timing cannot tell apart:

  (a) the `push` and `pull_request` copies of the same commit contend, so the
      loser of the race runs out of clock; or
  (b) the suite itself has grown until an uncontended run is inside the
      margin of error of the cap.

Neither is testable from `gh run list`, because a run's `createdAt -> updatedAt`
span folds queue time, setup and every step into one number. **The
discriminating measurement is per-step time on the same steps across runs**:
under (b) the slow runs are slow in the step that grew; under a whole-machine
slowdown every step stretches by the same factor, including the ones that are
two seconds long. This script pulls `.../actions/runs/<id>/jobs` for each run,
which carries `started_at`/`completed_at` per step, and lays the steps side by
side.

It also records `runner_name`, which is what actually settled #264: every
cancellation sat on a GitHub-hosted runner and none on a Blacksmith one. That
column is not visible anywhere in `gh run list` output.

REFUSED IS AN OUTCOME. This queries a live API, so it can fail to answer. It
distinguishes three exits, because a check that cannot run must never look
like a check that passed:

    0  measured, and the window is clean
    1  measured, and the window contains a timeout cancellation
    2  REFUSED -- could not measure (no `gh`, API error, window too short)

Network-dependent, so it is NOT part of `make verify`; run it by hand when you
need the evidence. Its own tests (`tools/test_ci_acceptance_timing.py`) are
offline and parse fixture payloads.

Usage:
    python tools/ci_acceptance_timing.py --limit 100
    python tools/ci_acceptance_timing.py --require-clean --window 40
    python tools/ci_acceptance_timing.py --snapshot /tmp/snap.json      # write
    python tools/ci_acceptance_timing.py --offline /tmp/snap.json       # read
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
import statistics
import subprocess
import sys
from dataclasses import dataclass, asdict, field

DEFAULT_WORKFLOW = "moog-acceptance.yml"
DEFAULT_JOB = "acceptance"
REFUSED = 2


class Refusal(Exception):
    """The measurement could not be taken. Not a failure -- an absence."""


# --------------------------------------------------------------------------
# pure helpers (everything below this line is tested offline)
# --------------------------------------------------------------------------

def runner_class(runner_name: str | None) -> str:
    """Bucket a job's `runner_name` into a runner class.

    Blacksmith names itself (`blacksmith-<id>-4vcpu`); GitHub-hosted runners
    are `GitHub Actions <n>`. Anything else is reported verbatim rather than
    guessed at, so a third class shows up as itself instead of being silently
    folded into one of these two.
    """
    if not runner_name:
        return "unknown"
    low = runner_name.lower()
    if "blacksmith" in low:
        return "blacksmith"
    if low.startswith("github actions"):
        return "github-hosted"
    return runner_name


def _parse_ts(value: str | None) -> _dt.datetime | None:
    if not value:
        return None
    return _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _span_seconds(start: str | None, end: str | None) -> float | None:
    a, b = _parse_ts(start), _parse_ts(end)
    if a is None or b is None:
        return None
    return (b - a).total_seconds()


@dataclass
class JobRecord:
    run_id: int
    event: str
    branch: str
    sha: str
    created_at: str
    conclusion: str
    runner: str
    runner_name: str
    queue_s: float | None
    job_minutes: float | None
    steps: dict[str, float] = field(default_factory=dict)

    @property
    def runner_bucket(self) -> str:
        return runner_class(self.runner_name)


def parse_jobs(run_meta: dict, jobs_payload: dict, job_name: str) -> list[JobRecord]:
    """Turn one run's `/jobs` payload into `JobRecord`s for the named job.

    Pure: no network, no clock. A step with a missing timestamp (it never
    started, or is still running) is omitted from `steps` rather than recorded
    as zero -- a zero would read as "instant" in every median downstream.
    """
    out: list[JobRecord] = []
    for job in jobs_payload.get("jobs") or []:
        if job.get("name") != job_name:
            continue
        steps: dict[str, float] = {}
        for step in job.get("steps") or []:
            dur = _span_seconds(step.get("started_at"), step.get("completed_at"))
            if dur is not None:
                steps[step.get("name", "?")] = dur
        job_s = _span_seconds(job.get("started_at"), job.get("completed_at"))
        out.append(JobRecord(
            run_id=run_meta.get("databaseId") or job.get("run_id"),
            event=run_meta.get("event", "?"),
            branch=run_meta.get("headBranch", "?"),
            sha=(run_meta.get("headSha") or "")[:8],
            created_at=run_meta.get("createdAt", ""),
            conclusion=job.get("conclusion") or "?",
            runner=runner_class(job.get("runner_name")),
            runner_name=job.get("runner_name") or "",
            queue_s=_span_seconds(job.get("created_at"), job.get("started_at")),
            job_minutes=(job_s / 60.0) if job_s is not None else None,
            steps=steps,
        ))
    return out


def summarise(records: list[JobRecord]) -> dict:
    """Cancellation rate and step timing, split by runner class.

    Step statistics deliberately exclude cancelled jobs: a cancelled job's last
    step was truncated by the cap, so including it would bias the very number
    being used to argue about the cap.
    """
    buckets: dict[str, list[JobRecord]] = {}
    for r in records:
        buckets.setdefault(r.runner_bucket, []).append(r)

    summary: dict = {"total": len(records), "by_runner": {}}
    for name, rows in sorted(buckets.items()):
        cancelled = [r for r in rows if r.conclusion == "cancelled"]
        finished = [r for r in rows if r.conclusion != "cancelled"]
        step_names: list[str] = []
        for r in finished:
            for s in r.steps:
                if s not in step_names:
                    step_names.append(s)
        steps = {}
        for s in step_names:
            vals = sorted(r.steps[s] for r in finished if s in r.steps)
            if vals:
                steps[s] = {"n": len(vals), "min": vals[0], "max": vals[-1],
                            "median": statistics.median(vals)}
        mins = sorted(r.job_minutes for r in finished if r.job_minutes is not None)
        summary["by_runner"][name] = {
            "n": len(rows),
            "cancelled": len(cancelled),
            "cancelled_pct": 100.0 * len(cancelled) / len(rows) if rows else 0.0,
            "job_minutes": ({"min": mins[0], "median": statistics.median(mins),
                             "max": mins[-1], "n": len(mins)} if mins else None),
            "steps": steps,
        }
    return summary


def timeout_cancellations(records: list[JobRecord], window: int) -> list[JobRecord]:
    """The cancelled jobs among the `window` most recent records.

    `records` must be newest-first, which is the order `gh run list` returns.
    """
    return [r for r in records[:window] if r.conclusion == "cancelled"]


# --------------------------------------------------------------------------
# collection (network)
# --------------------------------------------------------------------------

def _gh(args: list[str], cwd: pathlib.Path) -> str:
    try:
        proc = subprocess.run(["gh", *args], capture_output=True, text=True, cwd=cwd)
    except FileNotFoundError as exc:                       # no gh on PATH
        raise Refusal(f"`gh` is not installed: {exc}") from exc
    if proc.returncode != 0:
        raise Refusal(f"`gh {' '.join(args)}` exited {proc.returncode}: "
                      f"{proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else ''}")
    return proc.stdout


def collect(repo: str, workflow: str, job_name: str, limit: int,
            cwd: pathlib.Path) -> list[JobRecord]:
    fields = "databaseId,event,conclusion,status,headSha,headBranch,createdAt"
    runs = json.loads(_gh(["run", "list", f"--workflow={workflow}",
                           "--limit", str(limit), "--json", fields], cwd))
    records: list[JobRecord] = []
    for run in runs:
        if run.get("status") != "completed":
            continue                                       # still in flight
        payload = json.loads(
            _gh(["api", f"repos/{repo}/actions/runs/{run['databaseId']}/jobs"], cwd))
        records.extend(parse_jobs(run, payload, job_name))
    if not records:
        raise Refusal(f"no completed `{job_name}` jobs found in the last "
                      f"{limit} runs of {workflow}")
    return records


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------

def render(records: list[JobRecord], summary: dict) -> str:
    lines = [f"{summary['total']} `{DEFAULT_JOB}` job(s) measured "
             f"({records[-1].created_at} -> {records[0].created_at})", ""]
    for name, s in summary["by_runner"].items():
        lines.append(f"{name}: n={s['n']}  cancelled={s['cancelled']} "
                     f"({s['cancelled_pct']:.0f}%)")
        if s["job_minutes"]:
            j = s["job_minutes"]
            lines.append(f"  job minutes (completed only) "
                         f"min {j['min']:.1f} / median {j['median']:.1f} / max {j['max']:.1f}"
                         f"  n={j['n']}")
        for step, st in s["steps"].items():
            if st["median"] < 20:
                continue                                   # setup noise
            lines.append(f"  {st['median']:6.0f}s median  "
                         f"[{st['min']:.0f}-{st['max']:.0f}]  {step}")
        lines.append("")
    cancelled = [r for r in records if r.conclusion == "cancelled"]
    lines.append(f"cancelled jobs: {len(cancelled)}")
    for r in cancelled:
        lines.append(f"  {r.run_id} {r.event:<13} {r.runner:<14} {r.created_at} {r.branch}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default="2AMLogic/gf180-parasynth")
    ap.add_argument("--workflow", default=DEFAULT_WORKFLOW)
    ap.add_argument("--job", default=DEFAULT_JOB)
    ap.add_argument("--limit", type=int, default=60,
                    help="how many recent runs to pull (default 60)")
    ap.add_argument("--window", type=int, default=40,
                    help="how many recent jobs --require-clean inspects "
                         "(default 40, i.e. ~20 push+pull_request pairs)")
    ap.add_argument("--require-clean", action="store_true",
                    help="exit 1 if the window contains a timeout cancellation")
    ap.add_argument("--runner-class", default=None,
                    help="restrict to one runner class (e.g. blacksmith). "
                         "Printed in the report -- a filtered window is a "
                         "narrower claim, not a cleaner one.")
    ap.add_argument("--snapshot", type=pathlib.Path,
                    help="also write the collected records here")
    ap.add_argument("--offline", type=pathlib.Path,
                    help="analyse a previously written snapshot, no network")
    args = ap.parse_args(argv)

    try:
        if args.offline:
            raw = json.loads(args.offline.read_text())
            records = [JobRecord(**r) for r in raw]
        else:
            records = collect(args.repo, args.workflow, args.job, args.limit,
                              pathlib.Path(__file__).resolve().parent.parent)
        if args.snapshot:
            args.snapshot.write_text(json.dumps([asdict(r) for r in records], indent=1))
        if args.runner_class:
            records = [r for r in records if r.runner_bucket == args.runner_class]
            print(f"(restricted to runner class {args.runner_class!r})")
            if not records:
                raise Refusal(f"no jobs on runner class {args.runner_class!r}")
        if args.require_clean and len(records) < args.window:
            raise Refusal(f"asked for a clean window of {args.window} jobs but only "
                          f"{len(records)} are available -- raise --limit. A short "
                          f"window is not a clean window.")
    except Refusal as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return REFUSED

    print(render(records, summarise(records)))

    if args.require_clean:
        bad = timeout_cancellations(records, args.window)
        if bad:
            print(f"\nFAIL: {len(bad)} cancellation(s) in the last {args.window} jobs",
                  file=sys.stderr)
            return 1
        print(f"\nPASS: no cancellations in the last {args.window} jobs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
