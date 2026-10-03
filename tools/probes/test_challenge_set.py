#!/usr/bin/env python3
"""Tests for `tools/probes/challenge_set.py` (#521, part of #158).

    python3 -m pytest tools/probes/test_challenge_set.py -q

No corpus is needed: every guard here is exercised on the committed
definition, on a temporary corpus written by the test, or on a synthetic
record. The real-anchor run itself is not repeated in CI -- it needs the
Fischer corpus -- but the committed results are checked against the frozen
definition they claim to describe, and the report is re-rendered from them.

Every guard ships with the input that defeats its naive form
(docs/verification-rules.md rule 8):

  split identity      a byte-identical recording under a second PATH in a
                      second split -- a path comparison passes it
  lineage group       a hardware pack whose roles do not include the split's
                      group, and a `claimed` pack in held-out-validation
  freeze              a definition edited without re-freezing
  apparatus drift     a perturbation strength or estimator resolution changed
                      in the live module but not in the frozen definition
  anchor bytes        a file at the right path with the wrong bytes
  mel_dac floor       an anchor whose one-sample-shift floor is zero (digital
                      silence), where a ratio to it is infinite
  no-op transform     a perturbation that does not change the record (tail
                      noise starting after a short anchor ends): NO-VERDICT,
                      never BLIND

and the matrix itself carries the two start-red controls rule 1 asks for: a
constant estimator must come out BLIND on every defect column, and an
estimator that answers noise must come out FALSE-ALARM on the permitted ones.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import math
import pathlib
import sys

import numpy as np
import pytest
from scipy.io import wavfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "probes"))

import audio_measure as am                                            # noqa: E402
import challenge_set as cs                                            # noqa: E402
import perturbation_ladder as pl                                      # noqa: E402

SR = 44100


def _defn():
    return cs.load_definition()


def _failed_rules(findings):
    return {f["rule"] for f in findings if not f["ok"]}


def _strike(sr=SR, seconds=0.8, f=120.0, tau=0.12, lead_ms=5.0, seed=3):
    """A struck partial with a quiet noise floor, so every perturbation in the
    ladder has something to act on and decay_tau has a known tau."""
    n = int(seconds * sr)
    lead = int(lead_ms * 1e-3 * sr)
    t = np.arange(n - lead) / sr
    x = np.zeros(n)
    x[lead:] = 0.8 * np.exp(-t / tau) * np.sin(2 * math.pi * f * t)
    x += 1e-4 * np.random.default_rng(seed).standard_normal(n)
    return x


# ---------------------------------------------------------------------------
# 1. the committed definition is frozen, well-formed and admissible
# ---------------------------------------------------------------------------
def test_committed_definition_passes_every_rule():
    findings = cs.check_definition(_defn())
    bad = [f for f in findings if not f["ok"]]
    assert not bad, bad
    # and the rules actually ran: an empty findings list is not a pass
    assert {"C1", "C2", "C3", "C4", "C5", "C6", "C7"} <= {f["rule"] for f in findings}


def test_about_two_dozen_anchors_over_all_four_categories():
    d = _defn()
    assert 20 <= len(d["anchors"]) <= 28
    cats = [a["category"] for a in d["anchors"]]
    for c in ("tonal_drum", "noisy_drum", "metallic", "sustained_synth"):
        assert cats.count(c) >= 3, c


def test_an_edit_without_a_refreeze_is_caught():
    d = _defn()
    d["columns"]["defect"]["quantisation_bits"]["strengths"][0] = 15
    assert "C1" in _failed_rules(cs.check_definition(d))


def test_a_recording_in_two_splits_is_caught_by_CONTENT_not_path():
    """The naive form compares paths. The defeating input is the same bytes
    under another name -- exactly what a re-pressed pack is."""
    d = _defn()
    src = next(a for a in d["anchors"] if a["split"] == "calibration" and a["sha256"])
    twin = dict(src, id="twin", path="bd8/RENAMED.WAV", split="validation")
    d["anchors"].append(twin)
    d["freeze"]["definition_sha256"] = cs.definition_sha256(d)
    assert "C5" in _failed_rules(cs.check_definition(d))


def test_the_same_path_twice_is_caught_too():
    d = _defn()
    src = next(a for a in d["anchors"] if a["split"] == "calibration")
    d["anchors"].append(dict(src, id="dup", split="validation", sha256=None,
                             unfrozen_why="test"))
    d["freeze"]["definition_sha256"] = cs.definition_sha256(d)
    assert "C5" in _failed_rules(cs.check_definition(d))


def test_a_split_the_pack_has_no_role_for_is_caught():
    d = _defn()
    a = next(a for a in d["anchors"] if a["pack"] == "legowelt-minimoog-5529")
    a["split"] = "validation"
    d["freeze"]["definition_sha256"] = cs.definition_sha256(d)
    assert "C4" in _failed_rules(cs.check_definition(d))


def test_a_claimed_pack_in_held_out_validation_is_caught():
    d = _defn()
    a = next(a for a in d["anchors"] if a["split"] == "validation")
    a["pack"] = "808-from-mars"
    d["freeze"]["definition_sha256"] = cs.definition_sha256(d)
    assert "C4" in _failed_rules(cs.check_definition(d))


def test_software_reference_outside_development_is_caught():
    d = _defn()
    a = next(a for a in d["anchors"] if a["pack"] == "refprofile")
    a["split"] = "calibration"
    d["freeze"]["definition_sha256"] = cs.definition_sha256(d)
    assert "C4" in _failed_rules(cs.check_definition(d))


def test_an_excluded_recording_cannot_be_an_anchor():
    d = _defn()
    src = next(a for a in d["anchors"] if a["voice"] == "CY")
    d["anchors"].append(dict(src, id="holdout", path="cy8/CY2500.WAV", sha256=None,
                             unfrozen_why="test"))
    d["freeze"]["definition_sha256"] = cs.definition_sha256(d)
    assert "C6" in _failed_rules(cs.check_definition(d))


def test_an_unfrozen_anchor_must_say_why():
    d = _defn()
    a = next(a for a in d["anchors"] if a["pack"] == "legowelt-minimoog-5529")
    a.pop("unfrozen_why", None)
    d["freeze"]["definition_sha256"] = cs.definition_sha256(d)
    assert "C3" in _failed_rules(cs.check_definition(d))


def test_live_apparatus_drift_is_caught(monkeypatch):
    """A strength changed in perturbation_ladder.py but not in the frozen
    definition: the challenge set no longer describes what would run."""
    old = pl.PERTURBATIONS["clip_fraction"]
    monkeypatch.setitem(pl.PERTURBATIONS, "clip_fraction",
                        pl.Perturbation(old.name, old.strengths[:-1], old.apply,
                                        old.unit, old.no_op))
    assert "C7" in _failed_rules(cs.check_definition(_defn()))


def test_live_resolution_drift_is_caught(monkeypatch):
    cases = [pl.EstimatorCase(c.name, c.voice, c.measure,
                              c.resolution * (2 if c.name == "decay_tau" else 1),
                              c.relative, c.tone_hz)
             for c in pl.ESTIMATOR_CASES]
    monkeypatch.setattr(pl, "ESTIMATOR_CASES", cases)
    assert "C7" in _failed_rules(cs.check_definition(_defn()))


# ---------------------------------------------------------------------------
# 2. anchors are loaded only if they are the frozen bytes
# ---------------------------------------------------------------------------
def _write_wav(path: pathlib.Path, x, sr=SR):
    path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(str(path), sr, (np.clip(x, -1, 1) * 32767).astype(np.int16))


def test_anchor_with_wrong_bytes_is_refused(tmp_path):
    d = _defn()
    a = next(a for a in d["anchors"] if a["id"] == "bd5050")
    _write_wav(tmp_path / a["path"], _strike())
    with pytest.raises(cs.Refused, match="sha256"):
        cs.load_anchor(a, d, roots={a["pack"]: tmp_path})


def test_anchor_with_the_frozen_bytes_loads(tmp_path):
    d = copy.deepcopy(_defn())
    a = next(a for a in d["anchors"] if a["id"] == "bd5050")
    p = tmp_path / a["path"]
    _write_wav(p, _strike())
    a["sha256"] = hashlib.sha256(p.read_bytes()).hexdigest()
    x, sr = cs.load_anchor(a, d, roots={a["pack"]: tmp_path})
    assert sr == SR and len(x) == int(0.8 * SR)
    assert np.all(np.isfinite(x))


def test_missing_anchor_is_refused(tmp_path):
    d = _defn()
    a = next(a for a in d["anchors"] if a["id"] == "bd5050")
    with pytest.raises(cs.Refused):
        cs.load_anchor(a, d, roots={a["pack"]: tmp_path / "absent"})


def test_unfrozen_anchor_is_refused_even_when_the_file_exists(tmp_path):
    d = _defn()
    a = next(a for a in d["anchors"] if a["pack"] == "legowelt-minimoog-5529")
    _write_wav(tmp_path / a["path"], _strike())
    with pytest.raises(cs.Refused, match="not frozen"):
        cs.load_anchor(a, d, roots={a["pack"]: tmp_path})


def test_committed_software_anchor_loads_from_the_archive():
    """The refprofile anchors are committed, so this one runs everywhere."""
    d = _defn()
    a = next(a for a in d["anchors"] if a["pack"] == "refprofile")
    x, sr = cs.load_anchor(a, d)
    assert sr == 48000 and len(x) > 1000 and not am.is_silent(x)


# ---------------------------------------------------------------------------
# 3. the mel_dac floor guard
# ---------------------------------------------------------------------------
def test_mel_floor_is_positive_on_a_real_strike():
    p = cs.prepare_for_distance(_strike(), SR)
    f = cs.mel_floor(p, SR)
    assert np.isfinite(f) and f > 0


def test_mel_floor_refuses_digital_silence():
    """The defeating input: a floor of exactly zero makes every ratio infinite
    and every rung 'detected'."""
    with pytest.raises(cs.Refused, match="floor"):
        cs.mel_floor(np.zeros(SR), SR)


def test_mel_floor_refuses_non_finite_input():
    x = cs.prepare_for_distance(_strike(), SR)
    x[100] = np.nan
    with pytest.raises(cs.Refused):
        cs.mel_floor(x, SR)


# ---------------------------------------------------------------------------
# 4. the per-anchor run, its controls, and determinism
# ---------------------------------------------------------------------------
_ANCHOR = dict(id="synthetic", pack="test", path="-", voice="BD",
               category="tonal_drum", split="calibration")


def _constant(x, sr):
    return am.Estimate(1.0)


def _noisy(x, sr):
    # different answer for every different record -- a detector that fires on
    # every difference, which #158 calls useless
    h = int(hashlib.sha256(np.asarray(x, np.float64).tobytes()).hexdigest()[:8], 16)
    return am.Estimate(1.0 + (h % 1000) / 10.0)


def test_constant_estimator_is_blind_on_every_defect_column():
    r = cs.run_anchor(_ANCHOR, _strike(), SR, _defn(),
                      estimators=["decay_tau"], measures={"decay_tau": _constant},
                      with_mel=False)
    m = cs.coverage_matrix([r], _defn())["decay_tau"]
    for col in _defn()["columns"]["defect"]:
        assert m[col]["label"] in ("BLIND", "NO-VERDICT"), (col, m[col])
    assert any(m[c]["label"] == "BLIND" for c in _defn()["columns"]["defect"])
    for col in _defn()["columns"]["permitted"]:
        assert m[col]["label"] == "STILL", (col, m[col])


def test_estimator_that_answers_noise_false_alarms_on_permitted_columns():
    r = cs.run_anchor(_ANCHOR, _strike(), SR, _defn(),
                      estimators=["decay_tau"], measures={"decay_tau": _noisy},
                      with_mel=False)
    m = cs.coverage_matrix([r], _defn())["decay_tau"]
    assert m["pd_polarity"]["label"] == "FALSE-ALARM"
    assert m["delay_ms"]["label"] == "FALSE-ALARM"


def test_real_decay_tau_sees_clipping_and_tracks_the_pitch_correction():
    r = cs.run_anchor(_ANCHOR, _strike(), SR, _defn(), estimators=["decay_tau"],
                      with_mel=False)
    m = cs.coverage_matrix([r], _defn())["decay_tau"]
    # a 1/e decay read on a record hard-clipped to 2 % of its peak is wrong
    assert m["clip_fraction"]["label"] == "MOVED"
    # tau / a, in closed form, within resolution
    assert m["genuine_pitch"]["label"] == "TRACKS", m["genuine_pitch"]
    assert m["pd_polarity"]["label"] == "STILL"


def test_a_transform_that_changes_nothing_is_no_verdict_not_blind():
    """inject_tail_noise starts at 0.25 s; on a 0.2 s record it adds nothing.
    Counting that as 'not detected' would be a miss the defect never had the
    chance to cause."""
    x = _strike(seconds=0.2)
    r = cs.run_anchor(_ANCHOR, x, SR, _defn(), estimators=["decay_tau"],
                      measures={"decay_tau": _constant}, with_mel=False)
    cell = cs.coverage_matrix([r], _defn())["decay_tau"]["escape_tail_noise"]
    assert cell["label"] == "NO-VERDICT"
    assert cell["no_verdict"] == 1 and cell["scored"] == 0


def test_mel_dac_rows_carry_absolute_distance_and_floor_never_ratio_alone():
    r = cs.run_anchor(_ANCHOR, _strike(seconds=0.4), SR, _defn(), estimators=[],
                      with_mel=True)
    mel = r["mel_dac"]
    assert mel["status"] == "OK" and mel["floor"] > 0
    for col, rows in mel["columns"].items():
        for row in rows:
            if row.get("distance") is None:
                assert row.get("why")
                continue
            assert {"distance", "floor", "ratio"} <= set(row), (col, row)
            assert row["ratio"] == pytest.approx(row["distance"] / mel["floor"])


def test_mel_dac_sees_the_three_named_escapes_and_not_polarity():
    r = cs.run_anchor(_ANCHOR, _strike(seconds=0.6), SR, _defn(), estimators=[],
                      with_mel=True)
    m = cs.coverage_matrix([r], _defn())["mel_dac"]
    for col in ("escape_hf_tone", "escape_requantise_6bit", "escape_tail_noise"):
        assert m[col]["label"] == "MOVED", (col, m[col])
    assert m["pd_polarity"]["label"] == "STILL"


def test_run_anchor_is_deterministic():
    d = _defn()
    a = cs.run_anchor(_ANCHOR, _strike(seconds=0.4), SR, d, estimators=["decay_tau"])
    b = cs.run_anchor(_ANCHOR, _strike(seconds=0.4), SR, d, estimators=["decay_tau"])
    # not vacuous: the start-red stub returned {} twice and passed this line
    assert a["estimators"]["decay_tau"]["status"] == "OK"
    assert a["mel_dac"]["status"] == "OK"
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# ---------------------------------------------------------------------------
# 5. rank reversals
# ---------------------------------------------------------------------------
def test_no_reversal_on_a_monotone_ladder():
    assert cs.rank_reversals([(1, 0.0), (2, 0.5), (3, 1.0), (4, 3.0)], tol=0.1) == []


def test_a_stronger_rung_reading_smaller_is_a_reversal():
    rev = cs.rank_reversals([(1, 0.0), (2, 2.0), (3, 0.3), (4, 3.0)], tol=0.1)
    assert len(rev) == 1 and rev[0]["weaker"] == 2 and rev[0]["stronger"] == 3


def test_a_reversal_inside_tolerance_is_not_reported():
    assert cs.rank_reversals([(1, 1.0), (2, 0.95)], tol=0.1) == []


# ---------------------------------------------------------------------------
# 6. re-measure on change: the staleness rule, tied to scorecard.compare()'s
# ---------------------------------------------------------------------------
def test_identity_covers_the_scorecards_apparatus():
    import scorecard as sc
    ident = cs.apparatus_identity()
    for p in ("model/audio_measure.py", "tools/run_case.py"):
        assert p in sc.APPARATUS and p in ident


def test_results_measured_under_another_estimator_are_stale():
    ident = cs.apparatus_identity()
    old = dict(ident)
    old["model/audio_measure.py"] = "0" * 64
    stale = cs.staleness({"apparatus": old}, ident)
    assert "model/audio_measure.py" in stale


def test_results_under_the_same_apparatus_are_not_stale():
    ident = cs.apparatus_identity()
    assert cs.staleness({"apparatus": dict(ident)}, ident) == []


def test_results_with_no_apparatus_record_are_stale_not_fresh():
    assert cs.staleness({}, cs.apparatus_identity())


# ---------------------------------------------------------------------------
# 7. the committed results describe the frozen definition
# ---------------------------------------------------------------------------
def test_committed_results_are_for_the_committed_definition():
    res = json.loads(cs.RESULTS.read_text())
    assert res["definition_sha256"] == _defn()["freeze"]["definition_sha256"]


def test_report_renders_deterministically_and_keeps_the_floor_visible():
    res = json.loads(cs.RESULTS.read_text())
    a, b = cs.report_text(res), cs.report_text(res)
    assert a == b
    assert "shift(p, 1)" in a                     # the floor's definition
    assert "out-of-band" in a.lower()             # the over-time criterion
