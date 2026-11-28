#!/usr/bin/env python3
"""The union of what ran must equal what a by-directory collection lists.

Issue #1947. `pytest tools/` on N pytest-xdist workers, or split across two
shards, only saves wall time if every test still runs exactly once. The one
check that looks like it covers that -- ``tools/test_fpga_uncovered_workflow.py``
-- pins the ``fpga-uncovered`` job (``JOB = "fpga-uncovered"``), not the
``tools`` job's collection, so a ``tools/`` test that falls out of a worker or
a shard leaves every check on main green. This is that check.

Two sides, from two different sources:

* EXPECTED -- a fresh ``pytest --collect-only -q <dirs>``: single process, by
  directory, the way the shipped job collects. Run by this tool (``--collect``)
  or read from a saved run of it (``--expected``). An inherited
  ``PYTEST_ADDOPTS`` is dropped, because a ``-k`` there would remove ids and
  read as omission.
* OBSERVED -- the junit XML of the run under test, one file per worker group or
  shard. A ``<testcase>`` exists only where pytest produced a test report, so
  it is evidence that the test RAN, not that it was collected. The union of
  several files is taken, and an id present in two of them ran twice.

junit carries no node ids. Each expected id is keyed exactly the way pytest
keys its junit row (``NodeId.parse`` then ``mangle_test_address`` in
``_pytest/junitxml.py``, 9.2), and the rows are matched against that table --
never turned back into ids from the junit text. A param holding ``::`` survives
that way; splitting on ``::`` does not (#1816's first wrong-then-right).

Verdicts (``REFUSED`` is first-class, CLAUDE.md):

* ``PASS``    every expected id has exactly one testcase and nothing else ran.
* ``FAIL``    ``missing``, ``extra`` or ``duplicate`` is non-empty, each listed
  by node id; with a baseline, any test whose outcome class changed.
* ``REFUSED`` the comparison cannot be made, so no verdict is issued:
  the collection failed, is empty, or its count line disagrees with its lines;
  a junit file cannot be read or has no testcase; two expected ids key alike;
  the junit disagrees with pytest's own summary line in the log; the baseline
  is not itself a complete run. Exit 2.

Outcome classes are read from junit children: ``<failure>`` FAILED (an
XPASS(strict) is pytest's own failure), ``<error>`` ERROR unless its message
says "failed on teardown", ``<skipped type="pytest.xfail">`` XFAILED, any other
``<skipped>`` SKIPPED, no child PASSED. A non-strict XPASS is a bare
``<testcase>`` -- indistinguishable from a plain pass in junit -- so it is
split out from the log's ``XPASS <nodeid>`` lines (``-rX``) when a log is
given, and the parity check treats PASSED and XPASSED as one class: at a fixed
commit a given test is either xfail-marked or not, so only xfailed<->xpassed
can move, and that moves between two DIFFERENT classes here.

Given ``--pytest-log``, the per-test classes are totalled and must equal
pytest's own summary line; a disagreement is REFUSED, because a record that
disagrees with itself is corrupt, not a measurement. The set comparison runs
first, so one omitted test is reported as that, not as a count mismatch.

WHAT SATISFIES THIS CHECK WHILE VIOLATING ITS INTENT (rule 8) -- stated,
because none is cheaply closable:

* a junit and a log from a DIFFERENT run of a DIFFERENT commit that happens to
  hold the same ids. Nothing here binds a junit to a commit; the workflow that
  runs this does, by collecting and testing in one job on one checkout.
* ``--expected`` fed a collection that already lost the test (a collection
  with a ``-k``, or an xdist collection). Only ``--collect`` is self-contained;
  the file form exists to re-check a saved run and trusts its source.
* a test whose junit row exists because a rerun plugin reported it twice;
  that reads as ``duplicate`` (loud, not silent).
* an id whose param holds a control character: pytest writes it as ``#xNN``
  and the key is computed with pytest's own ``bin_xml_escape`` to match. With
  a pytest that renames that function the key falls back to the raw text and
  such an id reads as missing -- again loud.

Run::

    python tools/check_collection_union.py --collect tools/ --junit run.xml --pytest-log run.log
    python tools/check_collection_union.py --collect tools/ --junit shard0.xml --junit shard1.xml
    python tools/check_collection_union.py control --expected collect.txt --junit run.xml \\
        --pytest-log run.log --baseline-junit base.xml --baseline-log base.log

``control`` injects each defect this file names into REAL inputs and prints a
properties x defects matrix (docs/verification-rules.md rule 4): it exits 0
only if the clean comparison passes and every injected defect was caught
through the property meant to catch it. ``tools/test_check_collection_union.py``
runs the same controls against a project real pytest produced, and disables a
guard to see its control go NOT CAUGHT.

Exit 0 PASS, 1 FAIL, 2 REFUSED.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import refusal

REPO_ROOT = Path(__file__).resolve().parent.parent
PASS, FAIL, REFUSED = "PASS", "FAIL", "REFUSED"
EXIT = {PASS: 0, FAIL: 1, REFUSED: 2}

PASSED, XPASSED, FAILED, ERROR, SKIPPED, XFAILED = (
    "PASSED", "XPASSED", "FAILED", "ERROR", "SKIPPED", "XFAILED")
CLASSES = (PASSED, XPASSED, FAILED, ERROR, SKIPPED, XFAILED)

COLLECT_LINE = re.compile(r"^(\d+) tests? collected")
SUMMARY_LINE = re.compile(
    r"^((?:\d+ (?:passed|failed|skipped|xfailed|xpassed|errors?|warnings?|deselected),? ?)+)"
    r" in [\d.]+s")
SUMMARY_WORDS = ("passed", "failed", "skipped", "xfailed", "xpassed",
                 "errors", "error", "warnings", "warning")


class Refusal(refusal.Refused, Exception):
    """A stated missing precondition, with the property that could not run."""

    def __init__(self, reason: str, prop: str = "collection"):
        super().__init__(reason)
        self.prop = prop


try:
    from _pytest.junitxml import bin_xml_escape as _xml_escape   # pytest's own
except ImportError:                                              # pragma: no cover
    def _xml_escape(text: str) -> str:
        return text


# -- the two sources ----------------------------------------------------------

def junit_key(nodeid: str) -> tuple[str, str]:
    """``(classname, name)`` exactly as pytest writes them for ``nodeid``."""
    path, sep, tail = nodeid.partition("::")
    if not sep:
        raise Refusal(f"{nodeid!r} has no '::': not a test node id", "collection")
    name_part, bracket, param_part = tail.partition("[")
    names = name_part.split("::")
    params = param_part.removesuffix("]") if bracket else None
    dotted = re.sub(r"\.py$", "", path.replace("/", "."))
    classname = ".".join([dotted, *names[:-1]])
    name = names[-1] + (f"[{params}]" if params is not None else "")
    return classname, _xml_escape(name)


def parse_collect_output(text: str) -> list[str]:
    """Node ids from ``pytest --collect-only -q`` stdout, in pytest's order.
    The count line must account for every line before it."""
    if "ERROR collecting" in text:
        raise Refusal("the collection itself reported an error: no verdict")
    lines = text.splitlines()
    hits = [i for i, l in enumerate(lines) if COLLECT_LINE.match(l.strip())]
    if len(hits) != 1:
        raise Refusal(f"expected exactly one '<n> tests collected' line, found "
                      f"{len(hits)}: no verdict on the collection")
    claimed = int(COLLECT_LINE.match(lines[hits[0]].strip()).group(1))
    ids = [l.strip() for l in lines[:hits[0]] if l.strip()]
    stray = [l for l in ids if "::" not in l]
    if stray:
        raise Refusal(f"{len(stray)} line(s) before the summary are not node ids, "
                      f"e.g. {stray[0][:120]!r}")
    if not ids:
        raise Refusal("zero node ids precede the summary line: nothing to compare")
    if len(ids) != claimed:
        raise Refusal(f"the summary line claims {claimed} tests but {len(ids)} node "
                      f"ids precede it -- truncated or interleaved output")
    if len(set(ids)) != len(ids):
        raise Refusal("a node id is listed twice in the collection")
    return ids


def run_collection(root: Path, dirs: tuple[str, ...] | list[str]) -> str:
    """A fresh by-directory ``pytest --collect-only -q``, the way the shipped
    job collects: single process, from ``root``, with no inherited addopts."""
    env = dict(os.environ)
    env.pop("PYTEST_ADDOPTS", None)          # an inherited -k would read as omission
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q",
             "-p", "no:cacheprovider", "--rootdir", str(root), *dirs],
            cwd=root, capture_output=True, text=True, env=env, check=False)
    except OSError as exc:
        raise Refusal(f"could not run pytest: {exc}") from exc
    out = proc.stdout + proc.stderr
    if proc.returncode not in (0, 5) or "ERROR collecting" in out:
        tail = "\n".join(out.strip().splitlines()[-10:])
        raise Refusal(f"pytest collection exited {proc.returncode} (a collection "
                      f"error is not an omission):\n{tail}")
    return proc.stdout


def collect(root: Path, dirs) -> list[str]:
    return parse_collect_output(run_collection(root, dirs))


@dataclass
class Case:
    """One junit ``<testcase>``, classified."""
    classname: str
    name: str
    outcome: str
    teardown_error: bool = False
    source: str = ""

    @property
    def label(self) -> str:
        return f"{self.classname}.{self.name}" + (f" ({self.source})" if self.source else "")


def classify(tc, source: str = "") -> Case:
    failure = tc.find("failure") is not None
    errors = [e for e in tc.findall("error")
              if not str(e.get("message", "")).startswith("failed on teardown")]
    teardown_error = len(errors) != len(tc.findall("error"))
    skipped = tc.find("skipped")
    if failure:
        outcome = FAILED
    elif errors:
        outcome = ERROR
    elif skipped is not None:
        outcome = XFAILED if skipped.get("type") == "pytest.xfail" else SKIPPED
    else:
        outcome = PASSED
    return Case(tc.get("classname", ""), tc.get("name", ""), outcome, teardown_error, source)


def load_junit(path) -> list[Case]:
    try:
        root = ET.parse(str(path)).getroot()
    except (OSError, ET.ParseError) as exc:
        raise Refusal(f"{path}: cannot read junit ({exc})", "junit") from exc
    cases = []
    for tc in root.iter("testcase"):
        if tc.get("classname") is None or tc.get("name") is None:
            raise Refusal(f"{path}: a <testcase> with no classname/name is not "
                          f"pytest's junit", "junit")
        cases.append(classify(tc, str(path)))
    if not cases:
        raise Refusal(f"{path}: no <testcase> at all -- the run left no record", "junit")
    return cases


def parse_summary_line(text: str) -> dict | None:
    """pytest's final line, as ``{'passed': n, ...}``; the LAST such line."""
    found = None
    for line in text.splitlines():
        m = SUMMARY_LINE.match(line.strip())
        if m:
            found = {}
            for n, word in re.findall(r"(\d+) (\w+)", m.group(1)):
                if word == "errors":
                    word = "error"
                found[word] = int(n)
    return found


