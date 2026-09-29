#!/usr/bin/env python3
"""Ground truth for the sealed-holdout mechanism: the states it must REFUSE.

    .venv/bin/python -m pytest tools/test_holdout.py -q

A holdout's only failure mode that matters is a false holdout -- a setting that
was chosen, or moved, after its error was known, wearing a sealed case's label.
Every test here is one of those states, and each asserts that the mechanism
refuses or records it rather than producing a number:

  * a Holdout-split case with no seal is REFUSED, and the refusal carries no
    distance at all (`tools/run_case.py`'s rule: an invalid measurement has no
    distance, not a zero one);
  * a seal that git has never seen, or that has uncommitted modifications, is
    REFUSED -- otherwise "chosen before" and "chosen after" are the same state
    on disk;
  * a seal edited AFTER it was read is detected by `check`, which is the case
    the seal alone cannot catch: the settings really were committed first, and
    then changed once the error was known;
  * a second read taken after the model moved is REFUSED until the transition
    `docs/scorecard/README.md` describes in prose is recorded;
  * and the control for all of it: with the seal present and committed, F1D's
    refusal changes to a different, named reason (its frozen clip is not in the
    profile), so the refusals above are the gate's and not something else's.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "model"))

import holdout as ho                                                 # noqa: E402
import run_case as rc                                                # noqa: E402
import scorecard as sb                                               # noqa: E402


def _git(root: pathlib.Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        text=True, stderr=subprocess.STDOUT)


def _commit(root: pathlib.Path, message: str) -> str:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)
    return _git(root, "rev-parse", "HEAD").strip()


SPEC = {"settings": {"plan": "filter", "ref_clip": "x/lp-cut500-res0.00",
                     "ref_open_clip": "x/lp-open20k-res0.00", "cut_hz": 500.0,
                     "open_hz": 20000.0, "res_ref": 0.0, "res_ours": 0.0},
        "why": "a setting no fit here has read, for the test's purposes",
        "seen_by": ["nothing"]}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A real git repository, because the mechanism's load-bearing assertion is
    a git one. A mock would test the mock."""
    root = tmp_path / "repo"
    (root / "docs" / "scorecard" / "holdout").mkdir(parents=True)
    _git(root.parent, "init", "-q", str(root))
    (root / "README.md").write_text("fixture\n")
    _commit(root, "initial")
    monkeypatch.setattr(ho, "ROOT", root)
    monkeypatch.setattr(ho, "SEAL_DIR", root / "docs" / "scorecard" / "holdout")
    monkeypatch.setattr(ho, "LEDGER",
                        root / "docs" / "scorecard" / "holdout" / "LEDGER.json")
    return root


def _seal(repo_root, case_id="F1D", spec=None, commit=True):
    seal = ho.write_seal(case_id, dict(spec or SPEC), by="a party that is not tuning")
    if commit:
        _commit(repo_root, f"seal {case_id}")
    return seal


# ===========================================================================
# The seal itself
# ===========================================================================
def test_a_seal_git_has_never_seen_is_refused(repo):
    """An untracked seal is indistinguishable from one written after reading the
    error, which is the only thing the seal exists to rule out."""
    _seal(repo, commit=False)
    with pytest.raises(ho.Refused) as e:
        ho.assert_readable("F1D", model_state="a" * 16)
    assert "not committed" in str(e.value)


def test_a_seal_with_uncommitted_modifications_is_refused(repo):
    """The edit-then-render loop: the settings are in git, and the ones being
    measured are not the ones in git."""
    _seal(repo)
    seal = json.loads(ho.seal_path("F1D").read_text())
    seal["settings"]["cut_hz"] = 1234.0
    ho.seal_path("F1D").write_text(json.dumps(seal))
    with pytest.raises(ho.Refused) as e:
        ho.assert_readable("F1D", model_state="a" * 16)
    assert "uncommitted modifications" in str(e.value)


def test_a_committed_seal_reads_and_says_what_it_was_measured_against(repo):
    _seal(repo)
    block = ho.assert_readable("F1D", model_state="a" * 16)
    assert block["state"] == ho.SEALED and block["holdout_claim"] is True
    assert block["seal_core_sha256"] and block["seal_commit"]
    assert block["reads_before_this_one"] == 0
    # The reader of a record can re-derive the ordering from git, which is the
    # point of carrying the commit rather than a promise.
    assert "merge-base" in block["how_to_check"]


def test_a_seal_with_no_settings_or_no_argument_is_refused(repo):
    with pytest.raises(ho.Refused):
        ho.write_seal("F2D", {"settings": {}, "why": "x", "seen_by": ["y"]},
                      by="t")
    with pytest.raises(ho.Refused):
        ho.write_seal("F2D", {"settings": {"cut_hz": 1.0}, "why": "", "seen_by": []},
                      by="t")


