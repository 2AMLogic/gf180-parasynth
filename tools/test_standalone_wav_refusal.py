#!/usr/bin/env python3
"""Known-answer controls for the standalone WAV readers (#1861, #1848 part 2/3).

    .venv/bin/python -m pytest tools/test_standalone_wav_refusal.py -q

THE DEFECT CLASS. Nine readers of the drum, corpus and probe tools call
`scipy.io.wavfile.read` and let scipy's own exception out: `FileNotFoundError` /
`IsADirectoryError` (an `OSError`) for a path that is not a readable file, and
`ValueError` for a file that is not RIFF/RIFX/RF64 -- a git-lfs pointer, a
0-byte file, any text. A CLI that owes `exit_contract.REFUSED` (2) therefore
died with a traceback and exit 1, which `tools/run_all.py` renders as FAIL: a
RESULT, in the place where a missing precondition belongs.

THE KNOWN ANSWERS, independent of anything this repository models:

  a 1 kHz sine at exactly 0.5 full scale, 48 samples per cycle at 48 kHz. Its
  peak is 0.5 after the `/32768` conditioning, its first sample above 2 % of
  peak is sample 1 (sample 0 is zero, sample 1 is 0.13), and its maximum sits
  at sample 12 (a quarter cycle). None of that is read off our own model.

  a git-lfs pointer is not a WAV. scipy says so with `ValueError`; what the
  reader must do about it is the subject of every refusal test below.

WHAT LANDED RED, on a clean `origin/main` worktree (test file copied in,
nothing else), before any production edit -- recorded here because a
control nobody has watched fail is not a control (docs/verification-rules.md
rule 1). The complete file: 59 failed, 18 passed, 4 xfailed; on the fixed
tree 75 passed, 6 xfailed. So 57 red -> green, 18 pins green on both sides,
and the 6 strict xfails that are the rule-8 marker below (4 xfail on both
trees; 2 fail on the unfixed tree only because the class, or the module's
importability, did not exist yet). Every red was read by its own one-line
reason: no `Refused` class yet (22); scipy's ValueError out of a reader or
a CLI (18); ab_808_loud running on import (8); IsADirectoryError (3);
FileNotFoundError (2); an ab_808_loud CLI exiting 1 (4); drum_verify
reading twice (1); and the zero-frame file leaving drum_verify as numpy's
own ValueError (1).

  reader                  missing            pointer / directory / 0-byte
  ---------------------   ----------------   ------------------------------
  conga.read_wav          FileNotFoundError  ValueError / IsADirectoryError
  disc.read_wav           FileNotFoundError  ValueError / IsADirectoryError
  drum_verify.read_wav    FileNotFoundError  ValueError / IsADirectoryError
  tom.read_wav            FileNotFoundError  ValueError / IsADirectoryError
  ab_808_loud.load        (not importable)   (not importable)
  refprofile.read_clip    FileNotFoundError  ValueError / IsADirectoryError
  score_drum_i2s.load_w.  FileNotFoundError  ValueError / IsADirectoryError
  hh_probe5.scorecard     Refused (pinned)   ValueError / IsADirectoryError
  estimator_defects.d5    FileNotFoundError  ValueError / IsADirectoryError

and at the CLI: `measure_conga_body_spread`, `drum_verify`, `tom_pitch_probe`
and `ab_808_loud` exited 1 with a traceback on a pointer file;
`score_drum_i2s` already exited 2 but without naming the path;
`estimator_defects.defect_5` raised past `main()`'s `except refusal.Refused`.
`moog_probe.main` already exits 2 through `scan()` and is pinned, not fixed.

WRONG BEFORE RIGHT, this file's own: the first red run read 57 failed, and
four of those were red for the WRONG reason -- fixture errors, which rule 5
counts as no verdict rather than a catch. Raw pointer bytes were sent through
the 16-bit writer (a `TypeError` from `np.round`); a stubbed empty floor list
hit `max()` on nothing at the stage under test's predecessor; and the
refprofile fixture pointed `PROFILE_DIR` one directory above the file it
planted, so the refusal observed was the pre-existing "not in the cache".
Each was read off its one-line reason, not off the count.

WHAT IS DELIBERATELY NOT HERE. `hh_probe5.main()` has no CLI boundary yet
(#1611 owns it); the eight downstream callers of `test_discrimination.read_wav`
and `tom_pitch_probe.read_wav`'s sibling callers get no CLI handler here; a
truncated WAV's `WavFileWarning` is #1848's. `defect_1`'s read through
`run_case.load_reference` stays an exit-1 execution error until #1860 lands
(`tools/test_estimator_defects_refusal.py` pins that, and must stay green).

WHAT THE READ GUARD CANNOT SEE (rule 8, measured on the fixed tree): a
RIFF header with zero data frames. scipy raises nothing for it, so a
guard on `wavfile.read` passes it, and `measure_conga_body_spread`,
`test_discrimination`, `tom_pitch_probe`, `ab_808_loud` and
`refprofile.read_clip_file` answer with an empty array while
`drum_verify.read_wav` escapes numpy's own ValueError. `hh_probe5`
(silent) and `score_drum_i2s` (frame count) refuse it on their content
checks. Pinned below as strict xfails rather than fixed: it is #1848's
truncated-WAV class, a content precondition and not a readability one.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import pathlib
import struct
import subprocess
import sys
import wave

import numpy as np
import pytest
from scipy.io import wavfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in ("tools", "model", "tools/probes", "tools/probes/hihat"):
    if str(ROOT / _p) not in sys.path:
        sys.path.insert(0, str(ROOT / _p))

import drum_verify as dv                                        # noqa: E402
import estimator_defects as ed                                  # noqa: E402
import exit_contract                                            # noqa: E402
import hh_probe5                                                # noqa: E402
import measure_conga_body_spread as conga                       # noqa: E402
import moog_probe as mp                                         # noqa: E402
import refprofile as rp                                         # noqa: E402
import refusal                                                  # noqa: E402
import score_drum_i2s as score                                  # noqa: E402
import test_discrimination as disc                              # noqa: E402
import tom_pitch_probe as tom                                   # noqa: E402

SR = 48000
F0 = 1000.0                       # 48 samples per cycle at 48 kHz
PEAK = 0.5
SECS = 0.25
N = int(SECS * SR)

#: A real git-lfs pointer: what an un-pulled corpus file looks like on disk.
LFS_POINTER = (b"version https://git-lfs.github.com/spec/v1\n"
               b"oid sha256:" + b"0f" * 32 + b"\nsize 88244\n\n")


# ---------------------------------------------------------------------------
# fixtures whose answers are known without this repository
# ---------------------------------------------------------------------------
def _sine(n: int = N, pre_roll: int = 0) -> np.ndarray:
    t = np.arange(n) / SR
    return np.concatenate([np.zeros(pre_roll), PEAK * np.sin(2 * np.pi * F0 * t)])


def _write16(path: pathlib.Path, sig: np.ndarray | None = None) -> np.ndarray:
    raw = np.round((_sine() if sig is None else sig) * 32767).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(str(path), SR, raw)
    return raw


def _place_bytes(path: pathlib.Path, content: bytes) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _decay(secs: float = 1.0, sr: int = SR, hz: float = 180.0) -> np.ndarray:
    """A drum-like known signal: a decaying sine, peak 0.5, with a 5 ms lead."""
    t = np.arange(int(secs * sr)) / sr
    return np.concatenate([np.zeros(int(0.005 * sr)),
                           PEAK * np.exp(-t / 0.15) * np.sin(2 * np.pi * hz * t)])


def _decay_bytes(secs: float, sr: int) -> bytes:
    import io
    buf = io.BytesIO()
    wavfile.write(buf, sr, np.round(_decay(secs, sr) * 32767).astype("<i2"))
    return buf.getvalue()


def _peak(x) -> float:
    return float(np.abs(np.asarray(x, dtype=np.float64)).max())


BAD_INPUTS = {
    "missing": lambda p: p,                                  # never created
    "git-lfs-pointer": lambda p: _place_bytes(p, LFS_POINTER),
    "zero-byte": lambda p: _place_bytes(p, b""),
    "directory": lambda p: (p.mkdir(parents=True), p)[1],
}


def _ab():
    """`tools/ab_808_loud.py` is a script; it must import WITHOUT running."""
    return importlib.import_module("ab_808_loud")


# ---------------------------------------------------------------------------
# the readers, each returning (x, sr) whatever its own tuple order
# ---------------------------------------------------------------------------
class Reader:
    def __init__(self, name, module, read, known):
        self.name, self._module, self.read, self.known = name, module, read, known

    @property
    def module(self):
        return self._module() if callable(self._module) else self._module


def _half_scale(x, sr, raw):
    """/32768 readers: the known 0.5, bit-exact with the old conditioning."""
    assert sr == SR and len(x) == N
    assert abs(_peak(x) - PEAK) < 1e-3, _peak(x)
    assert np.array_equal(np.asarray(x, dtype=np.float64), raw.astype(np.float64) / 32768.0)


def _peak_normalised(x, sr, raw):
    """drum_verify divides by 32768, removes DC and peak-normalises."""
    assert sr == SR and len(x) == N
    assert abs(_peak(x) - 1.0) < 1e-12, _peak(x)
    assert np.allclose(x, _sine() / PEAK, atol=1e-3)


def _unscaled(x, sr, raw):
    """refprofile.read_clip_file reads the frozen float32 clips and scales
    nothing; an int16 file comes back as its raw sample values."""
    assert sr == SR
    assert np.array_equal(np.asarray(x, dtype=np.float64), raw.astype(np.float64))


READERS = [
    Reader("measure_conga_body_spread.read_wav", conga, lambda p: conga.read_wav(p),
           _half_scale),
    Reader("test_discrimination.read_wav", disc, lambda p: disc.read_wav(str(p)),
           _half_scale),
    Reader("drum_verify.read_wav", dv, lambda p: dv.read_wav(str(p))[::-1],
           _peak_normalised),
    Reader("tom_pitch_probe.read_wav", tom, lambda p: tom.read_wav(str(p))[::-1],
           _half_scale),
    Reader("ab_808_loud.load", _ab, lambda p: (_ab().load(str(p)), SR), _half_scale),
    Reader("refprofile.read_clip_file", rp, lambda p: rp.read_clip_file(p), _unscaled),
    Reader("score_drum_i2s.load_window", score,
           lambda p: (score.load_window(p, N), SR), _half_scale),
]
READER_IDS = [r.name for r in READERS]


@pytest.mark.parametrize("bad", sorted(BAD_INPUTS))
@pytest.mark.parametrize("reader", READERS, ids=READER_IDS)
def test_reader_refuses_a_wav_it_cannot_read_naming_path_and_reason(reader, bad, tmp_path):
    """The module's OWN class is asserted, not the base: a bare
    `raise refusal.Refused(...)` is a class nothing in that module catches
    (tools/refusal.py), so it must go red here too (rule 8)."""
    p = tmp_path / "take.wav"
    BAD_INPUTS[bad](p)
    with pytest.raises(reader.module.Refused, match=r"unreadable WAV") as why:
        reader.read(p)
    assert isinstance(why.value, refusal.Refused), type(why.value)
    assert str(p) in str(why.value), str(why.value)


@pytest.mark.parametrize("reader", READERS, ids=READER_IDS)
def test_reader_answers_the_known_sine_exactly_as_before(reader, tmp_path):
    """The other leg: a refusal added to a reader must not move its answer."""
    p = tmp_path / "known.wav"
    raw = _write16(p)
    x, sr = reader.read(p)
    reader.known(x, sr, raw)


# --- the class contract that keeps the existing suites green ----------------
def _zero_frame_wav(tmp_path: pathlib.Path) -> pathlib.Path:
    p = tmp_path / "empty.wav"
    wavfile.write(str(p), SR, np.zeros(0, dtype="<i2"))
    return p


#: THE INPUT THAT DEFEATS THE READ GUARD (verification-rules rule 8), measured
#: on the fixed tree: a RIFF header with zero data frames raises nothing in
#: scipy, so a guard on the read cannot see it, and these readers answer with
#: an empty array. drum_verify's escapes as numpy's own ValueError. A
#: zero-frame file is a content precondition, not a readability one -- #1848's
#: truncated-WAV class -- and is deliberately not closed here. Both controls
#: are strict: the day a reader refuses it, the xfail turns into a failure and
#: retires itself.
ZERO_FRAME_ANSWERS = [r for r in READERS if r.name.split(".")[0] in (
    "measure_conga_body_spread", "test_discrimination", "tom_pitch_probe",
    "ab_808_loud", "refprofile")]


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="read-level guard (#1861): a zero-frame WAV raises nothing in "
                          "scipy, so the reader answers with an empty array")
@pytest.mark.parametrize("reader", ZERO_FRAME_ANSWERS, ids=[r.name for r in ZERO_FRAME_ANSWERS])
def test_a_zero_frame_wav_refuses(reader, tmp_path):
    try:
        reader.read(_zero_frame_wav(tmp_path))
    except reader.module.Refused:
        return
    raise AssertionError("answered with an empty array instead of refusing")


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="read-level guard (#1861): a zero-frame WAV leaves drum_verify."
                          "read_wav as numpy's own ValueError, an execution error")
def test_a_zero_frame_wav_refuses_in_drum_verify(tmp_path):
    try:
        dv.read_wav(str(_zero_frame_wav(tmp_path)))
    except dv.Refused:
        return
    except ValueError as exc:
        raise AssertionError(f"escaped as {type(exc).__name__}: {exc}") from exc
    raise AssertionError("answered with an empty array instead of refusing")


def test_the_readers_with_a_content_check_refuse_a_zero_frame_wav(tmp_path):
    """The positive leg: these two never reach for the read guard, because
    each already states what a usable file must hold."""
    p = _zero_frame_wav(tmp_path)
    with pytest.raises(score.Refused, match=r"0 frames"):
        score.load_window(p, N)
    with pytest.raises(hh_probe5.Refused, match=r"silent"):
        hh_probe5.scorecard_record(np.zeros(10, dtype=np.int16), "empty.wav", "s", "c",
                                   tmp_path, _hh_prov(), {})


#: The readers whose width refusal was a `ValueError` before this issue.
#: `tools/test_wav_reader_refusal.py` reads conga and discrimination with
#: `pytest.raises(ValueError)`; `measure_harness._DATA_ERRORS` lists
#: `ValueError` and decides `unreadable` against `error` by it. A Refused that
#: is not also a ValueError breaks both, so these stay both. refprofile and
#: score_drum_i2s keep the classes they already had.
VALUEERROR_READERS = [r for r in READERS
                      if r.name.split(".")[0] in ("measure_conga_body_spread",
                                                  "test_discrimination", "drum_verify",
                                                  "tom_pitch_probe", "ab_808_loud")]


@pytest.mark.parametrize("reader", VALUEERROR_READERS, ids=[r.name for r in VALUEERROR_READERS])
def test_the_wav_reader_refusal_suites_still_see_a_valueerror(reader):
    assert issubclass(reader.module.Refused, ValueError)


# ===========================================================================
# hh_probe5.scorecard_record -- the reader only; main() is #1611's
# ===========================================================================
def _hh_prov() -> dict:
    return dict(source_commit="0000000", described="a test", worktree_dirty=False,
                cmd="scorecard_record under pytest", python="3.12")


def _hh_record(refdir: pathlib.Path, rel: str = "cy8/CY.WAV"):
    ours = np.round(_sine() * 32767).astype(np.int16)
    return hh_probe5.scorecard_record(ours, rel, "TONE 5.0, DECAY 2.5", "D14A",
                                       refdir, _hh_prov(), {})


@pytest.mark.parametrize("bad", sorted(BAD_INPUTS))
def test_hh_probe5_scorecard_record_refuses_an_unreadable_reference(bad, tmp_path):
    """`missing` is the pre-existing `exists()` refusal, pinned so it stays;
    the other three are scipy's own exceptions escaping (#1861)."""
    p = tmp_path / "cy8" / "CY.WAV"
    BAD_INPUTS[bad](p)
    with pytest.raises(hh_probe5.Refused) as why:
        _hh_record(tmp_path)
    assert "CY.WAV" in str(why.value), str(why.value)


