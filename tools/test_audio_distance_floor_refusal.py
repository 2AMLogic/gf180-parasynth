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

A FULL RUN THEREFORE EXITED 0, NOT 2, as #1646 merged it. The refusal is
printed in its E7 and E9 rows and the process succeeded; that is what the
in-band convention below already did for a file `load_ref` cannot read.
#1610's criterion says both "exits 2" and "follow the `refused:` entry
convention"; for a full run those cannot both hold, and #1646 left the
choice open. #1699 RULED: a run in which any experiment refused exits 2,
with every row that measured still reported and written. The controls for
that ruling are the last section of this file, "A PARTIAL RUN IS REFUSED".

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

import ast
import json
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


# ---------------------------------------------------------------------------
# #1699: a run in which SOME experiments refused is REFUSED, not a pass
# ---------------------------------------------------------------------------
# RED FIRST (docs/verification-rules.md rule 1), against the probe as #1646
# merged it plus a behaviour-free `_refusals` stub returning [] -- the right
# ports and no behaviour, so each control fails on its own assertion and not
# on a NameError (2026-11-28, this file alone, 202.10 s): 8 failed, 19 passed.
# The two CLI refusal controls failed on `assert 0 == 2`: the process exited 0
# with bd8/BD5050.WAV cut (70.8 s) and with mt8/MT50.WAV cut (61.0 s). The
# missing-file control failed on `assert 0 == 3`, the nested, both-shapes and
# writer-count controls on `assert [] == [...]`, and the two mutant controls
# on "mutation site gone" -- a stub has nothing to mutate. The 19
# green are green before AND after by design: the 17 above, the clean-corpus
# exit-0 control (59.6 s) and the unprefixed-string control (a stub that sees
# nothing ignores it too). With the ruling implemented: 27 passed in 188.16 s.
#
# THE THREE CLI CONTROLS RUN THE REAL PROCESS, at 54-71 s apiece (this file
# was 10-16 s before). 81 % of a run is E4b, E5 and E6, none of which is
# under test, and the alternative -- driving `main` in-process with those
# three replaced by stand-ins -- would test an exit status of a probe that
# does not ship. What is under test IS the process exit status, and only the
# process has one. Exit code before -> after, with --json:
#
#     bd8/BD5050.WAV cut            0 -> 2   3 rows: E7's two BD5050 pairs, E9
#     mt8/MT50.WAV cut              0 -> 2   1 row: E7's lt-vs-mt pair; E9 measured
#     unmodified corpus             0 -> 0   no `refused` anywhere in the JSON
#
# THE MISSING-FILE PATH (#1699 moves it 0 -> 2 as well) is a committed
# control one level in: the REAL e7 and e9 on a corpus without bd8/BD5050.WAV
# refuse three times and the detector `main` reads finds all three. A full
# CLI run of that corpus exits 2 with --json written too (60.3 s, by hand,
# from this file's `_corpus` and `_probe`) -- corroboration, not evidence;
# the evidence is the composition of that control with the three above.
#
# WHAT THE DETECTOR CATCHES (verification-rules 8), at any depth: a string
# beginning `refused: ` and a `refused` key. E5's refusal sits two keys down
# under `per_property_pitch_drop_hz`, where a scan of the first two levels --
# the hand-kept shape the issue forbids -- never looks. Each injected mutant
# below was run on a real result before anything was claimed about it: both
# read it exactly as the real detector does (3 refusals where it reads 3), so
# each is told apart by exactly ONE control, the nested one and the
# unprefixed one. WRONG BEFORE IT WAS RIGHT, caught that way: the first
# two-level mutant counted depth by the slashes in its path, and E7's pair
# names (`bd8/BD5050.WAV vs sd8/SD5050.WAV`) contain two -- so it lost E7's
# real rows as well, and the mt8/MT50.WAV control would have caught it too.
# What the detector DELIBERATELY does not catch: a string saying `refused`
# without the prefix (`"REFUSED: ..."`, `"refused"`, prose), which a
# word-match would flag. The prefix is this file's own convention -- three
# writers emit the string row (e5, e7 twice) and one the `{"refused": ...}`
# record (e9) -- and the writer-count control pins that at (3, 1), so a fifth
# writer, or one of the four moved to a third shape, cannot slip past unread.
#
# ALSO NOT REFUSALS, and not counted: E8's `"verdict": "no verdict"`
# per-property rows -- the board's own T20 estimator declining the 1.2 s
# render (`6-bit requantisation` / `decay`; E8 renders our own BD and reads
# no corpus file, so the row is in every run, and the clean-corpus control
# exits 0 with it there) -- and E5's `None` for an estimator that is not ok
# without raising (it does not occur: all five read as floats). Those are
# the estimators' answers, reported as findings about the board's metrics,
# not an experiment that could not measure. Whether they should ALSO make
# the run no-verdict is a ruling nobody has made, and it is not #1699's.


