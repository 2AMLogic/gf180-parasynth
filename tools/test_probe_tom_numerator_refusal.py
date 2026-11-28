#!/usr/bin/env python3
"""Known-answer exit-status controls: a cross-module refusal from
`run_case.load_reference` must reach the tom-numerator probe's exit
contract as REFUSED, never as an uncaught exit-1 traceback (#1183, the
`run_case.*` raiser family).

THE DEFECT, AS MEASURED ON main @ f912072f (2026-11-24, red-first run of
every control in this file): `tools/probe_tom_numerator.py` guards only
`render()` with `except rc.Refused` -- the register-overflow refusal of the
compensated amp -- but `score()` calls `rc.load_reference(voice, refdir)`
OUTSIDE that guard, and nothing at `main()` interprets a refusal at all. A
digitally silent `lt8/LT50.WAV` therefore escaped as a
`preparation_contract.Refused` traceback and the process exited 1: a
refusal about the apparatus rendered as FAIL, the exact inversion of the
exit contract (tools/exit_contract.py, #804). The repair widens the
boundary to `refusal.Refused` around the measurement loop, catching the
sibling `preparation_contract.Refused` and the exact-match render guard in
one place -- never `except Exception`.

THE KNOWN ANSWERS, independent of anything this repository models: a
digitally silent mono PCM16 44.1 kHz WAV (4,410 frames, the #1162 fixture
spec) must be refused "reference recording lt8/LT50.WAV is silent" by
`run_case.load_reference` before any report is written; an absent corpus
root must refuse naming the root; a corpus file scipy cannot read must
STILL exit 1 as an execution error. All three were verified by hand against
the pre-fix tree before the fix landed (each exited 1 with the traceback --
the score path had no boundary at all).

WHAT THE PROBE OWES, and what these controls assert:

  silent LT      exit `exit_contract.REFUSED` (2), "REFUSED" and the file
                 name on stdout, no traceback, and NO --out report written
                 (a refused sweep must not look like a completed one)
  absent corpus  exit 2 with the corpus-naming reason
  unreadable     a corpus file scipy cannot read raises ValueError at the
                 same boundary and must STILL exit 1

The fixture's file placement is derived from the probe's own VOICES order
and `run_case.REF_MAIN`, so this file never spells a corpus path the tool
could drift away from.

#1766 IS THE OTHER SIDE OF THE SAME BOUNDARY: a refusal that does not escape
but is recorded in band (`rows[label] = {"refused": ...}`) still made the
process exit 0. Those controls are the last section of this file.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import textwrap

import numpy as np
import pytest
from scipy.io import wavfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))

import exit_contract                                     # noqa: E402
import run_case as _rc_env                               # noqa: E402 -- REFS_ENV names only
import run_case as rc                                    # noqa: E402
import probe_tom_numerator as ptn                        # noqa: E402

SR = 44100
FRAMES = 4410               # 100 ms of mono PCM16: the #1162 fixture spec
FIRST_VOICE = next(iter(ptn.VOICES))
FIRST_REL = rc.REF_MAIN[FIRST_VOICE][0]
PROBE = ROOT / "tools" / "probe_tom_numerator.py"
RAW_LABEL = "RAW (shipped)"


def _corpus_with(tmp_path: pathlib.Path, content) -> pathlib.Path:
    d = tmp_path / "refs"
    (d / pathlib.Path(FIRST_REL).parent).mkdir(parents=True)
    p = d / FIRST_REL
    if isinstance(content, np.ndarray):
        wavfile.write(str(p), SR, content)
    else:
        p.write_bytes(content)
    return d


def _silent_corpus(tmp_path):
    return _corpus_with(tmp_path, np.zeros(FRAMES, dtype="<i2"))


def _unreadable_corpus(tmp_path):
    return _corpus_with(tmp_path, b"this is not a WAV file at all, just text")


#: The `-c` program that runs the real entry point with ONE input bent first.
#: Nothing in the probe is replaced: `main`, `render`, its `amp >= 1.0` guard
#: and the `except rc.Refused` that records the refusal in band are the shipped
#: code, and the bend is a statement about a kit register (`drums_fx.AMP_TOM`)
#: that `render` reads.
DRIVER = textwrap.dedent("""\
    import sys
    sys.path[:0] = [{tools!r}, {model!r}]
    import drums_fx as dx
    import probe_tom_numerator as ptn
    {bend}
    raise SystemExit(ptn.main(sys.argv[1:]))
    """)


def _probe(refs: pathlib.Path, tmp_path: pathlib.Path, bend: str | None = None,
           timeout: int = 300) -> subprocess.CompletedProcess:
    """The real interpreter and the real environment a batch runner sees
    (GF180_TR808_REFS / TR808_REFS agree, per the test plan's 'unset or
    equal'), with --refs naming the fixture corpus and --out under tmp.
    With `bend` the same entry point is run from DRIVER instead of the file,
    so one input is changed before `main` and nothing inside it is."""
    drop = (_rc_env.REFS_ENV, _rc_env.REFS_ALIAS_ENV)
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env[_rc_env.REFS_ENV] = str(refs)
    env[_rc_env.REFS_ALIAS_ENV] = str(refs)
    out = tmp_path / "report.json"
    cmd = [sys.executable, str(PROBE)]
    if bend is not None:
        cmd = [sys.executable, "-c",
               DRIVER.format(tools=str(ROOT / "tools"), model=str(ROOT / "model"), bend=bend)]
    return subprocess.run(
        [*cmd, "--refs", str(refs), "--out", str(out)],
        capture_output=True, text=True, timeout=timeout, env=env, cwd=str(ROOT))


# ---------------------------------------------------------------------------
# the class relations the whole fix rests on
# ---------------------------------------------------------------------------
def test_raiser_is_a_refusal_sibling_of_the_render_guard():
    import refusal

    assert issubclass(rc.Refused, refusal.Refused), (
        "the widened boundary relies on run_case.Refused subclassing refusal.Refused")


# ---------------------------------------------------------------------------
# the silent first reference must REFUSE by name, not traceback
# ---------------------------------------------------------------------------
def test_silent_reference_exits_refused_2_no_report(tmp_path):
    p = _probe(_silent_corpus(tmp_path), tmp_path)
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stderr[-400:])
    assert "REFUSED" in p.stdout, p.stdout[-400:]
    assert FIRST_REL in p.stdout and "silent" in p.stdout, p.stdout[-400:]
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-400:]
    assert not (tmp_path / "report.json").exists(), "a refused run must not write a report"


# ---------------------------------------------------------------------------
# the absent corpus keeps the raiser's own refusal
# ---------------------------------------------------------------------------
def test_absent_corpus_exits_refused_2(tmp_path):
    p = _probe(tmp_path / "no-such-refs", tmp_path)
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stderr[-400:])
    assert "REFUSED" in p.stdout and "reference corpus not at" in p.stdout, p.stdout[-400:]
    assert "Traceback" not in p.stderr, p.stderr[-400:]


# ---------------------------------------------------------------------------
# unrelated errors at the same boundary stay execution errors (exit 1)
# ---------------------------------------------------------------------------
def test_unreadable_reference_is_an_execution_error_not_a_refusal(tmp_path):
    p = _probe(_unreadable_corpus(tmp_path), tmp_path)
    assert p.returncode == 1, (p.returncode, p.stdout[-200:], p.stderr[-400:])
    assert "Traceback" in p.stderr, p.stderr[-400:]
    assert "REFUSED" not in p.stdout, "a scipy ValueError must not be relabelled a refusal"


# ---------------------------------------------------------------------------
# #1766: a candidate that was to answer and refused is REFUSED, not a pass
# ---------------------------------------------------------------------------
# THE DEFECT, measured red-first on `origin/main` @ a3870ddb (2026-11-28):
# `main` records a render refusal in band, scores the candidate
# `admissible: False`, writes `--out` and returns 0, so `tools/run_all.py` --
# which reads the exit status and nothing else -- rendered PASS for a run in
# which a candidate held no number. #1203 repaired the ESCAPE side of this
# boundary (the first section above); the exit-0 side is #1766's.
#
# WHAT THE PROBE OWES, and what these controls assert:
#
#   BP or BP+X4 refuses   exit 2; one line `REFUSED: BP ...` names it; every
#                         measured row is still printed and still in `--out`,
#                         which is written BEFORE the 2 is returned
#   the baseline's row    exit 2 with `--out` written too: nothing to select
#     refuses in band     against, so no selection, and no traceback. No
#                         real-path input reaches it (below), so it is driven
#                         in-process with stand-in engines
#   only HP refuses       exit 0
#   our render is silent  exit 2 BEFORE any row and no `--out`: the #1183
#                         outer boundary, unchanged. Doubles as the control
#                         that keeps the line above a checked property
#   --explain ...         exit 0, as before: it returns before any row exists
#
# WHY HP IS EXPECTED, and why nothing else is. `render` divides the kit's
# mode amp by |N(f0)| so a candidate cannot win on level, and refuses when the
# compensated amp does not fit Q0.16. On the shipped kit that is HP on every
# voice -- the amps `render` computes, from its own arithmetic at a3870ddb
# (HP's row is the message it raises, voice by voice):
#
#     voice          LT      MT      HT      LC      MC      HC
#     kit amp   0.00481 0.00620 0.00993 0.00935 0.01228 0.02068
#     BP          0.204   0.175   0.205   0.193   0.168   0.198
#     HP         34.632  19.839  16.940  15.951   9.145   7.543
#     BP+X4       0.051   0.044   0.051   0.048   0.042   0.049
#
# docs/scorecard/tom-body-334/README.md tables that refusal as HP's verdict: a
# recorded finding, not a measurement that failed to happen. "Any row refused
# -> 2" would therefore be unsatisfiable -- every clean run carries it -- the
# trap docs/verification-rules.md rule 8 warns of. HP measuring instead is not
# an error and exits 0. The set is pinned to exactly {HP} below, so widening
# it is a reviewed change and never a quiet one.
#
# HOW THE REFUSAL IS PROVOKED: through the real path, never by replacing the
# handler. The child is the shipped entry point with one statement run first
# (DRIVER), and the statement moves a kit register `render` reads:
#
#   BP refuses     dx.AMP_TOM["LT"] x8. The guard itself refuses BP at the
#                  first voice (compensated amp 1.633 >= 1.0) and the
#                  `except rc.Refused` records it; BP+X4, at a quarter of
#                  BP's amp, still fits and measures all six voices. HP
#                  refuses as always. LT's baseline renders 8x louder and
#                  saturates the bus; it still measures.
#
# WHY THE BASELINE'S ROW CANNOT REFUSE IN BAND on the shipped code -- which
# the first control written for it got wrong (wrong, then right): every
# dx.AMP_TOM at 0 was expected to leave four refused rows and a KeyError on
# main. It exits 2 with no report instead. The in-band writer wraps
# `render()` alone, whose only `rc.Refused` raiser is the amp guard under
# `if kind is not None`; `score()` sits outside it, so `drum_measurements`'
# silence refusal ("preparation contract (silent): [silent] our LT render is
# silent") takes the OUTER boundary. The silent-render control pins that.
# The ruling names the baseline anyway, so `main` handles a refused baseline
# row, and the stand-in control drives that branch: `render` raises the real
# class for the baseline only, `score` returns a fixed dict, and everything
# else -- loop, handler, selection guard, exit decision, `--out` -- is the
# shipped code, in milliseconds.
#
# WHAT IS DELIBERATELY NOT COUNTED, stated rather than left to be found: an
# estimator declining ONE metric of a measured voice -- `decay None vs None`,
# which a clean run carries for LC at baseline and for BP on MC and HC --
# is a `{"valid": False}` entry inside a measured row. The row holds its
# numbers; the selection reads the invalid metric (`LT decay became invalid`)
# and reports it. Only a whole row that is `{"refused": ...}` refuses the
# run, and that is the one shape the loop writes. A refusal written in any
# other shape would not be read; none exists, and the detector control below
# pins the shape it does read.
#
# TWO CORPORA, and what each is for. The full-run controls need six reference
# WAVs that pass `run_case.load_reference` and score. The SELF corpus is the
# probe's own shipped render of each voice written back as PCM16: circular
# for every METRIC in the report (the baseline scores distance 0 against
# itself), which is said here rather than left to be found, and immaterial to
# what is under test -- which rows refuse is computed by `render` from kit
# registers before any reference is read, and the exit status is the
# property. It is built in this file, so the controls run where CI runs,
# which has no Fischer corpus. The FISCHER corpus is the real one where it
# is present (optional locally, REQUIRED where ${GF180_REQUIRE_TR808_REFS}=1,
# the tools/test_run_case.py convention): the clean exit-0 control runs on it
# too, because "the HP refused row present and unchanged" is a claim about
# the real recordings. On main @ a3870ddb that run exits 0 with the row
# `LT HP: compensated amp 34.632 does not fit Q0.16`, identical to the
# committed docs/scorecard/tom-body-334/numerator.json.
#
# RED FIRST (docs/verification-rules.md rule 1), 2026-11-28, this file alone:
#
#   against main @ a3870ddb, the probe untouched: the real-CLI control failed
#   on `assert 0 == 2` -- BP refused, `--out` was written, exit 0 (66 s) --
#   and the two in-process controls on AttributeError (no such port yet).
#   Green before AND after by design: the four #1183 controls, the clean
#   exit-0 control on the self corpus (83 s) and on the Fischer corpus (85 s),
#   `--explain deadband`, and the silent-render control (run by hand on the
#   untouched tree: exit 2, no report, 24 s).
#   against main plus the wiring and a behaviour-free `unexpected_refusals`
#   stub returning [] -- the right ports and no behaviour, so each control
#   fails on its own assertion -- 3 failed, 1 passed in 87 s: the CLI control
#   on `assert 0 == 2`, the baseline stand-in on `assert 0 == 2`, the detector
#   on `assert [] == ['BP']`. The EXPECTED_REFUSED pin passed: it is data.
#   With the ruling implemented: 12 passed, 270-277 s over three full runs.
#
# WRONG BEFORE RIGHT, twice, both caught by reading the red run rather than
# by review. DRIVER first formatted its two paths as `PosixPath(...)` reprs,
# so both bent children died on a NameError -- red, but not for the defect.
# And the baseline control first expected the all-silent kit to leave four
# refused rows in band and a KeyError on main; it exits 2 with no report
# (below), and the control was rewritten around what it does.
FULL_RUN_S = 600              # a clean run is 80-105 s here; CI is slower
LT_AMP_X8 = 'dx.AMP_TOM["LT"] = dx.AMP_TOM["LT"] * 8'
ALL_AMPS_0 = "dx.AMP_TOM.update({v: 0.0 for v in dx.AMP_TOM})"


def _self_corpus(tmp_path_factory) -> pathlib.Path:
    root = tmp_path_factory.mktemp("self-refs")
    for v in ptn.VOICES:
        x, sr = rc.render_drum_solo(v)
        rel = pathlib.Path(rc.REF_MAIN[v][0])
        (root / rel.parent).mkdir(parents=True, exist_ok=True)
        wavfile.write(str(root / rel), sr, np.round(x * 32768.0).astype("<i2"))
    return root


@pytest.fixture(scope="module")
def self_corpus(tmp_path_factory) -> pathlib.Path:
    return _self_corpus(tmp_path_factory)


def _fischer_corpus() -> pathlib.Path:
    """Optional locally, REQUIRED where ${GF180_REQUIRE_TR808_REFS}=1: a
    required control REFUSES on a missing corpus rather than going green
    through a skip."""
    refs = rc.configured_refs()
    missing = [rc.REF_MAIN[v][0] for v in ptn.VOICES if not (refs / rc.REF_MAIN[v][0]).is_file()]
    if missing:
        msg = f"the Fischer corpus is not at {refs} (no {missing[0]})"
        if os.environ.get(rc.REFS_REQUIRED_ENV) == "1":
            pytest.fail(f"REFUSED: {msg}. {rc.REFS_REQUIRED_ENV}=1 makes this a required "
                        f"control, and a required control does not pass by skipping.", pytrace=False)
        pytest.skip(f"OPTIONAL local run, skipped: {msg}")
    return refs


def _report(tmp_path: pathlib.Path) -> dict:
    return json.loads((tmp_path / "report.json").read_text())


def _refused_line(p: subprocess.CompletedProcess) -> list[str]:
    """The lines that open with `REFUSED:` -- the exit-status-bearing summary,
    not the in-band `HP: REFUSED -- ...` a clean run prints by design."""
    return [ln for ln in p.stdout.splitlines() if ln.startswith("REFUSED:")]


def _assert_measured(data: dict, p: subprocess.CompletedProcess, *labels: str) -> None:
    """Each named row holds all six voices with a body-spectrum distance, and
    every one of its lines is still on stdout."""
    for label in labels:
        row = data["rows"][label]
        assert list(row) == list(ptn.VOICES), (label, list(row))
        assert all("distance" in row[v]["body spectrum"] for v in ptn.VOICES), label
        for v in ptn.VOICES:
            assert f"\n{label:14s} {v} (" in p.stdout, (label, v, p.stdout[-600:])


def _assert_clean_refusal(p: subprocess.CompletedProcess) -> None:
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stdout[-800:], p.stderr[-800:])
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-800:]


def test_a_refused_candidate_exits_refused_2_and_keeps_the_measured_rows(tmp_path, self_corpus):
    """RED-FIRST (#1766): on main this run exits 0 with BP holding no number.
    BP refuses through `render`'s own guard; the run is REFUSED, names BP and
    BP alone (HP's refusal is by design), and keeps every measured row in
    stdout and in `--out`, which is written first."""
    p = _probe(self_corpus, tmp_path, bend=LT_AMP_X8, timeout=FULL_RUN_S)
    _assert_clean_refusal(p)
    data = _report(tmp_path)
    bp = data["rows"]["BP"]
    assert list(bp) == ["refused"] and bp["refused"].startswith("LT BP: compensated amp"), bp
    assert float(bp["refused"].split("compensated amp ")[1].split()[0]) >= 1.0, bp
    _assert_measured(data, p, RAW_LABEL, "BP+X4")
    assert data["selection"]["BP"] == {"worst_dev_body_distance": None, "admissible": False,
                                       "reasons": [f"REFUSED: {bp['refused']}"]}
    assert data["selection"]["BP+X4"]["worst_dev_body_distance"] is not None, data["selection"]
    line = _refused_line(p)
    assert len(line) == 1 and line[0].startswith("REFUSED: BP "), line
    assert p.stdout.index(f"wrote {tmp_path / 'report.json'}") < p.stdout.rindex(line[0])


def test_a_silent_render_is_refused_before_any_row_and_writes_no_report(tmp_path, self_corpus):
    """Our side silent, through the real path: every mode amp at 0 makes each
    render digital silence, and `score` -> `drum_measurements` refuses the
    pair. That refusal is NOT recorded in band -- `score()` sits outside the
    `except rc.Refused` that wraps `render()` -- it takes the #1183 outer
    boundary: exit 2, no `--out`, our render named. Green before and after.
    If a report ever appears here, the baseline's row has become refusable in
    band and the stand-in control below is no longer the only one for it."""
    p = _probe(self_corpus, tmp_path, bend=ALL_AMPS_0, timeout=FULL_RUN_S)
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stdout[-800:], p.stderr[-800:])
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-800:]
    assert "our LT render is silent" in p.stdout, p.stdout[-600:]
    assert not (tmp_path / "report.json").exists(), "a refused run must not write a report"


def test_a_refused_baseline_row_exits_refused_2_with_the_report_written(tmp_path, self_corpus,
                                                                        monkeypatch, capsys):
    """The ruling's baseline leg, driven where no real-path input reaches it.
    `render` raises the real class for the baseline only; `score` is a
    stand-in, so this is the loop, the handler, the selection guard and the
    exit decision in milliseconds. On main the selection raises a KeyError
    against a baseline with no voices."""
    def render(voice, kind, nums=ptn.dx.N_MODES):
        if kind is None:
            raise rc.Refused(f"{voice} baseline: injected")
        return np.zeros(4800), ptn.dx.SR

    def score(voice, x, sr, refdir, ref=None):
        return {**{k: {"distance": 0.1} for k in ptn.PLAN[voice]},
                "body spectrum": {"distance": 0.1}, "_peak_fs": 0.2}

    monkeypatch.setattr(ptn, "render", render)
    monkeypatch.setattr(ptn, "score", score)
    monkeypatch.setattr(ptn, "control_bit_identity", lambda: {"LT": True, "BD": True, "SD": True})
    out = tmp_path / "report.json"
    code = ptn.main(["--refs", str(self_corpus), "--out", str(out)])
    stdout = capsys.readouterr().out
    assert code == exit_contract.REFUSED, (code, stdout[-800:])
    data = json.loads(out.read_text())
    assert data["rows"][RAW_LABEL] == {"refused": "LT baseline: injected"}
    for label in ptn.CANDIDATES:
        assert list(data["rows"][label]) == list(ptn.VOICES), (label, list(data["rows"][label]))
    assert data["selection"] is None and data["chosen"] is None and data["confirmation"] is None
    assert data["baseline_worst_dev_body_distance"] is None
    line = [ln for ln in stdout.splitlines() if ln.startswith("REFUSED:")]
    assert line == ["REFUSED: RAW (shipped) refused -- a row that was to answer holds no number, "
                    "so the report above is partial and this run is no verdict, not a pass"], line
    assert stdout.index(f"wrote {out}") < stdout.rindex(line[0])


def _assert_clean_run_exits_0(refs: pathlib.Path, tmp_path: pathlib.Path) -> None:
    """The control that proves the gate is satisfiable (verification-rules: an
    unsatisfiable gate is worse than none): HP's refusal is in every clean
    run, is still recorded in band and in `--out`, and does not refuse the
    run. Green before AND after the fix, by design."""
    p = _probe(refs, tmp_path, timeout=FULL_RUN_S)
    assert p.returncode == exit_contract.PASS, (p.returncode, p.stdout[-800:], p.stderr[-800:])
    data = _report(tmp_path)
    refused = {label: row["refused"] for label, row in data["rows"].items() if "refused" in row}
    assert list(refused) == ["HP"], refused
    assert refused["HP"].startswith("LT HP: compensated amp"), refused
    assert float(refused["HP"].split("compensated amp ")[1].split()[0]) >= 1.0, refused
    _assert_measured(data, p, RAW_LABEL, "BP", "BP+X4")
    assert _refused_line(p) == [], p.stdout[-600:]
    assert f"\nHP: REFUSED -- {refused['HP']}" in p.stdout, p.stdout[-600:]


def test_a_clean_run_exits_0_with_the_by_design_hp_refusal_on_record(tmp_path, self_corpus):
    """On the self corpus, which every host has: see
    _assert_clean_run_exits_0."""
    _assert_clean_run_exits_0(self_corpus, tmp_path)


def test_a_clean_run_on_the_fischer_corpus_exits_0(tmp_path):
    """The same control on the real recordings, where they are present; it
    skips visibly where they are not, so it does not build the self corpus
    first (see _fischer_corpus)."""
    _assert_clean_run_exits_0(_fischer_corpus(), tmp_path)


def test_the_explain_paths_still_exit_0(tmp_path):
    """`--explain deadband` answers before any row exists and keeps exiting 0
    (about 1 s), so it is committed; the per-voice `--explain LT` (7-13 s,
    also 0 on main @ a3870ddb and after) was run by hand for the PR instead."""
    out = tmp_path / "explain.json"
    p = subprocess.run([sys.executable, str(PROBE), "--explain", "deadband", "--out", str(out)],
                       capture_output=True, text=True, timeout=120, cwd=str(ROOT))
    assert p.returncode == exit_contract.PASS, (p.returncode, p.stdout[-400:], p.stderr[-400:])
    assert out.exists()


# The detector's own controls, in-process (milliseconds). `rows` is a clean
# run's: every row measured on every voice, HP refused by design, and the
# measured rows carrying an invalid `decay` the way real ones do.
def _rows(**over: dict) -> dict:
    measured = {v: {"body spectrum": {"distance": 0.0}, "decay": {"valid": False}} for v in ptn.VOICES}
    rows = {RAW_LABEL: measured, "BP": measured, "BP+X4": measured,
            "HP": {"refused": "LT HP: compensated amp 34.632 does not fit Q0.16"}}
    rows.update(over)
    return rows


def test_only_hp_is_expected_to_refuse_and_it_is_a_candidate():
    """Widening EXPECTED_REFUSED is how this gate would be loosened without
    anyone noticing (#1766's curation names that as the failure). The set is
    pinned here, so it moves only in a reviewed diff; and the baseline, which
    everything is selected against, is never expected to refuse."""
    assert set(ptn.EXPECTED_REFUSED) == {"HP"}
    assert set(ptn.EXPECTED_REFUSED) <= set(ptn.CANDIDATES)


def test_the_detector_reads_exactly_the_rows_that_were_to_answer():
    assert ptn.unexpected_refusals(_rows()) == []      # HP by design; an invalid `decay` is not a refusal
    assert ptn.unexpected_refusals(
        _rows(BP={"refused": "LT BP: compensated amp 1.633 does not fit Q0.16"})) == ["BP"]
    assert ptn.unexpected_refusals(_rows(**{"BP+X4": {"refused": "x"}})) == ["BP+X4"]
    assert ptn.unexpected_refusals(
        _rows(BP={"refused": "x"}, **{"BP+X4": {"refused": "y"}})) == ["BP", "BP+X4"]
    assert ptn.unexpected_refusals(_rows(**{RAW_LABEL: {"refused": "LT baseline: injected"}})) == [RAW_LABEL]
    assert ptn.unexpected_refusals(_rows(HP={"refused": "x"}, BP={"refused": "y"})) == ["BP"]
    # HP measuring instead is not a refusal either
    assert ptn.unexpected_refusals(
        _rows(HP={v: {"body spectrum": {"distance": 0.5}} for v in ptn.VOICES})) == []
