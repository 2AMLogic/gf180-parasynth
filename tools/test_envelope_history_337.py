"""Controls for tools/envelope_history_337.py (rule 2, rule 8, start-red)."""
import hashlib
import io
import json
import math
import shutil
import struct
import sys
from pathlib import Path

import numpy as np
import pytest
from scipy.io import wavfile

sys.path.insert(0, str(Path(__file__).parent))
import envelope_history_337 as eh  # noqa: E402

SR = 48000


def tone(on_s, amp_pre, amp_held=0.2, total=1.0, f=1046.5):
    t = np.arange(int(total * SR)) / SR
    x = np.sin(2 * np.pi * f * t)
    a = np.where(t < on_s, amp_pre, amp_held)
    return a * x


def test_known_residual_reads_minus_20_db():
    x = tone(0.5, 0.02)          # pre-onset 20 dB below held
    r, floored = eh.residual_db(x, SR, 0.5, 0.9)
    assert abs(r - (-20.0)) < 0.1 and not floored


def test_digital_zero_is_floor_limited_not_minus_inf():
    r, floored = eh.residual_db(tone(0.5, 0.0), SR, 0.5, 0.9)
    assert r == eh.FLOOR_DB and floored


def test_nan_refuses():
    x = tone(0.5, 0.02)
    x[1000] = np.nan
    with pytest.raises(eh.Refused):
        eh.residual_db(x, SR, 0.5, 0.9)


def test_window_reaching_the_onset_refuses():
    with pytest.raises(eh.Refused):
        eh.residual_db(tone(0.5, 0.02), SR, 0.5, 0.9, end_before_on_s=0.0)


def test_too_little_lead_in_refuses():
    with pytest.raises(eh.Refused):
        eh.residual_db(tone(0.03, 0.0), SR, 0.03, 0.9)


def test_silent_held_level_refuses():
    with pytest.raises(eh.Refused):
        eh.residual_db(np.zeros(SR), SR, 0.5, 0.9)


def test_wrong_onset_moves_the_number_start_red():
    """A stub with the onset index 40 ms late must NOT read the true value."""
    x = tone(0.5, 0.0)
    good, _ = eh.residual_db(x, SR, 0.5, 0.9)
    bad, _ = eh.residual_db(x, SR, 0.54, 0.94)
    assert good == eh.FLOOR_DB and bad > -30.0


def test_gate_rejects_nonfinite_and_short_groups():
    rows = [{"wave": "saw", "condition": c, "r_db": -10.0}
            for c in eh.HISTORY for _ in range(3)]
    with pytest.raises(eh.Refused):
        eh.gate(rows)                      # no-history group missing
    rows += [{"wave": "saw", "condition": c, "r_db": float("nan")}
             for c in eh.NO_HISTORY for _ in range(3)]
    with pytest.raises(eh.Refused):
        eh.gate(rows)


def test_gate_viable_only_when_history_has_level_and_isolated_does_not():
    def rows(h, n):
        return ([{"wave": "saw", "condition": c, "r_db": h} for c in eh.HISTORY for _ in range(3)]
                + [{"wave": "saw", "condition": c, "r_db": n} for c in eh.NO_HISTORY for _ in range(3)])
    assert eh.verdict(eh.gate(rows(-10.0, -80.0))) == "VIABLE"
    assert eh.verdict(eh.gate(rows(-10.0, -15.0))) == "REFUTED"     # no contrast
    assert eh.verdict(eh.gate(rows(-120.0, -120.0))) == "REFUTED"


def test_all_silent_file_is_refused_not_refuted():
    """Rule 8: the defeating input. A silent file satisfies 'residual below
    -30 dB' trivially; it must refuse (no held level), not read as REFUTED."""
    with pytest.raises(eh.Refused):
        eh.residual_db(np.zeros(2 * SR), SR, 1.0, 1.5)