def _full_run(sf, tmp_path: pathlib.Path, **kinds: str):
    """The real CLI, `--json` and nothing else, over one synthetic corpus.
    Returns the process and the parsed JSON (None when none was written)."""
    out = tmp_path / "full-run.json"
    p = _probe(_corpus(sf, tmp_path, **kinds), "--json", str(out))
    return p, (json.loads(out.read_text()) if out.exists() else None)


def _assert_partial_refusal(p, data, count: int, *refused_rows: str) -> None:
    """Exit REFUSED with no traceback, AND the partial result kept: the report
    printed, `--json` written, and the summary naming each refused row."""
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stdout[-600:], p.stderr[-600:])
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-600:]
    assert "E9  how much of a log-domain distance" in p.stdout, "the report was not printed"
    assert data is not None, "--json was not written"
    assert data["E0_ground_truth"]["ok"] is True and data["E1_determinism"]["bit_identical"] is True
    tail = p.stdout[-2500:]
    assert f"REFUSED: no measurement in {count} of the rows above" in tail, tail
    for row in refused_rows:
        assert row in tail, (row, tail)


def test_full_run_with_bd5050_cut_into_the_strike_exits_refused_2(tmp_path, sf):
    """#1646's own observation under #1699's ruling: 1 -> 0 -> 2. E7's two
    BD5050 pairs and the whole of E9 refuse; every other experiment measures
    and is still reported."""
    p, data = _full_run(sf, tmp_path, **{"bd8/BD5050.WAV": "cut"})
    _assert_partial_refusal(p, data, 3, "E7_ceiling/bd8/BD5050.WAV vs sd8/SD5050.WAV",
                            "E7_ceiling/bd8/BD5050.WAV vs ch8/CH.WAV", "E9_noise_floor/refused")
    e7 = data["E7_ceiling"]
    for pair, v in e7.items():
        if "bd8/BD5050.WAV" in pair:
            assert isinstance(v, str) and v.startswith("refused: bd8/BD5050.WAV begins"), (pair, v)
        else:
            assert set(DISTANCE_KEYS) <= set(v), (pair, v)
    assert list(data["E9_noise_floor"]) == ["refused"], data["E9_noise_floor"]
    assert len(data["E4_sweeps"]) and len(data["E8_blind_spot"]["injections"]) == 3


def test_full_run_with_mt50_cut_into_the_strike_exits_refused_2(tmp_path, sf):
    """The e7 pair path alone: ONE of three pairs refuses, E9 measures in full,
    and the run is still no verdict."""
    p, data = _full_run(sf, tmp_path, **{"mt8/MT50.WAV": "cut"})
    _assert_partial_refusal(p, data, 1, "E7_ceiling/lt8/LT50.WAV vs mt8/MT50.WAV")
    e7 = data["E7_ceiling"]
    assert str(e7["lt8/LT50.WAV vs mt8/MT50.WAV"]).startswith("refused: mt8/MT50.WAV begins"), e7
    assert all(set(DISTANCE_KEYS) <= set(e7[k]) for k in e7 if "mt8/MT50.WAV" not in k), e7
    assert "refused" not in data["E9_noise_floor"] and len(data["E9_noise_floor"]) == 5


