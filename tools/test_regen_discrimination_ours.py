"""The regenerator's contract is REFUSE-vs-report, so the refusal is tested.

`tools/regen_discrimination_ours.py` decides whether `docs/discrimination.md`
section 8.4/8.6 may be rewritten from a run. Its only failure mode that matters
is the false green: printing a report, and writing
`docs/reference-compare-results-shipped.json`, over a measurement that never
happened. The first version of that script asserted only that a
`{stage}-{device}.json` FILE existed -- and `guard()` in
`model/reference_compare.py` writes a perfectly well-formed
`[{"ok": false, "not_answerable": "..."}]` stub when a stage refuses, so the
gate passed on a refusal. That is the state CLAUDE.md names as worse than the
tool being absent, "because its output looks exactly like data", and it was
asserted only "by construction" until this file existed.

Same reasoning as `tools/test_run_all.py`, which exists because a runner's only
failure mode that matters is a false green. The three cases the review asked
for are `test_a_required_device_that_answers_is_reported`,
`test_a_non_required_refusal_is_announced_and_does_not_trip_the_gate`, and
`test_a_required_refusal_refuses_instead_of_reporting`; the rest are the other
`Refused` branches and the boundary that must NOT fire.

No simulation runs here: every case drives `--skip-run` over a prepared
directory, and the "answers" cases reuse the committed
`docs/reference-compare-results-shipped.json` payload so that the one path that
reaches `reference_compare.py --report` is exercised against real rows. Both
`FROZEN` and `SHIPPED` are monkeypatched per test -- a test that overwrote the
committed docs data would be its own worse defect.
"""
from __future__ import annotations
import copy
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import regen_discrimination_ours as rg                               # noqa: E402

ROOT = rg.ROOT
COMMITTED_SHIPPED = os.path.join(ROOT, "docs",
                                 "reference-compare-results-shipped.json")
GUARD_STUB_KEY = "response-ours"          # in `required`
# A probe, not in `required`. `selfosc` rather than `response` deliberately:
# report() filters selfosc rows on `ok` and prints `---` for a stub, whereas it
# raises `TypeError: unsupported format string passed to NoneType` on a
# `response` stub (reference_compare.py:465). Observed while writing this test,
# not fixed here -- it is a fragility in report()'s formatting, and its effect
# on this script is still a REFUSED (the `--report` subprocess exits non-zero),
# so it fails in the safe direction. Worth its own issue, not this PR's scope.
NON_REQUIRED_KEY = "selfosc-ours-huovtune"


def _committed_payload():
    if not os.path.exists(COMMITTED_SHIPPED):
        pytest.skip(f"{COMMITTED_SHIPPED} is not committed in this tree")
    with open(COMMITTED_SHIPPED) as f:
        return json.load(f)


def _guard_stub(device, why="the estimator refused at the point of use"):
    """Exactly the shape `guard()` in model/reference_compare.py writes."""
    return [dict(device=device, ok=False, not_answerable=why)]


def _materialize(out_dir, payload):
    """Write a `{key: rows}` mapping as the per-key JSON files the script
    collects from its `--out` directory."""
    os.makedirs(out_dir, exist_ok=True)
    for key, rows in payload.items():
        with open(os.path.join(out_dir, f"{key}.json"), "w") as f:
            json.dump(rows, f, indent=1, default=str)
    return out_dir


def _run(tmp_path, monkeypatch, payload, shipped_name="shipped.json"):
    """Drive main() with --skip-run over `payload`, with the committed docs
    output path redirected into the tmp tree. Returns (rc, shipped_path)."""
    out = _materialize(str(tmp_path / "out"), payload)
    shipped_path = str(tmp_path / shipped_name)
    monkeypatch.setattr(rg, "SHIPPED", shipped_path)
    rc = rg.main(["--out", out, "--skip-run"])
    return rc, shipped_path


# --- the answer/refusal classifier, in isolation -----------------------------

def test_a_guard_stub_is_a_refusal_not_an_answer():
    why = rg.answer_refusal_reason(_guard_stub("ours", "no onset found"))
    assert why is not None and "no onset found" in why


def test_one_refused_row_among_answers_is_still_a_refusal():
    """A stage that refused for part of its sweep did not answer for it."""
    rows = [dict(device="ours", ok=True, h3=-40.0),
            dict(device="ours", ok=False, not_answerable="floor")]
    assert rg.answer_refusal_reason(rows) is not None


def test_no_rows_at_all_is_a_refusal():
    assert rg.answer_refusal_reason([]) is not None
    assert rg.answer_refusal_reason(None) is not None


def test_every_row_failing_is_a_refusal_even_without_a_stub():
    assert rg.answer_refusal_reason([dict(device="ours", ok=False)]) is not None


def test_a_stage_whose_rows_carry_no_ok_field_is_an_answer():
    """Wrong-then-right, recorded because it was a real defect in this gate.

    The first version of `answer_refusal_reason()` refused unless some row had
    a truthy `ok`. Only `selfosc`/`bigdrive` rows carry `ok` at all --
    `tracking`, `response` and `peakdrive` rows do not -- so that version made
    six of the seven `required` keys REFUSE on the committed data the docs are
    built from: an unsatisfiable gate, which CLAUDE.md rates worse than no
    gate. This test is the regression lock, driven from the committed rows
    rather than from a hand-written shape, so it also fails if the row schema
    changes under it.
    """
    payload = _committed_payload()
    for key in ("tracking-ours", "response-ours", "peakdrive-ours"):
        rows = payload[key]
        assert all("ok" not in r for r in rows), \
            f"{key} rows gained an `ok` field -- re-derive this gate, do not relax it"
        assert rg.answer_refusal_reason(rows) is None, key