def xpass_ids(log: str, expected: list[str]) -> set[str]:
    """Expected ids the log's ``XPASS <nodeid>`` lines name."""
    named = set()
    for line in log.splitlines():
        if not line.startswith("XPASS "):
            continue
        rest = line[len("XPASS "):]
        hit = [n for n in expected if rest == n or rest.startswith(n + " - ")]
        if not hit:
            raise Refusal(f"the log's {line[:100]!r} names no collected test", "summary")
        named.add(max(hit, key=len))
    return named


# -- the comparison ----------------------------------------------------------

@dataclass
class Match:
    seen: dict[str, int]              # node id -> how many testcases
    outcomes: dict[str, Case]         # node id -> its (first) testcase
    extra: list[str]                  # testcases matching no expected id


def build(expected: list[str], cases: list[Case]) -> Match:
    """Bind each junit row to one expected id. Rows are never turned into ids."""
    table: dict[tuple[str, str], str] = {}
    for n in expected:
        k = junit_key(n)
        if k in table:
            raise Refusal(f"ambiguous junit key {k}: {table[k]!r} and {n!r} key alike, "
                          f"so a junit row cannot be bound to one of them", "key")
        table[k] = n
    seen: dict[str, int] = {}
    outcomes: dict[str, Case] = {}
    extra: list[str] = []
    for c in cases:
        n = table.get((c.classname, c.name))
        if n is None:
            extra.append(c.label)
        else:
            seen[n] = seen.get(n, 0) + 1
            outcomes.setdefault(n, c)
    return Match(seen, outcomes, extra)


