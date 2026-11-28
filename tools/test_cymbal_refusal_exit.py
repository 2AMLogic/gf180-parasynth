#!/usr/bin/env python3
"""Known-answer exit-status controls: a cymbal_bands refusal must reach a
caller's exit contract as REFUSED, never as an uncaught exit-1 traceback
(#1160).

THE DEFECT, AS MEASURED ON main @ 88c8574e (2026-11-24, red-first run of
every control in this file): `tools/cymbal_bands.Refused` and each caller's
`Refused` are siblings (both subclass `refusal.Refused`, neither subclasses
the other), and `run_case.prepare` raises a THIRD sibling
(`preparation_contract.Refused`). Handlers written as `except Refused`
therefore caught none of them, so a missing precondition escaped as a
traceback and the process exited 1 -- a refusal rendered as FAIL, the exact
inversion of the exit contract (`tools/exit_contract.py`, #804).

THE KNOWN ANSWERS, independent of anything this repository models: a digitally
silent 16-bit WAV must be refused "silent" by `cymbal_bands.measure` and by
`cymbal_mid.measure_mid` (which raises `cymbal_bands.Refused` itself); a WAV
whose first sample is its own peak must be refused "cut into the strike" by
`run_case.prepare` (ONSET_FRAC = 2 %, so index 0 is above threshold for any
amplitude). Both answers were verified by hand before the fix landed.

WHAT EACH CALLER OWES, and what these controls assert:

  cymbal_bands      main maps its own refusal to exit 2 (also proven through
                    a real subprocess, stderr checked, because argparse's
                    usage error ALSO exits 2 -- an exit code alone would be
                    satisfied by a misinvoked CLI)
  cymbal_mid        main maps a corpus refusal to exit 2
  cymbal_candidate_eval  refuses an unreadable corpus BEFORE minutes of
                    calibration (asserted with a bomb in calibrate)
  make_cymbal_pack  refuses a silent corpus BEFORE calibration (pre-fix this
                    drive died in _trim's IndexError after calibrating)
  cymbal_low_tail   a refusing record is RECORDED as data and the run exits 0
                    -- this module's designed per-file degradation, same as
                    its short-record refusals; the defect was the escape
  cymbal_tone_knob  the windowed measurement refuses into NO VERDICT and the
                    gate still answers on its corpus-free properties
  cymbal_tone_render  a cymbal_bands refusal maps to 3, this module's
                    declared REFUSED_ALTERNATE family (in NO_EVIDENCE); the
                    issue's "exit 2" is satisfied by the family, not the
                    literal, and changing the family is out of scope

WRONG-THEN-RIGHT (3, caught by running rather than reading):
  1. Drove tone_knob with the SILENT corpus first: it dies in a
     ZeroDivisionError (log10 of 0/0 band energies), not a refusal at all --
     filed as #1163, out of scope here; the control uses the onset-cut corpus.
  2. Assumed the issue's raise sites (:100/:140) were reachable through
     low_tail's and tone_knob's CLIs on main: they call only `cb._load` /
     `cb._bp`, which refuse nothing until #1144 lands. The sibling escape
     reachable TODAY through those CLIs is `run_case.prepare`'s, and the fix
     (catch the base) covers cb.Refused, preparation_contract.Refused and
     #1144's future `_load` refusal identically.
  3. Wrote `assert main(...) == 2` before noticing argparse errors also exit
     2; every exit assertion here also pins the "REFUSED:" message.

NOT EXERCISED, RECORDED PER THE ISSUE'S THIRD CRITERION:
  cymbal_tone_nodal imports cymbal_bands but dereferences nothing from it
  (`grep -c 'cb\\.'` = 0), so it has no cross-module refusal path to guard.
  probes/cymbal_400_production_tail.py already catches `cb.Refused` by class
  (:224) and `refusal.Refused` (:417); it is the pattern this fix copies.
  cymbal_vca_clip reaches cymbal_bands transitively (`lt.cb._bp`, non-raising)
  but calls rc.prepare under a local-only handler -- the shape outside this
  issue's importer list, filed with the repo-wide census as #1162.
  A corpus file that was merely ABSENT, or not a RIFF file (a git-lfs
  pointer), used to escape `cb._load` as FileNotFoundError / ValueError
  (exit 1) -- recorded not repaired by #1164; repaired in `cb._load` itself
  by #1793, whose controls are the file-level section at the end of this file.
"""
from __future__ import annotations