def test_a_null_measurement_that_answers_none_is_NOT_a_refusal():
    """The boundary that must not fire.

    `selfosc-ours-2pole` answers `ok: true` with a null fingerprint because the
    injected 2-pole defect genuinely does not self-oscillate; report() prints
    `none`, which is the correct answer to the question asked. Escalating that
    to REFUSED would turn a control this repo requires to be carried into a
    false red -- the mirror image of the false green above.
    """
    rows = _committed_payload()["selfosc-ours-2pole"]
    assert any(r.get("h2") is None for r in rows), "expected the null fingerprint"
    assert rg.answer_refusal_reason(rows) is None


# --- the three cases the review named ---------------------------------------

def test_a_required_device_that_answers_is_reported(tmp_path, monkeypatch):
    rc, shipped_path = _run(tmp_path, monkeypatch, _committed_payload())
    assert rc == 0
    written = json.load(open(shipped_path))
    assert "tracking-ours" in written and "bigdrive-ours" in written
    assert os.path.exists(str(tmp_path / "out" / "report-shipped.txt")), \
        "the report is the deliverable of a non-refused run"
    assert os.path.exists(COMMITTED_SHIPPED), "the committed data must be untouched"


def test_a_non_required_refusal_is_announced_and_does_not_trip_the_gate(
        tmp_path, monkeypatch, capsys):
    """A probe with nothing to say is a fact about the run, not a blocker.

    Two halves, and the second is the one that documents reality rather than
    the intent: the *gate* does not escalate a non-required refusal (and the
    script says so, tagged `not required for 8.4/8.6`, because a
    silently-dropped refusal is how a control stops being carried) -- but
    `reference_compare.py`'s `report()` is not stub-tolerant for any device in
    its `ORDER` (`max(live, ...)` over an empty list, line 416), so the run
    still ends REFUSED via a non-zero `--report` exit. That fails in the safe
    direction and is asserted here as the observed behaviour, not glossed.
    """
    payload = copy.deepcopy(_committed_payload())
    assert NON_REQUIRED_KEY in payload
    stub = _guard_stub("ours-huovtune", "no onset: probe declined")
    payload[NON_REQUIRED_KEY] = stub

    # The gate itself, directly: no exception, and nothing blocking found.
    assert NON_REQUIRED_KEY not in rg.REQUIRED_KEYS
    rg.required_gate(payload)
    found = rg.announce_refusals(payload)
    assert found.get(NON_REQUIRED_KEY), "the refusal must be announced"
    assert "not required for 8.4/8.6" in capsys.readouterr().out

    # End to end: REFUSED, but from report() rather than from the gate.
    rc, shipped_path = _run(tmp_path, monkeypatch, payload)
    assert rc == 2
    out = capsys.readouterr().out
    assert "--report exited" in out, \
        "the gate must not be what stopped this; report()'s intolerance is"
    assert "a measurement the 8.4/8.6 tables are built from REFUSED" not in out
    assert not os.path.exists(shipped_path), \
        "no artefact from a run that did not produce a report"


def test_a_required_refusal_refuses_instead_of_reporting(
        tmp_path, monkeypatch, capsys):
    """The case a presence-only gate got wrong: the file exists and is
    well-formed, and it contains no answer."""
    payload = copy.deepcopy(_committed_payload())
    payload[GUARD_STUB_KEY] = _guard_stub("ours", "cut_setting_for found no onset")
    rc, shipped_path = _run(tmp_path, monkeypatch, payload)
    assert rc == 2
    out = capsys.readouterr().out
    assert out.startswith("REFUSED:") or "\nREFUSED:" in out
    assert GUARD_STUB_KEY in out
    assert not os.path.exists(shipped_path), \
        "REFUSED must not write the docs data file"
    assert not os.path.exists(str(tmp_path / "out" / "report-shipped.txt")), \
        "REFUSED must not print a report either -- that is the false green"


# --- the remaining Refused branches -----------------------------------------

def test_a_required_key_absent_refuses(tmp_path, monkeypatch):
    payload = copy.deepcopy(_committed_payload())
    del payload["peakdrive-ours-tanh256"]
    rc, shipped_path = _run(tmp_path, monkeypatch, payload)
    assert rc == 2
    assert not os.path.exists(shipped_path)


def test_a_missing_frozen_reference_file_refuses(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rg, "FROZEN", str(tmp_path / "nope.json"))
    rc, shipped_path = _run(tmp_path, monkeypatch, _committed_payload())
    assert rc == 2
    assert "does not exist" in capsys.readouterr().out
    assert not os.path.exists(shipped_path)


def test_a_frozen_file_missing_a_reference_key_refuses(tmp_path, monkeypatch, capsys):
    with open(rg.FROZEN) as f:
        frozen = json.load(f)
    dropped = f"tracking-{rg.REF_DEVICES[0]}"
    assert dropped in frozen, "precondition of this test's own construction"
    del frozen[dropped]
    hobbled = str(tmp_path / "frozen-hobbled.json")
    with open(hobbled, "w") as f:
        json.dump(frozen, f)
    monkeypatch.setattr(rg, "FROZEN", hobbled)
    rc, shipped_path = _run(tmp_path, monkeypatch, _committed_payload())
    assert rc == 2
    assert dropped in capsys.readouterr().out
    assert not os.path.exists(shipped_path)
