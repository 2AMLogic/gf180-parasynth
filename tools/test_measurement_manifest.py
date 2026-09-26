#!/usr/bin/env python3
"""The schema's own controls.

A validator is a gate, and a gate nobody has watched fail is not a gate. So the
shape of almost every test here is: take a record that IS complete, break ONE
thing, and assert the break is reported. `test_every_declared_field_is_actually
_enforced` does that for all ten declared fields by construction -- a field
added to `MEASUREMENT_SCHEMA` and then never checked would pass a hand-written
suite and fails this one.

`test_a_bound_moved_silently_would_have_turned_the_v1_defect_green` is the one
worth reading: it shows the exact whitewash the changelog check prevents, by
computing the verdict both ways.
"""
from __future__ import annotations

import copy
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import measurement_manifest as mm                                    # noqa: E402


# ---------------------------------------------------------------------------
# a complete record, written out by hand so this suite does not depend on the
# producer it is meant to police
# ---------------------------------------------------------------------------
def complete_measurement(**over) -> dict:
    m = {
        "metric": "T20", "units": "ms", "valid": True, "value": 307.9792,
        "analyser": {"name": "drum decay from a per-voice moving-RMS envelope",
                     "version": "v2",
                     "method": "trim_onset -> moving RMS (12 ms) -> first sample at "
                               "or below -20 dB of the envelope peak"},
        "selection": {"hit_index": 0, "onset_s": 0.0094, "interval_s": [0.014, 0.322],
                      "interval_basis": "the envelope peak to its first -20 dB "
                                        "crossing"},
        "conditioning": {"channel": "mono, the summed drum buses",
                         "sample_rate_hz": 48000,
                         "resampled_from_hz": "not applicable: rendered and analysed "
                                              "at 48000 Hz",
                         "filter": "none on the envelope path",
                         "normalisation": "none: measured relative to the envelope's "
                                          "own peak"},
        "spectral": {"transform": "not applicable: a threshold crossing takes no "
                                  "transform",
                     "window": "moving RMS, 12.0 ms",
                     "nfft": "not applicable: no transform is taken",
                     "hop_samples": "not applicable: evaluated at every sample"},
        "quality": {"fit": "not applicable: a crossing is located, not fitted",
                    "uncertainty": 0.0208,
                    "uncertainty_basis": "+/-1 sample at 48 kHz",
                    "noise_floor_db": -74.2,
                    "noise_floor_treatment": "-20 dB is far above the render's floor"},
        "reference": {"kind": "model-lock",
                      "identity": "model/sound_report.py LOCKS at commit ce400a6",
                      "sha256": "sha256:deadbeefdeadbeef",
                      "matched_settings": "one BD hit at accent 1.0 through the whole "
                                          "drum path at 48000 Hz"},
        "bound": {"value": 307.979, "tolerance": 36.0, "units": "ms", "kind": "lock",
                  "rationale": "locked at ce400a6; comparable with Roland's chart "
                               "column (300 ms)",
                  "history": [{"at": "ce400a6", "recorded_by": "28dfd55",
                               "from": None, "to": 307.979,
                               "reason": "the lock table introduced with this report"}]},
    }
    m.update(over)
    return m


def analysis_of(measurements, *, render_id="render-aaaa", audio="sha256:aaaa",
                analyser_version="v2") -> dict:
    return {"schema_version": mm.SCHEMA_VERSION, "stage": "analyse",
            "id": f"analyse-{analyser_version}-{render_id}",
            "of_render": render_id, "render_audio": {"audio": audio},
            "analyser": {"id": f"an-{analyser_version}", "version": analyser_version},
            "measurements": measurements, "retention_class": "smoke"}


def test_the_hand_written_complete_record_is_accepted():
    assert mm.validate_measurement(complete_measurement()) == []