def missing_tests(expected: list[str], seen: dict) -> list[str]:
    return [n for n in expected if n not in seen]


def derived_counts(outcomes: dict[str, Case]) -> dict[str, int]:
    """Totals in pytest's own categories. pytest's ``passed`` excludes xpassed
    and its ``error`` counts a teardown error beside the test's own outcome."""
    c = dict.fromkeys(("passed", "failed", "error", "skipped", "xfailed", "xpassed"), 0)
    for case in outcomes.values():
        if case.outcome in (PASSED, FAILED, ERROR, SKIPPED, XFAILED):
            c[case.outcome.lower()] += 1
        elif case.outcome == XPASSED:
            c["xpassed"] += 1
        c["error"] += case.teardown_error
    return c


def tally(outcomes: dict[str, Case]) -> dict[str, int]:
    t = dict.fromkeys(CLASSES, 0)
    for case in outcomes.values():
        t[case.outcome] += 1
    return {k: v for k, v in t.items() if v}


def _mark_xpass(outcomes: dict[str, Case], named: set[str]) -> dict[str, Case]:
    out = dict(outcomes)
    for n in named:
        if n in out and out[n].outcome == PASSED:
            c = copy.copy(out[n])
            c.outcome = XPASSED
            out[n] = c
    return out


def _check_summary(outcomes: dict[str, Case], log: str, expected: list[str]) -> dict:
    summary = parse_summary_line(log)
    if summary is None:
        raise Refusal("the log has no '<n> passed ... in <t>s' summary line: the junit "
                      "cannot be bound to this log", "summary")
    derived = derived_counts(outcomes)
    if any(summary.get(k, 0) != v for k, v in derived.items()):
        said = ", ".join(f"{summary[k]} {k}" for k in derived if summary.get(k))
        got = ", ".join(f"{v} {k}" for k, v in derived.items() if v)
        raise Refusal(f"pytest's summary line says [{said}] but the junit parses to "
                      f"[{got}]: the record disagrees with itself -- no verdict", "summary")
    return summary


