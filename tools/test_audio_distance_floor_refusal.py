#!/usr/bin/env python3
"""Known-answer exit-status controls: a cross-module refusal raised by
`run_case.prepare` must reach audio_distance_floor's boundaries as REFUSED,
never as an uncaught exit-1 traceback (#1610).

THE DEFECT, AS MEASURED ON main @ 0a6b0861 (2026-11-28, red-first run of every
control in this file, before the fix): `tools/probes/audio_distance_floor.py`
guards `load_ref` with its own local `Refused` and calls `run_case.prepare`
outside that guard -- twice in `e7_ceiling`, once in `e9_noise_floor` and
twice in `main`. `run_case.Refused` is `preparation_contract.Refused`, a
SIBLING of the local class, so `except Refused` (local) does not catch it. A
reference `prepare` must refuse therefore escaped as a traceback and the
process exited 1: a refusal rendered as FAIL, the inversion
`tools/exit_contract.py` exists to prevent (#804), the same class as #1160,
#1162, #1183 and #1569.

THE KNOWN ANSWERS, independent of anything this repository models. The
fixtures are synthetic and analytic: a 100 Hz cosine under an exponential
envelope of tau 100 ms, 1.0 s long, behind 20 ms of digital silence. Its T20
is closed-form -- 20 dB is a factor of sqrt(10) in amplitude, so
T20 = 2 ln(sqrt 10) tau = 230.26 ms -- and `schroeder_t20` reads 230.24 ms
on it. From that one signal:

    valid       a lead, so `prepare` accepts it and T20 is 230.2 ms
    cut         the lead removed: sample 0 IS the peak, so `prepare` refuses
                "begins at or above 2 % of its own peak ... cut into the
                strike" (`run_case.ONSET_FRAC` is 0.02)
    nan / inf   one sample of a FLOAT WAV replaced; IEEE float stores both
                bit-exactly and soundfile reads them back, so `prepare`
                refuses "holds 1 non-finite samples ... (1 NaN, 0 Inf ...)"
    truncated   0.15 s long: the record ends 3.7 ms after the -25 dB point,
                so `schroeder_t20` refuses it -- the stand-in for the
                board's own `bd8/BD5050.WAV`, whose T20 is refused by design
    text        not a WAV at all: soundfile raises `LibsndfileError`

Each answer was read by hand against the pre-fix tree (the run above) before
any assertion here was written.

RED FIRST (docs/verification-rules.md rule 1), this file alone against the
unfixed probe: 11 failed, 6 passed in 10.7 s. Ten of the 11 assert a handled
refusal and each failed with the escaping `preparation_contract.Refused`,
raised from `run_case.py:1489` (cut into the strike) or `:1472` (non-finite)
-- exit 1 and a traceback through the CLI, the bare exception in-process --
and the listing case printed its `REFUSED:` line and THEN tracebacked. The
eleventh is the injected-bug control, which has nothing to mutate until the
repair exists. The 6 are green before and after by design (class relations,
the two refusals the fix must preserve, the valid corpus, and the two
inverse controls). With the fix applied: 17 passed (10-16 s over three
runs). The same inputs through the real CLI, exit code before -> after:

    --ref cut                               1 -> 2   REFUSED on stdout, names the file
    --ref nan                               1 -> 2
    --ref without T20 + malformed in list   1 -> 2   the malformed file skipped
    --ref unreadable (not a WAV)            1 -> 1   traceback, unchanged
    full run, bd8/BD5050.WAV cut            1 -> 0   was `main:718 > e7_ceiling:611`
    full run, mt8/MT50.WAV cut              1 -> 0   was `e7_ceiling:612`

A FULL RUN THEREFORE EXITS 0, NOT 2. The refusal is printed in its E7 and
E9 rows and the process succeeds; that is what the in-band convention below
already did for a file `load_ref` cannot read, and this fix does not change
it. #1610's criterion says both "exits 2" and "follow the `refused:` entry
convention"; for a full run those cannot both hold, and which is meant is
left to the PR, not decided here.

WHAT THE PROBE OWES, and what these controls assert:

    --ref cut / nan / inf     REFUSED on stdout naming the file and the
                              reason, exit 2, no traceback, and no report
                              started (`main`, before any experiment)
    --ref without a T20       the pre-existing refusal is preserved: exit 2
                              naming it, the list of usable alternatives
                              still printed
    ... + a malformed file    STATED CHOICE for the listing: such a file is
    in that list              SKIPPED, exactly as the list already skips one
                              `load_ref` refuses. It has no T20 to offer, so
                              leaving it out is accurate rather than
                              degraded; the run's own refusal (the --ref's
                              missing T20) is what is printed and the exit
                              stays 2. A "refuse here too" reading would
                              print the malformed file's name -- this file
                              asserts that it does not.
    absent --ref              the local `load_ref` refusal is unchanged by
                              widening the handler to the base class
    e7 / e9                   called in-process. They sit seven experiments
                              deep in a full run, which costs 59-78 s on the
                              synthetic corpus -- 81 % of it in E4b, E5 and
                              E6, none of which is under test -- so the
                              committed control drives the two functions that
                              hold the handlers (0.5 s and 0.8 s) rather than
                              the process. A malformed pair member becomes a
                              `refused:` entry naming the file (the
                              convention `e7_ceiling` already keeps for a
                              file `load_ref` cannot read); a malformed
                              `bd8/BD5050.WAV` makes `e9_noise_floor`
                              return its `{"refused": ...}` record.
    unrelated errors          an execution error raised by `prepare` itself
                              (an injected `ValueError`) propagates out of
                              `e7_ceiling`, `e9_noise_floor` and `main`, and
                              a corpus file soundfile cannot read still
                              exits 1 with a traceback. Widening the handler
                              to the base class must not relabel either as a
                              refusal (tools/refusal.py's "inverse defect").
                              Both are green before AND after the fix by
                              design, so the injected-bug control below
                              re-widens every handler to `except Exception`
                              and shows the relabelling appear.

The probe's own corpus reader needs `soundfile`. Every corpus-driven control
takes it from the `sf` fixture, an in-function `importorskip`, so this file
is collected everywhere and those controls skip visibly where the tool itself
could not run; the class-relation control runs unconditionally.

WRONG-THEN-RIGHT (1, caught by running rather than reading): the issue's
inverse-control example names "a corpus file scipy cannot read -> ValueError".
This probe does not read through scipy: `load_ref` calls `soundfile.read`,
which raises `LibsndfileError` (a RuntimeError subclass) on a non-WAV. The
control pins what the tree actually raises.

THE E5 HANDLER (#1771), a second defect in this file and the one site #1646
did not touch: `e5_tom` wrapped the per-property estimator
(`rc._pitch_drop("LT")`) in `except Exception` and filed whatever it raised as
a `refused:` row -- the inverse defect, an execution error relabelled as a
refusal. On main @ 02394528 that row sits in a run that exits 0; #1699 (PR
#1773, open on 2026-11-28) would make the run exit 2, where 1 and a traceback
are right either way.
The two controls at the end of this file pair as the ones above do: an
injected `ValueError` must propagate out of `e5_tom`, and a refusal the
estimator raises must still be a `refused:` row for every ratio. RED FIRST
against the unfixed handler (2026-11-28): 1 failed, 1 passed in 13.0 s -- the
`ValueError` control with `DID NOT RAISE ValueError`. The positive control is
green before AND after the fix by design, so its teeth were checked by
mutation instead: with the handler narrowed to the probe's LOCAL `Refused`
(#1646's shape) the injected cross-module refusal escapes as
`preparation_contract.Refused` and that control fails. With the fix: 19
passed (31.7 s and 36.9 s over two runs).

WHAT THE POSITIVE CONTROL DOES NOT CLAIM: the real estimator cannot raise a
`Refused` of any module, so no input is known that reaches the repaired
handler in production and the control exercises the handler's class boundary,
not a production path. `run_case.pitch_drop_hz` refuses IN BAND -- an
`am.Estimate` with `ok` False, which `e5_tom` records as `None` -- and the
only exceptions on its path are execution errors: `audio_measure` defines
`InsufficientEvidence(AssertionError)` and `NonFiniteAudio`, imports no
`refusal`, and its one `raise Refused("silent")` sits inside a docstring
(read on origin/main @ 02394528, 2026-11-28).

WHAT IS NOT CLAIMED: the six other `rc.prepare` calls in this file (:441,
:446, :467, :489, :490, :540) are not guarded, here or by this fix.
`tools/refusal_sibling_scan.py` read them `candidate-escape` before it and
reads them `covered(cli-base)` after it -- its one-level heuristic, since a
base handler now exists at this module's CLI boundary, though those six are
called from `main` outside the `try` it lives in. They prepare our own
model's renders, which lead with 10 ms of silence and are integer output
over 32768, so no input is known that makes `prepare` refuse there.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import types

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "tools" / "probes"))

import exit_contract                                     # noqa: E402
import refusal                                           # noqa: E402
import run_case as rc                                    # noqa: E402

SR = 44100
PROBE = ROOT / "tools" / "probes" / "audio_distance_floor.py"
DISTANCE_KEYS = ("mss_l1", "mss_log", "mrstft", "mel_dac")
KIT = ("bd8/BD2550.WAV", "bd8/BD5050.WAV", "sd8/SD5050.WAV", "ch8/CH.WAV",
       "lt8/LT50.WAV", "mt8/MT50.WAV")


@pytest.fixture
def sf():
    return pytest.importorskip("soundfile")


@pytest.fixture
def probe():
    import audio_distance_floor as adf
    return adf


def _strike(tau: float = 0.10, seconds: float = 1.0, lead_s: float = 0.02) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    return np.concatenate([np.zeros(int(round(lead_s * SR))),
                           np.exp(-t / tau) * np.cos(2 * np.pi * 100.0 * t)])


def _one_sample(x: np.ndarray, value: float) -> np.ndarray:
    x = x.copy()
    x[len(x) // 2] = value
    return x


KINDS = {
    "valid": _strike,
    "cut": lambda: _strike(lead_s=0.0),
    "nan": lambda: _one_sample(_strike(), np.nan),
    "inf": lambda: _one_sample(_strike(), np.inf),
    "truncated": lambda: _strike(seconds=0.15),
}


def _corpus(sf, root: pathlib.Path, **kinds: str) -> pathlib.Path:
    """A synthetic Fischer-shaped tree under `root`: every file E7 and E9
    name plus the default `--ref`, each `valid` unless `kinds` maps its
    relative name to another KIND."""
    for rel in (*KIT, *kinds):
        x = KINDS[kinds.get(rel, "valid")]()
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        subtype = "PCM_16" if np.isfinite(x).all() else "FLOAT"
        sf.write(str(root / rel), x, SR, subtype=subtype)
    return root


def _corpus_env(refs: pathlib.Path) -> dict:
    drop = (rc.REFS_ENV, rc.REFS_ALIAS_ENV)
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env[rc.REFS_ENV] = str(refs)
    env[rc.REFS_ALIAS_ENV] = str(refs)
    return env


def _probe(refs: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(PROBE), *args],
        capture_output=True, text=True, timeout=300, env=_corpus_env(refs), cwd=str(ROOT))


def _assert_refused(p: subprocess.CompletedProcess, *needles: str) -> None:
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stdout[-400:], p.stderr[-400:])
    assert "REFUSED" in p.stdout, p.stdout[-400:]
    for n in needles:
        assert n in p.stdout, (n, p.stdout[-400:])
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-600:]
    assert "E1  two renders" not in p.stdout, "a refused run must not start a report"


def _raise_value_error(*_args, **_kwargs):
    raise ValueError("injected: an execution error inside prepare, not a refusal")


def test_sibling_refused_classes_share_only_the_base():
    import audio_distance_floor as adf
    import preparation_contract as pc

    assert adf.Refused is not rc.Refused
    assert not issubclass(rc.Refused, adf.Refused)
    for klass in (adf.Refused, rc.Refused, pc.Refused):
        assert issubclass(klass, refusal.Refused), klass


def test_ref_cut_into_the_strike_refuses_2_naming_the_reason(tmp_path, sf):
    p = _probe(_corpus(sf, tmp_path, **{"bd8/CUT.WAV": "cut"}), "--ref", "bd8/CUT.WAV")
    _assert_refused(p, "bd8/CUT.WAV", "cut into the strike")


@pytest.mark.parametrize("kind, counted", [("nan", "1 NaN, 0 Inf"), ("inf", "0 NaN, 1 Inf")])
def test_ref_holding_a_non_finite_sample_refuses_2(tmp_path, sf, kind, counted):
    rel = f"bd8/{kind.upper()}.WAV"
    p = _probe(_corpus(sf, tmp_path, **{rel: kind}), "--ref", rel)
    _assert_refused(p, rel, "non-finite", counted)


def test_ref_without_a_t20_still_refuses_2_and_lists_alternatives(tmp_path, sf):
    root = _corpus(sf, tmp_path, **{"bd8/BD5050.WAV": "truncated"})
    p = _probe(root, "--ref", "bd8/BD5050.WAV")
    _assert_refused(p, "bd8/BD5050.WAV has no usable T20", "record ends before the decay does")
    assert "bd8/BD2550.WAV" in p.stdout and "230.2 ms" in p.stdout, p.stdout[-400:]


def test_listing_skips_a_file_prepare_refuses(tmp_path, sf):
    root = _corpus(sf, tmp_path, **{"bd8/BD5050.WAV": "truncated",
                                    "bd8/CUT.WAV": "cut", "bd8/NAN.WAV": "nan"})
    p = _probe(root, "--ref", "bd8/BD5050.WAV")
    _assert_refused(p, "bd8/BD5050.WAV has no usable T20", "record ends before the decay does")
    assert "bd8/BD2550.WAV" in p.stdout, p.stdout[-400:]
    assert "CUT" not in p.stdout and "NAN" not in p.stdout, p.stdout[-600:]


def test_absent_reference_keeps_the_local_refusal(tmp_path, sf):
    p = _probe(_corpus(sf, tmp_path), "--ref", "bd8/NO-SUCH-FILE.WAV")
    _assert_refused(p, "bd8/NO-SUCH-FILE.WAV", "is not on this host")


def test_valid_corpus_still_measures_e7_and_e9(probe, monkeypatch, tmp_path, sf):
    monkeypatch.setattr(probe, "REFDIR", _corpus(sf, tmp_path))
    e7 = probe.e7_ceiling()
    assert len(e7) == 3 and all(set(DISTANCE_KEYS) <= set(v) for v in e7.values()), e7
    e9 = probe.e9_noise_floor()
    assert "refused" not in e9 and len(e9) == 5, e9


@pytest.mark.parametrize("bad", ["bd8/BD5050.WAV", "sd8/SD5050.WAV", "ch8/CH.WAV", "mt8/MT50.WAV"])
def test_e7_ceiling_records_a_refused_pair_naming_the_file(probe, monkeypatch, tmp_path, sf, bad):
    monkeypatch.setattr(probe, "REFDIR", _corpus(sf, tmp_path, **{bad: "cut"}))
    out = probe.e7_ceiling()
    assert len(out) == 3, out
    for pair, v in out.items():
        if bad in pair:
            assert isinstance(v, str) and v.startswith("refused: "), (pair, v)
            assert f"{bad} begins at or above 2 % of its own peak" in v, v
        else:
            assert set(DISTANCE_KEYS) <= set(v), (pair, v)


@pytest.mark.parametrize("kind, needle", [("cut", "cut into the strike"), ("nan", "non-finite")])
def test_e9_noise_floor_returns_its_refused_record(probe, monkeypatch, tmp_path, sf, kind, needle):
    monkeypatch.setattr(probe, "REFDIR", _corpus(sf, tmp_path, **{"bd8/BD5050.WAV": kind}))
    out = probe.e9_noise_floor()
    assert list(out) == ["refused"], out
    assert "bd8/BD5050.WAV" in out["refused"] and needle in out["refused"], out


def test_unreadable_reference_is_an_execution_error_not_a_refusal(tmp_path, sf):
    root = _corpus(sf, tmp_path)
    (root / "bd8" / "TEXT.WAV").write_bytes(b"this is not a WAV file at all, just text")
    p = _probe(root, "--ref", "bd8/TEXT.WAV")
    assert p.returncode == exit_contract.FAIL, (p.returncode, p.stdout[-200:], p.stderr[-400:])
    assert "Traceback" in p.stderr and "LibsndfileError" in p.stderr, p.stderr[-400:]
    assert "REFUSED" not in p.stdout, "a reader error must not be relabelled a refusal"


def test_prepare_execution_error_propagates_from_every_repaired_site(probe, monkeypatch, tmp_path, sf):
    monkeypatch.setattr(probe, "REFDIR", _corpus(sf, tmp_path))
    monkeypatch.setattr(rc, "prepare", _raise_value_error)
    for call in (probe.e7_ceiling, probe.e9_noise_floor, lambda: probe.main([])):
        with pytest.raises(ValueError, match="injected"):
            call()


def test_control_catches_a_handler_widened_to_except_exception(probe, monkeypatch, tmp_path, sf):
    """Injected bug: every handler the fix widened to `refusal.Refused` is
    widened further to `Exception`. The inverse control above is green before
    AND after the fix, so this is the only place its red is on record."""
    source = PROBE.read_text()
    assert "except refusal.Refused" in source, "the repair is gone; nothing left to mutate"
    mutant = types.ModuleType("audio_distance_floor_mutant")
    mutant.__file__ = str(PROBE)
    exec(compile(source.replace("except refusal.Refused", "except Exception"),
                 str(PROBE), "exec"), mutant.__dict__)
    monkeypatch.setattr(rc, "prepare", _raise_value_error)

    monkeypatch.setattr(probe, "REFDIR", _corpus(sf, tmp_path))
    with pytest.raises(ValueError, match="injected"):
        probe.e7_ceiling()

    monkeypatch.setattr(mutant, "REFDIR", _corpus(sf, tmp_path))
    relabelled = mutant.e7_ceiling()
    assert len(relabelled) == 3, relabelled
    assert all(str(v).startswith("refused: injected") for v in relabelled.values()), relabelled


#: `e5_tom` renders LT five times through the real integer model: 3.8 s a
#: render at its default 1.2 s, 35 s for the call. The handler under test does
#: not depend on how long the render is, and the real estimator reads the same
#: drop at 0.2 s as at 1.2 s (36.6 / 10.7 / 6.1 / 2.6 / -1.0 Hz, 2026-11-28),
#: so the E5 controls render 0.2 s: about 7 s each, not 35.
E5_SECONDS = 0.2
E5_RATIOS = {"1.70 shipped", "1.24 more-accent", "1.14 accent", "1.06 unaccented",
             "1.00 no drop"}


def _raise_refused(sound: str):
    """An estimator that raises `run_case.Refused` when called on a render -- a
    CROSS-MODULE sibling of the probe's own `Refused`, so a handler narrowed
    to the local class lets it escape."""
    def f(_y, _sr):
        raise rc.Refused("injected: a cross-module refusal from the estimator")
    return f


def test_e5_tom_estimator_execution_error_propagates(probe, monkeypatch):
    """#1771's injected-bug control. `e5_tom`'s handler around the per-property
    estimator was `except Exception`, which filed a `ValueError` from inside
    the estimator as a `refused:` row: an execution error relabelled as a
    refusal (tools/refusal.py's "inverse defect"). A run carrying that row
    exits 0 today and would exit 2 once #1699 lands; 1 and a traceback are
    right either way."""
    monkeypatch.setattr(rc, "_pitch_drop", _raise_value_error)
    with pytest.raises(ValueError, match="injected"):
        probe.e5_tom(seconds_full=E5_SECONDS)


def test_e5_tom_estimator_refusal_is_still_a_row(probe, monkeypatch):
    """Its paired positive control: a refusal the estimator raises still lands
    as a `refused:` row for every ratio, and the rest of E5 still runs. The
    refusal is a cross-module sibling on purpose: a handler narrowed to the
    probe's LOCAL `Refused` (#1646's shape) lets it escape and fails this."""
    monkeypatch.setattr(rc, "_pitch_drop", _raise_refused)
    out = probe.e5_tom(seconds_full=E5_SECONDS)
    rows = out["per_property_pitch_drop_hz"]
    assert set(rows) == E5_RATIOS, rows
    assert all(isinstance(v, str) and v.startswith("refused: injected")
               for v in rows.values()), rows
    assert set(DISTANCE_KEYS) <= set(out["floor_same_patch_twice"]), out
    assert out["alignment_on_this_signal"], out
