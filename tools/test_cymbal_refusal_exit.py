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
  A corpus file that is merely ABSENT still escapes `cb._load` as
  FileNotFoundError (exit 1) -- #1117/#1144's layer, recorded not repaired.
"""
from __future__ import annotations

import json
import pathlib
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
    assert res["controls_pass"] is True
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