@dataclass
class Diff:
    nodeid: str
    baseline: str
    candidate: str


@dataclass
class Report:
    verdict: str = PASS
    reason: str = ""
    property: str = ""
    expected: int = 0
    missing: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)
    duplicate: list[str] = field(default_factory=list)
    outcome_diffs: list[Diff] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    baseline_counts: dict[str, int] = field(default_factory=dict)
    summary: dict[str, int] = field(default_factory=dict)

    def moved(self) -> list[str]:
        """Which comparison properties are non-empty (or, for a refusal, the
        one property that could not run)."""
        if self.verdict == REFUSED:
            return [self.property]
        out = [p for p in ("missing", "extra", "duplicate") if getattr(self, p)]
        if self.outcome_diffs:
            out.append("outcome")
        return out

    def render(self) -> str:
        if self.verdict == REFUSED:
            return f"REFUSED [{self.property}]: {self.reason}"
        ran = self.expected - len(self.missing)
        head = (f"{self.verdict}: {self.expected} expected, {ran} ran, "
                f"{len(self.missing)} missing, {len(self.extra)} extra, "
                f"{len(self.duplicate)} duplicate")
        outcomes = ", ".join(f"{k} {self.counts[k]}" for k in CLASSES if k in self.counts)
        lines = [head + (f"; {outcomes}" if outcomes else "")]
        for prop, title in (("missing", "expected, never ran"),
                            ("extra", "ran, not in the collection"),
                            ("duplicate", "ran more than once")):
            items = getattr(self, prop)
            if items:
                lines.append(f"  {prop} ({len(items)}) -- {title}:")
                lines.extend(f"    {i}" for i in items)
        if self.outcome_diffs:
            lines.append(f"  outcome differences ({len(self.outcome_diffs)}) "
                         f"-- baseline -> candidate:")
            lines.extend(f"    {d.nodeid}: {d.baseline} -> {d.candidate}"
                         for d in self.outcome_diffs)
        return "\n".join(lines)

    def as_json(self) -> dict:
        return {"verdict": self.verdict, "reason": self.reason, "property": self.property,
                "expected": self.expected, "missing": self.missing, "extra": self.extra,
                "duplicate": self.duplicate,
                "outcome_diffs": [d.__dict__ for d in self.outcome_diffs],
                "counts": self.counts, "baseline_counts": self.baseline_counts,
                "summary": self.summary}


