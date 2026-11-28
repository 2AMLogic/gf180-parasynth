"""Known answers for the drive experiment's pre-declared rules: the volume
compensation, the regression detector and the choice rule. Each check is also
shown to fire on an injected defect."""
import io
import struct

import numpy as np
import pytest
from scipy.io import wavfile

import measure_m1a_drive as m


def test_compensation_is_the_mean_gain_window_difference():
    assert m.compensation_db([-26., -26.2, -26.], [-29.5, -29.8, -29.6]) == pytest.approx(3.5667, abs=1e-4)
    assert m.compensation_db([-26.] * 3, [-26.] * 3) == 0.


def _measured(pitch_err=0., gain_err=-1., h5=(.5, -.3, 2.), valid_attack=False):
    prop = lambda e, tol: {"valid": True, "value": e, "reference": 0., "error": e, "tolerance": tol}
    events = [{"note": n, "on_s": t, "harmonic_error_db_model_minus_reference": {"h5": v, "h8": 19.8}}
              for n, t, v in zip((36, 43, 36), (.1, 2.1, 4.1), h5)]
    return {"properties": {"Pitch": prop(pitch_err, 1.), "Gain": prop(gain_err, 3.),
                           "Envelope release": prop(8., 20.), "Clipping": prop(0., .01),
                           "Harmonic shape": prop(19.8, 1.),
                           "Envelope attack": {"valid": valid_attack}},
            "events": events}


def test_no_regressions_against_itself():
    assert m.regressions(_measured(), _measured()) == []


def test_every_regression_kind_is_caught():
    base = _measured()
    assert any("Gain" in r for r in m.regressions(base, _measured(gain_err=-3.5)))
    assert any("Pitch" in r for r in m.regressions(base, _measured(pitch_err=1.2)))
    got = m.regressions(base, _measured(h5=(1.4, -.3, 2.)))
    assert got and "h5" in got[0] and "36 @0.1" in got[0]
    assert any("validity" in r for r in m.regressions(base, _measured(valid_attack=True)))
    # a failing cell that stays failing is not a regression; one that improves is fine
    assert m.regressions(base, _measured(h5=(.5, -.3, 5.))) == []


def _row(regs, shape, odd):
    return {"regressions": regs, "harmonic_shape_db": shape, "odd_range_mean_db": odd}


def test_choice_rule():
    base = _row([], 19.82, 10.)
    assert m.choose({.75: base, .5: _row([], 19.0, 8.), .25: _row([], 18.0, 6.)}, base) == .25
    assert m.choose({.75: base, .5: _row([], 19.0, 8.), .25: _row(["x"], 18.0, 6.)}, base) == .5
    assert m.choose({.75: base, .5: _row([], 19.5, 8.)}, base) is None          # < 0.5 dB better
    assert m.choose({.75: base, .5: _row([], 18.0, 10.5)}, base) is None        # odd range not reduced
    assert m.choose({.75: base}, base) is None                                   # baseline never chosen


# ---- the committed-model WAV the baseline precondition reads (#1862) ----
def _bad_wavs():
    """One file per way a header can fail its reader: ValueError, struct.error
    (a header cut inside a chunk), UnboundLocalError (a size field that lies)."""
    buf = io.BytesIO()
    wavfile.write(buf, m.SR, np.zeros(4800, dtype=np.int16))
    good = buf.getvalue()
    fmt, data = good[20:36], good[44:]
    chunk = lambda cid, payload: cid + struct.pack("<I", len(payload)) + payload
    return {"non-RIFF": b"not a WAV", "empty": b"", "cut-inside-chunk": good[:20],
            "RIFF-magic-only": b"RIFF",
            "size-field-lies": b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt)) + b"WAVE"
                              + chunk(b"fmt ", fmt) + chunk(b"data", data)}


BAD_WAVS = {"missing": None, **_bad_wavs()}


@pytest.mark.parametrize("name", sorted(BAD_WAVS))
def test_unreadable_committed_model_wav_is_refused_naming_it(monkeypatch, tmp_path, capsys, name):
    """main() up to the committed-model read: the baseline render is answered,
    the reader and the refusal boundary are real."""
    before = (m.OUT / "report.json").read_bytes()
    model = tmp_path / "m1a-model.wav"
    if BAD_WAVS[name] is not None:
        model.write_bytes(BAD_WAVS[name])
    monkeypatch.setattr(m.bass, "MANIFEST", tmp_path / "manifest.json")
    monkeypatch.setattr(m, "render_phrase", lambda patch, n: np.zeros(n, dtype=np.int16))
    assert m.main() == 2
    out = capsys.readouterr().out
    assert "REFUSED:" in out and "m1a-model.wav" in out
    assert (m.OUT / "report.json").read_bytes() == before