# ---------------------------------------------------------------------------
# start red: every declared field must actually be enforced
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("field", sorted(mm.MEASUREMENT_SCHEMA))
def test_every_declared_field_is_actually_enforced(field):
    """Dropping any one declared field must be reported.

    The failure mode this catches is a schema that grows a field and never
    checks it -- which reads as coverage and is not."""
    m = complete_measurement()
    del m[field]
    problems = mm.validate_measurement(m)
    assert problems, f"{field} was dropped and nothing complained"
    assert any(field in p for p in problems), problems


@pytest.mark.parametrize("field,sub", [(f, s) for f, subs in
                                       mm.MEASUREMENT_SCHEMA.items() for s in subs])
def test_every_declared_subfield_is_actually_enforced(field, sub):
    m = complete_measurement()
    del m[field][sub]
    problems = mm.validate_measurement(m)
    assert problems, f"{field}.{sub} was dropped and nothing complained"
    assert any(sub in p for p in problems), problems


@pytest.mark.parametrize("placeholder", ["", None, "n/a", "N/A", "unknown", "TBD",
                                         "not applicable", " none ", "?"])
def test_a_placeholder_is_not_a_statement(placeholder):
    """The cheap way to satisfy a schema is to type "n/a" into every box, and
    the result looks exactly like a complete record."""
    assert not mm.stated(placeholder)
    m = complete_measurement()
    m["conditioning"]["normalisation"] = placeholder
    assert any("normalisation" in p for p in mm.validate_measurement(m))


def test_an_inapplicable_field_is_accepted_only_when_it_says_why():
    assert not mm.stated("not applicable")
    assert not mm.stated("not applicable: x")
    assert mm.stated("not applicable: a threshold crossing takes no transform")


def test_a_number_that_is_not_finite_is_not_a_statement():
    assert not mm.stated(float("nan"))
    assert not mm.stated(float("inf"))
    assert mm.stated(0.0)
    assert mm.stated(0)


def test_a_valid_measurement_with_a_nan_value_is_refused():
    m = complete_measurement(value=float("nan"))
    assert any("value" in p for p in mm.validate_measurement(m))


def test_an_invalid_measurement_needs_a_reason_and_may_not_carry_an_error():
    m = complete_measurement(valid=False, value=None)
    assert any("no stated reason" in p for p in mm.validate_measurement(m))
    m["why"] = "the estimator refused: the envelope peak is zero"
    assert mm.validate_measurement(m) == []
    m["error"] = 0.0
    assert any("error key" in p for p in mm.validate_measurement(m))


def test_a_units_category_error_is_reported():
    """`CP decay tau 47 ms` was the E_CPTAIL REGISTER, not the voice's decay."""
    m = complete_measurement(units="register LSB")
    assert any("bounded in" in p for p in mm.validate_measurement(m))


def test_an_unordered_interval_is_reported():
    m = complete_measurement()
    m["selection"]["interval_s"] = [0.322, 0.014]
    assert any("interval_s" in p for p in mm.validate_measurement(m))
    m["selection"]["interval_s"] = 0.3
    assert any("interval_s" in p for p in mm.validate_measurement(m))


def test_a_recording_reference_must_state_its_control_settings():
    """The study that drove our snare with the wrong TONE law would have passed
    every other check in this file."""
    m = complete_measurement()
    m["reference"] = {"kind": "recording", "identity": "bd8/BD5050.WAV",
                      "sha256": "sha256:1234", "matched_settings": ""}
    problems = mm.validate_measurement(m)
    assert any("matched control settings" in p for p in problems), problems


def test_an_unknown_reference_kind_is_reported():
    m = complete_measurement()
    m["reference"]["kind"] = "vibes"
    assert any("reference.kind" in p for p in mm.validate_measurement(m))


def test_two_measurements_with_the_same_name_are_reported():
    ms = [complete_measurement(), complete_measurement()]
    assert any("twice" in p for p in mm.validate_measurements(ms))


