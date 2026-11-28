#!/usr/bin/env python3
"""The head and tail the compare_*_candidate.py scripts share.

The tail is the provenance block every candidate delta record carries, and the
path handling and output guard its main() wraps the write in. That is the
block that was byte-identical in `compare_drum_i2s_candidate.py`,
`compare_ensemble_candidate.py` and `compare_m5a_i2s_candidate.py`, extracted
once (issue #1187) so it cannot drift into three competing shapes -- the same
reason `tools/provenance.py` exists for the render/analyse/accept scheme.

The head is `evaluate_record`: the checks that decide whether a JSON document
is a board record at all, and THE BOARD's verdict on it. #1187 left each
script's own copy of those checks in place, and the three copies had drifted
into three different subsets of the same gate (issue #1601): one refused every
M5B record outright, one never asked the board and trusted the verdict the
record stored about itself, two accepted a record whose provenance named a
different engine, and all three crashed on a JSON file that was not an
object when it was the baseline.

FAIL-CLOSED, BY DECISION. The git calls run with `check=True`: a git that
cannot answer raises `CalledProcessError`, the caller's `main()` renders it
as REFUSED (exit 2), and no record is written. That is deliberately NOT
`provenance.git()`'s contract (empty string on any failure, #615): a delta
record whose provenance says `source_commit: ""` with `source_dirty: False`
is an answer from an apparatus that could not answer, and it looks exactly
like data. When #599 lands one failure encoding for git-state readers this
is a call site to migrate; until then fail-closed IS the contract, and
`tools/test_compare_candidate_tails.py` pins it.

The key set and value shapes are frozen to what the three comparators
already wrote into `docs/scorecard/`: `run_at_utc` keeps aware-isoformat's
`...+00:00` shape, not `provenance.now()`'s `%Y-%m-%dT%H:%M:%SZ`, and
`source_dirty` stays "does `git status --porcelain` say anything" -- not
`worktree_state()["dirty"]`'s diff-plus-untracked, fail-closed-on-no-commit
definition, which would change the meaning of every committed delta record.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import math
import pathlib
import subprocess

import run_case
import scorecard

ROOT = pathlib.Path(__file__).resolve().parent.parent


def repo_path(text: str, root: pathlib.Path = ROOT) -> pathlib.Path:
    """Resolve a CLI path argument against the repository root: absolute
    paths as given, relative paths inside the repo."""
    path = pathlib.Path(text)
    return path if path.is_absolute() else root / path


def require_json_number(value, label: str, *, refused: type[Exception]) -> float:
    """`value` as a float when it is a finite JSON number; otherwise raise.

    A JSON number is an int or a float. `bool` is an `int` subclass in Python
    and `float()` accepts strings, so a raw coercion reads `False` as a
    perfect 0.0 and `"0.5"` as half -- the same two readings
    `scorecard._finite` refuses for the board's own numbers. `refused` is the
    caller's own `Refused` class, so the name at its catch sites does not
    change (#163)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise refused(f"{label} must be a JSON number, not {value!r}")
    try:
        number = float(value)
    except OverflowError:
        number = math.inf if value > 0 else -math.inf
    if not math.isfinite(number):
        raise refused(f"{label} must be finite, not {number!r}")
    return number


def evaluate_record(record, label: str, *, engines: tuple, family: str | None = None,
                    case: dict | None = None, refused: type[Exception]) -> dict:
    """Assert `record` is a record THE BOARD can score, and return the board's
    verdict on it -- recomputed here, never read out of the record.

    A record that names its own state is a claim; `scorecard.evaluate` is the
    only thing entitled to make it. What the board answers for itself (an
    unknown engine, missing provenance, a non-finite or boolean number, a
    missing required property) is reported in the board's own words. What it
    cannot answer without raising is checked here first, because a raised
    exception is exit 1 and a traceback where REFUSED is exit 2.

    REFUSES, in this order:

      * not a JSON object (`[]` and `"x"` are valid JSON files);
      * an `engine` outside `engines`, or a `provenance` that does not name
        that same engine -- the header says chip and the provenance says
        model is a lie this cannot adjudicate, so it declines both;
      * no `case_id`, a case `docs/scorecard/cases.csv` does not list, or one
        outside `family` when the caller reads one family;
      * a `provenance` that is not an object or carries no `inputs`;
      * a metric whose `purpose` (absent = `match`) is not the fixed contract's
        `run_case.metric_purpose(name)` -- a change of scoring direction;
      * a supplied `case` row whose `case_id` is not the record's;
      * `metrics` that are not all objects, or a property whose `valid` is
        not exactly `true` (the board reads a missing or truthy `valid` as
        valid; an invalid measurement has no distance to compare);
      * anything the board grades other than pass or fail, or grades at a
        distance that is not finite -- a finite error over a near-zero
        tolerance overflows to `inf`, which a report would carry as a bare
        `Infinity` token (#1406's class, from finite inputs);
      * a stored `scorecard_state` that contradicts the board, null included.

    `refused` is raised, never this module's own class (see
    `require_json_number`). Returns `{"engine", "case_id", "case", "metrics",
    "verdict"}`.

    What this does NOT establish (docs/verification-rules.md rule 8, form 2):
    that the numbers in the record are the ones the WAV or the simulator
    transcript on disk would give. A fabricated record whose metrics,
    provenance and state agree with each other passes, because binding a
    record to its audio is the scoring tools' job, upstream."""
    if not isinstance(record, dict):
        raise refused(f"{label} is not a record")
    engine = record.get("engine")
    if engine not in engines:
        raise refused(f"{label} must name engine {' or '.join(repr(e) for e in engines)} "
                      f"(saw {engine!r})")
    case_id = record.get("case_id")
    if not case_id:
        raise refused(f"{label} does not name its case")
    if case is None:
        case = next((c for c in scorecard.load_cases() if c["case_id"] == case_id), None)
        if case is None:
            raise refused(f"{case_id} is not a case in docs/scorecard/cases.csv")
    elif case.get("case_id") != case_id:
        # a caller-supplied row is a claim about which case this is (#1601)
        raise refused(f"{label} is case {case_id!r} but was handed the board row for "
                      f"{case.get('case_id')!r}")
    if family is not None and case.get("family") != family:
        raise refused(f"{case_id} belongs to the {case.get('family')} family, but this "
                      f"comparison reads {family} cases")
    provenance = record.get("provenance")
    if not isinstance(provenance, dict):
        raise refused(f"{label} provenance is absent, null or not an object")
    if provenance.get("engine") != engine:
        raise refused(f"{label} provenance and record disagree about the engine "
                      f"({engine!r} vs {provenance.get('engine')!r})")
    if not provenance.get("inputs"):
        raise refused(f"{label} carries no provenance inputs: it cannot be re-derived")
    metrics = record.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        raise refused(f"{label} carries no metrics")
    for name, metric in metrics.items():
        if not isinstance(metric, dict):
            raise refused(f"{label} property {name} is malformed")
        if metric.get("valid") is not True:
            raise refused(f"{label} property {name} is invalid, so it has no distance")
        # The scoring DIRECTION is part of the measurement contract: a ceiling
        # clamps a negative error to zero where a match takes its magnitude.
        # Absent means `match`; the fixed contract is run_case.metric_purpose.
        purpose, fixed = metric.get("purpose", scorecard.MATCH), run_case.metric_purpose(name)
        if purpose != fixed:
            raise refused(f"{label} property {name} is scored as {purpose!r}, but the fixed "
                          f"measurement contract scores it as {fixed!r}")
    try:
        verdict = scorecard.evaluate(case, record)
    except OverflowError as exc:
        # a JSON integer beyond float range: the board's isfinite cannot answer
        raise refused(f"{label} has no pass/fail verdict on the board: {exc}") from exc
    if verdict["state"] not in (scorecard.PASS, scorecard.FAIL):
        raise refused(f"{label} has no pass/fail verdict on the board: {verdict['why']}")
    if not math.isfinite(verdict["worst"]):
        # finite inputs, infinite distance: a report would carry a bare Infinity
        raise refused(f"{label} has no finite distance on the board (worst "
                      f"{verdict['worst']!r}): its error overflows its tolerance")
    stored = record.get("scorecard_state")
    if "scorecard_state" in record and stored != verdict["state"]:
        raise refused(f"{label} stores state {stored!r} but the board evaluates it as "
                      f"{verdict['state']!r}")
    return {"engine": engine, "case_id": case_id, "case": case, "metrics": metrics,
            "verdict": verdict}


def comparison_provenance(baseline_path, candidate_path, sources,
                          root: pathlib.Path = ROOT) -> dict:
    """The provenance block of a candidate delta record: which commit, which
    tree state, the exact bytes of both input records and of every source
    that produced them, and when.

    REFUSES (by raising, for the caller's main() to render) rather than
    recording when git cannot answer -- see the module docstring."""
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                            check=True, capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=root,
                                check=True, capture_output=True, text=True).stdout.strip())
    return {
        "source_commit": commit, "source_dirty": dirty,
        "input_sha256": {
            "baseline_record": hashlib.sha256(pathlib.Path(baseline_path).read_bytes()).hexdigest(),
            "candidate_record": hashlib.sha256(pathlib.Path(candidate_path).read_bytes()).hexdigest()},
        "source_sha256": {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                          for name in sources},
        "run_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


def write_report(report: dict, out_text: str, tool: str,
                 root: pathlib.Path = ROOT) -> pathlib.Path | None:
    """Write one delta record, refusing an `--out` that escapes the tree it
    describes.

    Returns the resolved output path, or None when `out_text` resolves
    outside `root` -- the caller renders that as REFUSED, exit 2, with the
    same `<tool>: REFUSED -- ...` line the three scripts printed inline."""
    out = repo_path(out_text, root=root)
    if not out.resolve().is_relative_to(root):
        print(f"{tool}: REFUSED -- output must remain inside the repository")
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    return out
