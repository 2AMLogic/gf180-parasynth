#!/usr/bin/env python3
"""Derive the tables docs/ci-tools-xdist-measurement-2026-11-28.md quotes, from
the downloaded artifacts of runs 46155589951 (xdist) and 46162689576 (shards).

    python derive.py <artifacts-dir> <out-dir> <jobs-xdist.json> <jobs-shards.json>

`table.md` comes from tools/xdist_run_table.py; the other three read the
`--durations=0` block of each pytest.log. Every number in the document is one
of these script's outputs; none was typed from a log.
"""
import collections
import pathlib
import re
import statistics
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "tools"))
import xdist_run_table as xt  # noqa: E402

ART, OUT = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
JOBS = [sys.argv[3], sys.argv[4]]
LINE = re.compile(r"^(\d+\.\d+)s (setup|call|teardown)\s+(.*)$")
xt.CANDIDATES = ("n1", "n2", "nauto", "n8", "s0", "s1")


def phases(run):
    """nodeid -> {phase: seconds} from the durations block."""
    out = collections.defaultdict(dict)
    for l in (ART / run / "pytest.log").read_text(errors="replace").splitlines():
        m = LINE.match(l)
        if m:
            out[m.group(3)][m.group(2)] = float(m.group(1))
    return out


def cpu_model(run):
    for l in (ART / run / "env.txt").read_text().splitlines():
        if l.startswith("lscpu"):
            return re.search(r"Model name:\s+(.*?) \| Thread", l).group(1).strip()
    return "(unknown)"


def runs_of(cand):
    return sorted(p.name for p in ART.glob(f"xdist-{cand}-r*"))