def _parity_class(case: Case) -> tuple[str, bool]:
    o = PASSED if case.outcome in (PASSED, XPASSED) else case.outcome
    return o, case.teardown_error


def check(collect_text: str | None = None, expected: list[str] | None = None,
          cases: list[Case] | None = None, log: str | None = None,
          baseline_cases: list[Case] | None = None,
          baseline_log: str | None = None) -> Report:
    """The verdict. One comparison; every refusal carries its property."""
    try:
        ids = parse_collect_output(collect_text) if collect_text is not None else list(expected or [])
        if not ids:
            raise Refusal("no expected node ids: nothing to compare")
        if not cases:
            raise Refusal("no junit testcases: the run left no record", "junit")
        m = build(ids, cases)
    except Refusal as exc:
        return Report(REFUSED, str(exc), exc.prop, expected=len(expected or []))

    r = Report(expected=len(ids))
    r.missing = missing_tests(ids, m.seen)
    r.extra = m.extra
    r.duplicate = sorted(n for n, k in m.seen.items() if k > 1)
    if r.missing or r.extra or r.duplicate:
        r.verdict = FAIL
        r.counts = tally(m.outcomes)
        return r

    outcomes = m.outcomes
    if log is not None:
        outcomes = _mark_xpass(outcomes, xpass_ids(log, ids))
        try:
            r.summary = _check_summary(outcomes, log, ids)
        except Refusal as exc:
            return Report(REFUSED, str(exc), exc.prop, expected=len(ids))
    r.counts = tally(outcomes)

    if baseline_cases is not None:
        try:
            b = build(ids, baseline_cases)
            holes = missing_tests(ids, b.seen)
            if holes or b.extra or any(k > 1 for k in b.seen.values()):
                raise Refusal("the baseline is not a complete run: "
                              f"{len(holes)} missing, {len(b.extra)} extra; first "
                              f"missing {holes[0] if holes else b.extra[0]}", "baseline")
            base_outcomes = b.outcomes
            if baseline_log is not None:
                base_outcomes = _mark_xpass(base_outcomes, xpass_ids(baseline_log, ids))
                _check_summary(base_outcomes, baseline_log, ids)
        except Refusal as exc:
            return Report(REFUSED, str(exc), exc.prop, expected=len(ids))
        r.baseline_counts = tally(base_outcomes)
        for n in ids:
            was, now = _parity_class(base_outcomes[n]), _parity_class(outcomes[n])
            if was != now:
                r.outcome_diffs.append(Diff(n, _name(*was), _name(*now)))
        if r.outcome_diffs:
            r.verdict = FAIL
    return r


def _name(outcome: str, teardown_error: bool) -> str:
    return outcome + ("+TEARDOWN-ERROR" if teardown_error else "")


# -- controls -----------------------------------------------------------------

@dataclass
class Control:
    name: str
    verdict: str
    moved: list[str] = field(default_factory=list)
    reason: str = ""
    intended: str = ""

    @property
    def caught(self) -> bool:
        if self.verdict == "NOT EVALUATED":
            return False
        if self.name == "clean":
            return self.verdict == PASS
        return self.verdict in (FAIL, REFUSED) and self.intended in self.moved


def _flip(cases: list[Case], nodeid: str, outcome: str) -> list[Case]:
    out = copy.deepcopy(cases)
    key = junit_key(nodeid)
    for c in out:
        if (c.classname, c.name) == key:
            c.outcome, c.teardown_error = outcome, False
    return out


