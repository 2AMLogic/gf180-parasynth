"""Known answers for the M1A phase-cycle analysis.

The synthetic takes have a psi-dependent odd-partial gain known by
construction, so the decision rule's every branch and the darkening estimate
have an answer independent of both the Mini V3 and the model. Injected
analysis defects must turn the control red."""
import json
import sys

import numpy as np
import pytest

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


def _capture_copy(tmp_path):
    """The committed capture under `tmp_path`: its record and every WAV it
    names, copied rather than linked so that one can be damaged in place."""
    import shutil
    record = json.loads((a.CAPTURE / "capture.json").read_text())
    for take in record["takes"].values():
        for row in take["conditions"].values():
            shutil.copy(a.CAPTURE / row["file"], tmp_path / row["file"])
    return record


def _cut_data_chunk(path, keep, rewritten):
    """Cut a WAV's `data` chunk to `keep` of its samples; returns the samples
    left. Rewritten, it is a well-formed shorter WAV that no reader can tell
    from a complete one; not, the file simply ends early while its header
    still claims the whole take."""
    from scipy.io import wavfile
    rate, x = wavfile.read(path)
    left = round(len(x) * keep)
    if rewritten:
        wavfile.write(path, rate, x[:left])
        return left
    raw = path.read_bytes()
    at = 12
    while raw[at:at + 4] != b"data":
        at += 8 + int.from_bytes(raw[at + 4:at + 8], "little")
    wide = int.from_bytes(raw[at + 4:at + 8], "little") // len(x)
    path.write_bytes(raw[:at + 8 + wide * left])
    return left


def _load(monkeypatch, tmp_path, record):
    """`load_capture()` over a record written under a scratch CAPTURE."""
    (tmp_path / "capture.json").write_text(json.dumps(record))
    monkeypatch.setattr(a, "CAPTURE", tmp_path)
    return a.load_capture()


def test_the_committed_capture_is_as_long_as_its_record_describes():
    _, takes = a.load_capture()
    for take, audio in takes.values():
        for cond, x in audio.items():
            row = take["conditions"][cond]
            assert len(x) == round(take["seconds"] * a.SR) - row["apparatus"]["latency_removed"], row["file"]
    assert len(takes["A"][1]["full"]) == 628756
    assert len(takes["B"][1]["full"]) == 719956


@pytest.mark.filterwarnings("ignore::scipy.io.wavfile.WavFileWarning")
@pytest.mark.parametrize("rewritten", [False, True], ids=["cut", "rewritten"])
@pytest.mark.parametrize("keep", [.99, .95, .40, 0.])
def test_a_take_shorter_than_its_record_with_its_hash_rewritten_is_refused(monkeypatch, tmp_path, capsys, keep, rewritten):
    """#1931: the hash binds a file to the record and says nothing about
    whether the record describes the audio. A `data` chunk cut short parses
    as fewer samples, so with the hash rewritten every other precondition
    held and `main()` answered from a truncated take. scipy's own
    `WavFileWarning` is ignored: the length refuses the take, not the reader."""
    record = _capture_copy(tmp_path)
    take, row = record["takes"]["A"], record["takes"]["A"]["conditions"]["full"]
    left = _cut_data_chunk(tmp_path / row["file"], keep, rewritten)
    full = round(take["seconds"] * a.SR) - row["apparatus"]["latency_removed"]
    row["sha256"] = a.bass.sha(tmp_path / row["file"])
    code, out = _analyse(monkeypatch, tmp_path, capsys, record)
    assert code == 2 and "REFUSED" in out, out
    assert row["file"] in out and f"{left} samples" in out and f"describes {full}" in out, out
    assert json.loads((tmp_path / "analysis-refused.json").read_text())["state"] == "REFUSED"
    assert not (tmp_path / "report.json").exists()