def test_the_seal_hash_covers_the_settings_and_ignores_the_recorded_transition(repo):
    """`open` appends to the same file. If that changed the hash, every recorded
    read would read as tampering and the staleness check would be noise."""
    _seal(repo)
    before = ho.core_sha256(ho.load_seal("F1D"))
    ho.record_read("F1D", ho.assert_readable("F1D", model_state="a" * 16),
                   engine="fixed-model", state="fail", worst=2.0,
                   result_path="build/x", command="t")
    ho.open_case("F1D", why="it guided the cutoff change", guided_change="#100",
                 by="t")
    after = ho.load_seal("F1D")
    assert after["state"] == ho.OPENED
    assert ho.core_sha256(after) == before
    settings = dict(SPEC["settings"])
    settings["cut_hz"] = 501.0
    assert ho.core_sha256({**after, "settings": settings}) != before


# ===========================================================================
# Reading it, and reading it twice
# ===========================================================================
def test_a_refusal_is_not_a_read(repo):
    """A refusal carries no distance, so it has learned nothing about the
    holdout and must not consume its one sealed reading."""
    assert ho.was_read({"metrics": {"a": {"valid": False, "why": "no reference"}}}) is False
    assert ho.was_read({"metrics": {}}) is False
    assert ho.was_read({"metrics": {"a": {"valid": False}, "b": {"valid": True}}}) is True


def test_a_second_read_after_the_model_moved_is_refused(repo):
    """The operational meaning of "its detailed errors have guided a change":
    between the two readings, the thing being measured moved."""
    _seal(repo)
    first = ho.assert_readable("F1D", model_state="a" * 16)
    ho.record_read("F1D", first, engine="fixed-model", state="fail", worst=3.0,
                   result_path="build/x", command="t")
    # The same model state is a re-measurement, not a second bite.
    again = ho.assert_readable("F1D", model_state="a" * 16)
    assert again["reads_before_this_one"] == 1
    with pytest.raises(ho.Refused) as e:
        ho.assert_readable("F1D", model_state="b" * 16)
    assert "development data" in str(e.value)
    assert "holdout.py open" in str(e.value)


def test_recording_the_transition_is_what_unblocks_it_and_it_costs_the_claim(repo):
    _seal(repo)
    ho.record_read("F1D", ho.assert_readable("F1D", model_state="a" * 16),
                   engine="fixed-model", state="fail", worst=3.0,
                   result_path="build/x", command="t")
    ho.open_case("F1D", why="the 500 Hz error chose the new trim",
                 guided_change="#100", by="t")
    _commit(repo, "open F1D")
    block = ho.assert_readable("F1D", model_state="b" * 16)
    assert block["state"] == ho.OPENED
    assert block["holdout_claim"] is False          # this is the price
    assert "development data" in block["note"]
    entries = ho.load_ledger()["entries"]
    assert [e["kind"] for e in entries] == ["read", "opened"]
    assert entries[1]["guided_change"] == "#100" and entries[1]["after_reads"] == [1]


def test_opening_a_holdout_nothing_has_read_is_refused(repo):
    """A seal that has never been read is still a holdout. Opening it would
    launder a case into development data for free."""
    _seal(repo)
    with pytest.raises(ho.Refused) as e:
        ho.open_case("F1D", why="x", guided_change="y", by="t")
    assert "no transition to record" in str(e.value)


def test_an_unexplained_transition_is_refused(repo):
    _seal(repo)
    ho.record_read("F1D", ho.assert_readable("F1D", model_state="a" * 16),
                   engine="fixed-model", state="fail", worst=3.0,
                   result_path="build/x", command="t")
    with pytest.raises(ho.Refused):
        ho.open_case("F1D", why="  ", guided_change="#100", by="t")


# ===========================================================================
# check: the after-the-fact audit, including the case the seal cannot catch
# ===========================================================================
def test_check_passes_on_a_sealed_and_read_case(repo):
    _seal(repo)
    ho.record_read("F1D", ho.assert_readable("F1D", model_state="a" * 16),
                   engine="fixed-model", state="fail", worst=3.0,
                   result_path="build/x", command="t")
    _commit(repo, "the read")
    code, lines = ho.check()
    assert code == ho.OK, "\n".join(lines)