import json
import pathlib
import struct
import subprocess
import sys

import numpy as np
import pytest
from scipy.io import wavfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))

import exit_contract                                     # noqa: E402
import refusal                                           # noqa: E402

SR = 44100
DUR_S = 3.0


def _write_silent(path: pathlib.Path) -> None:
    wavfile.write(str(path), SR, np.zeros(int(DUR_S * SR), dtype="<i2"))


def _write_onset_cut(path: pathlib.Path) -> None:
    """First sample is the record's own peak: `run_case.prepare` refuses it
    ("cut into the strike") because the onset lands at index 0."""
    n = int(DUR_S * SR)
    sig = np.zeros(n)
    sig[0] = 0.95
    t = np.arange(1, n) / SR
    sig[1:] = 0.90 * np.exp(-t / 0.4) * np.sin(2 * np.pi * 3000.0 * t)
    wavfile.write(str(path), SR, np.round(sig * 32767).astype("<i2"))


def _refs_dir(tmp_path: pathlib.Path, writer, *names: str) -> pathlib.Path:
    d = tmp_path / "refs"
    (d / "cy8").mkdir(parents=True)
    for name in names:
        writer(d / "cy8" / name)
    return d


# ---------------------------------------------------------------------------
# the class relations the whole fix rests on (the issue's own probe, committed
# so the boundary cannot silently regress)
# ---------------------------------------------------------------------------
def test_sibling_refused_classes_share_only_the_base():
    import cymbal_bands as cb
    import cymbal_low_tail as clt
    import run_case as rc

    assert clt.Refused is not cb.Refused
    assert not issubclass(cb.Refused, clt.Refused)
    # the contract the handlers rely on after the fix:
    for klass in (cb.Refused, rc.Refused):
        assert issubclass(klass, refusal.Refused), klass


# ---------------------------------------------------------------------------
# cymbal_bands: its own main, in-process and as a real process
# ---------------------------------------------------------------------------
def test_cymbal_bands_main_maps_silent_corpus_to_refused_2(tmp_path, capsys):
    import cymbal_bands as cb

    refs = _refs_dir(tmp_path, _write_silent, "CY0000.WAV")
    rc_ = cb.main(["--refs", str(refs), "--out", str(tmp_path / "o.json")])
    assert rc_ == exit_contract.REFUSED
    err = capsys.readouterr().err
    assert "REFUSED:" in err and "silent" in err, err


def test_cymbal_bands_process_exit_status_is_refused_2(tmp_path):
    """Through the real interpreter: the exit status a batch runner sees."""
    refs = _refs_dir(tmp_path, _write_silent, "CY0000.WAV")
    out = tmp_path / "o.json"
    p = subprocess.run([sys.executable, str(ROOT / "tools" / "cymbal_bands.py"),
                        "--refs", str(refs), "--out", str(out)],
                       capture_output=True, text=True, timeout=300)
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stderr[-400:])
    assert "REFUSED:" in p.stderr, p.stderr[-400:]
    assert "Traceback" not in p.stderr, p.stderr[-400:]


# ---------------------------------------------------------------------------
# cymbal_mid: raises cb.Refused itself, had no handler at all
# ---------------------------------------------------------------------------
def test_cymbal_mid_fischer_maps_silent_corpus_to_refused_2(tmp_path, capsys):
    import cymbal_mid as cm

    refs = _refs_dir(tmp_path, _write_silent, "CY0000.WAV")
    rc_ = cm.main(["--refs", str(refs), "--fischer", "--out", str(tmp_path / "o.json")])
    assert rc_ == exit_contract.REFUSED
    err = capsys.readouterr().err
    assert "REFUSED:" in err and "silent" in err, err