def test_no_measurements_at_all_is_a_problem_not_an_empty_pass():
    assert mm.validate_measurements([])
    assert mm.validate_measurements("not a list")


# ---------------------------------------------------------------------------
# bounds and their changelog
# ---------------------------------------------------------------------------
def test_a_bound_with_no_rationale_is_reported():
    m = complete_measurement()
    m["bound"]["rationale"] = ""
    assert any("rationale" in p for p in mm.validate_measurement(m))


def test_a_bound_with_no_history_is_reported():
    m = complete_measurement()
    m["bound"]["history"] = []
    assert any("history" in p for p in mm.validate_measurement(m))


def test_a_history_entry_with_no_reason_is_reported():
    m = complete_measurement()
    del m["bound"]["history"][0]["reason"]
    assert any("reason" in p for p in mm.validate_measurement(m))


def test_a_first_lock_may_record_from_as_null_but_the_key_must_be_present():
    m = complete_measurement()
    assert m["bound"]["history"][0]["from"] is None
    assert mm.validate_measurement(m) == []
    del m["bound"]["history"][0]["from"]
    assert any("no from field" in p for p in mm.validate_measurement(m))


def test_a_bound_that_moved_without_an_entry_is_reported():
    m = complete_measurement()
    m["bound"]["value"] = 207.0
    problems = mm.validate_measurement(m)
    assert any("moved with no reason recorded" in p for p in problems), problems


def test_a_bound_kind_outside_target_or_lock_is_reported():
    m = complete_measurement()
    m["bound"]["kind"] = "vibes"
    assert any("bound.kind" in p for p in mm.validate_measurement(m))


# ---------------------------------------------------------------------------
# accept
# ---------------------------------------------------------------------------
def test_an_incomplete_record_gets_no_verdict_and_never_a_pass():
    m = complete_measurement()
    del m["units"]
    v = mm.accept(analysis_of([m]), criteria_id="c", criteria_rationale="why")
    assert v["outcome"] == mm.REFUSED
    assert v["outcome_code"] == 2, "no evidence is not the same code as a bad result"
    assert v["schema_problems"]


def test_a_complete_record_inside_its_bound_passes():
    v = mm.accept(analysis_of([complete_measurement()]), criteria_id="c",
                  criteria_rationale="why")
    assert v["outcome"] == "pass" and v["outcome_code"] == 0
    assert v["metrics"]["T20"]["bound_rationale"]


def test_a_complete_record_outside_its_bound_fails_with_code_one():
    v = mm.accept(analysis_of([complete_measurement(value=207.04)]),
                  criteria_id="c", criteria_rationale="why")
    assert v["outcome"] == "fail" and v["outcome_code"] == 1


def test_an_invalid_measurement_is_no_verdict_not_a_fail():
    m = complete_measurement(valid=False, value=None,
                             why="the estimator refused: no sample reached -20 dB")
    v = mm.accept(analysis_of([m]), criteria_id="c", criteria_rationale="why")
    assert v["outcome"] == "no verdict" and v["outcome_code"] == 2
    assert "error" not in v["metrics"]["T20"]


def test_no_verdict_outranks_fail_which_outranks_pass():
    ms = [complete_measurement(),
          complete_measurement(metric="decay tau", value=999.0,
                               units="ms",
                               bound=dict(complete_measurement()["bound"],
                                          value=144.0,
                                          history=[{"at": "7be1490", "from": 127.0,
                                                    "to": 144.0,
                                                    "reason": "DR 0009"}]))]
    assert mm.accept(analysis_of(ms), criteria_id="c",
                     criteria_rationale="w")["outcome"] == "fail"
    ms.append(complete_measurement(metric="attack", valid=False, value=None,
                                   why="the estimator refused"))
    assert mm.accept(analysis_of(ms), criteria_id="c",
                     criteria_rationale="w")["outcome"] == "no verdict"