def main():
    dirs = sorted(ART.glob("xdist-*"), key=lambda d: xt.read_run_dir(d).key)
    jobs = {}
    for j in JOBS:
        jobs.update(xt.read_jobs(j))
    table = xt.build_table(dirs, jobs)
    (OUT / "table.md").write_text(table.render() + "\n\n" + table.render_summary() + "\n")
    print(table.render_summary())

    # ---- the work profile, from the single-process run with the least wall ----
    n1 = min(runs_of("n1"), key=lambda r: xt.read_pytest_log(
        (ART / r / "pytest.log").read_text(errors="replace"))["pytest_s"])
    ph = phases(n1)
    calls = {n: p.get("call", 0.0) for n, p in ph.items()}
    tot = sum(calls.values())
    longest_n, longest = max(calls.items(), key=lambda kv: kv[1])
    own = xt.read_pytest_log((ART / n1 / "pytest.log").read_text(errors="replace"))["pytest_s"]
    lines = [f"single-process reference: `{n1}`, pytest's own {own:.1f} s", "",
             "| tests with a printed duration | summed call (s) | longest one test (s) | >= 60 s | >= 30 s | >= 10 s |",
             "|---|---|---|---|---|---|",
             f"| {len(calls)} | {tot:.0f} | {longest:.0f} | {sum(v >= 60 for v in calls.values())} | "
             f"{sum(v >= 30 for v in calls.values())} | {sum(v >= 10 for v in calls.values())} |", "",
             f"the longest: `{longest_n}`", "",
             "| workers | summed call / N (s) | floored by the longest test (s) | as minutes |", "|---|---|---|---|"]
    for n in (1, 2, 4, 8):
        ideal = max(longest, tot / n)
        lines.append(f"| {n} | {tot / n:.0f} | {ideal:.0f} | {ideal / 60:.1f} |")
    (OUT / "work-profile.md").write_text("\n".join(lines) + "\n")

    # ---- deadline headroom of the tests that carry a subprocess timeout ------
    watched = [n for n in calls if (
        "tools/test_audio_distance_floor_refusal.py::test_full_run" in n
        and ("bd5050" in n or "mt50" in n or "unmodified" in n))
        or ("tools/test_m1a_refusal_boundaries.py::test_unqualified_reference_manifest_exits_refused_2[analyse_m1a" in n)
        or ("tools/test_m1a_refusal_boundaries.py::test_non_refused_exception_is_not_swallowed_by_cli_boundary[analyse_m1a" in n)]
    deadline = {n: (300 if "audio_distance" in n else 180) for n in watched}
    lines = ["call seconds per repeat; `timed out` is the test's own `subprocess.run(timeout=)` expiring", "",
             "| test (single-process s) | deadline | n1 | n2 r1 / r2 / r3 | nauto r1 / r2 / r3 | n8 r1 / r2 / r3 | its shard, r1 / r2 / r3 |",
             "|---|---|---|---|---|---|---|"]
    worst = collections.defaultdict(float)
    for n in watched:
        row = [f"`{n.split('::')[0].split('/')[-1]}::{n.split('::')[1][:58]}` ({calls[n]:.0f} s)", f"{deadline[n]} s"]
        base = None
        for cand in ("n1", "n2", "nauto", "n8", "shard"):
            cands = runs_of(cand) if cand != "shard" else runs_of("s0") + runs_of("s1")
            vals = []
            for r in cands:
                v = phases(r).get(n, {}).get("call")
                if v is None:
                    continue
                if cand == "n1" and base is None:
                    base = v
                timed_out = v >= deadline[n]
                vals.append("timed out" if timed_out else f"{v:.0f}")
                worst[cand] = max(worst.get(cand, 0.0), v / deadline[n])
            row.append(" / ".join(vals) if vals else "(not in this shard)")
        lines.append("| " + " | ".join(row) + " |")
    lines += ["", "worst observed call time as a share of the deadline, by candidate:", ""]
    for cand in ("n1", "n2", "nauto", "n8", "shard"):
        if cand in worst:
            lines.append(f"- {cand}: {worst[cand]:.0%}")
    (OUT / "deadline-headroom.md").write_text("\n".join(lines) + "\n")

    # ---- the spread on one commit: pairwise per-test ratios among n1 runs ----
    n1s = runs_of("n1")
    lines = ["| run | CPU | pytest's own (s) | CPU user+sys (s) | CPU / wall |", "|---|---|---|---|---|"]
    for r in n1s:
        log = xt.read_pytest_log((ART / r / "pytest.log").read_text(errors="replace"))
        lines.append(f"| {r[-3:]} | {cpu_model(r)} | {log['pytest_s']:.1f} | {log['cpu_s']:.0f} | "
                     f"{log['cpu_s'] / log['step_wall_s']:.2f} |")
    lines += ["", "per-test ratio of call seconds, fast run -> slow run (tests of >= 1 s in the fast run):", ""]
    for x, y in [(x, y) for x in n1s for y in n1s if x < y]:
        # orient every pair fast -> slow, so each ratio is how many times slower
        tot = lambda r: sum(p.get("call", 0) for p in phases(r).values())
        a, b = (x, y) if tot(x) <= tot(y) else (y, x)
        pa, pb = phases(a), phases(b)
        common = [n for n in pa if n in pb and pa[n].get("call", 0) >= 1.0 and "call" in pb[n]]
        ratios = sorted(pb[n]["call"] / pa[n]["call"] for n in common)
        q = lambda p: ratios[min(len(ratios) - 1, int(p * len(ratios)))]
        sa, sb = (sum(p.get("call", 0) for p in ph_.values()) for ph_ in (pa, pb))
        lines.append(f"- `{a[-3:]}` -> `{b[-3:]}`: {len(common)} tests, p10 {q(.10):.2f}, median {q(.50):.2f}, "
                     f"p90 {q(.90):.2f}; summed call {sa:.0f} -> {sb:.0f} s ({sb / sa:.2f})")
    (OUT / "spread.md").write_text("\n".join(lines) + "\n")

    # ---- skips by recorded reason: the baseline against one nauto and one n8 run ----
    import xml.etree.ElementTree as ET
    cols = (("n1", "xdist-n1-r1"), ("nauto", "xdist-nauto-r1"), ("n8", "xdist-n8-r1"),
            ("s0", "xdist-s0-r1"), ("s1", "xdist-s1-r1"))
    by_reason = collections.defaultdict(collections.Counter)
    for col, run in cols:
        for tc in ET.parse(ART / run / "junit.xml").getroot().iter("testcase"):
            sk = tc.find("skipped")
            if sk is not None and sk.get("type") == "pytest.skip":
                by_reason[sk.get("message", "")][col] += 1
    lines = ["skipped testcases by the reason recorded in junit, first repeat of each candidate "
             "(for the shards, the two halves sum to the single process)", "",
             "| reason | " + " | ".join(c for c, _ in cols) + " |", "|---|" + "---|" * len(cols)]
    for msg, c in sorted(by_reason.items(), key=lambda kv: (-kv[1]["nauto"], kv[0])):
        lines.append(f"| `{msg}` | " + " | ".join(str(c[col]) for col, _ in cols) + " |")
    (OUT / "skips.md").write_text("\n".join(lines) + "\n")

    # ---- whole-job minutes by CPU model x candidate: runner against candidate ----
    by = {}
    for r in table.runs:
        by.setdefault(cpu_model("xdist-" + r.label + "-r" + str(r.repeat)), {}).setdefault(
            r.label, []).append(r.job_wall_min)
    cands = ["n1", "n2", "nauto", "n8", "s0", "s1"]
    lines = ["whole-job minutes, CPU model down and candidate across; a cell lists every run of that pair", "",
             "| CPU | " + " | ".join(cands) + " |", "|---|" + "---|" * len(cands)]
    for model in sorted(by, key=lambda m: -max(by[m].get("n1", [0]))):
        cells = []
        for c in cands:
            v = sorted(x for x in by[model].get(c, []) if x is not None)
            cells.append(", ".join(f"{x:.1f}" for x in v) if v else "-")
        lines.append(f"| {model} | " + " | ".join(cells) + " |")
    lines += ["", "same-model speed-up against the single process (n1 wall / candidate wall):", ""]
    for model, d in by.items():
        if "n1" not in d:
            continue
        base = statistics.median(d["n1"])
        parts = [f"{c} {base / statistics.median(v):.2f}x" for c, v in d.items() if c != "n1" and v]
        if parts:
            lines.append(f"- {model}: " + ", ".join(parts))
    (OUT / "by-model.md").write_text("\n".join(lines) + "\n")
    print("wrote table.md work-profile.md deadline-headroom.md spread.md by-model.md skips.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