def test_full_run_on_an_unmodified_corpus_exits_0(tmp_path, sf):
    """The green control: nothing refused, exit PASS, and no `refused` anywhere
    in what was written."""
    p, data = _full_run(sf, tmp_path)
    assert p.returncode == exit_contract.PASS, (p.returncode, p.stdout[-600:], p.stderr[-600:])
    assert data is not None and "refused" not in json.dumps(data)
    assert "REFUSED:" not in p.stdout, p.stdout[-600:]


def test_a_missing_corpus_file_is_a_refusal_too(probe, monkeypatch, tmp_path, sf):
    """The pre-existing path #1699 moves from 0 to 2: a kit file that is not
    on the host at all. The REAL e7 and e9 on a corpus without bd8/BD5050.WAV,
    read by the detector `main` reads."""
    root = _corpus(sf, tmp_path)
    (root / "bd8" / "BD5050.WAV").unlink()
    monkeypatch.setattr(probe, "REFDIR", root)
    found = probe._refusals({"E7_ceiling": probe.e7_ceiling(), "E9_noise_floor": probe.e9_noise_floor()})
    assert len(found) == 3, found
    assert all("bd8/BD5050.WAV is not on this host" in r for r in found), found


# The detector's own controls, in-process (milliseconds). Two shapes are all
# the probe writes; each is found at any depth.
NESTED = {"E0_ground_truth": {"ok": True},
          "E5_tom_pitch_drop": {
              "vs_shipped": {"1.24 more-accent": {"full": {"mss_l1": 0.1}}},
              "per_property_pitch_drop_hz": {"1.06 unaccented": "refused: injected"}},
          "E9_noise_floor": {"gated below -60 dBFS": {"mss_l1": 0.2}}}

# Guard-defeat input 2 (verification-rules 8): `refused` WITHOUT the prefix.
UNPREFIXED = {"upper": "REFUSED: upper case", "bare": "refused",
              "prose": "the pair was refused by prepare",
              "labelled": {"verdict": "refused"}}


def test_detector_walks_to_a_refusal_nested_below_the_top_level(probe):
    """Guard-defeat input 1 (verification-rules 8): the refusal sits two keys
    down, where e5 writes one, and no top-level key holds one. A scan of the
    first two levels -- E7's and E9's -- reads this as a clean run."""
    assert probe._refusals(NESTED) == [
        "E5_tom_pitch_drop/per_property_pitch_drop_hz/1.06 unaccented: injected"]


def test_detector_reads_both_shapes_at_any_depth(probe):
    assert probe._refusals({"refused": "top"}) == ["refused: top"]
    assert probe._refusals({"E9_noise_floor": {"refused": "bd8/BD5050.WAV begins"}}) == [
        "E9_noise_floor/refused: bd8/BD5050.WAV begins"]
    assert probe._refusals({"a": {"b": [{"refused": "x"}]}}) == ["a/b[0]/refused: x"]
    assert probe._refusals({"E7_ceiling": {"a vs b": "refused: top"}}) == [
        "E7_ceiling/a vs b: top"]
    assert probe._refusals({}) == [] and probe._refusals({"mss_l1": 0.0}) == []


def test_detector_deliberately_ignores_a_string_that_is_not_prefixed(probe):
    """Guard-defeat input 2, and the one the detector does NOT catch, on
    purpose: a string saying `refused` without the `refused: ` prefix. The
    prefix is this file's own convention; a word-match would flag prose. That
    leaves a writer inventing a third shape unread -- the writer-count control
    below is what closes that, and it is the reason this is safe to leave."""
    assert probe._refusals(UNPREFIXED) == []


