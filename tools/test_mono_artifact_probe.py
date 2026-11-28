"""Known-answer tests for tools/mono_artifact_probe.py.

Every signal here has an answer known independently of the voice model:
additive band-limited waveforms, planted tones at chosen levels, and the
closed-form alias energy of a naive sawtooth. The render tests at the bottom
exercise the voice only to show the controls turn the probe red.
"""
from __future__ import annotations

import json
import math
import pathlib
import re
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import mono_artifact_probe as p  # noqa: E402

SR = 48_000
N = 36_000
F0 = 1046.502261           # MIDI 84; not bin-coherent at N
T = np.arange(N) / SR


def bl_saw(f0, amp=0.5, sr=SR, n=N, top=None):
    t = np.arange(n) / sr
    top = sr / 2 if top is None else top
    x = np.zeros(n)
    k = 1
    while k * f0 < top:
        x += (2 * amp / (math.pi * k)) * (-1) ** (k + 1) * np.sin(2 * math.pi * k * f0 * t)
        k += 1
    return x


def analytic_saw_power(f0, amp=0.5, top=SR / 2):
    k = np.arange(1, int(math.ceil(top / f0)))
    k = k[k * f0 < top]
    return float(np.sum((2 * amp / (math.pi * k)) ** 2 / 2))


def db(pw):
    return 10 * math.log10(pw)


def test_bandlimited_saw_intended_is_exact_and_unwanted_is_floor():
    r = p.split_spectrum(bl_saw(F0), SR, F0)
    assert r["intended_dbfs"] == pytest.approx(db(analytic_saw_power(F0)), abs=0.02)
    assert r["unwanted_rel_db"] < -80.0          # window leakage floor, not a signal


def test_planted_image_tone_is_recovered_as_image():
    fa = p.fold(30 * F0, SR)
    tone = math.sqrt(2) * 10 ** (-60 / 20) * np.sin(2 * math.pi * fa * T)
    r = p.split_spectrum(bl_saw(F0) + tone, SR, F0)
    assert r["image_dbfs"] == pytest.approx(-60.0, abs=0.3)
    assert r["residual_dbfs"] < -85.0


def test_planted_spur_is_residual_not_image():
    clean = p.split_spectrum(bl_saw(F0), SR, F0)
    hz = 0.5 * F0 + 3131.0                          # the SPUR_M70 placement rule
    kn = int(SR / 2 / F0)
    busy = [k * F0 for k in range(1, kn + 2)] + [p.fold(k * F0, SR) for k in range(kn + 1, 16 * kn + 1)]
    while min(abs(hz - b) for b in busy) < 40:
        hz += 7.0
    spur = math.sqrt(2) * 10 ** (-80 / 20) * np.sin(2 * math.pi * hz * T)
    r = p.split_spectrum(bl_saw(F0) + spur, SR, F0)
    assert r["residual_dbfs"] == pytest.approx(-80.0, abs=0.5)
    assert r["image_dbfs"] == pytest.approx(clean["image_dbfs"], abs=0.5)


def test_scale_moves_absolute_not_relative():
    a = p.split_spectrum(bl_saw(F0) + 1e-3 * np.sin(2 * math.pi * 5000.3 * T), SR, F0)
    b = p.split_spectrum(0.5 * (bl_saw(F0) + 1e-3 * np.sin(2 * math.pi * 5000.3 * T)), SR, F0)
    for key in ("intended_dbfs", "unwanted_dbfs", "residual_dbfs"):
        assert b[key] - a[key] == pytest.approx(-6.0206, abs=0.01)
    for key in ("unwanted_rel_db", "residual_rel_db", "upper_wanted_rel_db"):
        assert b[key] == pytest.approx(a[key], abs=0.01)


