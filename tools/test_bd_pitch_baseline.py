"""bd_pitch_baseline.py REFUSES (exit 2) instead of reporting when its
preconditions fail (#557).  No corpus needed: the refusals are the point; the
measurement against the real corpus is a build-box run."""
import pathlib
import shutil
import sys

import numpy as np
import pytest
from scipy.io import wavfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import bd_pitch_baseline as b  # noqa: E402


def test_estimator_precondition_passes_on_closed_form():
    assert b.qualify_estimator()["closed_form_worst_cents"] < 15.0


def test_missing_corpus_refuses(tmp_path, capsys):
    assert b.main(["--refs", str(tmp_path / "nope"), "--second", str(tmp_path / "x.wav")]) == 2
    assert "REFUSED" in capsys.readouterr().out


def test_no_second_recording_refuses(tmp_path, capsys):
    assert b.main(["--refs", str(tmp_path)]) == 2
    out = capsys.readouterr().out
    assert "REFUSED" in out


def test_second_equal_to_fischer_refuses(tmp_path, capsys):
    sys.path.insert(0, str(b.ROOT / "model"))
    import drum_verify as dv
    f = tmp_path / dv.REF_MAIN["BD"][0]
    f.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(f, 44100, np.zeros(100, dtype=np.int16))
    cp = tmp_path / "copy.wav"
    shutil.copy(f, cp)
    assert b.main(["--refs", str(tmp_path), "--second", str(cp)]) == 2
    assert "same bytes" in capsys.readouterr().out


def test_full_path_on_synthetic_stand_in_corpus(tmp_path):
    """Exercises run() end to end on SYNTHETIC stand-ins (a 58->50 Hz glide as
    'Fischer', a 56->50 Hz one as 'second'); ours is the real render.  This
    proves the plumbing and the known answers, NOT any claim about the 808."""
    sys.path.insert(0, str(b.ROOT / "model"))
    import drum_verify as dv

    def hit(f0, tg, path):
        sr = 44100
        t = np.arange(int(0.8 * sr)) / sr
        ph = 2 * np.pi * (50 * t + (f0 - 50) * tg * (1 - np.exp(-t / tg)))
        y = np.concatenate([np.zeros(300), np.sin(ph) * np.exp(-t / 0.15)]) * 0.5
        path.parent.mkdir(parents=True, exist_ok=True)
        wavfile.write(path, sr, np.round(y * 32767).astype(np.int16))
    fis = tmp_path / dv.REF_MAIN["BD"][0]
    hit(58.0, 0.020, fis)
    sec = tmp_path / "second.wav"
    hit(56.0, 0.020, sec)
    res = b.run(tmp_path, [sec], tmp_path / "out.json")
    g_f, g_s = res["rows"]["fischer"]["glide_cents"], res["rows"]["second0"]["glide_cents"]
    assert 40 < g_f < 75 and 0 < g_s < g_f           # closed-form ordering
    assert res["recording_glide_spread_cents"] == abs(g_f - g_s)
    assert (tmp_path / "out.json").exists()


# --- #1860: the gate's reader raises a SIBLING Refused; it must exit 2 -------
LFS_POINTER = ("version https://git-lfs.github.com/spec/v1\n"
               "oid sha256:" + "0" * 64 + "\nsize 12345\n")


def _hit(f0, path=None, sr=44100):
    """The closed-form stand-in the full-path test above measures, as a signal."""
    t = np.arange(int(0.8 * sr)) / sr
    ph = 2 * np.pi * (50 * t + (f0 - 50) * 0.020 * (1 - np.exp(-t / 0.020)))
    y = np.concatenate([np.zeros(300), np.sin(ph) * np.exp(-t / 0.15)]) * 0.5
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        wavfile.write(path, sr, np.round(y * 32767).astype(np.int16))
    return y, sr


@pytest.mark.parametrize("trigger", ["git-lfs-pointer", "sibling-refusal"])
def test_the_gates_refusal_on_the_fischer_take_exits_2(tmp_path, monkeypatch, capsys, trigger):
    """#1860. `run` reads the Fischer take through `perceptual_gate.load_wav`,
    which raises perceptual_gate's OWN `Refused` -- a sibling of this module's
    class. `main` caught `(Refused, pt.Refused)` alone, so that refusal escaped
    as a traceback (exit 1). START RED on main for both triggers: the reader
    was unguarded (scipy's ValueError), and the handler was narrow. The render
    is stubbed (~10 s, and not what is under test); the estimator qualification
    the real run performs first is not."""
    import drum_verify as dv
    import perceptual_gate as g
    fis = tmp_path / dv.REF_MAIN["BD"][0]
    sec = tmp_path / "second.wav"
    _hit(56.0, sec)
    monkeypatch.setattr(g, "render_ours", lambda sound: _hit(58.0))
    if trigger == "git-lfs-pointer":
        fis.parent.mkdir(parents=True, exist_ok=True)
        fis.write_text(LFS_POINTER)
    else:
        _hit(58.0, fis)

        def refuse(path):
            raise g.Refused(f"{path}: raised by perceptual_gate, a sibling module")
        monkeypatch.setattr(g, "load_wav", refuse)
    out = tmp_path / "out.json"
    assert b.main(["--refs", str(tmp_path), "--second", str(sec), "--out", str(out)]) == 2
    text = capsys.readouterr().out
    assert text.startswith("REFUSED:") and str(fis) in text, text[-400:]
    assert not out.exists()