def test_a_full_length_take_whose_hash_was_not_rewritten_still_refuses_on_the_hash(monkeypatch, tmp_path, capsys):
    from scipy.io import wavfile
    record = _capture_copy(tmp_path)
    row = record["takes"]["A"]["conditions"]["full"]
    path = tmp_path / row["file"]
    rate, x = wavfile.read(path)
    x[len(x) // 2] = -x[len(x) // 2]
    wavfile.write(path, rate, x)
    assert a.bass.sha(path) != row["sha256"]
    code, out = _analyse(monkeypatch, tmp_path, capsys, record)
    assert code == 2 and "REFUSED" in out, out
    assert "hash differs" in out and row["file"] in out, out


def _damaged(tmp_path, damage):
    """A committed-capture copy whose take A has `damage(take, full_row)`."""
    record = _capture_copy(tmp_path)
    take = record["takes"]["A"]
    damage(take, take["conditions"]["full"])
    return record


_LENGTH_DAMAGE = [
    pytest.param(lambda take, row: take.pop("seconds"), "'seconds'", id="seconds-missing"),
    pytest.param(lambda take, row: take.update(seconds=None), "'seconds'", id="seconds-null"),
    pytest.param(lambda take, row: take.update(seconds="13.1"), "seconds is '13.1'", id="seconds-text"),
    pytest.param(lambda take, row: take.update(seconds=float("nan")), "seconds is nan", id="seconds-nan"),
    pytest.param(lambda take, row: take.update(seconds=float("inf")), "seconds is inf", id="seconds-inf"),
    pytest.param(lambda take, row: take.update(seconds=True), "seconds is True", id="seconds-flag"),
    pytest.param(lambda take, row: row.pop("apparatus"), "'apparatus'", id="apparatus-missing"),
    pytest.param(lambda take, row: row.update(apparatus=None), "'apparatus'", id="apparatus-null"),
    pytest.param(lambda take, row: row.update(apparatus=44), "is a JSON int, not a JSON object", id="apparatus-not-an-object"),
    pytest.param(lambda take, row: row["apparatus"].pop("latency_removed"), "'latency_removed'", id="latency-missing"),
    pytest.param(lambda take, row: row["apparatus"].update(latency_removed=None), "'latency_removed'", id="latency-null"),
    pytest.param(lambda take, row: row["apparatus"].update(latency_removed="44"), "latency_removed is '44'", id="latency-text"),
    pytest.param(lambda take, row: row["apparatus"].update(latency_removed=float("nan")), "latency_removed is nan", id="latency-nan"),
    pytest.param(lambda take, row: row["apparatus"].update(latency_removed=True), "latency_removed is True", id="latency-flag"),
]


@pytest.mark.parametrize("damage, needle", _LENGTH_DAMAGE)
def test_a_record_that_does_not_say_how_long_the_take_is_refused(monkeypatch, tmp_path, damage, needle):
    with pytest.raises(a.Refused, match=needle):
        _load(monkeypatch, tmp_path, _damaged(tmp_path, damage))


def test_a_record_without_the_take_length_reaches_the_cli_as_refused(monkeypatch, tmp_path, capsys):
    record = _damaged(tmp_path, lambda take, row: take.pop("seconds"))
    code, out = _analyse(monkeypatch, tmp_path, capsys, record)
    assert code == 2 and "REFUSED" in out and "'seconds'" in out, out
    assert json.loads((tmp_path / "analysis-refused.json").read_text())["state"] == "REFUSED"
    assert not (tmp_path / "report.json").exists()


def test_the_length_guard_does_not_see_a_take_cut_and_padded_back_to_length(monkeypatch, tmp_path):
    """Pins the limit #1931's guard discloses: it counts samples in the
    array, not signal. The hash was rewritten, so nothing binds the padded
    tail to the capture. Closing that is a deliberate change that turns this
    red."""
    from scipy.io import wavfile
    record = _capture_copy(tmp_path)
    row = record["takes"]["A"]["conditions"]["full"]
    path = tmp_path / row["file"]
    rate, x = wavfile.read(path)
    left = _cut_data_chunk(path, .95, True)
    rate, short = wavfile.read(path)
    wavfile.write(path, rate, np.concatenate([short, np.zeros(len(x) - left, dtype=short.dtype)]))
    row["sha256"] = a.bass.sha(path)
    _, takes = _load(monkeypatch, tmp_path, record)
    assert len(takes["A"][1]["full"]) == len(x)


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
