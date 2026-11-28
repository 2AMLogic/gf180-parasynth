"""Qualification, start-red and injected-bug controls for
tools/noise_stage_attribution.py (#556).

Cheap tests use signals whose answer is not from our model (Zwicker's z(f),
white-noise theory).  Two tests render the kit (~10 s each); the full MA/RS
stage list is a build-box job (docs/ma-rs-stage-attribution-request.md).
"""
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import noise_stage_attribution as n  # noqa: E402
import perceptual_gate as pg  # noqa: E402


def test_qualification_passes_on_known_answers():
    q = n.qualify()
    assert q["glide_worst_bark"] < n.GLIDE_TOL_BARK
    assert abs(q["white_flatness_db"] - n.WHITE_FLAT_DB) < n.WHITE_FLAT_TOL


def test_zwicker_formula_is_the_published_one():
    # published anchors of z(f): ~8.5 Bark at 1 kHz, ~17.5 at 4 kHz (Zwicker & Terhardt 1980)
    assert abs(float(n.zwicker_bark(1000.0)) - 8.5) < 0.2
    assert abs(float(n.zwicker_bark(4000.0)) - 17.5) < 0.4


@pytest.mark.parametrize("scale", [1.3, 0.7])
def test_control_bark_axis_defect_turns_qualification_red(monkeypatch, scale):
    monkeypatch.setattr(pg, "_BARK_OF_BAND", pg._BARK_OF_BAND * scale)
    with pytest.raises(n.Refused, match="Zwicker"):
        n.qualify()


def test_control_flatness_defect_turns_qualification_red(monkeypatch):
    # flatness taken over a 100 Hz-wide slice instead of 100 Hz-16 kHz: not white-noise -2.5 dB
    monkeypatch.setattr(pg, "_FREQS", np.where(pg._FREQS < 400, pg._FREQS, 1.0))
    with pytest.raises(n.Refused):
        n.qualify()


def test_start_red_stub_renderer_is_refused_not_scored():
    # a 1 kHz sine is not the kit: nothing to perturb, so no number
    with pytest.raises(n.Refused, match="not the kit"):
        n.attribute("MA", renderer=n.render_stub)


def test_unknown_sound_refused():
    with pytest.raises(n.Refused):
        n.attribute("BD")


def test_silent_and_nonfinite_refused():
    with pytest.raises(n.Refused):
        n.analyse_signal(np.zeros(48000), 48000)
    x = np.ones(48000)
    x[100] = np.nan
    with pytest.raises(n.Refused, match="non-finite"):
        n.analyse_signal(x, 48000)


def test_noop_perturbation_is_refused():
    # the constant exists but the sound does not read it as a different register image
    st = [("envelope", "MA_TAU x1", {"MA_TAU": n._dx().MA_TAU})]
    with pytest.raises(n.Refused, match="unchanged"):
        n.attribute("MA", stages=st, shifts=(3,))


def test_stale_stage_list_refused():
    with pytest.raises(n.Refused, match="stale"):
        with n.patched(NO_SUCH_CONSTANT=1):
            pass


def test_reference_side_refuses_without_corpus(tmp_path):
    with pytest.raises(n.Refused, match="corpus"):
        n.attribute_vs_reference("MA", None)
    with pytest.raises(n.Refused, match="manifest"):
        n.attribute_vs_reference("MA", tmp_path)


def test_constants_are_restored_after_patch():
    dx = n._dx()
    before = dx.MA_TAU
    with n.patched(MA_TAU=before * 3):
        assert dx.MA_TAU == before * 3
    assert dx.MA_TAU == before


def test_ma_attribution_sees_nonlinearity_and_not_an_inert_register():
    """Control pair on the real kit: the swing stage MUST read above the repeat
    floor, a register the MA hit does not read (the snare's noise filter) MUST NOT."""
    dx = n._dx()
    pert = {p[1]: p for p in n._perturbations("MA")}
    nl = pert["NL_SWING -> NL_LIN"]
    inert = ("inert_control", "SD_NOISE_HZ x2 (the snare is not struck by MA)", {"SD_NOISE_HZ": dx.SD_NOISE_HZ * 2})
    r = n.attribute("MA", stages=[nl, inert], shifts=(3,))
    assert r["stages"]["gate_nonlinearity"]["centroid_attributed"]
    assert not r["stages"]["inert_control"]["centroid_attributed"]
    assert not r["stages"]["inert_control"]["flatness_attributed"]
    assert r["stages"]["gate_nonlinearity"]["centroid"] > 20 * r["stages"]["inert_control"]["centroid"] \
        or r["stages"]["inert_control"]["centroid"] < 1e-3


# --- #1860: the gate's reader raises a SIBLING Refused; it must exit 2 -------
LFS_POINTER = ("version https://git-lfs.github.com/spec/v1\n"
               "oid sha256:" + "0" * 64 + "\nsize 12345\n")


@pytest.mark.parametrize("trigger", ["git-lfs-pointer", "sibling-refusal"])
def test_the_gates_refusal_on_the_fischer_take_exits_2(tmp_path, monkeypatch, capsys, trigger):
    """#1860. `attribute_vs_reference` reads the Fischer take through
    `pg.load_wav`, whose `Refused` is a sibling of this module's class; `main`
    caught only its own, so the refusal escaped as a traceback (exit 1).
    START RED on main for both triggers. The attribution itself is stubbed: it
    renders the kit (~10 s) and is not what is under test."""
    import drum_verify as dv
    from scipy.io import wavfile
    (tmp_path / "manifest.json").write_text("{}")
    p = tmp_path / dv.REF_MAIN["MA"][0]
    p.parent.mkdir(parents=True, exist_ok=True)
    if trigger == "git-lfs-pointer":
        p.write_text(LFS_POINTER)
    else:
        wavfile.write(p, 44100, (8000 * np.sin(2 * np.pi * 90 * np.arange(44100) / 44100)).astype(np.int16))

        def refuse(path):
            raise pg.Refused(f"{path}: raised by perceptual_gate, a sibling module")
        monkeypatch.setattr(pg, "load_wav", refuse)
    monkeypatch.setattr(n, "attribute", lambda sound, renderer, stages=None, shifts=n.REPEAT_SHIFTS: {})
    monkeypatch.setattr(n, "prediction_check", lambda result: {})
    assert n.main(["MA", "--refs", str(tmp_path)]) == 2
    text = capsys.readouterr().out
    assert text.startswith("REFUSED:") and str(p) in text, text[-400:]