def test_naive_saw_alias_energy_matches_closed_form():
    """A naive saw's aliased energy is the power of its harmonics above
    Nyquist, sum_{k>kn} (2A/(pi k))^2 / 2 -- independent of this probe."""
    amp = 0.5
    ph = (F0 * T) % 1.0
    x = amp * (2 * ph - 1)
    r = p.split_spectrum(x, SR, F0)
    kn = int(SR / 2 / F0)
    k = np.arange(kn + 1, 200_000)
    alias = float(np.sum((2 * amp / (math.pi * k)) ** 2 / 2))
    # the few images that land on a harmonic's guard are attributed to it
    assert r["unwanted_dbfs"] == pytest.approx(db(alias), abs=1.0)


def test_upper_band_power_sees_a_lowpass():
    x = bl_saw(F0)
    from scipy.signal import butter, sosfilt
    y = sosfilt(butter(2, 4000, fs=SR, output="sos"), x)
    a, b = p.split_spectrum(x, SR, F0), p.split_spectrum(y[4000:], SR, F0)
    assert b["upper_wanted_rel_db"] < a["upper_wanted_rel_db"] - 6.0


def test_oversampled_stage_counts_supra_band_separately():
    x = bl_saw(F0, sr=2 * SR, n=2 * N, top=SR)       # harmonics to 48 kHz at 96 kHz
    r = p.split_spectrum(x, 2 * SR, F0, band_hz=SR / 2)
    assert r["intended_dbfs"] == pytest.approx(db(analytic_saw_power(F0, top=SR / 2)), abs=0.02)
    supra = analytic_saw_power(F0, top=SR) - analytic_saw_power(F0, top=SR / 2)
    assert r["supra_dbfs"] == pytest.approx(db(supra), abs=0.05)
    assert r["unwanted_rel_db"] < -80.0


def test_silence_is_refused_not_scored():
    with pytest.raises(p.Refused, match="silent"):
        p.split_spectrum(np.zeros(N), SR, F0)


def test_unresolvable_low_note_is_refused():
    with pytest.raises(p.Refused, match="bins"):
        p.split_spectrum(bl_saw(20.0), SR, 20.0)


def test_dropout_detector():
    x = bl_saw(F0)
    assert p.dropout_depth_db(x) > -1.0
    y = x.copy()
    y[10_000:10_480] = 0
    assert p.dropout_depth_db(y) < -40.0


# ---- the voice: start red, then controls ---------------------------------------
def test_pulse2x_engine_is_bit_identical_for_saw():
    patch = p.held_patch()
    a = p.render_held("r1", 84, patch)["stages"]["output"][0]
    b = p.render_held("pulse2x", 84, patch)["stages"]["output"][0]
    assert np.array_equal(a, b)


def test_pulse2x_engine_changes_pulse():
    patch = p.held_patch(waves=("pulse29",) * 3)
    a = p.render_held("r1", 96, patch)["stages"]["output"][0]
    b = p.render_held("pulse2x", 96, patch)["stages"]["output"][0]
    assert not np.array_equal(a, b)


def test_every_control_moves_its_property():
    res = p.controls("r1", 84, "saw")
    assert res["all_caught"], res["caught"]


# ---- controls: a clean baseline whose output stage refused is REFUSED, never BLIND
# measure_point marks a row MEASURED while its stages refuse, so a reader that
# indexes stages["output"] through .get() reads None and calls the control BLIND.
OUTPUT_KEYS = ("unwanted_dbfs", "image_dbfs", "residual_dbfs", "upper_wanted_rel_db")
STAGE_TEXT = "f0 16.35 Hz is 12.3 bins; need >= 16"


def _measured_row(output) -> dict:
    """A row measure_point marks MEASURED whatever its output stage holds."""
    return {"verdict": "MEASURED", "stages": {"output": output},
            "clip": {"output_rail_samples": 0}, "dropout_depth_db": -0.1}


def test_controls_refuse_when_the_clean_output_stage_refused(monkeypatch):
    monkeypatch.setattr(p, "measure_point",
                        lambda engine, note, patch, inject="": _measured_row({"refused": STAGE_TEXT}))
    with pytest.raises(p.Refused) as e:
        p.controls("r1", 12, "saw")
    assert str(e.value) == f"clean control baseline output stage refused: {STAGE_TEXT}"