# ---------------------------------------------------------------------------
# cymbal_low_tail: the refusal is recorded as data, the run must not die
# ---------------------------------------------------------------------------
def test_cymbal_low_tail_full_run_records_prepare_refusal_not_traceback(tmp_path):
    import cymbal_low_tail as clt

    refs = _refs_dir(tmp_path, _write_onset_cut, "CY5025.WAV")
    out = tmp_path / "o.json"
    rc_ = clt.main(["--refs", str(refs), "--out", str(out)])
    assert rc_ == 0, "the designed per-file degradation; controls still pass"
    res = json.loads(out.read_text())
    entry = res["windows"]["1.5s"]["fischer"]["CY5025"]
    assert "refused" in entry and "cut into the strike" in entry["refused"], entry
    assert "refused" in res["noise_floor_control"], res["noise_floor_control"]


# ---------------------------------------------------------------------------
# cymbal_tone_knob: corpus refusal degrades to NO VERDICT, gate still answers
# ---------------------------------------------------------------------------
def test_cymbal_tone_knob_check_records_corpus_refusal_not_traceback(tmp_path, capsys):
    import cymbal_tone_knob as ctk

    refs = _refs_dir(tmp_path, _write_onset_cut, "CY0000.WAV")
    rc_ = ctk.main(["--refs", str(refs), "--check"])
    assert rc_ == 0, "corpus-gated controls are NO VERDICT without --require-corpus"
    out = capsys.readouterr().out
    assert "the windowed measurement REFUSED:" in out and "cut into the strike" in out, out[-600:]


# ---------------------------------------------------------------------------
# cymbal_tone_render: a cb.Refused maps to 3, its declared no-evidence family
# ---------------------------------------------------------------------------
def test_cymbal_tone_render_maps_a_cymbal_bands_refusal_to_no_evidence(tmp_path, capsys, monkeypatch):
    """The raise is planted at `sweep_row`'s boundary because no FILE input
    reaches `cb.measure` in this CLI on main (it measures our own renders);
    the class raised and the handler exercised are the real ones."""
    import cymbal_tone_render as ctr

    monkeypatch.setattr(ctr, "anchor_equivalence", lambda: {})
    monkeypatch.setattr(ctr, "sweep_row",
                        lambda *a, **k: (_ for _ in ()).throw(
                            ctr.cb.Refused("silent (planted at the cb.measure boundary)")))
    rc_ = ctr.main(["--knob-render", "--out", str(tmp_path / "o.json")])
    assert rc_ == exit_contract.REFUSED_ALTERNATE
    assert rc_ in exit_contract.NO_EVIDENCE and rc_ != exit_contract.FAIL
    assert "REFUSED:" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# cymbal_candidate_eval: precondition asserted BEFORE minutes of calibration
# ---------------------------------------------------------------------------
def test_cymbal_candidate_eval_refuses_bad_corpus_before_any_rendering(tmp_path, capsys, monkeypatch):
    import cymbal_candidate_eval as ce

    def _bomb():
        raise AssertionError("calibrate ran: the corpus precondition was not asserted first")

    monkeypatch.setattr(ce, "calibrate", _bomb)
    refs = _refs_dir(tmp_path, _write_onset_cut, "CY5025.WAV")
    rc_ = ce.main(["--refs", str(refs), "--out", str(tmp_path / "o.json")])
    assert rc_ == exit_contract.REFUSED
    err = capsys.readouterr().err
    assert "REFUSED:" in err and "cut into the strike" in err, err


