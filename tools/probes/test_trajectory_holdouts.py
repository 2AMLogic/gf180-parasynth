"""Selection controls for the two Fischer trajectory reports. No corpus required."""
from pathlib import Path
import sys

import numpy as np
import pytest
from scipy.io import wavfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "probes"))
import excitation_energy as energy


@pytest.fixture(params=["trajectory", "excitation"])
def report(request, tmp_path, monkeypatch):
    # Filename and knob position determine the split. Use the same sounding
    # PCM clip everywhere so no acoustic difference can determine selection.
    sr = 44100
    t = np.arange(sr // 3) / sr
    tone = np.sin(2 * np.pi * 100 * t) * np.exp(-t / 0.08)
    rendered = []

    def render(voice, knobs, laws, arm):
        rendered.append(knobs)
        return tone, sr

    monkeypatch.setattr(energy.td, "render", render)

    def run(voice, codes):
        directory = tmp_path / energy.td.ALL_REF[voice][0]
        directory.mkdir()
        for code in codes:
            wavfile.write(directory / f"{voice}{code}.WAV", sr,
                          (16000 * tone).astype(np.int16))
        refs = energy.td.ref_clips(str(tmp_path))
        if request.param == "trajectory":
            row = energy.dtj.compare(voice, refs, {})
        else:
            row = energy.voice_map(voice, refs, {}, tmp_path)
        return row, rendered

    return run


def test_low_tom_uses_only_the_two_held_out_tuning_positions(report):
    row, rendered = report("LT", ["00", "25", "50", "75", "10"])
    assert row["held_out"] and row["n"] == 2
    assert rendered == [(2.5,), (7.5,)]


def test_fixed_handclap_remains_descriptive(report):
    row, rendered = report("CP", [""])
    assert not row["held_out"] and row["n"] == 1
    assert rendered == [()]


def test_removing_low_tom_holdouts_cannot_turn_fit_clips_into_report_rows(report):
    row, rendered = report("LT", ["00", "50", "10"])
    assert not rendered, f"missing holdouts triggered renders at fit positions {rendered}"
    assert row is None


def test_a_fit_only_corpus_its_refusal_is_named_and_attributed(report, capsys):
    """The None of the test above is a REFUSAL, not a silent skip: it must be
    emitted, attributed to the voice, and say what was actually wrong -- an
    adjustable voice whose corpus holds fit settings only -- so it can never
    be read back as 'no knob to hold out', the label the old fallback gave
    it (#1362)."""
    row, rendered = report("LT", ["00", "50", "10"])
    assert row is None and not rendered
    out = capsys.readouterr().out
    assert "LT" in out, out
    assert "REFUSED" in out and "no held-out recording" in out, out
    assert "no knob to hold out" not in out, out


@pytest.fixture
def fit_only_corpus(tmp_path, monkeypatch):
    """The same fit-only low-tom corpus for the two 808 reports that do not
    go through `compare` or `voice_map`: `--bands` and `--shared` carried the
    identical fallback at 570e4632 (excitation_energy.py:1039 and :1256) and
    select through `td.report_clips` now. The law fitters are stubbed because
    this file tests SELECTION, not fitting, and `render` is recorded so a
    refusal that still rendered would be visible."""
    sr = 44100
    t = np.arange(sr // 3) / sr
    tone = np.sin(2 * np.pi * 100 * t) * np.exp(-t / 0.08)
    rendered = []

    def render(voice, knobs, laws, arm):
        rendered.append(knobs)
        return tone, sr

    monkeypatch.setattr(energy.td, "render", render)
    monkeypatch.setattr(energy.td, "fit_laws", lambda *a, **k: {})
    directory = tmp_path / energy.td.ALL_REF["LT"][0]
    directory.mkdir()
    for code in ("00", "50", "10"):
        wavfile.write(directory / f"LT{code}.WAV", sr,
                      (16000 * tone).astype(np.int16))
    return str(tmp_path), rendered


def test_the_bands_report_refuses_a_fit_only_corpus(fit_only_corpus, capsys):
    refdir, rendered = fit_only_corpus
    energy.report_bands(refdir, voices=["LT"])
    assert not rendered, f"missing holdouts triggered renders at fit positions {rendered}"
    out = capsys.readouterr().out
    assert "REFUSED" in out and "LT" in out and "no held-out recording" in out, out


def test_the_shared_circuit_report_refuses_a_fit_only_corpus(fit_only_corpus, capsys):
    refdir, rendered = fit_only_corpus
    energy.report_shared(refdir, voices=["LT"])
    assert not rendered, f"missing holdouts triggered renders at fit positions {rendered}"
    out = capsys.readouterr().out
    assert "REFUSED" in out and "LT" in out and "no held-out recording" in out, out