@pytest.mark.parametrize("how", ["absent", "none"])
@pytest.mark.parametrize("field", OUTPUT_KEYS)
def test_controls_refuse_when_the_clean_output_stage_lacks_a_field(monkeypatch, field, how):
    stage = {k: -60.0 for k in OUTPUT_KEYS}
    if how == "absent":
        del stage[field]
    else:
        stage[field] = None
    monkeypatch.setattr(p, "measure_point", lambda engine, note, patch, inject="": _measured_row(stage))
    with pytest.raises(p.Refused, match=f"output stage refused: output stage lacks {field}$"):
        p.controls("r1", 84, "saw")


def test_controls_refuse_when_the_clean_output_stage_is_not_a_measurement(monkeypatch):
    monkeypatch.setattr(p, "measure_point", lambda engine, note, patch, inject="": _measured_row(None))
    with pytest.raises(p.Refused, match="output stage refused: output stage is NoneType"):
        p.controls("r1", 84, "saw")


def test_output_fields_are_the_output_stage_fields_the_matrix_reads():
    read = set()

    class Recording(dict):
        def get(self, key, default=None):
            read.add(key)
            return None

    row = {"stages": {"output": Recording()}, "clip": {"output_rail_samples": 0},
           "dropout_depth_db": -0.1}
    for get in p.PROPERTIES.values():
        get(row)
    assert read == set(p.OUTPUT_FIELDS) == set(OUTPUT_KEYS)


def test_controls_cli_refuses_a_low_note_and_writes_no_report(tmp_path, capsys):
    out = tmp_path / "controls-12.json"
    rc = p.main(["controls", "--note", "12", "--out", str(out)])
    assert rc == 2
    assert re.fullmatch(r"REFUSED: clean control baseline output stage refused: "
                        r"f0 \d+\.\d\d Hz is \d+\.\d bins; need >= 16", capsys.readouterr().out.strip())
    assert not out.exists()


def test_controls_cli_at_the_default_note_is_unchanged(tmp_path, capsys):
    out = tmp_path / "controls-84.json"
    rc = p.main(["controls", "--out", str(out)])
    lines = capsys.readouterr().out.splitlines()
    assert rc == 0
    assert lines[:6] == [f"{inj:14s} CAUGHT  expected {p.EXPECTED[inj]}" for inj in p.INJECTIONS]
    assert json.loads(out.read_text())["caught"] == {inj: True for inj in p.INJECTIONS}


def test_out_of_domain_stimuli_refuse():
    with pytest.raises(p.Refused):
        p.render_held("r1", 84, p.held_patch(q=1.0))
    with pytest.raises(p.Refused):
        p.render_held("r1", 84, p.held_patch(noise=0.5))


def test_oversampled_stage_splits_unwanted_by_band():
    x = bl_saw(F0, sr=2 * SR, n=2 * N, top=SR / 2)
    t = np.arange(2 * N) / (2 * SR)
    lo = math.sqrt(2) * 10 ** (-70 / 20) * np.sin(2 * math.pi * 13_333.0 * t)   # in band
    hi = math.sqrt(2) * 10 ** (-60 / 20) * np.sin(2 * math.pi * 36_111.0 * t)   # above 24 kHz
    r = p.split_spectrum(x + lo + hi, 2 * SR, F0, band_hz=SR / 2)
    assert r["unwanted_inband_dbfs"] == pytest.approx(-70.0, abs=0.5)
    assert r["unwanted_outband_dbfs"] == pytest.approx(-60.0, abs=0.5)


def test_a_control_moved_the_wrong_way_is_not_caught():
    assert p._moved_way("image_dbfs", -60.0, -50.0, +1)
    assert not p._moved_way("image_dbfs", -60.0, -70.0, +1)        # moved, but the wrong way
    assert not p._moved_way("image_dbfs", -60.0, -60.5, +1)        # inside MOVE_DB
    assert p._moved_way("output_rail_samples", 0, 5, +1)
    assert not p._moved_way("output_rail_samples", 5, 0, +1)