# ---------------------------------------------------------------------------
# make_cymbal_pack: a silent corpus refuses; pre-fix it died in _trim
# ---------------------------------------------------------------------------
def test_make_cymbal_pack_refuses_silent_corpus_before_calibrating(tmp_path, capsys, monkeypatch):
    import make_cymbal_pack as mp

    def _bomb():
        raise AssertionError("calibrate ran: the corpus precondition was not asserted first")

    monkeypatch.setattr(mp.ev, "calibrate", _bomb)
    refs = _refs_dir(tmp_path, _write_silent, "CY5025.WAV")
    rc_ = mp.main(["--refs", str(refs), "--out", str(tmp_path / "pack")])
    assert rc_ == exit_contract.REFUSED
    err = capsys.readouterr().err
    assert "REFUSED:" in err and "silent" in err, err


# ---------------------------------------------------------------------------
# rule 8, the input that would defeat the guard: an implementation error must
# NOT be relabelled REFUSED by any of the new handlers
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("modname, first_called", [("cymbal_bands", "fischer"),
                                                 ("cymbal_mid", "fischer"),
                                                 ("cymbal_candidate_eval", "calibrate")])
def test_a_non_refusal_error_is_not_relabelled_refused(modname, first_called, tmp_path, monkeypatch):
    mod = __import__(modname)
    refs = _refs_dir(tmp_path, _write_silent, "CY0000.WAV", "CY5025.WAV")
    argv = (["--refs", str(refs), "--out", str(tmp_path / "o.json")] +
            (["--fischer"] if modname == "cymbal_mid" else []))
    monkeypatch.setattr(mod, first_called, lambda *a, **k: (_ for _ in ()).throw(
        ValueError("an implementation error, not a precondition")))
    with pytest.raises(ValueError, match="an implementation error"):
        mod.main(argv)


# ===========================================================================
# #1793: the file-level trigger. `cb._load` called scipy before anything of
# its own could refuse, so an ABSENT corpus file (scipy's FileNotFoundError)
# or one scipy cannot read as RIFF -- a git-lfs pointer standing in for the
# recording (ValueError: File format b'vers' not understood) -- escaped every
# `except refusal.Refused` in this family and the CLI died with a traceback:
# exit 1, which tools/run_all.py renders FAIL. #1164 repaired the refusals
# `_load` and `rc.prepare` raise THEMSELVES; these two were raised by scipy
# before either ran.
#
# MEASURED RED FIRST (2026-11-28, origin/main @ 03148399, a host with no
# Fischer corpus -- /tmp/tr808-ref absent, GF180_TR808_REFS unset -- each real
# CLI driven through its interpreter): every drive below exited 1 with the
# exception its docstring names, and no --out was written.
#
# WHAT EACH DRIVE'S EXIT STATUS IS, and why they are not all 2:
#   cymbal_bands, cymbal_mid, make_cymbal_pack, cymbal_candidate_eval
#                     the handler returns REFUSED: exit 2, REFUSED: on stderr
#   cymbal_tone_knob  the module's own degradation: the windowed measurement
#                     is NO VERDICT, the corpus-free gate still answers, exit 0
#   cymbal_low_tail   a refusing record is RECORDED in band and --out is
#                     written (this module's designed degradation, above).
#                     Whether THAT run then exits 0 or 2 is #1765's question,
#                     in flight as PR #1798 and filed out of #1793's scope;
#                     what #1793 owes is that the drive is never exit 1.
# ===========================================================================
LFS_POINTER = ("version https://git-lfs.github.com/spec/v1\n"
               "oid sha256:" + "0" * 64 + "\n"
               "size 123456\n")


def _write_lfs_pointer(path: pathlib.Path) -> None:
    """What an un-pulled git-lfs checkout leaves on disk in place of the
    recording: text. scipy reads its first four bytes, b'vers', and raises
    ValueError("File format b'vers' not understood. ...")."""
    path.write_text(LFS_POINTER)


def _refs_root_without_cy8(tmp_path: pathlib.Path) -> pathlib.Path:
    """The issue's own drive: a --refs directory with no cy8/ at all."""
    d = tmp_path / "refs"
    d.mkdir()
    return d