def test_a_bound_moved_silently_would_have_turned_the_v1_defect_green():
    """The whitewash, computed both ways.

    The withdrawn 5 ms-moving-average estimator reads BD T20 as 207.04 ms
    against a lock of 307.979 -- a fail. Move the lock to 207.0 and the same
    measurement passes, with no line of measurement code touched. That is the
    "did it go green because the implementation improved, or because a tolerance
    moved" question, and the only thing standing between the two answers is the
    changelog."""
    measured = complete_measurement(value=207.0417)
    as_is = mm.accept(analysis_of([measured]), criteria_id="c", criteria_rationale="w")
    assert as_is["outcome"] == "fail"

    whitewashed = copy.deepcopy(measured)
    whitewashed["bound"]["value"] = 207.0
    refused = mm.accept(analysis_of([whitewashed]), criteria_id="c",
                        criteria_rationale="w")
    assert refused["outcome"] == mm.REFUSED, "a silently moved bound got a verdict"
    assert any("moved with no reason recorded" in p
               for p in refused["schema_problems"])

    # and what it WOULD have been, had nothing checked: a pass. This is the
    # control that proves the check is load-bearing rather than decorative.
    declared = copy.deepcopy(whitewashed)
    declared["bound"]["history"].append(
        {"at": "HEAD", "recorded_by": "(this test)", "from": 307.979, "to": 207.0,
         "reason": "a deliberately indefensible relaxation, recorded so that it is "
                   "visible: this is what moving a bound to fit a broken estimator "
                   "looks like when it is written down"})
    now_green = mm.accept(analysis_of([declared]), criteria_id="c",
                          criteria_rationale="w")
    assert now_green["outcome"] == "pass"
    assert now_green["metrics"]["T20"]["bound_last_changed"]["reason"]


# ---------------------------------------------------------------------------
# attribution
# ---------------------------------------------------------------------------
def test_the_measurement_changed_and_the_sound_did_not():
    a = analysis_of([complete_measurement(value=207.04)], analyser_version="v1")
    b = analysis_of([complete_measurement(value=307.98)], analyser_version="v2")
    d = mm.classify_delta(a, b)
    assert d["attribution"] == mm.MEASUREMENT_ONLY and d["attributable"]
    assert d["metrics"]["T20"]["saw"] == "MOVED"


def test_the_sound_changed_and_the_measurement_did_not():
    a = analysis_of([complete_measurement(value=307.98)], render_id="render-a",
                    audio="sha256:a")
    b = analysis_of([complete_measurement(value=186.10)], render_id="render-b",
                    audio="sha256:b")
    d = mm.classify_delta(a, b)
    assert d["attribution"] == mm.SOUND_ONLY and d["attributable"]


def test_both_changed_is_refused_rather_than_guessed():
    a = analysis_of([complete_measurement(value=207.04)], render_id="render-a",
                    audio="sha256:a", analyser_version="v1")
    b = analysis_of([complete_measurement(value=186.10)], render_id="render-b",
                    audio="sha256:b", analyser_version="v2")
    d = mm.classify_delta(a, b)
    assert not d["attributable"]
    assert d["render_changed"] and d["analyser_changed"]


def test_the_same_render_id_with_different_audio_still_counts_as_changed():
    """A render id that matches while the bytes differ means an input is not
    being recorded, so trusting the id would attribute a real sound change to
    the measurement."""
    a = analysis_of([complete_measurement()], audio="sha256:a")
    b = analysis_of([complete_measurement()], audio="sha256:b")
    assert mm.classify_delta(a, b)["render_changed"]


def test_a_metric_inside_its_own_tolerance_reads_blind_not_moved():
    a = analysis_of([complete_measurement(value=307.98)], analyser_version="v1")
    b = analysis_of([complete_measurement(value=310.00)], analyser_version="v2")
    assert mm.classify_delta(a, b)["metrics"]["T20"]["saw"] == "BLIND"


