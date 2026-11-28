"""Known answers for the M1A phase-cycle analysis.

The synthetic takes have a psi-dependent odd-partial gain known by
construction, so the decision rule's every branch and the darkening estimate
have an answer independent of both the Mini V3 and the model. Injected
analysis defects must turn the control red."""
import json
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


# ---- the committed-model-audio precondition binds the WHOLE file (#2114)
COMMITTED = 359956            # samples in the shipped m1a-model.wav: 360000 - 44


def _committed():
    return wavfile.read(a.d.MODEL_WAV)[1]


def _model_wav(tmp_path, pcm):
    path = tmp_path / "m1a-model.wav"
    wavfile.write(path, a.SR, np.asarray(pcm, dtype=np.int16))
    return path


def test_expected_committed_samples_comes_from_the_apparatus_not_the_file():
    manifest, reference = a.bass.load_reference()
    expected = a.expected_committed_samples(manifest)
    # the length `mono_m1a_score.load_reference` admits the reference at, and
    # the length the shipped model audio holds: the guard must be satisfiable
    assert expected == len(reference) == len(_committed()) == COMMITTED
    assert round(a.bass.reference.SECONDS * a.SR) == 360000        # the render, before the trim
    assert expected == 360000 - manifest["renders"][0]["apparatus"]["latency_removed"]


def test_expected_committed_samples_follows_the_apparatus_constants(monkeypatch):
    # a bare 359956 would satisfy every test above: the expectation must move
    # with the apparatus, or it rots silently when SECONDS or the latency does
    manifest, _ = a.bass.load_reference()
    monkeypatch.setattr(a.bass.reference, "SECONDS", 8.)
    manifest["renders"][0]["apparatus"] = {**manifest["renders"][0]["apparatus"], "latency_removed": 17}
    assert a.expected_committed_samples(manifest) == 8 * a.SR - 17 == 383983


@pytest.mark.parametrize("cut", [1, 44, COMMITTED - 1])
def test_a_committed_model_wav_cut_short_is_refused(tmp_path, cut):
    # #2114's own input: the file under test, cut short. Before the repair
    # the render was sliced to len(committed), the cut file matched its own
    # prefix, and the precondition passed on audio it had never checked past N
    with pytest.raises(a.Refused, match=f"holds {COMMITTED - cut} samples, expected {COMMITTED}"):
        a.reproduces_committed_model_audio(_model_wav(tmp_path, _committed()[:COMMITTED - cut]))


def test_a_committed_model_wav_longer_than_the_trimmed_render_is_refused(tmp_path):
    longer = np.concatenate([_committed(), np.zeros(44, np.int16)])
    with pytest.raises(a.Refused, match=f"holds {COMMITTED + 44} samples, expected {COMMITTED}"):
        a.reproduces_committed_model_audio(_model_wav(tmp_path, longer))


def test_the_committed_model_wav_still_reproduces():
    # the control: the untouched file passes the length and the hash
    assert a.reproduces_committed_model_audio(a.d.MODEL_WAV)


def test_a_model_wav_cut_and_padded_back_passes_the_length_and_fails_the_hash(tmp_path):
    # Rule 8, recorded rather than closed (the pad-back class #2038): the
    # length guard is satisfied by a pad-back; only the byte comparison refuses
    pcm = _committed()
    expected = a.expected_committed_samples(a.bass.load_reference()[0])
    path = _model_wav(tmp_path, np.concatenate([pcm[44:], np.zeros(44, pcm.dtype)]))
    assert len(a.committed_model_audio(path, expected)) == expected
    assert not a.reproduces_committed_model_audio(path)


def _readable_capture(tmp_path):
    """The committed capture, readable under a scratch CAPTURE: nothing
    `main()` writes can land on the committed report."""
    capture = json.loads((a.CAPTURE / "capture.json").read_text())
    (tmp_path / "capture.json").write_text(json.dumps(capture))
    for take in capture["takes"].values():
        for cond in ("full", "osc1_open", "osc2_open"):
            (tmp_path / take["conditions"][cond]["file"]).symlink_to(
                a.CAPTURE / take["conditions"][cond]["file"])


def test_main_refuses_a_truncated_committed_model_wav(monkeypatch, tmp_path, capsys):
    # the shipped path: the record, the reader, the reference measurement and
    # the precondition are real; only the ~20 s known-answer control, which
    # has its own tests above, is answered, and the plot, which a refusal
    # never reaches -- stubbed so a regression that passes the cut file is
    # reported as "exit 0 with a report", not as a missing matplotlib
    pcm = _committed()
    monkeypatch.setattr(a.d, "MODEL_WAV", _model_wav(tmp_path, pcm[:COMMITTED - 44]))
    _readable_capture(tmp_path)
    monkeypatch.setattr(a, "CAPTURE", tmp_path)
    monkeypatch.setattr(a, "synthetic_control", lambda defect=None: {"stub": {"ok": True}})
    monkeypatch.setattr(a, "plot", lambda *args: None)
    monkeypatch.setattr(sys, "argv", ["analyse_m1a_phase_cycle.py"])
    code = a.main()
    out = capsys.readouterr().out
    assert code == 2 and "REFUSED" in out, (code, out[-400:])
    assert f"holds {COMMITTED - 44} samples, expected {COMMITTED}" in out
    # and nothing was reported as data
    assert not (tmp_path / "report.json").exists()
    assert json.loads((tmp_path / "analysis-refused.json").read_text())["state"] == "REFUSED"
