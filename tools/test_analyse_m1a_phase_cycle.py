"""Known answers for the M1A phase-cycle analysis.

The synthetic takes have a psi-dependent odd-partial gain known by
construction, so the decision rule's every branch and the darkening estimate
have an answer independent of both the Mini V3 and the model. Injected
analysis defects must turn the control red."""
import hashlib
import io
import json
import os
import shutil
import struct
import sys

import numpy as np
import pytest
from scipy.io import wavfile

import analyse_m1a_phase_cycle as a


@pytest.fixture(scope="module")
def control():
    return a.synthetic_control()


def test_known_answer_control_passes(control):
    assert all(v["ok"] for v in control.values()), control


@pytest.mark.parametrize("defect", ["psi_offset_180", "ignore_take_agreement"])
def test_injected_defects_turn_the_control_red(defect):
    report = a.synthetic_control(defect)
    assert not all(v["ok"] for v in report.values())


def test_bins_centre_on_zero_and_wrap():
    assert a.bin_of(0.) == 0 and a.bin_of(9.9) == 0 and a.bin_of(-9.9) == 0
    assert a.bin_of(180.) == a.bin_of(-179.) == 9
    assert a.bin_centre(a.bin_of(-40.)) == pytest.approx(-40.)


def test_locked_oscillators_refuse_for_coverage():
    # a static-phase render cannot be compared: psi never leaves one bin
    mix, o1, o2, _ = a.synth_take(-10., 0., 0.)
    m = a.measure(mix, o1, o2, .1, 12.)
    with pytest.raises(a.Refused, match="coverage"):
        a.summarise(m["windows"])


def test_psi_that_is_not_one_number_refuses():
    mix, o1, o2, _ = a.synth_take(0., 0., -.2634)
    rng = np.random.default_rng(3)
    t = np.arange(len(o2)) / a.SR
    # oscillator 2's partials with unrelated phases: theta_j != j*psi
    scrambled = sum(.05 * .535 / j * np.sin(2 * np.pi * j * (2 * a.F36 - .2634) * t + rng.uniform(0, 2 * np.pi))
                    for j in range(1, a.JMAX + 1)) * (o2 != 0)
    with pytest.raises(a.Refused, match="single relative phase"):
        a.measure(mix, o1, scrambled, .1, 12.)


def _summary(dark, rng_db, darkest=0.):
    parts = {h: {"dark_db": dark, "range_db": rng_db, "darkest_bin_deg": darkest,
                 "binned_dbfs": [0.] * a.NBINS} for h in a.ODD}
    return {"partials": parts}


def test_decision_boundaries_are_the_preregistered_ones():
    model = _summary(-10., 11.)
    assert a.decide(_summary(0., .9), _summary(0., .9), _summary(0., .9), model)["verdict"] == "SUPPORTS-DRIVE"
    assert a.decide(_summary(0., 1.1), _summary(0., 1.1), _summary(0., 1.1), model)["verdict"] == "INCONCLUSIVE"
    assert a.decide(_summary(-5., 6.), _summary(-5., 6.), _summary(-5., 6.), model)["verdict"] == "REDIRECT-TO-PHASE"
    assert a.decide(_summary(-5., 6., 60.), _summary(-5., 6., 60.), _summary(-5., 6., 60.),
                    model)["verdict"] == "INCONCLUSIVE"            # darkest away from 0
    assert a.decide(_summary(-4.9, 6.), _summary(-4.9, 6.), _summary(-4.9, 6.), model)["verdict"] == "INCONCLUSIVE"
    weak_model = _summary(-2.9, 4.)
    assert a.decide(_summary(0., .5), _summary(0., .5), _summary(0., .5), weak_model)["step"] == 2
    disagree = _summary(-1.2, 1.)
    assert a.decide(_summary(0., .5), disagree, _summary(0., .5), model)["step"] == 1


# ---- a capture record that cannot be read is REFUSED, not a traceback (#1604)
def _analyse(monkeypatch, tmp_path, capsys, record):
    """`main()` over a capture record written under a scratch CAPTURE dir.

    The known-answer control is answered, not exercised: it has its own tests
    above and costs ~20 s a run. The record and the reader are real."""
    (tmp_path / "capture.json").write_text(json.dumps(record))
    monkeypatch.setattr(a, "CAPTURE", tmp_path)
    monkeypatch.setattr(a, "synthetic_control", lambda defect=None: {"stub": {"ok": True}})
    monkeypatch.setattr(sys, "argv", ["analyse_m1a_phase_cycle.py"])    # main() reads argv for --control
    code = a.main()
    return code, capsys.readouterr().out


def test_the_committed_capture_carries_the_key_the_analysis_reads():
    # the guard below must be satisfiable against the record that ships
    assert json.loads((a.CAPTURE / "capture.json").read_text())["takes"] is not None


def test_a_capture_record_without_takes_is_refused(monkeypatch, tmp_path, capsys):
    # #1604's own input: `KeyError: 'takes'`, exit 1, before the repair
    code, out = _analyse(monkeypatch, tmp_path, capsys, {"state": "CAPTURED"})
    assert code == 2 and "REFUSED" in out and "'takes'" in out
    # and nothing was reported as data
    assert not (tmp_path / "report.json").exists()
    assert json.loads((tmp_path / "analysis-refused.json").read_text())["state"] == "REFUSED"