def _refusal_writers() -> tuple[int, int]:
    """(string rows, whole-result records) the probe's CODE writes. By AST, so
    a docstring quoting `{"refused": ...}` is not a writer."""
    tree = ast.parse(PROBE.read_text())
    rows = [n for n in ast.walk(tree) if isinstance(n, ast.JoinedStr)
            and isinstance(n.values[0], ast.Constant)
            and str(n.values[0].value).startswith("refused: ")]
    records = [n for n in ast.walk(tree) if isinstance(n, ast.Dict)
               and any(isinstance(k, ast.Constant) and k.value == "refused" for k in n.keys)]
    return len(rows), len(records)


def test_every_refusal_writer_in_the_probe_uses_a_shape_the_detector_reads(probe):
    """Three writers emit a `refused: ` string row (e5; e7 twice) and one a
    `{"refused": ...}` record (e9). A fifth writer, or one of these four moved
    to a third shape, moves this count: it must then be read by `_refusals`
    before the count is updated.

    THIS CONTROL'S OWN DEFEATING INPUT (verification-rules 8), stated rather
    than left to be found: it counts those two LITERAL shapes, so a refusal
    assembled any other way -- `f"{word}: {exc}"`, `"refused" + f": {exc}"`,
    `dict(refused=str(e))`, or a bare reason with no label at all -- is not a
    writer to it, the count stays (3, 1), and the exit-2 check misses the row.
    None exists in this file. That is the price of a content-based detector
    over a typed record, and it is why the count is a tripwire, not a proof."""
    assert _refusal_writers() == (3, 1)
    assert probe._refusals({"row": "refused: x"}) == ["row: x"]
    assert probe._refusals({"rec": {"refused": "x"}}) == ["rec/refused: x"]


def _mutant(old: str, new: str):
    """The probe's source with ONE textual mutation, as a module. The mutation
    site must still exist exactly once (verification-rules 5, condition 2: the
    mutant must build)."""
    source = PROBE.read_text()
    assert source.count(old) == 1, f"mutation site gone or ambiguous: {old!r}"
    mod = types.ModuleType("audio_distance_floor_mutant")
    mod.__file__ = str(PROBE)
    exec(compile(source.replace(old, new), str(PROBE), "exec"), mod.__dict__)
    return mod


def test_control_a_two_level_scan_misses_the_nested_refusal(probe):
    """Injected bug: the detector stopped after two levels -- the hand-kept
    shape, which reads E7's rows and E9's record and nothing deeper. On a
    real result it reads exactly what the real detector reads, so only the
    nested control can tell them apart."""
    m = _mutant("out.extend(_refusals(v, here))",
                "out.extend(_refusals(v, here) if not isinstance(v, dict) or path == '' else [])")
    top = {"E7_ceiling": {"bd8/BD5050.WAV vs sd8/SD5050.WAV": "refused: top"},
           "E9_noise_floor": {"refused": "record"}}
    assert m._refusals(top) == probe._refusals(top) == [
        "E7_ceiling/bd8/BD5050.WAV vs sd8/SD5050.WAV: top", "E9_noise_floor/refused: record"]
    assert probe._refusals(NESTED) == ["E5_tom_pitch_drop/per_property_pitch_drop_hz/"
                                       "1.06 unaccented: injected"] and m._refusals(NESTED) == []


def test_control_a_word_match_flags_the_unprefixed_string(probe):
    """Injected bug: the detector matching the WORD rather than this file's
    prefix. Only the unprefixed control can catch it; every other control
    stays green on this mutant."""
    m = _mutant("node.startswith(REFUSED_PREFIX)", '"refused" in node.lower()')
    assert probe._refusals(UNPREFIXED) == [] and m._refusals(UNPREFIXED)
    assert m._refusals(NESTED) == probe._refusals(NESTED)