def _rewrite_summary(log: str, **delta: int) -> str:
    """The same log with its summary-line counts moved: what a REAL run that
    behaved differently would have printed."""
    lines = log.splitlines()
    idx = [i for i, l in enumerate(lines) if SUMMARY_LINE.match(l.strip())]
    if len(idx) != 1:
        raise Refusal("the log has exactly one summary line or none is used", "summary")
    counts = parse_summary_line(log)
    for k, d in delta.items():
        counts[k] = counts.get(k, 0) + d
    order = [w for w in SUMMARY_WORDS if counts.get(w)]
    line = ", ".join(f"{counts[w]} {w}" for w in order)
    return "\n".join(lines[:idx[0]] + [line + " in 0.00s"] + lines[idx[0] + 1:]) + "\n"


def _row(name: str, report: Report, intended: str = "") -> Control:
    return Control(name, report.verdict, report.moved(), report.reason, intended)


#: the matrix columns, in order: the four comparison properties, then the
#: five refusal properties.
PROPERTIES = ("missing", "extra", "duplicate", "outcome", "summary",
              "collection", "junit", "key", "baseline")


def control(collect_text: str, cases: list[Case], log: str | None = None,
            baseline_cases: list[Case] | None = None,
            baseline_log: str | None = None) -> list[Control]:
    """Every defect this file names, injected into the given REAL inputs.

    Each row is caught only if the comparison went red through the property
    meant to see it (rule 5 condition 3). A defect whose input is absent is
    NOT EVALUATED, which reads as not caught, never as caught."""
    ids = parse_collect_output(collect_text)      # a broken clean input is no control
    rows = [_row("clean", check(collect_text=collect_text, cases=cases, log=log,
                                baseline_cases=baseline_cases, baseline_log=baseline_log))]
    victim = ids[-1]

    dropped = [c for c in cases if (c.classname, c.name) != junit_key(victim)]
    rows.append(_row("omit-one",
                     check(collect_text=collect_text, cases=dropped), "missing"))

    ghost = Case("pytest", "internal", "ERROR", source="injected")
    rows.append(_row("extra-one",
                     check(collect_text=collect_text, cases=cases + [ghost]), "extra"))

    rows.append(_row("ran-twice",
                     check(collect_text=collect_text, cases=cases + cases[:1]),
                     "duplicate"))

    plain = next((n for n, c in build(ids, cases).outcomes.items()
                  if c.outcome == PASSED and not c.teardown_error), None)
    if baseline_cases is None or plain is None:
        rows.append(Control("outcome-changed", "NOT EVALUATED", reason=(
            "needs a baseline (--baseline-junit) and a plain passing test")))
    else:
        cand = _flip(cases, plain, "SKIPPED")
        cand_log = _rewrite_summary(log, passed=-1, skipped=1) if log is not None else None
        rows.append(_row("outcome-changed",
                         check(collect_text=collect_text, cases=cand, log=cand_log,
                               baseline_cases=baseline_cases, baseline_log=baseline_log),
                         "outcome"))

    if log is None or plain is None:
        rows.append(Control("junit-edited", "NOT EVALUATED", reason=(
            "needs a log (--pytest-log) and a plain passing test")))
    else:
        rows.append(_row("junit-edited",
                         check(collect_text=collect_text,
                               cases=_flip(cases, plain, "SKIPPED"), log=log),
                         "summary"))

    truncated = collect_text.replace(victim + "\n", "", 1)
    if truncated == collect_text:                 # pragma: no cover
        raise Refusal(f"could not remove {victim!r} from the collection text", "collection")
    rows.append(_row("collection-truncated",
                     check(collect_text=truncated, cases=cases), "collection"))

    rows.append(_row("junit-empty", check(collect_text=collect_text, cases=[]), "junit"))

    try:
        build(["a.b/test_x.py::test_y", "a/b/test_x.py::test_y"], [])
    except Refusal as exc:
        rows.append(Control("key-ambiguous", REFUSED, [exc.prop], str(exc), "key"))
    else:
        rows.append(Control("key-ambiguous", PASS, [],
                            "build accepted two ids that key alike", "key"))

    if baseline_cases is None:
        rows.append(Control("baseline-incomplete", "NOT EVALUATED", reason=(
            "needs a baseline (--baseline-junit)")))
    else:
        hole = [c for c in baseline_cases
                if (c.classname, c.name) != junit_key(ids[0])]
        rows.append(_row("baseline-incomplete",
                         check(collect_text=collect_text, cases=cases, log=log,
                               baseline_cases=hole, baseline_log=baseline_log), "baseline"))
    return rows


