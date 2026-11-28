#!/usr/bin/env python3
"""Where one pytest run's time went, read from its ``--durations=0`` block.

Issue #1816: the ``tools`` job of ``rungs.yml`` runs ``pytest tools/ -q`` by
directory, so every test file lands in it and nobody knew which files made up
its 60-80 minutes. This tool turns one hosted run's ``--durations=0`` block
into per-test, per-file and per-candidate-group totals.

It REFUSES (exit 2) rather than reports on any input that could yield a false
table. A line silently dropped is time silently reassigned to "everything
else", and the table still looks like data, so a malformed or truncated line
inside the block, a second block, a duration line after the block ended and an
empty block are all refusals by name.

What the text cannot see is stated rather than assumed: with no ``--junit``
oracle the report carries ``completeness: None``, because a complete-looking
block with one test removed is indistinguishable from a run in which that test
was fast. With ``--junit`` the two are reconciled test by test (see
``check_junit``).

Usage::

    python tools/pytest_durations_report.py --durations run.log --json r.json \\
        --junit junit.xml --group-file "iverilog-5=list.txt" \\
        --group-grep "subprocess=^import subprocess" --scan-dir tools
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import refusal

REFUSED = 2
PHASES = ("setup", "call", "teardown")

HEADER_RE = re.compile(r"^=+\s*slowest(?:\s+\d+)?\s+durations\s*=+$")
ROW_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)s\s+(setup|call|teardown)\s+(\S.*?)\s*$")
TRAILER_RE = re.compile(r"^\((\d+) durations < ([\d.]+)s hidden\.")
SEPARATOR_RE = re.compile(r"^=+ .+ =+$")
SUMMARY_RE = re.compile(
    r"^(?P<counts>(?:\d+ [a-z]+(?:, )?)+) in (?P<secs>[\d.]+)s(?: \([^)]*\))?$"
)
LOG_PREFIX_RE = re.compile(
    r"^[^\t]*\t[^\t]*\t\d{4}-\d{2}-\d{2}T[\d:.]+(?:Z|[+-]\d{2}:\d{2}) ?"
)

#: A junit ``time`` is the sum of setup+call+teardown at 3 decimals. A
#: ``--durations`` row is one phase at 2 decimals and phases under 0.005 s are
#: not printed. So per test the two may differ by at most 3 x 0.005 (rounding,
#: or a phase hidden instead of rounded) + 0.0005 (junit rounding).
JUNIT_PER_TEST_TOL_S = 3 * 0.005 + 0.0005


class Refused(refusal.Refused, Exception):
    """The input cannot yield a trustworthy table; no number is reported."""


@dataclass
class Block:
    """The one ``--durations`` block: its rows, what pytest said around them."""

    rows: list[tuple[float, str, str]]
    hidden: int | None
    summary: dict | None
    lines: list[str]

    @property
    def text(self) -> str:
        """The block as pytest printed it, header through the summary line."""
        return "\n".join(self.lines) + "\n"


def parse(text: str) -> Block:
    lines = [LOG_PREFIX_RE.sub("", line) for line in text.splitlines()]
    headers = [i for i, line in enumerate(lines) if HEADER_RE.match(line)]
    if not headers:
        raise Refused("no --durations block found in the input")
    if len(headers) > 1:
        raise Refused(f"{len(headers)} --durations blocks found; expected exactly one")
    rows: list[tuple[float, str, str]] = []
    i = headers[0] + 1
    while i < len(lines):
        m = ROW_RE.match(lines[i])
        if not m:
            break
        rows.append((float(m.group(1)), m.group(2), m.group(3)))
        i += 1
    if not rows:
        raise Refused("the --durations block holds no duration lines")
    if i < len(lines):
        line = lines[i]
        ends_block = (
            not line.strip()
            or TRAILER_RE.match(line)
            or SEPARATOR_RE.match(line)
            or SUMMARY_RE.match(line)
        )
        if not ends_block:
            raise Refused(f"malformed line inside the block (line {i + 1}): {line!r}")
    for j in range(i + 1, len(lines)):
        if ROW_RE.match(lines[j]) or HEADER_RE.match(lines[j]):
            raise Refused(
                f"duration line after the block ended (line {j + 1}): {lines[j]!r}"
            )
    hidden = None
    summary = None
    last = i - 1
    for k in range(i, len(lines)):
        t = TRAILER_RE.match(lines[k])
        if t and hidden is None:
            hidden = int(t.group(1))
            last = k
        if SUMMARY_RE.match(lines[k]):
            summary = _summary(lines[k])
            last = k
            break
    return Block(rows, hidden, summary, lines[headers[0] : last + 1])


def _summary(line: str) -> dict:
    m = SUMMARY_RE.match(line)
    assert m is not None
    counts = re.findall(r"(\d+) ([a-z]+)", m.group("counts"))
    passed = 0
    skipped = 0
    other: dict[str, int] = {}
    for n, word in counts:
        if word == "passed":
            passed += int(n)
        elif word == "skipped":
            skipped += int(n)
        else:
            other[word] = other.get(word, 0) + int(n)
    return {
        "passed": passed,
        "skipped": skipped,
        "other": other,
        "total_s": float(m.group("secs")),
    }


def _counts_to_reach(shares: list[float]) -> dict[str, int]:
    out = {}
    for pct in (50, 80, 90):
        cum = 0.0
        n = 0
        for s in shares:
            if cum >= pct / 100 - 1e-9:
                break
            cum += s
            n += 1
        out[f"for_{pct}pct"] = n
    return out


def build_report(
    text: str,
    groups: dict[str, list[str]] | None = None,
    junit: ET.Element | None = None,
) -> dict:
    return build_from_block(parse(text), groups=groups, junit=junit)


def build_from_block(
    block: Block,
    groups: dict[str, list[str]] | None = None,
    junit: ET.Element | None = None,
) -> dict:
    rows, hidden, summary = block.rows, block.hidden, block.summary
    per_test: dict[str, list[float]] = {}
    for secs, phase, nodeid in rows:
        per_test.setdefault(nodeid, [0.0, 0.0, 0.0])[PHASES.index(phase)] += secs
    listed = sum(r[0] for r in rows)
    if listed <= 0.0:
        raise Refused("every listed duration rounds to 0.00s; nothing to attribute")
    tests = []
    for nodeid, phases in per_test.items():
        total = sum(phases)
        tests.append(
            {
                "nodeid": nodeid,
                "file": nodeid.split("::")[0],
                "total_s": total,
                "share": total / listed,
                "call_s": phases[1],
                "setup_s": phases[0],
                "teardown_s": phases[2],
            }
        )
    tests.sort(key=lambda t: -t["total_s"])
    per_file: dict[str, list[float]] = {}
    for t in tests:
        slot = per_file.setdefault(t["file"], [0.0, 0.0])
        slot[0] += t["total_s"]
        slot[1] += 1
    files = [
        {"file": f, "total_s": v[0], "share": v[0] / listed, "n_tests": int(v[1])}
        for f, v in per_file.items()
    ]
    files.sort(key=lambda f: -f["total_s"])
    file_total = {f["file"]: f["total_s"] for f in files}
    file_shares = [f["share"] for f in files]
    test_shares = [t["share"] for t in tests]
    out_groups = []
    for name, members in (groups or {}).items():
        out_groups.append(
            {
                "name": name,
                "n_files": len(members),
                "total_s": sum(file_total.get(f, 0.0) for f in members),
                "share": sum(file_total.get(f, 0.0) for f in members) / listed,
                "files_with_no_durations": sorted(f for f in members if f not in file_total),
            }
        )
    concentration = {
        "n_files": len(files),
        "n_tests": len(tests),
        "files_for_50pct": _counts_to_reach(file_shares)["for_50pct"],
        "files_for_80pct": _counts_to_reach(file_shares)["for_80pct"],
        "files_for_90pct": _counts_to_reach(file_shares)["for_90pct"],
        "tests_for_50pct": _counts_to_reach(test_shares)["for_50pct"],
        "tests_for_80pct": _counts_to_reach(test_shares)["for_80pct"],
        "tests_for_90pct": _counts_to_reach(test_shares)["for_90pct"],
        "top5_files_share": sum(file_shares[:5]),
        "top10_files_share": sum(file_shares[:10]),
        "top25_tests_share": sum(test_shares[:25]),
    }
    return {
        "n_lines": len(rows),
        "hidden": hidden,
        "summary": summary,
        "listed_total_s": listed,
        "phase_totals_s": {
            p: sum(r[0] for r in rows if r[1] == p) for p in PHASES
        },
        "n_tests": len(tests),
        "n_files": len(files),
        "tests": tests,
        "files": files,
        "buckets": _buckets(tests, listed),
        "groups": out_groups,
        "concentration": concentration,
        "completeness": check_junit(junit, tests, summary) if junit is not None else None,
    }


BUCKETS = (
    (60.0, float("inf"), ">=60"),
    (30.0, 60.0, "30-60"),
    (10.0, 30.0, "10-30"),
    (1.0, 10.0, "1-10"),
    (0.0, 1.0, "<1"),
)


def _buckets(tests: list[dict], listed: float) -> list[dict]:
    out = []
    for lo, hi, label in BUCKETS:
        sel = [t for t in tests if lo <= t["total_s"] < hi]
        total = sum(t["total_s"] for t in sel)
        out.append({"bucket": label, "n_tests": len(sel), "total_s": total, "share": total / listed})
    return out


def junit_key(nodeid: str) -> tuple[str, str]:
    """The ``(classname, name)`` pytest writes for ``nodeid`` (xunit2 family).

    Mirrors ``_pytest.nodes.NodeId.parse``: partition at the FIRST ``::`` and
    peel the params bracket before splitting names, so a parametrization that
    itself carries a pytest nodeid (``test_x[model/a.py::thing]``) is one name.
    """
    path, _sep, tail = nodeid.partition("::")
    name_part, bracket, param_part = tail.partition("[")
    names = [path, *name_part.split("::")]
    names[0] = re.sub(r"\.py$", "", names[0].replace("\\", "/").replace("/", "."))
    if bracket:
        names[-1] += f"[{param_part}"
    return ".".join(names[:-1]), names[-1]


def check_junit(junit: ET.Element, tests: list[dict], summary: dict | None) -> dict:
    """Reconcile the block with the run's junit.xml, or REFUSE.

    Every junit testcase must appear in the block with a matching time and
    vice versa; the only asymmetry allowed is a test whose every phase is
    under the block's print threshold (then it is in junit and absent from the
    block, with time near zero). A slow test missing from the block -- a
    dropped or truncated line -- is refused by nodeid.
    """
    cases = list(junit.iter("testcase"))
    junit_times: dict[tuple[str, str], float] = {}
    for c in cases:
        key = (c.get("classname", ""), c.get("name", ""))
        if key in junit_times:
            raise Refused(f"junit holds two testcases with the same id: {key}")
        junit_times[key] = float(c.get("time", "nan"))
    block_keys = {junit_key(t["nodeid"]) for t in tests}
    missing_in_block = sorted(k for k in junit_times if k not in block_keys)
    slow_missing = [k for k in missing_in_block if junit_times[k] > JUNIT_PER_TEST_TOL_S]
    if slow_missing:
        raise Refused(
            f"{len(slow_missing)} junit test(s) with real time have no duration line "
            f"(first: {slow_missing[0]} at {junit_times[slow_missing[0]]:.3f}s)"
        )
    missing_in_junit = sorted(k for k in block_keys if k not in junit_times)
    if missing_in_junit:
        raise Refused(
            f"{len(missing_in_junit)} duration-line test(s) are absent from junit "
            f"(first: {missing_in_junit[0]})"
        )
    worst = 0.0
    worst_key = None
    for t in tests:
        key = junit_key(t["nodeid"])
        gap = abs(junit_times[key] - t["total_s"])
        if gap > worst:
            worst, worst_key = gap, key
        if gap > JUNIT_PER_TEST_TOL_S:
            raise Refused(
                f"junit and durations disagree on {key}: "
                f"{junit_times[key]:.3f}s vs {t['total_s']:.2f}s (tolerance {JUNIT_PER_TEST_TOL_S}s)"
            )
    junit_total = sum(junit_times.values())
    block_total = sum(t["total_s"] for t in tests)
    return {
        "n_junit": len(cases),
        "n_block": len(tests),
        "n_only_in_junit": len(missing_in_block),
        "junit_total_s": junit_total,
        "block_total_s": block_total,
        "worst_per_test_gap_s": worst,
        "worst_per_test_gap_test": list(worst_key) if worst_key else None,
        "per_test_tolerance_s": JUNIT_PER_TEST_TOL_S,
        "junit_counts_match_summary": (
            None
            if summary is None
            else len(cases) == summary["passed"] + summary["skipped"] + sum(summary["other"].values())
        ),
    }


def group_from_grep(scan_dir: str, pattern: str) -> list[str]:
    """Test files under ``scan_dir`` (at any depth) whose text matches ``pattern``."""
    rx = re.compile(pattern, re.MULTILINE)
    root = Path(scan_dir)
    found = []
    for p in sorted(root.rglob("test_*.py")):
        if p.is_file() and rx.search(p.read_text(errors="replace")):
            found.append(f"{scan_dir.rstrip('/')}/{p.relative_to(root).as_posix()}")
    return found


def _read_group_file(path: str) -> list[str]:
    p = Path(path)
    if not p.is_file():
        raise Refused(f"group file not found: {path}")
    return [line.strip() for line in p.read_text().splitlines() if line.strip() and not line.startswith("#")]


def _fmt_pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def _cell(s: str) -> str:
    """A code span for a Markdown table cell: a `|` in an id would end the row."""
    return f"`{s.replace('|', chr(92) + '|')}`"


def render(report: dict, top: int, top_files: int) -> str:
    s = report["summary"]
    out = ["# pytest --durations=0 report", ""]
    out.append(
        f"- duration lines: {report['n_lines']} "
        f"(hidden under the print threshold: {report['hidden']}); "
        f"tests: {report['n_tests']}; files: {report['n_files']}"
    )
    if s:
        wall = s["total_s"]
        out.append(
            f"- listed total: {report['listed_total_s']:.1f} s of pytest wall {wall:.2f} s "
            f"({s['passed']} passed, {s['skipped']} skipped"
            + (", " + ", ".join(f"{v} {k}" for k, v in s["other"].items()) if s["other"] else "")
            + f") -- listed / wall = {_fmt_pct(report['listed_total_s'] / wall)}"
        )
    else:
        out.append(f"- listed total: {report['listed_total_s']:.1f} s (no pytest summary line found)")
    pt = report["phase_totals_s"]
    out.append(
        f"- by phase: call {pt['call']:.1f} s, setup {pt['setup']:.1f} s, teardown {pt['teardown']:.1f} s"
    )
    c = report["completeness"]
    if c is None:
        out.append(
            "- completeness: NOT CHECKED -- no --junit oracle, so a line dropped from the "
            "block is invisible here"
        )
    else:
        out.append(
            f"- completeness: reconciled against junit ({c['n_junit']} testcases; "
            f"{c['n_only_in_junit']} only in junit, all under {c['per_test_tolerance_s']}s); "
            f"worst per-test gap {c['worst_per_test_gap_s']:.4f} s; "
            f"junit counts match the summary line: {c['junit_counts_match_summary']}"
        )
    out += [
        "",
        "## Tests by duration bracket",
        "",
        "| bracket (s) | tests | of tests | s | share of listed |",
        "|---|---:|---:|---:|---:|",
    ]
    n_tests = report["n_tests"]
    for b in report["buckets"]:
        out.append(
            f"| {b['bucket']} | {b['n_tests']} | {_fmt_pct(b['n_tests'] / n_tests)} | "
            f"{b['total_s']:.1f} | {_fmt_pct(b['share'])} |"
        )
    heavy = report["buckets"][:3]
    heavy_n = sum(b["n_tests"] for b in heavy)
    heavy_share = sum(b["share"] for b in heavy)
    out += [
        "",
        f"- tests at 10 s or more: {heavy_n} of {n_tests} ({_fmt_pct(heavy_n / n_tests)}) "
        f"hold {_fmt_pct(heavy_share)} of listed time",
    ]
    out += [
        "",
        f"## Top {min(top, report['n_tests'])} tests (share of listed time)",
        "",
        "| # | s | share | call s | nodeid |",
        "|---:|---:|---:|---:|---|",
    ]
    for k, t in enumerate(report["tests"][:top], 1):
        out.append(
            f"| {k} | {t['total_s']:.2f} | {_fmt_pct(t['share'])} | {t['call_s']:.2f} | "
            f"{_cell(t['nodeid'])} |"
        )
    out += [
        "",
        f"## Files (top {min(top_files, report['n_files'])} of {report['n_files']})",
        "",
        "| # | s | share | cum | tests | file |",
        "|---:|---:|---:|---:|---:|---|",
    ]
    cum = 0.0
    for k, f in enumerate(report["files"][:top_files], 1):
        cum += f["share"]
        out.append(
            f"| {k} | {f['total_s']:.1f} | {_fmt_pct(f['share'])} | {_fmt_pct(cum)} | "
            f"{f['n_tests']} | {_cell(f['file'])} |"
        )
    if report["groups"]:
        out += [
            "",
            "## Candidate groups (they may overlap; shares do not sum to 100%)",
            "",
            "| group | files | s | share | files with no duration line |",
            "|---|---:|---:|---:|---|",
        ]
        for g in report["groups"]:
            note = (
                ", ".join(_cell(f) for f in g["files_with_no_durations"])
                if g["files_with_no_durations"]
                else ""
            )
            if g["n_files"] == 0:
                note = "NO FILES MATCHED"
            out.append(
                f"| {g['name']} | {g['n_files']} | {g['total_s']:.1f} | {_fmt_pct(g['share'])} | {note} |"
            )
    k = report["concentration"]
    out += [
        "",
        "## Concentration",
        "",
        f"- files to reach 50 / 80 / 90% of listed time: {k['files_for_50pct']} / "
        f"{k['files_for_80pct']} / {k['files_for_90pct']} of {k['n_files']}",
        f"- tests to reach 50 / 80 / 90% of listed time: {k['tests_for_50pct']} / "
        f"{k['tests_for_80pct']} / {k['tests_for_90pct']} of {k['n_tests']}",
        f"- top-5 files hold {_fmt_pct(k['top5_files_share'])}; top-10 files "
        f"{_fmt_pct(k['top10_files_share'])}; top-25 tests {_fmt_pct(k['top25_tests_share'])}",
    ]
    return "\n".join(out) + "\n"


def _name_value(arg: str, flag: str) -> tuple[str, str]:
    name, sep, value = arg.partition("=")
    if not sep or not name or not value:
        raise Refused(f"{flag} takes NAME=VALUE, got {arg!r}")
    return name, value


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--durations", required=True, help="log or text holding the --durations=0 block ('-' for stdin)")
    ap.add_argument("--junit", help="the same run's --junitxml output; enables the completeness check")
    ap.add_argument("--extract", help="write the block as pytest printed it (header through the summary line) here")
    ap.add_argument("--json", help="write the full report (every test and file) here")
    ap.add_argument("--group-file", action="append", default=[], metavar="NAME=PATH", help="files listed one per line")
    ap.add_argument("--group-grep", action="append", default=[], metavar="NAME=REGEX", help="test_*.py under --scan-dir whose text matches REGEX")
    ap.add_argument("--scan-dir", default="tools", help="directory --group-grep scans (default: tools)")
    ap.add_argument("--top", type=int, default=25, help="tests to print (default: 25)")
    ap.add_argument("--top-files", type=int, default=40, help="files to print (default: 40)")
    args = ap.parse_args(argv)
    try:
        text = sys.stdin.read() if args.durations == "-" else Path(args.durations).read_text(errors="replace")
        groups: dict[str, list[str]] = {}
        for spec in args.group_file:
            name, path = _name_value(spec, "--group-file")
            groups[name] = _read_group_file(path)
        for spec in args.group_grep:
            name, pattern = _name_value(spec, "--group-grep")
            groups[name] = group_from_grep(args.scan_dir, pattern)
        junit = ET.parse(args.junit).getroot() if args.junit else None
        block = parse(text)
        if args.extract:
            Path(args.extract).write_text(block.text)
        report = build_from_block(block, groups=groups or None, junit=junit)
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return REFUSED
    except (OSError, ET.ParseError, re.error) as e:
        print(f"REFUSED: cannot read the inputs: {e}", file=sys.stderr)
        return REFUSED
    sys.stdout.write(render(report, args.top, args.top_files))
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