# ---------------------------------------------------------------------------
# #2100 (a slice of #2040's class): the destination of `--json`
# ---------------------------------------------------------------------------
# The record is written after all nine experiments. With `--json` under a
# directory that does not exist, the real CLI exited 1 on `FileNotFoundError`
# at `a.json.write_text` -- after every row had measured and with the report
# never printed, since the write precedes it. FAIL, "the comparison ran and
# disagreed", for a run that measured, and the record lost.
#
# RED FIRST on main @ 8eb04e5b (2026-11-28), the real CLI before the fix:
# `--json no/such/dir/a.json` on the synthetic corpus exited 1 on
# `FileNotFoundError` after all nine experiments; `--json <a directory>` and
# `--json <a file>/a.json`, with no corpus, exited 2 for the CORPUS's own
# refusal -- E0 had already run, and the destination was never consulted.
# The 34 controls of this slice (seven probes) failed together in 110.77 s;
# with the probes reverted and the committed tests kept, 33 failed again in
# 6.04 s (the full run deselected). Read by failure REASON, not by count: the
# first draft of four cymbal controls was red on a helper-name collision, a
# test bug, which the count alone could not tell from the defect.
# WHAT IS UNDER TEST: the destination, before anything loads, the write.
#
#   --json a directory, or under a file     the real process. The refusal is
#                                            at argument parsing, so no
#                                            experiment, no corpus and no
#                                            soundfile are reached: exit 2,
#                                            REFUSED on stdout, no report.
#   --json under a missing directory         the real process, a whole run
#                                            (60-70 s, the cost of the three
#                                            controls above): the directory
#                                            is made, exit 0, the record
#                                            parses.
#   the write failing after the run          in-process `main`, E1-E9 doubled
#                                            (below): what is asserted is
#                                            `main`'s handling of `OSError`
#                                            at the write, and a second full
#                                            run would repeat 81 % of its
#                                            time on experiments that are not
#                                            under test.
#
# A write that fails is REFUSED, exit 2, with the report still printed: the
# rows measured and are evidence (the #1699 ruling, which writes the report
# and the record first for the same reason), but a batch runner reads the
# record, and the exit status must not say PASS over a record that is not there.

def _assert_destination_refused(p: subprocess.CompletedProcess, target: pathlib.Path) -> None:
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stdout[-400:], p.stderr[-400:])
    assert "REFUSED" in p.stdout and str(target) in p.stdout, p.stdout[-400:]
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-600:]
    assert "E1  two renders" not in p.stdout, "a refused run must not start a report"


@pytest.mark.parametrize("kind", ["a directory", "a parent that is a file"])
def test_an_unwritable_json_destination_is_refused_before_any_experiment(tmp_path, kind):
    if kind == "a directory":
        target = tmp_path / "record.json"
        target.mkdir()
    else:
        (tmp_path / "not-a-dir").write_text("a file")
        target = tmp_path / "not-a-dir" / "record.json"
    p = _probe(tmp_path, "--json", str(target))      # no corpus: none must be read
    _assert_destination_refused(p, target)


def test_full_run_with_json_under_a_missing_directory_writes_it_and_exits_0(tmp_path, sf):
    """The green control for the class: the directory is made, the run keeps
    its own status, and the record is the one a run without the missing
    directory writes."""
    root = _corpus(sf, tmp_path)
    out = tmp_path / "no" / "such" / "dir" / "full-run.json"
    p = _probe(root, "--json", str(out))
    assert p.returncode == exit_contract.PASS, (p.returncode, p.stdout[-600:], p.stderr[-600:])
    assert "Traceback" not in p.stderr and "REFUSED:" not in p.stdout, p.stderr[-600:]
    assert f"wrote {out}" in p.stdout, p.stdout[-600:]
    data = json.loads(out.read_text())
    assert data["E0_ground_truth"]["ok"] is True and "refused" not in json.dumps(data)
    assert len(data["E4_sweeps"]) and data["reference_file"] == "bd8/BD2550.WAV"