def test_hh_probe5_scorecard_record_reads_a_known_reference_unchanged(tmp_path):
    """A 10 ms digital-silence lead, then the sine: the record's own windowing
    report must put the onset at sample 480 + 1, the first sample above 2 % of
    peak. Read off the reference through the reader, written to the record."""
    pre = int(0.010 * SR)
    _write16(tmp_path / "cy8" / "CY.WAV", _sine(N, pre))
    rec = _hh_record(tmp_path)
    onset = rec["windowing"]["reference"]["onset_index"]
    assert onset == pre + 1, rec["windowing"]["reference"]
    assert rec["case_id"] == "D14A"


# ===========================================================================
# refprofile: a hash-matching non-RIFF entry
# ===========================================================================
def test_refprofile_load_clip_refuses_a_hash_matching_non_riff_entry(tmp_path, monkeypatch):
    """Every integrity check says this file is the one the profile describes;
    none of them can say it is audio. On main the scipy ValueError escaped
    past `load_clip`'s callers' `except Refused` boundaries."""
    pdir = tmp_path / "refprofile"
    monkeypatch.setattr(rp, "PROFILE_DIR", pdir)         # what clip_path() reads / "refprofile"
    rel = pathlib.Path("cache") / "fake" / "clip.wav"
    dest = _place_bytes(pdir / rel, LFS_POINTER)
    prof = {"schema": rp.SCHEMA, "sr": SR, "probe_level_dbfs": rp.PROBE_LEVEL_DBFS,
            "built": {"worktree": {"commit": "deadbee", "dirty": False}},
            "rigs": {"fake": {"qualified": True}},
            "clips": {"fake/clip": {
                "rig": "fake", "kind": "tone_train", "why": "a pointer",
                "file": str(rel), "sr": SR, "frames": N, "dtype": "float32",
                "bytes": dest.stat().st_size, "sha256": rp.file_sha256(dest),
                "amp": 0.25, "freqs_hz": [200.0], "parts": [[0, N, 200.0]]}}}
    with pytest.raises(rp.Refused) as why:
        rp.load_clip("fake/clip", prof)
    assert str(dest) in str(why.value), str(why.value)


