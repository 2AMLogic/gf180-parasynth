"""#1994: the two M5 operating-level selection rules refuse a row that carries
a value which is not a finite number, instead of reading it as "within bounds".

THE DEFECT. `select` in `measure_m5_operating_level` and `measure_m5_saw_drive`
compares each candidate's scored errors and probe readings with the
baseline's. IEEE makes every ordering comparison against NaN False (#134), so

    if abs(abs(c["Gain"]) - abs(base["Gain"])) > 0.5:

did not trip on a NaN Gain: no reason was recorded and the candidate stayed
admissible. `json.dumps` writes a bare `NaN`, which is how a non-finite score
reaches a per-drive record, and `--merge` reads that record back with
`json.loads`, so the path from a file on disk to the comparison is open.

WHAT THE CONTROLS ASSERT:

  committed rows   the repaired reader returns exactly the selection each
                   record committed -- the gate is satisfiable, and the repair
                   moved no finite verdict
  read, exactly    every value the rule reads, and only those: the baseline's
                   Foldback energy and Clipping are never compared (only a
                   candidate's are, against constants), so they are not
                   asserted; a finite value moved across its bound is reported
                   by the rule for each key that is
  every read value NaN, +inf and -inf REFUSE naming the row and the field
  --merge          a record file carrying a NaN leaves the process REFUSED (2)
                   with one line on stdout and no --out report: the path a
                   real record takes
  never read       a candidate row REFUSED at render time is reported as such
                   and its numbers are not compared (the committed
                   operating-level record holds one, rows[0.25])
  absent           `upper_wanted_rel_db: null` (no harmonic in 5-20 kHz) reads
                   as 0 dB: `None` is no reading, not a NaN

RED FIRST, on a tree at e2d1bae7 with the readers unasserted (2026-11-28): 86
of this file's 119 controls failed -- 84 with `DID NOT RAISE Refused` and the
two `--merge` controls with the process at exit 0 -- and the other 33, which
must be green on any tree, were green. With the assertion removed from the
finished tree, the same 86 fail and nothing else does.
"""
from __future__ import annotations

import importlib
import json
import pathlib
import sys

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import exit_contract  # noqa: E402


RECORDS = {
    "measure_m5_operating_level":
        "docs/scorecard/m5-artifacts-333/operating-level/p2x-selection.json",
    "measure_m5_saw_drive": "docs/scorecard/m5-artifacts-333/saw-drive/selection.json",
}


MODULES = sorted(RECORDS)
BASELINE, CANDIDATE = "0.75", "0.5"


ROWS = (BASELINE, CANDIDATE)


ROW_ID = {BASELINE: "baseline", CANDIDATE: "candidate"}


SHARED = ("Gain", "Envelope attack", "Harmonic shape")


CANDIDATE_ONLY = ("Foldback energy", "Clipping")


PROBES = {"measure_m5_operating_level": ("saw/84", "saw/96", "pulse29/84", "pulse29/96"),
          "measure_m5_saw_drive": ("84", "96")}


NON_FINITE = (float("nan"), float("inf"), -float("inf"))


NON_FINITE_IDS = ("nan", "inf", "-inf")


REFUSED = "the selection rows carry a value that is not a finite number: "


def _reads(module):
    """(row, where) for every value `select` reads, where is ("errors", name)
    or ("probe", key). SHARED errors are compared between a candidate and the
    baseline; CANDIDATE_ONLY errors only a candidate's are, against a constant;
    PROBES are the keys the "darker" comparison reads."""
    return (tuple((row, ("errors", f)) for row in ROWS for f in SHARED)
            + tuple((CANDIDATE, ("errors", f)) for f in CANDIDATE_ONLY)
            + tuple((row, ("probe", k)) for row in ROWS for k in PROBES[module]))


READS = tuple((module, row, where) for module in MODULES for row, where in _reads(module))


READ_IDS = [f"{module.removeprefix('measure_m5_').replace('_', '-')}-{ROW_ID[row]}"
            f"-{'.'.join(str(w) for w in where).replace(' ', '-')}"
            for module, row, where in READS]


def _committed(module):
    return json.loads((ROOT / RECORDS[module]).read_text())


def _name(module, row, where):
    if where[0] == "errors":
        return f"rows[{row}].M5A.errors.{where[1]}"
    return f"rows[{row}].probe.{where[1]}.upper_wanted_rel_db"


def _table(doc, module, row, where):
    """The dict `select` reads `where` from, and the key it reads in it."""
    if where[0] == "errors":
        return doc["rows"][row]["M5A"]["errors"], where[1]
    return doc["rows"][row]["probe"][where[1]], "upper_wanted_rel_db"


def _select(module, doc):
    """`select` as the CLI serialises it: the live dict keys ints where the
    committed file holds strings."""
    return json.loads(json.dumps(importlib.import_module(module).select(doc["rows"])))


@pytest.mark.parametrize("module", MODULES)


def test_the_committed_rows_still_select_exactly_what_was_committed(module):
    doc = _committed(module)
    assert _select(module, doc) == doc["selection"]


@pytest.mark.parametrize("module", MODULES)


def test_a_candidate_row_refused_at_render_time_is_reported_and_never_read(module):
    """The row is skipped before any of its numbers is compared, so a NaN
    planted where they would be read changes nothing. The committed
    operating-level record holds such a row, rows[0.25]."""
    doc = _committed(module)
    doc["rows"][CANDIDATE] = {"refused": "planted render refusal",
                              "M5A": {"errors": {f: float("nan") for f in SHARED + CANDIDATE_ONLY}},
                              "probe": {k: {"upper_wanted_rel_db": float("nan")} for k in PROBES[module]}}
    verdict = _select(module, doc)["verdicts"][CANDIDATE]
    assert verdict["admissible"] is False
    assert verdict["reasons"] == ["REFUSED: planted render refusal"]