def _stub_experiments(probe, monkeypatch) -> None:
    """E1-E9 as stand-ins shaped for `report()`: rows of zeros, nothing
    refused. E0, `load_ref`, `prepare`, the T20, `_refusals`, `report` and
    the write are the probe's own."""
    def zero():
        return dict.fromkeys(DISTANCE_KEYS, 0.0)

    monkeypatch.setattr(probe, "e1_determinism", lambda: zero() | {"bit_identical": True})
    monkeypatch.setattr(probe, "e2_e3_alignment", lambda x, sr: {"1 samples (0.023 ms)": zero()})
    monkeypatch.setattr(probe, "e4_sweeps", lambda x, sr: {"decay_pct": {"0": zero()},
                                                          "f0_cents_floor_equivalent": 0.0})
    monkeypatch.setattr(probe, "e4b_pure_f0", lambda: {"base_hz": 49.4,
                                                       "rungs": {"0 cents": zero() | {"f0_hz": 49.4,
                                                                                      "f0_error_pct": 0.0}}})
    monkeypatch.setattr(probe, "e5_tom", lambda: {"full_clip_s": 1.2, "drop_window_ms": 60.0,
                                                  "floor_same_patch_twice": zero(),
                                                  "floor_1_sample_shift": zero(),
                                                  "floor_1_sample_shift_first_60ms": zero(),
                                                  "vs_shipped": {}, "per_property_pitch_drop_hz": {},
                                                  "alignment_on_this_signal": {}})
    monkeypatch.setattr(probe, "e6_exchange", lambda x, sr: {"target_5pct_decay": zero(),
                                                            "equivalent_gain_db": zero()})
    monkeypatch.setattr(probe, "e7_ceiling", lambda: {})
    monkeypatch.setattr(probe, "e8_blind_spot", lambda: {"floor_1_sample_shift": zero(), "injections": {}})
    monkeypatch.setattr(probe, "e9_noise_floor", lambda: {})


def test_a_write_failure_after_the_run_is_refused_with_the_report_printed(probe, monkeypatch, tmp_path, sf, capsys):
    """The pre-flight passed and the destination became a directory before the
    write -- what a disk filling or a permission changing mid-run looks like
    from the write's side: an `OSError`, after everything has measured."""
    monkeypatch.setattr(probe, "REFDIR", _corpus(sf, tmp_path))
    _stub_experiments(probe, monkeypatch)
    target = tmp_path / "record.json"

    def last_experiment_then_occupy():
        target.mkdir()
        return {}

    monkeypatch.setattr(probe, "e9_noise_floor", last_experiment_then_occupy)
    code = probe.main(["--json", str(target)])
    out = capsys.readouterr().out
    assert code == exit_contract.REFUSED, out[-600:]
    assert "E9  how much of a log-domain distance" in out, "the report was not printed"
    assert f"REFUSED: --json {target} was not written" in out, out[-600:]
    assert "wrote" not in out, out[-600:]


def test_the_stubs_alone_write_a_record_and_exit_0(probe, monkeypatch, tmp_path, sf, capsys):
    """The control for the test above, on the same stubs: without the fault
    the same run writes the record and passes, so its REFUSED is the write's
    and not the stand-ins'."""
    monkeypatch.setattr(probe, "REFDIR", _corpus(sf, tmp_path))
    _stub_experiments(probe, monkeypatch)
    target = tmp_path / "no" / "such" / "dir" / "record.json"
    code = probe.main(["--json", str(target)])
    out = capsys.readouterr().out
    assert code == exit_contract.PASS, out[-600:]
    assert f"wrote {target}" in out and "REFUSED" not in out, out[-600:]
    assert json.loads(target.read_text())["E0_ground_truth"]["ok"] is True