def test_a_seal_edited_after_it_was_read_is_reported_stale(repo):
    """THE EDGE CASE THE SEAL ALONE CANNOT CATCH. The settings were genuinely
    committed before the render; then the error was known and they moved. The
    seal is tracked and clean at every moment, so only the ledger's record of
    WHAT was read can show it."""
    _seal(repo)
    ho.record_read("F1D", ho.assert_readable("F1D", model_state="a" * 16),
                   engine="fixed-model", state="fail", worst=9.0,
                   result_path="build/x", command="t")
    _commit(repo, "the read")
    code, lines = ho.check()
    assert code == ho.OK, "\n".join(lines)

    seal = json.loads(ho.seal_path("F1D").read_text())
    seal["settings"]["cut_hz"] = 1000.0            # the region F1B already reads
    ho.seal_path("F1D").write_text(json.dumps(seal, indent=1) + "\n")
    _commit(repo, "quietly move the holdout to where the answer is known")

    code, lines = ho.check()
    assert code == ho.FAIL
    assert any("STALE" in ln and "AFTER they were read" in ln for ln in lines), lines


def test_check_reports_a_read_of_a_case_with_no_seal(repo):
    """A ledger entry whose seal has been deleted is not a clean tree."""
    _seal(repo)
    ho.record_read("F1D", ho.assert_readable("F1D", model_state="a" * 16),
                   engine="fixed-model", state="fail", worst=1.0,
                   result_path="build/x", command="t")
    ho.seal_path("F1D").unlink()
    _commit(repo, "delete the seal")
    code, lines = ho.check()
    assert code == ho.FAIL
    assert any("no readable seal" in ln for ln in lines), lines


def test_check_reports_two_reads_at_different_model_states_under_one_seal(repo):
    """The runner refuses this; a hand-edited ledger cannot be refused after the
    fact, so the audit has to see it too."""
    _seal(repo)
    block = ho.assert_readable("F1D", model_state="a" * 16)
    ho.record_read("F1D", block, engine="fixed-model", state="fail", worst=1.0,
                   result_path="build/x", command="t")
    ho.record_read("F1D", {**block, "model_state_sha256": "b" * 16},
                   engine="fixed-model", state="pass", worst=0.2,
                   result_path="build/x", command="t")
    _commit(repo, "two reads")
    code, lines = ho.check()
    assert code == ho.FAIL
    assert any("different model states" in ln for ln in lines), lines


# ===========================================================================
# The runner: the gate, the marker on the record, and the ledger entry
# ===========================================================================
def _holdout_row(cid="F1D"):
    for row in rc.load_cases():
        if row["case_id"] == cid:
            assert row["split"] == "Holdout", row
            return row
    raise AssertionError(cid)


def test_an_unsealed_holdout_case_is_refused_and_carries_no_distance(tmp_path,
                                                                    monkeypatch):
    """D01B has no seal. It must come back as a stated no-verdict naming the
    missing seal -- not scored like a development case, and not silently
    absent."""
    monkeypatch.setattr(ho, "LEDGER", tmp_path / "LEDGER.json")
    row = _holdout_row("D01B")
    res = rc.run_case(row, tmp_path, keep_audio=False)
    assert res is not None
    assert res["holdout"]["state"] == "unsealed"
    assert res["holdout"]["holdout_claim"] is False
    assert "no sealed settings" in res["note"]
    for name, m in res["metrics"].items():
        assert m["valid"] is False and "error" not in m and "value" not in m, name
    assert sb.evaluate(row, res)["state"] == sb.NO_VERDICT
    assert not (tmp_path / "LEDGER.json").exists(), "a refusal is not a read"


def test_f1d_is_sealed_in_this_repository_and_no_longer_deliberately_not_run():
    """The four cases this issue found in `NOT_RUN` were all held there on one
    sentence about sealing. F1D's settings are now committed, so the sequencing
    reason is gone and the case is attempted."""
    assert "F1D" not in rc.NOT_RUN
    assert rc.plan_for("F1D") == "filter"
    seal = ho.load_seal("F1D")
    assert seal["state"] == ho.SEALED and seal["settings"]["plan"] == "filter"
    spec = rc.filter_spec("F1D")
    assert set(spec) == set(rc.FILTER_SPEC_KEYS) and spec["cut_hz"] == 500.0
    # ... and the OTHER three stay, each with its own reason, none of which is
    # sealing. One sentence covering four cases is what this issue found.
    for cid in ("F2D", "F3D", "F5D"):
        assert "NOT on sealing" in rc.NOT_RUN[cid]


def test_the_committed_seals_and_ledger_hold_up_in_this_repository():
    """`tools/holdout.py check` against the tree as it stands -- run before this
    gate was committed, because an unsatisfiable gate is worse than no gate."""
    code, lines = ho.check()
    assert code == ho.OK, "\n".join(lines)