def control_exit(rows: list[Control]) -> int:
    """0 only if the clean comparison passed AND every defect was caught."""
    return 0 if all(r.caught for r in rows) else 1


def render_matrix(rows: list[Control], properties=PROPERTIES) -> str:
    """Properties x defects: MOVED where a property saw it, BLIND where it did
    not (docs/verification-rules.md rule 4)."""
    head = f"{'control':22} " + " ".join(f"{p[:9]:>9}" for p in properties) + "  verdict"
    lines = [head, "-" * len(head)]
    for r in rows:
        cells = []
        for p in properties:
            if r.name == "clean":
                cells.append(f"{'-':>9}")
            elif r.verdict == "NOT EVALUATED":
                cells.append(f"{'--':>9}")
            elif p in r.moved:
                cells.append(f"{'MOVED':>9}")
            else:
                cells.append(f"{'BLIND':>9}")
        verdict = r.verdict
        if r.verdict == "NOT EVALUATED":
            verdict = f"NOT EVALUATED ({r.reason})"
        elif not r.caught:
            verdict = f"{r.verdict} NOT CAUGHT"
        lines.append(f"{r.name:22} " + " ".join(cells) + f"  {verdict}")
    return "\n".join(lines)


# -- the command line -----------------------------------------------------------

def _read(path) -> str:
    try:
        return Path(path).read_text(errors="replace")
    except OSError as exc:
        raise Refusal(f"cannot read {path}: {exc}", "input") from exc


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="check_collection_union.py",
        description="The union of what ran (junit) must equal a by-directory collection.")
    p.add_argument("mode", nargs="?", default="check", choices=("check", "control"))
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--collect", nargs="+", metavar="DIR",
                     help="run a fresh single-process `pytest --collect-only -q` on these")
    src.add_argument("--expected", metavar="FILE",
                     help="saved output of that collection (trusted, see the module docstring)")
    p.add_argument("--root", default=str(REPO_ROOT), help="where --collect runs (default: the repo)")
    p.add_argument("--junit", action="append", required=True, metavar="FILE",
                   help="junit of the run under test; repeat once per shard")
    p.add_argument("--pytest-log", metavar="FILE", help="the run's pytest output")
    p.add_argument("--baseline-junit", metavar="FILE", help="a complete run to compare outcomes with")
    p.add_argument("--baseline-log", metavar="FILE")
    p.add_argument("--json", metavar="FILE", help="also write the report as JSON")
    try:
        args = p.parse_args(argv)
    except SystemExit as exc:                       # argparse's usage error
        return exc.code if isinstance(exc.code, int) else EXIT[REFUSED]
    try:
        collect_text = (run_collection(Path(args.root), args.collect) if args.collect
                        else _read(args.expected))
        cases = [c for f in args.junit for c in load_junit(Path(f))]
        log = _read(args.pytest_log) if args.pytest_log else None
        baseline_cases = (load_junit(Path(args.baseline_junit))
                          if args.baseline_junit else None)
        baseline_log = _read(args.baseline_log) if args.baseline_log else None
    except Refusal as exc:
        print(f"REFUSED [{exc.prop}]: {exc}")
        return EXIT[REFUSED]
    if args.mode == "control":
        rows = control(collect_text, cases, log, baseline_cases, baseline_log)
        print(render_matrix(rows))
        return control_exit(rows)
    r = check(collect_text=collect_text, cases=cases, log=log,
              baseline_cases=baseline_cases, baseline_log=baseline_log)
    print(r.render())
    if args.json:
        Path(args.json).write_text(json.dumps(r.as_json(), indent=1) + "\n")
    return EXIT[r.verdict]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