def test_a_capture_record_with_null_takes_is_refused(monkeypatch, tmp_path, capsys):
    code, out = _analyse(monkeypatch, tmp_path, capsys, {"state": "CAPTURED", "takes": None})
    assert code == 2 and "'takes'" in out


@pytest.mark.parametrize("record", [[], 5, None])
def test_a_capture_record_that_is_not_an_object_is_refused(monkeypatch, tmp_path, capsys, record):
    code, out = _analyse(monkeypatch, tmp_path, capsys, record)
    assert code == 2 and "REFUSED" in out


def test_an_uncaptured_record_keeps_its_own_refusal(monkeypatch, tmp_path, capsys):
    # the refusal that predates #1604: a record that is not CAPTURED says so,
    # whatever else it carries
    code, out = _analyse(monkeypatch, tmp_path, capsys, {"state": "ABANDONED", "takes": {}})
    assert code == 2 and "not CAPTURED" in out


# ---- a WAV that cannot be read is REFUSED, naming it -- never an exit-1 traceback (#1862)
#
# `wavfile.read` signals a malformed file with more than one exception class:
# ValueError for a non-RIFF header, struct.error for one cut inside a chunk,
# UnboundLocalError for a size field that lies (scipy 1.18.1). The corpus carries
# one input per class, so a guard that allow-listed (OSError, ValueError) --
# the shape this repair started from -- goes red here.
CONDITIONS = ("full", "osc1_open", "osc2_open")


def _bad_wavs():
    buf = io.BytesIO()
    wavfile.write(buf, a.SR, np.zeros(4800, dtype=np.int16))
    good = buf.getvalue()
    fmt, data = good[20:36], good[44:]
    chunk = lambda cid, payload: cid + struct.pack("<I", len(payload)) + payload
    return {"non-RIFF": b"not a WAV", "empty": b"", "cut-inside-chunk": good[:20],
            "RIFF-magic-only": b"RIFF",
            "size-field-lies": b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt)) + b"WAVE"
                              + chunk(b"fmt ", fmt) + chunk(b"data", data)}


BAD_WAVS = {"missing": None, **_bad_wavs()}


def _scratch_capture(tmp_path, monkeypatch):
    """The committed capture record under a scratch CAPTURE, its WAVs symlinked,
    so a refusal artefact lands in tmp_path and never in the tree."""
    shutil.copy(a.CAPTURE / "capture.json", tmp_path / "capture.json")
    for take in json.loads((tmp_path / "capture.json").read_text())["takes"].values():
        for row in take["conditions"].values():
            os.symlink(a.CAPTURE / row["file"], tmp_path / row["file"])
    monkeypatch.setattr(a, "CAPTURE", tmp_path)


def _past_the_capture(monkeypatch):
    """Answer the steps before the committed-model read; the reader is real."""
    monkeypatch.setattr(a, "synthetic_control", lambda defect=None: {"stub": {"ok": True}})
    monkeypatch.setattr(a, "measure", lambda *args, **kw: {
        "windows": [], "drift_hz": -.26, "octave_offset_cents": -3.49})


@pytest.mark.parametrize("name", sorted(BAD_WAVS))
def test_unreadable_committed_model_wav_is_refused_naming_it(monkeypatch, tmp_path, capsys, name):
    _scratch_capture(tmp_path, monkeypatch)
    _past_the_capture(monkeypatch)
    model = tmp_path / "m1a-model.wav"
    if BAD_WAVS[name] is not None:
        model.write_bytes(BAD_WAVS[name])
    monkeypatch.setattr(a.d, "MODEL_WAV", model)
    monkeypatch.setattr(sys, "argv", ["analyse_m1a_phase_cycle.py"])
    code = a.main()
    out = capsys.readouterr().out
    assert code == 2 and "REFUSED:" in out and "m1a-model.wav" in out
    refused = json.loads((tmp_path / "analysis-refused.json").read_text())
    assert refused["state"] == "REFUSED" and "m1a-model.wav" in refused["reason"]
    assert not (tmp_path / "report.json").exists()


def _capture_record():
    row = {"file": "takeA-full.wav", "sha256": "0" * 64}
    return {"state": "CAPTURED", "takes": {"A": {"note_on_s": .1, "gate_s": 12.,
            "conditions": {c: dict(row) for c in CONDITIONS}}}}


def test_a_missing_capture_wav_is_refused_naming_it(monkeypatch, tmp_path, capsys):
    code, out = _analyse(monkeypatch, tmp_path, capsys, _capture_record())
    assert code == 2 and "REFUSED" in out and "takeA-full.wav" in out
    assert json.loads((tmp_path / "analysis-refused.json").read_text())["state"] == "REFUSED"
    assert not (tmp_path / "report.json").exists()


@pytest.mark.parametrize("name", sorted(_bad_wavs()))
def test_an_unreadable_capture_wav_matching_its_recorded_hash_is_refused(monkeypatch, tmp_path, capsys, name):
    """The hash binds the file to the record; when the record names the bad
    bytes themselves, only the reader is left to refuse."""
    blob = _bad_wavs()[name]
    (tmp_path / "takeA-full.wav").write_bytes(blob)
    record = _capture_record()
    record["takes"]["A"]["conditions"]["full"]["sha256"] = hashlib.sha256(blob).hexdigest()
    code, out = _analyse(monkeypatch, tmp_path, capsys, record)
    assert code == 2 and "takeA-full.wav" in out
