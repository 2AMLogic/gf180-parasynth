"""tools/test_rescore_m1a_attack_v3.py -- issue #1671, site 1.

`rescore_m1a_attack_v3.main` caught `bass.Refused` (`mono_m1a_score.Refused`)
and returned 2. But `compare_audio` reaches a SIBLING class one frame deeper:
`attack_fit_v3.attack_fit_v3` raises `measure_mono_m5a_reference.Refused`
(`attack_fit_v3.Refused = v2.ref.Refused`), which `except bass.Refused` does
not name. A degenerate per-event audio state left the CLI as an exit-1
traceback where the exit contract owes REFUSED (2). `compare_audio`'s
phrase-level guards screen the whole-phrase cases first, so the escape needs
an audio that passes them and still refuses INSIDE one event's attack fit.

Known-answer input: the frozen M1A reference (it passes every phrase-level
guard `compare_audio` applies, by construction) as the sweep WAV, int16, with
ONE event's attack-fit window zeroed. The fold region and the pitch clip of
that event are untouched, so pitch, harmonics and release all still measure;
`attack_fit_v3`'s own guard then refuses ("attack fit window is silent") with
the sibling class.

The control for the handler: an implementation error at the same call site is
an EXECUTION ERROR and must leave `main` untouched (tools/refusal.py).
"""
from __future__ import annotations

import hashlib
import inspect
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest
from scipy.io import wavfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import attack_fit_v3                                     # noqa: E402
import measure_mono_m5a_reference                        # noqa: E402
import mono_m1a_score as bass                            # noqa: E402
import refusal                                           # noqa: E402
import rescore_m1a_attack_v3 as rescore                  # noqa: E402
import sweep_m1a_attack as sweep                         # noqa: E402

SIBLING = attack_fit_v3.Refused
WAV_NAME = "silent-attack-window.wav"


def _silent_attack_window(reference: np.ndarray) -> np.ndarray:
    """`reference` (the float M1A reference) with event 0's attack-fit window
    zeroed: [on - pre_ms, on - pre_ms + fit_ms), the window attack_fit_v3
    fits, read from its own signature so the two cannot drift apart."""
    event = bass.reference.EVENTS[0]
    fit = inspect.signature(attack_fit_v3.attack_fit_v3).parameters
    sr = bass.SR
    start = round(event["on_s"] * sr) - round(fit["pre_ms"].default * 1e-3 * sr)
    nfit = round(fit["fit_ms"].default * 1e-3 * sr)
    out = reference.copy()
    out[start:start + nfit] = 0.0
    return out


def _sweep_points(tmp_path: Path, monkeypatch) -> np.ndarray:
    """A sweep directory holding the degenerate render as the WAV of every
    rescored setting, under its true hash, and a rescore ROOT that resolves
    it. Returns the float audio the CLI will read back."""
    _manifest, reference = bass.load_reference()
    degenerate = _silent_attack_window(reference)
    pcm16 = np.clip(np.round(degenerate * 32767), -32768, 32767).astype(np.int16)
    wav = tmp_path / WAV_NAME
    wavfile.write(wav, bass.SR, pcm16)
    digest = hashlib.sha256(wav.read_bytes()).hexdigest()
    for ms in rescore.SETTINGS_MS:
        (tmp_path / f"{sweep.point_name(ms)}.json").write_text(
            json.dumps({"amp_attack_ms": ms, "audio": WAV_NAME, "sha256": digest}))
    monkeypatch.setattr(sweep, "OUT", tmp_path)
    monkeypatch.setattr(rescore, "ROOT", tmp_path)
    # the rescore writes QUALIFICATION/rescore.json on success: a test that
    # ever got past the refusal must not clobber the committed record
    qualification = tmp_path / "attack-qualification"
    shutil.copytree(bass.QUALIFICATION, qualification)
    monkeypatch.setattr(bass, "QUALIFICATION", qualification)
    return pcm16.astype(np.float64) / 32768


def test_the_class_attack_fit_raises_is_a_sibling_of_the_one_main_caught():
    """The precondition of every test below: attack_fit_v3's refusal is a
    refusal.Refused that is NOT the class `main` named before #1671. If the
    classes are ever re-based onto each other the escape is gone and these
    tests no longer measure it -- say so here rather than pass for nothing."""
    assert SIBLING is measure_mono_m5a_reference.Refused
    assert issubclass(SIBLING, refusal.Refused)
    assert not issubclass(SIBLING, bass.Refused)


def test_the_degenerate_render_refuses_inside_the_attack_fit(tmp_path, monkeypatch):
    ours = _sweep_points(tmp_path, monkeypatch)
    _manifest, reference = bass.load_reference()
    # through the real code path, with no handler in the way: the phrase-level
    # guards pass and attack_fit_v3 refuses with ITS class
    with pytest.raises(SIBLING, match="attack fit window is silent"):
        bass.compare_audio(ours, reference)


def test_silent_attack_window_is_refused_with_exit_2(tmp_path, monkeypatch, capsys):
    _sweep_points(tmp_path, monkeypatch)
    rc = rescore.main()
    out = capsys.readouterr().out
    assert rc == 2, out
    assert "REFUSED: attack fit window is silent" in out


def test_a_non_refusal_error_at_the_same_site_still_propagates(tmp_path, monkeypatch):
    def broke(ours, ref, **_):
        raise ValueError("not a refusal: the estimator broke")

    _sweep_points(tmp_path, monkeypatch)
    monkeypatch.setattr(bass, "compare_audio", broke)
    with pytest.raises(ValueError, match="the estimator broke"):
        rescore.main()