def test_refprofile_read_clip_file_answers_a_float32_clip_unchanged(tmp_path):
    p = tmp_path / "clip.wav"
    wavfile.write(str(p), SR, _sine().astype(np.float32))
    y, sr = rp.read_clip_file(p)
    assert sr == SR and abs(_peak(y) - PEAK) < 1e-6


# ===========================================================================
# CLI boundaries: exit_contract.REFUSED, REFUSED: on a stream, nothing written
# ===========================================================================
def test_conga_main_check_refuses_a_pointer_anchor(tmp_path, capsys):
    """The anchor is a file, so `require_takes` passes it; only the read can
    know. On main: ValueError traceback out of `validate_reproduction`."""
    for rel in conga.PUBLISHED_TAKES:
        _write16(tmp_path / rel, _decay())
    _place_bytes(tmp_path / "lc8" / "LC50.WAV", LFS_POINTER)
    out = tmp_path / "r.json"
    code = conga.main(["--check", "--refs", str(tmp_path), "--json", str(out)])
    assert code == exit_contract.REFUSED
    err = capsys.readouterr().err
    assert "REFUSED:" in err and "lc8/LC50.WAV" in err, err[-400:]
    rep = json.loads(out.read_text())
    assert rep["outcome_code"] == code
    assert "lc8/LC50.WAV" in rep["refusal"]["why"]
    assert "known_answer" in rep and "reproduction" not in rep