def test_positive_control_on_real_audio_sees_the_previous_release():
    """The instrument can see a residual that exists: in the frozen repeat
    render, a window inside the earlier note's release must read far above the floor."""
    path = eh.SRC / "saw-repeat84_gap3p4-0.wav"
    if not path.exists():
        pytest.skip("frozen audio absent")
    from scipy.io import wavfile
    sr, x = wavfile.read(path)
    x = x.astype(np.float64)
    # pretend a note starts at 1.0 s, inside the earlier note's release: the
    # 40 ms before it holds the louder, earlier part of that release.
    r, floored = eh.residual_db(x, sr, 1.0, 1.5, end_before_on_s=0.005)
    assert not floored and r > -10.0


def test_hash_mismatch_refuses(tmp_path):
    import json, shutil
    for n in ("report.json",):
        shutil.copy(eh.SRC / n, tmp_path / n)
    rep = json.loads((tmp_path / "report.json").read_text())
    for row in rep["renders"]:
        shutil.copy(eh.SRC / row["wav"], tmp_path / row["wav"])
        break
    (tmp_path / rep["renders"][0]["wav"]).write_bytes(b"x" + (tmp_path / rep["renders"][0]["wav"]).read_bytes())
    with pytest.raises(eh.Refused):
        eh.measure(tmp_path)


# ---- an input that cannot be read is REFUSED, naming it (#1862) ----
def test_missing_report_json_refuses(tmp_path):
    with pytest.raises(eh.Refused, match="report.json"):
        eh.measure(tmp_path)


def test_missing_render_wav_refuses_naming_it(tmp_path):
    shutil.copy(eh.SRC / "report.json", tmp_path / "report.json")      # every render absent
    with pytest.raises(eh.Refused, match=r"\.wav"):
        eh.measure(tmp_path)


def test_directory_in_place_of_a_render_wav_refuses(tmp_path):
    def mkdir(report):
        for row in report["renders"]:
            (tmp_path / row["wav"]).mkdir()
    _scratch_src(tmp_path, mkdir)
    with pytest.raises(eh.Refused, match=r"\.wav"):
        eh.measure(tmp_path)


def _scratch_src(tmp_path, write):
    """The committed report.json under tmp_path; `write(report)` supplies the
    renders it names and may rewrite their hashes."""
    shutil.copy(eh.SRC / "report.json", tmp_path / "report.json")
    report = json.loads((tmp_path / "report.json").read_text())
    write(report)
    (tmp_path / "report.json").write_text(json.dumps(report))


def _bad_wavs():
    """One file per way a header can fail its reader: ValueError, struct.error
    (a header cut inside a chunk), UnboundLocalError (a size field that lies)."""
    buf = io.BytesIO()
    wavfile.write(buf, SR, np.zeros(4800, dtype=np.int16))
    good = buf.getvalue()
    fmt, data = good[20:36], good[44:]
    chunk = lambda cid, payload: cid + struct.pack("<I", len(payload)) + payload
    return {"non-RIFF": b"not a WAV", "empty": b"", "cut-inside-chunk": good[:20],
            "RIFF-magic-only": b"RIFF",
            "size-field-lies": b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt)) + b"WAVE"
                              + chunk(b"fmt ", fmt) + chunk(b"data", data)}


@pytest.mark.parametrize("name", sorted(_bad_wavs()))
def test_unreadable_render_wav_matching_its_recorded_hash_refuses(tmp_path, name):
    """The hash binds each render to report.json; when the report names the bad
    bytes themselves, only the reader is left to refuse."""
    blob = _bad_wavs()[name]

    def write(report):
        for row in report["renders"]:
            (tmp_path / row["wav"]).write_bytes(blob)
            row["sha256"] = hashlib.sha256(blob).hexdigest()
    _scratch_src(tmp_path, write)
    with pytest.raises(eh.Refused, match=r"\.wav"):
        eh.measure(tmp_path)


def test_main_returns_2_when_the_report_is_missing(tmp_path, monkeypatch, capsys):
    """`main()` takes no source path and `measure()`'s default is bound at
    definition time, so the CLI handler is driven with the real `measure`
    pointed at an empty directory."""
    real = eh.measure
    monkeypatch.setattr(eh, "measure", lambda src=None: real(tmp_path))
    assert eh.main([]) == 2
    out = capsys.readouterr().out
    assert "REFUSED:" in out and "report.json" in out and "VERDICT" not in out
