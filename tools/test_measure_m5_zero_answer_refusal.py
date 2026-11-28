#!/usr/bin/env python3
"""Exit-status controls for a zero-answer run of the two M5 operating-level
tools (#1857): when every drive's phrase refused, the process must leave as
REFUSED (2), never as a PASS (0).

THE DEFECT, AS MEASURED ON origin/main @ 528a228b (2026-11-28). Both `_run`s
record a drive whose M5A phrase raised `score.Refused` as a row carrying
`refused`, print it and go on to the next drive; after the loop they wrote the
report and `return 0`. So a run in which NO drive was measured exited 0 -- PASS,
a RESULT -- holding nothing but refusals. Reached three ways, each reproduced
on that tree: the baseline alone (`--drives 0.75`, the per-job invocation
`docs/scorecard/m5-artifacts-333/operating-level/b9.json` records), the
candidates alone, and the default list. And pinned, until #1857, by
`tools/test_m5_refusal_boundaries.py::test_m5a_refusal_is_still_recorded_per_drive`,
which asserted exit 0 for the frozen-audio case.

THE KNOWN ANSWER, independent of anything this repository models: a report
whose every row says `refused` holds no measurement, and
`tools/exit_contract.py` gives "a precondition was not met; nothing was
measured" exactly one status.

WHAT THE TOOLS OWE, and what these controls assert:

  nothing answered  exit `exit_contract.REFUSED` (2). The `--out` report is
                    STILL WRITTEN, holding every refused row and the
                    provenance, so the record exists. Each refusal is still
                    printed on its own per-drive row, and then exactly one
                    line begins `REFUSED: ` -- the last line printed.
  one answered      THE CONTROL: exit 0, the refused rows recorded beside the
                    measured ones. Widening the rule to "any refusal" turns
                    this red, and would refuse the split-across-jobs sweep the
                    `--drives` option exists for.

Red-first on that tree, 2026-11-28: 6 of the 10 controls here failed --
every nothing-answered case, with rc 0 where 2 is owed -- and the four
controls were green, as they must be on any tree.

The renders are replaced by recorders, so each control runs the real `main()`
without rendering. The same refusal through the real CLI, raised by the real
scorer over corrupted frozen audio, is the frozen-audio control in
`tools/test_m5_refusal_boundaries.py`. The frozen budget is restated here as
literals, as `tools/test_measure_m5_budget_refusal.py` restates it.
"""
from __future__ import annotations

import importlib
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import exit_contract                                     # noqa: E402

#: per tool: the frozen budget as literals -- the baseline, then its candidates
BUDGET = {
    "measure_m5_operating_level": (0.75, 0.5, 0.35, 0.25),
    "measure_m5_saw_drive": (0.75, 0.5, 0.35, 0.3),
}
MODULES = sorted(BUDGET)
#: what a phrase that refuses says -- the scorer's own text for frozen audio
#: whose hash does not match its manifest
REFUSAL = "frozen M5A Mini V3 audio hash mismatch"
ANSWERED = {"errors": {"Gain": 0.0}, "events": []}
SHAPES = {"baseline-alone": lambda budget: budget[:1],
          "candidates": lambda budget: budget[1:],
          "default": lambda budget: budget}


def _run(monkeypatch, tmp_path, capsys, module, refusing, argv=()):
    """(rc, the report, the stdout lines) of the real `main()` with every
    render replaced: `phrase` refuses for the drives in `refusing` and answers
    for the rest; the calibration and the probe are recorders."""
    tool = importlib.import_module(module)
    ol = importlib.import_module("measure_m5_operating_level")
    score = importlib.import_module("mono_m5a_score")

    def phrase(engine, case, drive, vol_factor):
        if drive in refusing:
            raise score.Refused(REFUSAL)
        return ANSWERED

    monkeypatch.setattr(ol, "calibrate", lambda engine, drive: 1.0)
    monkeypatch.setattr(tool, "phrase", phrase)
    monkeypatch.setattr(tool, "probe", lambda *a: {"probe": True})
    if module == "measure_m5_saw_drive":
        monkeypatch.setattr(tool, "r1_binding", lambda *a: {"candidate_changes_r1_bytes": False})
    out = tmp_path / "out.json"
    rc = tool.main([*argv, "--out", str(out)])
    return rc, json.loads(out.read_text()), capsys.readouterr().out.splitlines()


# ---------------------------------------------------------------------------
# nothing measured: REFUSED, the record still written
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("shape", sorted(SHAPES))
@pytest.mark.parametrize("module", MODULES)
def test_nothing_measured_exits_refused_not_a_pass(monkeypatch, tmp_path, capsys, module, shape):
    budget = BUDGET[module]
    drives = SHAPES[shape](budget)
    argv = [] if shape == "default" else ["--drives", *(str(d) for d in drives)]
    rc, report, out = _run(monkeypatch, tmp_path, capsys, module, set(budget), argv)

    assert rc == exit_contract.REFUSED, (rc, out)
    rows = report["rows"]
    assert list(rows) == [str(d) for d in drives]
    assert all(set(r) == {"drive", "vol_factor", "refused"} and r["refused"] == REFUSAL
               for r in rows.values()), rows
    assert {"engine", "rows", "commit", "sources_dirty"} <= set(report), \
        "the report is still written on a refused run, so the record exists"
    assert f"wrote {tmp_path / 'out.json'}" in out, out

    per_drive = [ln for ln in out if "REFUSED" in ln and not ln.startswith("REFUSED")]
    assert len(per_drive) == len(drives) and all(REFUSAL in ln for ln in per_drive), out
    verdicts = [ln for ln in out if ln.startswith("REFUSED")]
    assert len(verdicts) == 1 and out[-1] == verdicts[0], out
    assert f"0 of {len(drives)} drive(s) answered" in verdicts[0], verdicts[0]
    assert all(f"drive {d}" in verdicts[0] for d in drives), verdicts[0]


# ---------------------------------------------------------------------------
# THE CONTROL: one drive answered, and the run is a result
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("answering", ["the-baseline", "the-candidates"])
@pytest.mark.parametrize("module", MODULES)
def test_the_control_a_run_where_one_drive_answers_still_passes(
        monkeypatch, tmp_path, capsys, module, answering):
    base, *candidates = BUDGET[module]
    answered = [base] if answering == "the-baseline" else candidates
    refused = [d for d in BUDGET[module] if d not in answered]
    rc, report, out = _run(monkeypatch, tmp_path, capsys, module, set(refused))

    assert rc == exit_contract.PASS, (rc, out)
    rows = report["rows"]
    assert sorted((d for d, r in rows.items() if "refused" in r), key=float) == [str(d) for d in sorted(refused)]
    assert sorted((d for d, r in rows.items() if "refused" not in r), key=float) == [str(d) for d in sorted(answered)]
    per_drive = [ln for ln in out if "REFUSED" in ln and not ln.startswith("REFUSED")]
    assert len(per_drive) == len(refused), "each refusal is still recorded on its own row"
    assert not any(ln.startswith("REFUSED") for ln in out), out