def _floor_row() -> list[dict]:
    """One row in the shape `floor_for_these_signals` returns, so `main()`'s
    floor table prints and the stage under test is the next one."""
    return [dict(file="f", lead_1ms_db=0.0, lead_10ms_db=0.0, sample_rate_44k_to_48k_db=0.0,
                 quantisation_16bit_db=0.0, window_10ms_db=0.0)]


def test_conga_main_full_run_refuses_a_pointer_non_anchor_take(tmp_path, capsys, monkeypatch):
    """A non-anchor take is first read by `measure_corpus`, past every control
    in the `--check` block -- the stage the old `except InventoryRefused` did
    not cover. Rendering is stubbed; the inventory and the reads are real."""
    for rel in conga.expected_takes():
        _write16(tmp_path / rel, _decay())
    _place_bytes(tmp_path / "mc8" / "MC00.WAV", LFS_POINTER)
    monkeypatch.setattr(conga, "validate_reproduction", lambda refdir: [])
    monkeypatch.setattr(conga, "validation_status", lambda report: 0)
    monkeypatch.setattr(conga, "floor_for_these_signals", lambda refdir: _floor_row())
    out = tmp_path / "r.json"
    code = conga.main(["--refs", str(tmp_path), "--json", str(out)])
    assert code == exit_contract.REFUSED
    err = capsys.readouterr().err
    assert "REFUSED:" in err and "mc8/MC00.WAV" in err, err[-400:]
    rep = json.loads(out.read_text())
    assert rep["outcome_code"] == code
    assert not {"corpus", "spread", "sensitivity", "within_10pct"} & set(rep)