def test_a_sealed_holdout_whose_reference_is_not_frozen_is_a_stated_no_verdict(
        tmp_path, monkeypatch):
    """F1D's clip does not exist yet: no host in this fleet has the plugin. The
    case says so, names the clip, and keeps the seal on the record -- which is
    the control for the two refusals above, because it is a DIFFERENT reason
    reached through the same gate."""
    monkeypatch.setattr(ho, "LEDGER", tmp_path / "LEDGER.json")
    row = _holdout_row("F1D")
    res = rc.run_case(row, tmp_path, keep_audio=False)
    assert "lp-cut500-res0.00" in res["note"] and "REFUSED" in res["note"]
    assert res["holdout"]["state"] == ho.SEALED
    assert res["holdout"]["holdout_claim"] is True
    assert res["holdout"]["seal_core_sha256"] == ho.core_sha256(ho.load_seal("F1D"))
    assert sb.evaluate(row, res)["state"] == sb.NO_VERDICT
    assert not (tmp_path / "LEDGER.json").exists()


def test_an_unsealed_seal_directory_makes_f1d_refuse_for_the_seal_and_not_the_clip(
        tmp_path, monkeypatch):
    """The discriminating control. Same case, same runner: with the seal
    untracked the refusal names the SEAL; with the committed seal (the test
    above) it names the missing clip. So the gate is what produced the first
    refusal."""
    seal_dir = tmp_path / "holdout"
    seal_dir.mkdir()
    shutil.copy(ho.seal_path("F1D"), seal_dir / "F1D.json")
    monkeypatch.setattr(ho, "SEAL_DIR", seal_dir)
    monkeypatch.setattr(ho, "LEDGER", seal_dir / "LEDGER.json")
    res = rc.run_case(_holdout_row("F1D"), tmp_path, keep_audio=False)
    assert "not committed" in res["note"]
    assert "lp-cut500-res0.00" not in res["note"]


def test_the_runner_records_a_read_with_the_seal_hash_and_the_model_state(
        repo, monkeypatch):
    """The integration point: when a number IS taken off a sealed holdout, the
    record says which seal it came from and the ledger gains one entry. Fed a
    synthetic measured record, because F1D's reference does not exist yet -- the
    thing under test here is the runner's bookkeeping, not the estimators."""
    _seal(repo)
    measured = {"engine": "fixed-model", "case_id": "F1D", "metrics": {
        "Corner frequency": {"value": 210.0, "units": "Hz", "reference": 217.0,
                             "error": -7.0, "tolerance": 21.7, "valid": True}}}
    monkeypatch.setattr(rc, "_measure_case", lambda *a, **k: dict(measured))
    monkeypatch.setattr(rc, "verdict_of", lambda case, res: ("pass", 0.32, "ok"))
    row = dict(_holdout_row("F1D"))
    row["required_measurements"] = "Corner frequency"
    res = rc.run_case(row, repo, keep_audio=False, results_dir="build/x")
    assert res["holdout"]["ledger_read"] == 1
    entry = ho.load_ledger()["entries"][0]
    assert entry["kind"] == "read" and entry["case_id"] == "F1D"
    assert entry["seal_core_sha256"] == ho.core_sha256(ho.load_seal("F1D"))
    assert entry["model_state_sha256"] == res["holdout"]["model_state_sha256"]
    assert entry["board_state"] == "pass" and entry["result"] == "build/x"
    # A second run at a different model state is now refused by the gate, in the
    # runner rather than only in the module.
    monkeypatch.setattr(rc, "model_input_hashes", lambda extra=None: {"x": "moved"})
    again = rc.run_case(row, repo, keep_audio=False)
    assert again["holdout"]["state"] == "unsealed"        # i.e. no holdout claim
    assert "development data" in again["note"]


def test_an_injected_control_never_consumes_a_sealed_reading(repo, monkeypatch):
    """A control's output is not evidence about the instrument, so it is not a
    reading of the holdout either -- the same rule that keeps `--inject` out of
    docs/scorecard/results."""
    _seal(repo)
    measured = {"engine": "fixed-model", "case_id": "F1D", "metrics": {
        "Corner frequency": {"value": 1.0, "units": "Hz", "reference": 2.0,
                             "error": -1.0, "tolerance": 0.2, "valid": True}}}
    monkeypatch.setattr(rc, "_measure_case", lambda *a, **k: dict(measured))
    row = dict(_holdout_row("F1D"))
    row["required_measurements"] = "Corner frequency"
    rc.run_case(row, repo, inject="REF_CORNER_2X", keep_audio=False)
    assert ho.load_ledger()["entries"] == []