def test_a_changed_bound_is_reported_separately_from_a_changed_value():
    a = analysis_of([complete_measurement()], analyser_version="v1")
    m = complete_measurement()
    m["bound"] = dict(m["bound"], tolerance=100.0)
    b = analysis_of([m], analyser_version="v2")
    assert mm.classify_delta(a, b)["bounds_changed"] == ["T20"]


# ---------------------------------------------------------------------------
# identity and the store
# ---------------------------------------------------------------------------
def test_a_render_id_is_the_same_for_the_same_inputs_and_config():
    kw = dict(case="BD-solo", code_version={"drums_fx": "sha256:1"},
              config={"accent": 1.0}, inputs={"a": "sha256:2"})
    assert mm.make_render(**kw)["id"] == mm.make_render(**kw)["id"], (
        "the render id must not depend on the clock, or every re-render reads as "
        "a sound change and the scheme says nothing")
    other = dict(kw, config={"accent": 0.6})
    assert mm.make_render(**other)["id"] != mm.make_render(**kw)["id"]


def test_a_render_id_moves_when_the_rendering_code_moves():
    kw = dict(case="BD-solo", code_version={"drums_fx": "sha256:1"},
              config={"accent": 1.0}, inputs={"a": "sha256:2"})
    assert (mm.make_render(**dict(kw, code_version={"drums_fx": "sha256:9"}))["id"]
            != mm.make_render(**kw)["id"])


def test_an_analysis_id_moves_with_the_analyser_and_with_the_audio():
    r1 = mm.make_render(case="c", code_version={}, config={}, inputs={})
    r1["artefacts"] = {"audio": {"sha256": "sha256:aaa", "path": "a.wav"}}
    r2 = copy.deepcopy(r1)
    r2["artefacts"]["audio"]["sha256"] = "sha256:bbb"
    ms = [complete_measurement()]
    base = mm.make_analysis(render=r1, analyser={"v": 1}, measurements=ms)["id"]
    assert mm.make_analysis(render=r1, analyser={"v": 2}, measurements=ms)["id"] != base
    assert mm.make_analysis(render=r2, analyser={"v": 1}, measurements=ms)["id"] != base
    assert mm.make_analysis(render=r1, analyser={"v": 1}, measurements=ms)["id"] == base


def test_an_analysis_id_does_not_move_when_only_the_value_moves():
    """Deliberate: the id identifies the METHOD and the INPUT, not the answer.
    If the same method on the same bytes gave two answers, that is what
    `classify_delta` reporting "nothing changed" over a moved value is for."""
    r = mm.make_render(case="c", code_version={}, config={}, inputs={})
    r["artefacts"] = {"audio": {"sha256": "sha256:aaa", "path": "a.wav"}}
    a = mm.make_analysis(render=r, analyser={"v": 1},
                         measurements=[complete_measurement(value=1.0)])
    b = mm.make_analysis(render=r, analyser={"v": 1},
                         measurements=[complete_measurement(value=2.0)])
    assert a["id"] == b["id"]
    d = mm.classify_delta(a, b)
    assert d["attribution"] == mm.NEITHER


def test_an_analysis_carrying_an_incomplete_record_is_marked_refused_on_write():
    r = mm.make_render(case="c", code_version={}, config={}, inputs={})
    m = complete_measurement()
    del m["units"]
    a = mm.make_analysis(render=r, analyser={"v": 1}, measurements=[m])
    assert a["outcome"] == mm.REFUSED and a["schema_problems"]