def test_conga_main_keeps_the_inventory_refusals_missing_field(tmp_path, capsys):
    """The two refusals are different facts and the JSON must still say which:
    an absent take lists `missing`; an unreadable one names the file in `why`."""
    for rel in conga.expected_takes():
        _write16(tmp_path / rel, _decay())
    (tmp_path / "hc8" / "HC10.WAV").unlink()
    out = tmp_path / "r.json"
    code = conga.main(["--refs", str(tmp_path), "--json", str(out)])
    assert code == exit_contract.REFUSED
    rep = json.loads(out.read_text())
    assert rep["refusal"]["missing"] == ["hc8/HC10.WAV"]


def test_conga_main_maps_a_descent_refusal_to_exit_2(tmp_path, capsys, monkeypatch):
    """`descent_test` sits outside the old `try`; a refusal raised in it is
    now caught at the same boundary as every other stage."""
    for rel in conga.expected_takes():
        _write16(tmp_path / rel, _decay())
    out = tmp_path / "r.json"
    monkeypatch.setattr(conga, "validate_reproduction", lambda refdir: [])
    monkeypatch.setattr(conga, "validation_status", lambda report: 0)
    monkeypatch.setattr(conga, "floor_for_these_signals", lambda refdir: _floor_row())
    for name in ("measure_corpus", "measure_ours", "sensitivity", "windowed_alike"):
        monkeypatch.setattr(conga, name, lambda *a, **k: [])

    def refuse(refdir, cands):
        raise conga.Refused(f"{refdir}/lc8/LC50.WAV: unreadable WAV (test)")

    monkeypatch.setattr(conga, "descent_test", refuse)
    code = conga.main(["--refs", str(tmp_path), "--json", str(out), "--descent", str(tmp_path)])
    assert code == exit_contract.REFUSED
    assert "REFUSED:" in capsys.readouterr().err
    assert json.loads(out.read_text())["outcome_code"] == code