def _corpus_hint() -> str:
    """What `run_case.load_reference` tells an operator whose corpus is not
    there, read from the owner rather than retyped: #1793's refusal must point
    where the owner's does, and this file may not name the variable or the
    corpus path outside an f-string (tools/test_corpus_path_single_source.py)."""
    import run_case as rc

    with pytest.raises(rc.Refused) as owner:
        rc.load_reference("RS", pathlib.Path("/nonexistent-refs-root"))
    return str(owner.value).split(" -- ", 1)[1]


# ---------------------------------------------------------------------------
# cymbal_bands._load itself: the two refusals, the input that would defeat
# the guard, and the good path pinned unchanged
# ---------------------------------------------------------------------------
def test_load_refuses_an_absent_file_naming_the_path_and_the_corpus(tmp_path):
    import cymbal_bands as cb

    p = tmp_path / "refs" / "cy8" / "CY5025.WAV"
    with pytest.raises(cb.Refused) as why:
        cb._load(p)
    msg = str(why.value)
    assert str(p) in msg and "missing" in msg, msg
    assert msg.endswith(_corpus_hint()), msg


@pytest.mark.parametrize("label", ["lfs-pointer", "empty", "directory", "riff-but-not-wave"])
def test_load_refuses_a_file_scipy_cannot_read_naming_the_path(tmp_path, label):
    """Each escaped `_load` pre-fix as the exception scipy raises (ValueError,
    or IsADirectoryError for the directory). The refusal keeps scipy's reason
    as its cause, so an operator still sees b'vers' and not only a refusal."""
    import cymbal_bands as cb

    p = tmp_path / "refs" / "cy8" / "CY5025.WAV"
    p.parent.mkdir(parents=True)
    if label == "lfs-pointer":
        _write_lfs_pointer(p)
    elif label == "empty":
        p.write_bytes(b"")
    elif label == "directory":
        p.mkdir()
    else:
        p.write_bytes(b"RIFF" + struct.pack("<I", 36) + b"LIST" + b"\x00" * 36)
    with pytest.raises(cb.Refused) as why:
        cb._load(p)
    msg = str(why.value)
    assert str(p) in msg and "not a readable WAV file" in msg, msg
    assert isinstance(why.value.__cause__, (ValueError, OSError)), why.value.__cause__


@pytest.mark.parametrize("error", [TypeError, KeyError, RuntimeError])
def test_load_does_not_relabel_an_implementation_error_as_a_refusal(tmp_path, monkeypatch, error):
    """The input that would defeat the guard: `except (ValueError, OSError)`
    is scoped to the one scipy call, and anything else is an execution error,
    never a refusal (tools/refusal.py)."""
    import cymbal_bands as cb

    p = tmp_path / "CY5025.WAV"
    _write_silent(p)

    def _bug(*a, **k):
        raise error("an implementation error, not a precondition")

    monkeypatch.setattr(wavfile, "read", _bug)
    with pytest.raises(error, match="an implementation error"):
        cb._load(p)