def test_write_and_read_round_trip_and_the_index(tmp_path):
    r = mm.make_render(case="c", code_version={}, config={}, inputs={})
    mm.write_stage(tmp_path, r)
    assert mm.read_stage(tmp_path, "render", r["id"])["id"] == r["id"]
    a = mm.make_analysis(render=r, analyser={"v": 1},
                         measurements=[complete_measurement()])
    mm.write_stage(tmp_path, a)
    v = mm.accept(a, criteria_id="c", criteria_rationale="why")
    mm.write_stage(tmp_path, v)
    assert (tmp_path / "runs" / r["id"] / "manifest.json").exists()
    assert (tmp_path / "jobs" / a["id"] / "manifest.json").exists()
    assert (tmp_path / "jobs" / v["id"] / "verdict.json").exists()
    index = json.loads((tmp_path / "index.json").read_text())
    assert {e["stage"] for e in index} == {"render", "analyse", "accept"}
    with pytest.raises(FileNotFoundError):
        mm.read_stage(tmp_path, "render", "render-nope")


def test_binding_refuses_a_tampered_artefact(tmp_path):
    r = mm.make_render(case="c", code_version={}, config={}, inputs={})
    d = tmp_path / "runs" / r["id"] / "audio"
    d.mkdir(parents=True)
    (d / "x.wav").write_bytes(b"not really a wav, but it hashes")
    r["artefacts"] = {"audio": {"path": "audio/x.wav",
                                "sha256": mm.sha256_file(d / "x.wav")}}
    assert mm.bind_to_retained(tmp_path, r, "audio")[1] == r["artefacts"]["audio"]["sha256"]
    (d / "x.wav").write_bytes(b"not really a wav, but it hashe$")
    with pytest.raises(ValueError, match="REFUSED"):
        mm.bind_to_retained(tmp_path, r, "audio")


def test_binding_refuses_when_the_audio_was_never_retained(tmp_path):
    r = mm.make_render(case="c", code_version={}, config={}, inputs={})
    with pytest.raises(FileNotFoundError):
        mm.bind_to_retained(tmp_path, r, "audio")
    r["artefacts"] = {"audio": {"path": "audio/gone.wav", "sha256": "sha256:x"}}
    with pytest.raises(FileNotFoundError, match="not retained|not on disk"):
        mm.bind_to_retained(tmp_path, r, "audio")


def test_a_reference_fixture_outlives_a_smoke_run():
    assert (mm.RETENTION_DAYS["reference-fixture"] > mm.RETENTION_DAYS["smoke"]
            and mm.RETENTION_DAYS["release-evidence"] > mm.RETENTION_DAYS["smoke"]), (
        "the scope note asks for reference fixtures and release evidence to be kept "
        "longer than smoke-test output; equal retention is not that")


def test_provenance_carries_the_uncommitted_tree_not_only_the_commit():
    """A clean SHA that silently means "plus whatever was in the working tree" is
    worse than no SHA. `run_case.worktree_state` is reused for this rather than
    a second implementation."""
    b = mm.provenance_block()
    w = b["worktree"]
    assert "refused" in w or ("uncommitted_sha256" in w and "dirty" in w)


def test_an_unknown_stage_cannot_get_an_id():
    with pytest.raises(ValueError):
        mm.stage_id("measure", {"a": 1})


def test_a_defeated_stated_defeats_the_whole_schema(monkeypatch):
    """A mutation control on the validator itself, not on a record.

    Every field check in `validate_measurement` goes through `stated`, so if
    `stated` were wrong the schema would be decorative and every test above
    would still pass -- they assert that BREAKING a record is reported, and a
    validator that reports nothing fails them, but a validator that reports the
    absence of a field while accepting "n/a" in every field would pass most of
    them. This observes the mutant failing rather than assuming it would."""
    m = complete_measurement()
    for sub in ("normalisation", "filter", "channel"):
        m["conditioning"][sub] = "n/a"
    m["quality"]["uncertainty_basis"] = "TBD"
    assert len(mm.validate_measurement(m)) == 4

    monkeypatch.setattr(mm, "stated", lambda v: v is not None)
    assert mm.validate_measurement(m) == [], (
        "with `stated` replaced by 'anything not None counts', a record whose "
        "conditioning is four boxes of n/a is accepted -- so `stated` is the "
        "load-bearing part and the placeholder vocabulary is not decoration")