def test_conga_descent_does_not_swallow_a_reference_read_refusal(tmp_path, capsys, monkeypatch):
    """The real `measure_harness.descent_test`, no stub on that path. `Refused`
    is also a `ValueError`, which the harness lists in `_DATA_ERRORS`: were
    `read_ref` caught under it, an unreadable reference take would come back
    as an `unreadable` ROW, `descent_status` would return 0, and a missing
    precondition would read as a completed run."""
    try:
        import soundfile  # noqa: F401
    except ImportError:
        pytest.fail("soundfile is not installed: the descent comparison did not run")
    for i, rel in enumerate(conga.expected_takes(("LC", "MC", "HC"))):
        _write16(tmp_path / rel, _decay(hz=150.0 + 10 * i))
    cands = tmp_path / "candidates"
    _write16(cands / "low conga a.wav", _decay(hz=333.0))
    bad = tmp_path / "lc8" / "LC75.WAV"
    _place_bytes(bad, LFS_POINTER)
    monkeypatch.setattr(conga, "validate_reproduction", lambda refdir: [])
    monkeypatch.setattr(conga, "validation_status", lambda report: 0)
    monkeypatch.setattr(conga, "floor_for_these_signals", lambda refdir: _floor_row())
    for name in ("measure_corpus", "measure_ours", "sensitivity", "windowed_alike"):
        monkeypatch.setattr(conga, name, lambda *a, **k: [])
    out = tmp_path / "r.json"
    code = conga.main(["--refs", str(tmp_path), "--json", str(out), "--descent", str(cands)])
    assert code == exit_contract.REFUSED
    err = capsys.readouterr().err
    assert "REFUSED:" in err and "lc8/LC75.WAV" in err, err[-400:]
    rep = json.loads(out.read_text())
    assert rep["outcome_code"] == code and "descent" not in rep


# --- model/drum_verify.py ---------------------------------------------------
def _dv_argv(refs, ours, out):
    return ["drum_verify", "--refs", str(refs), "--ours", str(ours), "--out", str(out),
            "--no-plots", "--voices", "BD"]


def _dv_corpus(root):
    ours = root / "ours"
    ours.mkdir()
    _write16(root / dv.REF_MAIN["BD"][0], _decay())
    _write16(ours / "00-solo-00-BD.wav", _decay(1.5))
    return ours


@pytest.mark.parametrize("side", ["reference", "render"])
def test_drum_verify_main_refuses_an_unreadable_wav(tmp_path, capsys, monkeypatch, side):
    ours = _dv_corpus(tmp_path)
    target = (tmp_path / dv.REF_MAIN["BD"][0]) if side == "reference" \
        else (ours / "00-solo-00-BD.wav")
    _place_bytes(target, LFS_POINTER)
    out = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", _dv_argv(tmp_path, ours, out))
    code = dv.main()
    assert code == exit_contract.REFUSED
    err = capsys.readouterr().err
    assert "REFUSED:" in err and str(target) in err, err[-400:]
    assert not list(out.glob("*.json"))


def test_drum_verify_main_still_skips_an_absent_voice(tmp_path, capsys, monkeypatch):
    """The pre-existing rule for a voice that is simply not there: say so,
    skip it, and exit 0. Unchanged, and pinned so a refusal cannot swallow it."""
    ours = _dv_corpus(tmp_path)
    (tmp_path / dv.REF_MAIN["BD"][0]).unlink()
    out = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", _dv_argv(tmp_path, ours, out))
    assert dv.main() == exit_contract.PASS
    assert "!! missing reference" in capsys.readouterr().err


def test_drum_verify_main_measures_a_readable_pair(tmp_path, capsys, monkeypatch):
    ours = _dv_corpus(tmp_path)
    out = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", _dv_argv(tmp_path, ours, out))
    assert dv.main() == exit_contract.PASS


def test_drum_verify_read_wav_reads_the_file_once(tmp_path, monkeypatch):
    """It used to call `wavfile.read` twice on the same path to learn the
    dtype it had already been handed."""
    p = tmp_path / "known.wav"
    _write16(p, _decay())
    calls = []
    real = wavfile.read

    def counting(path, *a, **k):
        calls.append(path)
        return real(path, *a, **k)

    monkeypatch.setattr(dv.wavfile, "read", counting)
    dv.read_wav(str(p))
    assert calls == [str(p)], calls


# --- model/tom_pitch_probe.py ------------------------------------------------
def test_tom_pitch_probe_read_wav_refuses_an_unsupported_format(tmp_path):
    """The width refusal this reader already had, now carried by the class
    `main()` catches -- and still a ValueError for its sibling callers."""
    p = tmp_path / "uint8.wav"
    wavfile.write(str(p), SR, np.round(_sine() * 127 + 128).astype(np.uint8))
    with pytest.raises(tom.Refused, match=r"unsupported sample format uint8") as why:
        tom.read_wav(str(p))
    assert isinstance(why.value, ValueError) and str(p) in str(why.value)


def test_tom_pitch_probe_main_refuses_an_unreadable_wav(tmp_path, capsys):
    p = _place_bytes(tmp_path / "take.wav", LFS_POINTER)
    code = tom.main([str(p)])
    assert code == exit_contract.REFUSED
    err = capsys.readouterr().err
    assert "REFUSED:" in err and str(p) in err, err[-400:]


def test_tom_pitch_probe_main_measures_a_readable_wav(tmp_path, capsys):
    """A steady sine has no pitch drop, so the row itself is REFUSED by the
    estimator -- a stated decision on readable data, and still exit 0. The
    exit code separates `could not read` (2) from `read, and declined` (0)."""
    p = tmp_path / "known.wav"
    _write16(p, _decay(0.5))
    assert tom.main([str(p)]) == exit_contract.PASS