def test_an_absent_upper_wanted_reading_is_zero_to_the_rule_not_a_refusal():
    """`upper_wanted_rel_db` is `None` for a stage with no harmonic in
    5-20 kHz; the rule reads that as 0 dB, and must keep doing so."""
    module = "measure_m5_operating_level"
    doc = _committed(module)
    for row in doc["rows"].values():
        for point in row.get("probe", {}).values():
            point["upper_wanted_rel_db"] = None
    verdicts = _select(module, doc)["verdicts"]
    assert set(verdicts) == {"0.5", "0.35", "0.25"}
    assert not any("darker" in r for v in verdicts.values() for r in v["reasons"])


REASON = {("errors", "Gain"): "gain", ("errors", "Foldback energy"): "foldback",
          ("errors", "Clipping"): "clipping", ("errors", "Envelope attack"): "attack"}


WITNESS_IDS = [i for i, (module, row, where) in enumerate(READS) if where[1] != "Harmonic shape"]


def _across_its_bound(doc, module, row, where):
    """Move one read value across the bound the rule holds it to. A baseline
    move is large enough to dominate the real candidate-baseline spread."""
    table, key = _table(doc, module, row, where)
    other, other_key = _table(doc, module, CANDIDATE if row == BASELINE else BASELINE, where)
    if where == ("errors", "Gain"):
        table[key] = other[other_key] + (5.0 if row == CANDIDATE else -5.0)
    elif where == ("errors", "Envelope attack"):
        table[key] = other[other_key] + (1.0 if row == CANDIDATE else -5.0)
    elif where == ("errors", "Foldback energy"):
        table[key] = 10.0
    elif where == ("errors", "Clipping"):
        table[key] = 1.0
    else:
        table[key] = other[other_key] + (-20.0 if row == CANDIDATE else 20.0)


def _gives_reason(verdict, where):
    if where[0] == "probe":
        return any("darker" in r for r in verdict["reasons"])
    return any(r.startswith(REASON[where]) for r in verdict["reasons"])


@pytest.mark.parametrize("index", WITNESS_IDS, ids=[READ_IDS[i] for i in WITNESS_IDS])


def test_the_rule_reads_each_of_these_values(index):
    """The witness for the refusal below: a finite value moved across its
    bound is a reason the rule gives, in this key. Green before and after."""
    module, row, where = READS[index]
    doc = _committed(module)
    _across_its_bound(doc, module, row, where)
    verdicts = _select(module, doc)["verdicts"]
    judged = [verdicts[CANDIDATE]] if row == CANDIDATE else list(verdicts.values())
    judged = [v for v in judged if not (v["reasons"] and v["reasons"][0].startswith("REFUSED"))]
    assert judged
    for verdict in judged:
        assert _gives_reason(verdict, where), (row, where, verdict)
        assert verdict["admissible"] is False


@pytest.mark.parametrize("index", range(len(READS)), ids=READ_IDS)


@pytest.mark.parametrize("bad", NON_FINITE, ids=NON_FINITE_IDS)


def test_a_read_value_that_is_not_a_finite_number_refuses_naming_row_and_field(index, bad):
    module, row, where = READS[index]
    doc = _committed(module)
    table, key = _table(doc, module, row, where)
    table[key] = bad
    with pytest.raises(importlib.import_module(module).Refused) as info:
        _select(module, doc)
    assert str(info.value) == REFUSED + _name(module, row, where) + " = " + repr(bad)


@pytest.mark.parametrize("module", MODULES)


@pytest.mark.parametrize("field", CANDIDATE_ONLY)


def test_the_baseline_foldback_and_clipping_are_not_compared_only_a_candidates_are(module, field):
    """Why the refusal above does not assert the baseline's Foldback energy or
    Clipping: the rule never compares them. The same finite value is a reason
    in a candidate row and changes nothing in the baseline row."""
    doc = _committed(module)
    doc["rows"][BASELINE]["M5A"]["errors"][field] = 10.0
    assert _select(module, doc) == doc["selection"]
    doc["rows"][CANDIDATE]["M5A"]["errors"][field] = 10.0
    verdict = _select(module, doc)["verdicts"][CANDIDATE]
    assert _gives_reason(verdict, ("errors", field)), verdict


@pytest.mark.parametrize("module", MODULES)


def test_merge_refuses_a_record_file_carrying_a_nan_and_writes_no_report(module, tmp_path, capsys):
    doc = _committed(module)
    clean = tmp_path / "clean.json"
    clean.write_text(json.dumps({"rows": {d: r for d, r in doc["rows"].items() if d != CANDIDATE}}))
    row = json.loads(json.dumps(doc["rows"][CANDIDATE]))
    row["M5A"]["errors"]["Gain"] = float("nan")
    damaged = tmp_path / "damaged.json"
    damaged.write_text(json.dumps({"rows": {CANDIDATE: row}}, indent=1))
    assert '"Gain": NaN' in damaged.read_text()

    out = tmp_path / "selection.json"
    code = importlib.import_module(module).main(["--merge", str(clean), str(damaged), "--out", str(out)])
    lines = capsys.readouterr().out.splitlines()
    assert code == exit_contract.REFUSED, lines
    assert [line for line in lines if line.startswith("REFUSED")] == [
        "REFUSED: " + REFUSED + f"rows[{CANDIDATE}].M5A.errors.Gain = nan"], lines
    assert not out.exists(), "a refused merge must leave no --out report behind"