def test_load_good_path_is_unchanged_bit_exact(tmp_path):
    """The known answer is independent of this repository: a 1 kHz sine at
    0.5 full scale written as 16-bit PCM comes back as those samples over
    32768 (the answer tools/test_wav_reader_refusal.py pins), and a stereo
    pair is still averaged channel-wise."""
    import cymbal_bands as cb

    t = np.arange(SR // 4) / SR
    sig = np.round(0.5 * np.sin(2 * np.pi * 1000.0 * t) * 32767).astype("<i2")
    mono = tmp_path / "mono.wav"
    wavfile.write(str(mono), SR, sig)
    x, sr = cb._load(mono)
    assert sr == SR
    assert np.array_equal(x, sig.astype(np.float64) / 32768.0)
    assert abs(float(np.abs(x).max()) - 0.5) < 1e-3
    st = tmp_path / "stereo.wav"
    wavfile.write(str(st), SR, np.column_stack([sig, sig]))
    y, _ = cb._load(st)
    assert y.ndim == 1 and np.array_equal(y, sig.astype(np.float64) / 32768.0)


# ---------------------------------------------------------------------------
# the CLIs, each through its real main
# ---------------------------------------------------------------------------
def test_cymbal_bands_cli_absent_corpus_is_refused_2_not_a_traceback(tmp_path, capsys):
    """Pre-fix: exit 1, FileNotFoundError: .../cy8/CY0000.WAV."""
    import cymbal_bands as cb

    refs = _refs_root_without_cy8(tmp_path)
    out = tmp_path / "o.json"
    rc_ = cb.main(["--refs", str(refs), "--out", str(out)])
    assert rc_ == exit_contract.REFUSED, rc_
    err = capsys.readouterr().err
    assert "REFUSED:" in err and str(refs / "cy8" / "CY0000.WAV") in err, err
    assert _corpus_hint() in err, err
    assert not out.exists()


def test_cymbal_bands_cli_lfs_pointer_is_refused_2_not_a_traceback(tmp_path, capsys):
    """Pre-fix: exit 1, ValueError: File format b'vers' not understood."""
    import cymbal_bands as cb

    refs = _refs_dir(tmp_path, _write_lfs_pointer, "CY0000.WAV")
    out = tmp_path / "o.json"
    rc_ = cb.main(["--refs", str(refs), "--out", str(out)])
    assert rc_ == exit_contract.REFUSED, rc_
    err = capsys.readouterr().err
    assert "REFUSED:" in err and "CY0000.WAV" in err and "b'vers'" in err, err
    assert not out.exists()


def test_cymbal_mid_cli_absent_corpus_is_refused_2_not_a_traceback(tmp_path, capsys):
    """Pre-fix: exit 1, FileNotFoundError -- fischer() has no per-record
    handler and main's catches the base, which scipy's error is not."""
    import cymbal_mid as cm

    refs = _refs_root_without_cy8(tmp_path)
    out = tmp_path / "o.json"
    rc_ = cm.main(["--refs", str(refs), "--fischer", "--out", str(out)])
    assert rc_ == exit_contract.REFUSED, rc_
    err = capsys.readouterr().err
    assert "REFUSED:" in err and "CY0000.WAV" in err, err
    assert not out.exists()


def test_cymbal_tone_knob_cli_absent_file_is_no_verdict_not_a_traceback(tmp_path, capsys):
    """cy8/ present and empty, so the directory guard measure_windows already
    has does not fire and the file is what refuses. Pre-fix: exit 1,
    FileNotFoundError out of measured_or_refused's `except refusal.Refused`."""
    import cymbal_tone_knob as ctk

    refs = tmp_path / "refs"
    (refs / "cy8").mkdir(parents=True)
    rc_ = ctk.main(["--refs", str(refs), "--check"])
    assert rc_ == 0, "corpus-gated controls are NO VERDICT without --require-corpus"
    printed = capsys.readouterr().out
    assert "the windowed measurement REFUSED:" in printed, printed[-600:]
    assert "CY0000.WAV" in printed and _corpus_hint() in printed, printed[-600:]


def _calibrate_bomb():
    raise AssertionError("calibrate ran: the corpus precondition was not asserted first")


def test_make_cymbal_pack_cli_absent_corpus_is_refused_2_before_calibrating(
        tmp_path, capsys, monkeypatch):
    """Pre-fix: exit 1, FileNotFoundError -- the handler at the load catches
    refusal.Refused only. calibrate is bombed, as the silent-corpus control
    above bombs it, so the precondition is proved asserted BEFORE it."""
    import make_cymbal_pack as mp

    monkeypatch.setattr(mp.ev, "calibrate", _calibrate_bomb)
    refs = _refs_root_without_cy8(tmp_path)
    pack = tmp_path / "pack"
    rc_ = mp.main(["--refs", str(refs), "--out", str(pack)])
    assert rc_ == exit_contract.REFUSED, rc_
    err = capsys.readouterr().err
    assert "REFUSED:" in err and "CY5025.WAV" in err, err
    assert not pack.exists()


def test_cymbal_candidate_eval_cli_absent_corpus_is_refused_2_before_calibrating(
        tmp_path, capsys, monkeypatch):
    """The call site #1793's curation read by line number and did not trace:
    _main_body loads CY5025 first and main wraps the body in
    `except refusal.Refused`. Pre-fix: exit 1, FileNotFoundError."""
    import cymbal_candidate_eval as ce

    monkeypatch.setattr(ce, "calibrate", _calibrate_bomb)
    refs = _refs_root_without_cy8(tmp_path)
    out = tmp_path / "o.json"
    rc_ = ce.main(["--refs", str(refs), "--out", str(out)])
    assert rc_ == exit_contract.REFUSED, rc_
    err = capsys.readouterr().err
    assert "REFUSED:" in err and "CY5025.WAV" in err, err
    assert not out.exists()


@pytest.fixture(scope="module")
def shipped_cy_render():
    """The shipped kit's CY render, made once. `render_drum_solo` is 10.3 s a
    call and a cymbal_low_tail run makes four, none on the path a corpus
    refusal can come from; it is deterministic (two calls compared
    bit-exact), so a copy per call is what the real call returns."""
    import cymbal_low_tail as clt
    return clt.rc.render_drum_solo("CY")


@pytest.fixture
def low_tail_main(monkeypatch, shipped_cy_render):
    """The real cymbal_low_tail.main with that ONE substitution. `fischer`,
    `noise_floor_control`, `cb._load`, `check`, every synthetic control, the
    printer, `--out` and the return value are the module's own."""
    import cymbal_low_tail as clt
    x, sr = shipped_cy_render
    monkeypatch.setattr(clt.rc, "render_drum_solo", lambda sound, *a, **k: (x.copy(), sr))
    return clt.main


@pytest.mark.parametrize("kind", ["absent", "lfs-pointer"])
def test_cymbal_low_tail_cli_carries_a_corpus_it_cannot_load_into_its_report(
        low_tail_main, tmp_path, capsys, kind):
    """Pre-fix: exit 1 with FileNotFoundError (absent) or `ValueError: File
    format b'vers' not understood` (lfs pointer), --out not written.

    The run's exit status is asserted only as "never FAIL": on this tree
    `main` records the refusing row and returns 0, the #1765 exit-0 shape
    that issue is filed for (its fix, PR #1798, returns REFUSED for this
    drive and still writes --out). What #1793 owes is that the refusal is
    CARRIED -- into the printed table and into --out, naming the file --
    instead of escaping as a traceback."""
    refs = (_refs_root_without_cy8(tmp_path) if kind == "absent"
            else _refs_dir(tmp_path, _write_lfs_pointer, "CY5025.WAV"))
    out = tmp_path / "o.json"
    rc_ = low_tail_main(["--refs", str(refs), "--out", str(out)])
    printed = capsys.readouterr()
    assert rc_ in (exit_contract.PASS, exit_contract.REFUSED), (rc_, printed.err[-600:])
    assert "Traceback" not in printed.err, printed.err[-600:]
    res = json.loads(out.read_text())
    for w in ("1.5s", "2.0s", "3.5s"):
        refused = res["windows"][w]["fischer"]["CY5025"]
        assert str(refs / "cy8" / "CY5025.WAV") in refused["refused"], refused
        assert res["windows"][w]["shipped"]["rho"]["M"]["-10"] is not None, w
    row = next(ln for ln in printed.out.splitlines() if ln.startswith("808 CY5025"))
    assert "REFUSED:" in row and str(refs / "cy8" / "CY5025.WAV") in row, row
    assert "refused" in res["noise_floor_control"], res["noise_floor_control"]
    why = res["windows"]["1.5s"]["fischer"]["CY5025"]["refused"]
    assert why.endswith(_corpus_hint()) if kind == "absent" else ("b'vers'" in why), why