def test_tom_pitch_probe_main_refuses_a_later_file_after_a_readable_one(tmp_path, capsys):
    good = tmp_path / "a.wav"
    _write16(good, _decay(0.5))
    bad = _place_bytes(tmp_path / "b.wav", LFS_POINTER)
    assert tom.main([str(good), str(bad), "--json"]) == exit_contract.REFUSED
    out = capsys.readouterr()
    assert "REFUSED:" in out.err and str(bad) in out.err
    assert "{" not in out.out, "a refused run must not print a rows document"


# --- tools/ab_808_loud.py ----------------------------------------------------
def _ab_run(pack: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "tools" / "ab_808_loud.py"),
                           str(pack)], capture_output=True, text=True, timeout=300, check=False)


def _ab_pair(pack: pathlib.Path, tag="t", b=None):
    _write16(pack / f"{tag}-a-808.wav", _decay())
    if b is None:
        _write16(pack / f"{tag}-b-ours.wav", _decay(0.8))
    return pack / f"{tag}-b-ours.wav"


def test_ab_808_loud_imports_without_running(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mod = _ab()
    assert hasattr(mod, "main") and not list(tmp_path.iterdir())


def test_ab_808_loud_refuses_a_pointer_and_writes_nothing(tmp_path):
    pack = tmp_path / "pack"
    a = _ab_pair(pack)
    _place_bytes(pack / "t-a-808.wav", LFS_POINTER)
    r = _ab_run(pack)
    assert r.returncode == exit_contract.REFUSED, (r.returncode, r.stderr[-400:])
    assert "REFUSED:" in r.stderr and str(a.with_name("t-a-808.wav")) in r.stderr
    assert "Traceback" not in r.stderr
    assert not (pack / "00-all-AB.wav").exists() and not (pack / "t-c-AB.wav").exists()


def test_ab_808_loud_refuses_a_missing_ours_side(tmp_path):
    pack = tmp_path / "pack"
    _write16(pack / "t-a-808.wav", _decay())            # no t-b-ours.wav at all
    r = _ab_run(pack)
    assert r.returncode == exit_contract.REFUSED, (r.returncode, r.stderr[-400:])
    assert "REFUSED:" in r.stderr and "t-b-ours.wav" in r.stderr
    assert not (pack / "00-all-AB.wav").exists()


def test_ab_808_loud_refuses_an_empty_pack(tmp_path):
    pack = tmp_path / "pack"
    pack.mkdir()
    r = _ab_run(pack)
    assert r.returncode == exit_contract.REFUSED, (r.returncode, r.stderr[-400:])
    assert "REFUSED:" in r.stderr and str(pack) in r.stderr


def test_ab_808_loud_refuses_24bit_as_exit_2(tmp_path):
    pack = tmp_path / "pack"
    _ab_pair(pack)
    q = np.round(_decay() * (2 ** 23 - 1)).astype(np.int32)
    p = pack / "t-a-808.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(3)
        w.setframerate(SR)
        w.writeframes(b"".join(struct.pack("<i", int(v))[:3] for v in q))
    r = _ab_run(pack)
    assert r.returncode == exit_contract.REFUSED, (r.returncode, r.stderr[-400:])
    assert "int32" in r.stderr and "16-bit" in r.stderr and "REFUSED:" in r.stderr
    assert not (pack / "00-all-AB.wav").exists()


def test_ab_808_loud_matches_a_readable_pair(tmp_path):
    pack = tmp_path / "pack"
    _ab_pair(pack)
    r = _ab_run(pack)
    assert r.returncode == exit_contract.PASS, (r.returncode, r.stderr[-400:])
    assert "ours was" in r.stdout and "matched" in r.stdout
    assert (pack / "00-all-AB.wav").is_file() and (pack / "t-c-AB.wav").is_file()


# --- tools/score_drum_i2s.py -------------------------------------------------
def test_score_drum_i2s_cli_refuses_a_pointer_window_naming_it(tmp_path, capsys):
    """The CLI already exited 2 (via `except (OSError, ValueError, KeyError)`)
    but printed scipy's message, which names no file. The transcript is a
    valid one for THIS window's hash, so the window read is what refuses."""
    from test_score_drum_i2s import _transcript
    frames = int(2.2 * score.SR)
    wav = _place_bytes(tmp_path / "w.wav", LFS_POINTER)
    sha = hashlib.sha256(wav.read_bytes()).hexdigest()
    verification = tmp_path / "verify.log"
    verification.write_text(_transcript(sha=sha, sound="SD", frames=frames))
    code = score.main(["--case", "D02A", "--wav", str(wav), "--verification", str(verification),
                       "--out", str(tmp_path / "r.json")])
    out = capsys.readouterr()
    assert code == exit_contract.REFUSED
    assert "score_drum_i2s: REFUSED --" in out.out and str(wav) in out.out, out.out[-400:]
    assert not (tmp_path / "r.json").exists()


# --- tools/probes/estimator_defects.py, defect_5 ------------------------------
def _ed_corpus(root: pathlib.Path, bd=None, extra=None) -> pathlib.Path:
    """`bd` is a signal (written as 16-bit PCM) or raw bytes (planted as is)."""
    root.mkdir(parents=True, exist_ok=True)
    if isinstance(bd, bytes):
        _place_bytes(root / "bd8" / "BD5050.WAV", bd)
    elif bd is not None:
        _write16(root / "bd8" / "BD5050.WAV", bd)
    for rel, content in (extra or {}).items():
        if isinstance(content, bytes) and not content.startswith(b"RIFF"):
            _place_bytes(root / rel, content)
        else:
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_bytes(content)
    return root


def test_estimator_defects_defect5_refuses_a_pointer_bd(tmp_path, monkeypatch, capsys):
    refs = _ed_corpus(tmp_path / "refs", bd=LFS_POINTER)
    monkeypatch.setattr(ed, "REFS", refs)
    with pytest.raises(refusal.Refused) as why:
        ed.defect_5()
    assert "bd8/BD5050.WAV" in str(why.value), str(why.value)


def test_estimator_defects_defect5_refuses_a_pointer_in_the_floor_sweep(tmp_path, monkeypatch):
    """The second read (:676) walks every WAV under the corpus for the tail
    floor table; a pointer anywhere in it must refuse the whole table."""
    refs = _ed_corpus(tmp_path / "refs", bd=_decay(1.0, 44100),
                      extra={"sd8/SD5050.WAV": LFS_POINTER})
    monkeypatch.setattr(ed, "REFS", refs)
    with pytest.raises(refusal.Refused) as why:
        ed.defect_5()
    assert "sd8/SD5050.WAV" in str(why.value), str(why.value)


def test_estimator_defects_defect5_reads_a_readable_corpus(tmp_path, monkeypatch):
    refs = _ed_corpus(tmp_path / "refs", bd=_decay(1.0, 44100),
                      extra={"cl8/CL.WAV": _decay_bytes(0.3, 44100)})
    monkeypatch.setattr(ed, "REFS", refs)
    assert ed.defect_5() is None


def test_estimator_defects_cli_maps_defect5_to_exit_2(tmp_path, monkeypatch, capsys):
    """Through `main()`, so the exit is the shipped one: its existing
    `except refusal.Refused` now also covers `defect_5`'s own reads. The
    earlier sections are stubbed only because they read other voices."""
    refs = _ed_corpus(tmp_path / "refs", bd=LFS_POINTER)
    monkeypatch.setattr(ed, "REFS", refs)
    for name in ("defect_1", "defect_2", "defect_3", "defect_4", "defect_6"):
        monkeypatch.setattr(ed, name, lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", ["estimator_defects.py"])
    assert ed.main() == exit_contract.REFUSED
    out, err = capsys.readouterr()
    assert "REFUSED" in out and "bd8/BD5050.WAV" in out, out[-400:]
    assert "check(s) FAILED" not in out and "Traceback" not in err


# --- model/moog_probe.py: already guarded at the CLI, pinned ------------------
def test_moog_probe_main_refuses_a_pointer_in_the_set(tmp_path, capsys):
    """No production edit: `scan()` at :310 reads every `*.wav` in the set
    and refuses the whole set before the bare read at :349 is reached."""
    setdir = tmp_path / "set"
    t = np.arange(44100) / 44100
    _write16(setdir / "good.wav", 0.5 * np.sin(2 * np.pi * 220 * t))
    _place_bytes(setdir / "SYNTH-SimpleThinSquareFilterSlope.wav", LFS_POINTER)
    code = mp.main(["--set", str(setdir)])
    out = capsys.readouterr().out
    assert code == exit_contract.REFUSED
    assert "REFUSED" in out and "SYNTH-SimpleThinSquareFilterSlope.wav" in out
    assert "the only self-documenting file" not in out, "the bare read at :349 ran"


def test_moog_probe_bare_read_is_unguarded_without_scan(tmp_path, monkeypatch):
    """The control for the pin above, so it is not green by accident: with
    `scan` removed the same input reaches :349 and escapes as scipy's own
    ValueError -- not a refusal of any class. `scan()` is the whole guard."""
    setdir = tmp_path / "set"
    _place_bytes(setdir / "SYNTH-SimpleThinSquareFilterSlope.wav", LFS_POINTER)
    monkeypatch.setattr(mp, "scan", lambda *a, **k: [])
    with pytest.raises(ValueError) as why:
        mp.main(["--set", str(setdir)])
    assert not isinstance(why.value, refusal.Refused)
