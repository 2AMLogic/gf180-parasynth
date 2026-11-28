#!/usr/bin/env python3
"""Known-answer exit-status controls: a cross-module refusal must reach the
rimshot probe's exit contract as REFUSED, never as an uncaught exit-1
traceback (#1162).

THE DEFECT, AS MEASURED ON main @ 334e0856 (2026-11-24, red-first run of
every control in this file): `tools/probes/rs_guard_band.py` catches only its
own local `Refused` at the CLI dispatch, but `compare` and `guards` reach
`run_case.load_reference` / `run_case.prepare` through `both_records()`, and
`run_case.Refused` is `preparation_contract.Refused` -- a SIBLING class, not
the local one. A digitally silent `rs8/RS.WAV` therefore escaped as a
`preparation_contract.Refused` traceback and the process exited 1: a refusal
rendered as FAIL, the exact inversion of the exit contract
(tools/exit_contract.py, #804). The D10A partial-balance investigation needs
an unusable reference reported as a refusal, because "the corpus is silent"
is evidence about the apparatus, not about the instrument.

THE KNOWN ANSWERS, independent of anything this repository models: a
digitally silent mono PCM16 44.1 kHz WAV (4,410 frames, per the issue's
fixture spec) must be refused "reference recording rs8/RS.WAV is silent" by
`run_case.load_reference` before any render or verdict; an absent corpus root
must keep the probe's own missing-corpus refusal. Both answers were verified
by hand against the pre-fix tree before the fix landed (exit 1 + traceback,
and exit 2 + reason, respectively).

WHAT THE PROBE OWES, and what these controls assert:

  compare / guards  the silent reference is reported by name, exit
                    `exit_contract.REFUSED` (2), no traceback and no sound
                    verdict (the report header must not have started)
  missing corpus    the pre-existing local refusal is preserved: exit 2 and
                    the corpus-naming reason
  unrelated errors  a corpus file scipy cannot read at all raises ValueError
                    at the same call boundary and must STILL exit 1 as an
                    execution error -- widening the handler to the base class
                    must not relabel an implementation/input error as a
                    refusal (tools/refusal.py's "inverse defect")

COMPARATOR, NOT A REPAIR: `tools/measure_partial_balance.py measure` on the
same silent fixture already exits 2 and names the file (its `Refused =
pc.Refused` resolves to the raising class); #1162 keeps it out of the repair
set for this path. `tools/probes/cymbal_400_production_tail.py` and the
cymbal callers fixed by #1160 are the pattern this fix copies.

WRONG-THEN-RIGHT (1, caught by running rather than reading): the first draft
asserted the REFUSED line on stderr, because the cymbal fix (#1160) prints
`REFUSED:` there; this probe's established convention (its missing-corpus
control, unchanged by the fix) prints `REFUSED  <reason>` on stdout with two
spaces. Every assertion here pins the message, not just the code, because an
exit code alone is satisfied by a crashed-and-caught-anything handler.

#1751, THE RENDER SIDE OF THE SAME PROBE (red-first on main @ 45774600,
2026-11-28): given a USABLE reference and a digitally silent RS render,
`rs_mode_drive.py distort` printed its table header and exited 1 with
`audio_measure.InsufficientEvidence: harmonic_powers: silent` on stderr.
`harmonic_powers` refuses silence by raising `InsufficientEvidence` -- an
AssertionError subclass, not a `Refused` of any module -- so the base-class
widening above could not see it: the estimator's own refusal, rendered as
FAIL. The dispatch handler now names that class beside the base. What the
two controls at the foot of this file pin:

  silent render     exit `exit_contract.REFUSED` (2); the whole line
                    `REFUSED  harmonic_powers: silent` on stdout; no
                    traceback; the table header printed and immediately
                    followed by REFUSED, so the refusal came from inside the
                    first row (not from the reference load the
                    silent-reference controls cover) and that row printed
                    no number
  plain Assertion   the SAME render call raising an ordinary AssertionError
                    is still exit 1, with the fault named in its traceback and
                    no REFUSED line: the handler did not widen to every
                    AssertionError. Green before the fix and must stay so.

WRONG-THEN-RIGHT for #1751 (1, in the apparatus, caught because the
red-first run was red for the WRONG reason): the first seam passed the
probe's path to the child as argv[1] after `-c` and handed it to
`runpy.run_path` from there, expecting the probe to then see `distort` at
its own argv[1]. `run_path` REPLACES argv[0] in place instead of shifting,
so the probe saw its own path at argv[1], fell into its `except KeyError`
usage branch and exited 64 -- not the exit-1 traceback this issue
describes. The seam now gives the child `['-c', cmd]`; `run_path` then puts
the probe's path at argv[0], which is what a direct invocation leaves there.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import numpy as np
from scipy.io import wavfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "probes"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))

import exit_contract                                     # noqa: E402
import refusal                                           # noqa: E402
import run_case as _rc_env                               # noqa: E402 -- REFS_ENV names only

SR = 44100
FRAMES = 4410               # 100 ms of mono PCM16: the issue's fixture spec


def _silent_corpus(tmp_path: pathlib.Path) -> pathlib.Path:
    d = tmp_path / "refs"
    (d / "rs8").mkdir(parents=True)
    wavfile.write(str(d / "rs8" / "RS.WAV"), SR, np.zeros(FRAMES, dtype="<i2"))
    return d


def _unreadable_corpus(tmp_path: pathlib.Path) -> pathlib.Path:
    d = tmp_path / "refs"
    (d / "rs8").mkdir(parents=True)
    (d / "rs8" / "RS.WAV").write_bytes(b"this is not a WAV file at all, just text")
    return d


def _probe(cmd: str, refs: pathlib.Path) -> subprocess.CompletedProcess:
    """The real interpreter and the real environment a batch runner sees:
    GF180_TR808_REFS names the corpus root, TR808_REFS agrees with it (the
    test plan's 'unset or equal'), and nothing else about the ambient corpus
    configuration can leak in. The variable NAMES come from run_case so this
    file never spells them (test_corpus_path_single_source)."""
    drop = (_rc_env.REFS_ENV, _rc_env.REFS_ALIAS_ENV)
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env[_rc_env.REFS_ENV] = str(refs)
    env[_rc_env.REFS_ALIAS_ENV] = str(refs)
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / "probes" / "rs_guard_band.py"), cmd],
        capture_output=True, text=True, timeout=300, env=env, cwd=str(ROOT))


# ---------------------------------------------------------------------------
# the class relations the whole fix rests on (the issue's own probe, committed
# so the boundary cannot silently regress)
# ---------------------------------------------------------------------------
def test_sibling_refused_classes_share_only_the_base():
    import run_case as rc
    import preparation_contract as pc
    import rs_guard_band as rsb

    assert rsb.Refused is not rc.Refused
    assert not issubclass(rc.Refused, rsb.Refused)
    # the contract the post-fix handler relies on:
    for klass in (rsb.Refused, rc.Refused, pc.Refused):
        assert issubclass(klass, refusal.Refused), klass


# ---------------------------------------------------------------------------
# compare / guards: the silent reference must REFUSE by name, not traceback
# ---------------------------------------------------------------------------
def test_compare_silent_reference_exits_refused_2_no_verdict(tmp_path):
    p = _probe("compare", _silent_corpus(tmp_path))
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stderr[-400:])
    assert "REFUSED" in p.stdout, p.stdout[-400:]
    assert "rs8/RS.WAV" in p.stdout and "silent" in p.stdout, p.stdout[-400:]
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-400:]
    assert "PROVENANCE" not in p.stdout, "a refused run must not start a sound report"


def test_guards_silent_reference_exits_refused_2_no_verdict(tmp_path):
    p = _probe("guards", _silent_corpus(tmp_path))
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stderr[-400:])
    assert "REFUSED" in p.stdout, p.stdout[-400:]
    assert "rs8/RS.WAV" in p.stdout and "silent" in p.stdout, p.stdout[-400:]
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-400:]
    assert "SHIPPING ESTIMATOR" not in p.stdout, "a refused run must not start a sound report"


# ---------------------------------------------------------------------------
# the pre-existing local refusal is preserved
# ---------------------------------------------------------------------------
def test_compare_absent_corpus_still_refuses_with_local_reason(tmp_path):
    p = _probe("compare", tmp_path / "no-such-refs")
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stderr[-400:])
    assert "REFUSED" in p.stdout, p.stdout[-400:]
    assert "reference corpus not at" in p.stdout, p.stdout[-400:]
    assert "Traceback" not in p.stderr, p.stderr[-400:]


# ---------------------------------------------------------------------------
# an undecodable reference is a missing precondition: REFUSED (exit 2), #1860
# (retires the #1183 / b7c88251 position that a scipy ValueError at this
# boundary stays an execution error. That position predates the readers
# refusing; unrelated errors still exit 1 -- pinned at the reader in
# test_run_case.py / test_perceptual_gate.py)
# ---------------------------------------------------------------------------
def test_unreadable_reference_is_a_refusal_naming_the_file(tmp_path):
    p = _probe("compare", _unreadable_corpus(tmp_path))
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stdout[-200:], p.stderr[-400:])
    assert "REFUSED" in p.stdout and "unreadable" in p.stdout, p.stdout[-400:]
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-400:]


# ---------------------------------------------------------------------------
# rs_mode_drive: the SAME shape in the rimshot family's sibling probe, found
# by the #1162 census and reproduced red-first by hand before the fix
# (confirm and distort exited 1 with the preparation_contract.Refused
# traceback on this exact fixture; derive and sweep, which never read the
# corpus, exited 0 then and must still)
# ---------------------------------------------------------------------------
def _mode_drive(cmd: str, refs: pathlib.Path,
                bootstrap: str | None = None) -> subprocess.CompletedProcess:
    """`rs_mode_drive.py <cmd>` against a corpus root, with `_probe`'s
    environment hygiene. `bootstrap`, when given, is Python source the child
    runs INSTEAD of the probe, with `sys.argv == ['-c', cmd]`: the seam
    through which #1751 injects a render fault before the probe's own
    `__main__` dispatch runs. The bootstrap hands off with
    `runpy.run_path(..., run_name='__main__')`, which puts the probe's path
    at argv[0] -- the argv a direct invocation gives it."""
    drop = (_rc_env.REFS_ENV, _rc_env.REFS_ALIAS_ENV)
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env[_rc_env.REFS_ENV] = str(refs)
    env[_rc_env.REFS_ALIAS_ENV] = str(refs)
    script = str(ROOT / "tools" / "probes" / "rs_mode_drive.py")
    argv = ([sys.executable, script, cmd] if bootstrap is None else
            [sys.executable, "-c", bootstrap, cmd])
    return subprocess.run(
        argv, capture_output=True, text=True, timeout=300, env=env, cwd=str(ROOT))


def test_mode_drive_confirm_silent_reference_exits_refused_2(tmp_path):
    p = _mode_drive("confirm", _silent_corpus(tmp_path))
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stderr[-400:])
    assert "REFUSED" in p.stdout, p.stdout[-400:]
    assert "rs8/RS.WAV" in p.stdout and "silent" in p.stdout, p.stdout[-400:]
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-400:]
    assert "PROVENANCE" not in p.stdout, "a refused run must not start a report"


def test_mode_drive_distort_silent_reference_exits_refused_2(tmp_path):
    p = _mode_drive("distort", _silent_corpus(tmp_path))
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stderr[-400:])
    assert "REFUSED" in p.stdout and "rs8/RS.WAV" in p.stdout, p.stdout[-400:]
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-400:]


def test_mode_drive_corpus_free_command_still_runs_on_silent_corpus(tmp_path):
    """derive reads no reference, so the widened handler must not have made
    the corpus-free commands refuse (the successful-path check)."""
    p = _mode_drive("derive", _silent_corpus(tmp_path))
    assert p.returncode == 0, (p.returncode, p.stdout[-300:], p.stderr[-300:])


def test_mode_drive_absent_corpus_still_refuses_with_local_reason(tmp_path):
    p = _mode_drive("confirm", tmp_path / "no-such-refs")
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stderr[-400:])
    assert "reference corpus not at" in p.stdout, p.stdout[-400:]


# ---------------------------------------------------------------------------
# rs_mode_drive distort, the RENDER side (#1751): the estimator's OWN refusal
# ---------------------------------------------------------------------------
def _tonal_corpus(tmp_path: pathlib.Path) -> pathlib.Path:
    """A USABLE reference: 0.5 s of mono PCM16 at 44.1 kHz, two exponentially
    decaying partials (455 Hz and 1786 Hz, tau 30 ms, 6000 peak each). Not
    silent, so `reference()` loads it and `distort` gets as far as its first
    render -- the path the silent-reference fixture never reaches, because
    that one is refused while the reference is still loading."""
    d = tmp_path / "refs"
    (d / "rs8").mkdir(parents=True)
    t = np.arange(int(0.5 * SR)) / SR
    y = 6000 * np.exp(-t / 0.03) * (np.sin(2 * np.pi * 455 * t) + np.sin(2 * np.pi * 1786 * t))
    wavfile.write(str(d / "rs8" / "RS.WAV"), SR, np.round(y).astype("<i2"))
    return d


#: what the estimator says of a silent record, as the dispatch prints it
REFUSED_LINE = "REFUSED  harmonic_powers: silent"
#: the message of the plain AssertionError the control injects
RENDER_FAULT = "unrelated render fault"


def _injected_render(play_body: str) -> str:
    """Child-interpreter source: replace `DrumsFx.play` -- the one render
    call `_raw_render` makes for both arms of every dHARM row -- with
    `play_body`, then hand off to the probe's own `__main__` dispatch."""
    return (f"import sys\n"
            f"sys.path.insert(0, {str(ROOT / 'model')!r})\n"
            f"import numpy as np\n"
            f"import drums_fx as dx\n"
            f"def _play(self, writes, n):\n"
            f"{play_body}"
            f"dx.DrumsFx.play = _play\n"
            f"import runpy\n"
            f"runpy.run_path({str(ROOT / 'tools' / 'probes' / 'rs_mode_drive.py')!r}, "
            f"run_name='__main__')\n")


#: an exactly-zero RS render: what a dead voice, a muted output path or a
#: render that never fired looks like at the estimator
SILENT_RS_RENDER = _injected_render("    return np.zeros(n), None\n")
#: the control against catching too broadly: an ordinary AssertionError at the
#: same call, which is an execution error and must stay one
FAULTING_RS_RENDER = _injected_render(f"    raise AssertionError({RENDER_FAULT!r})\n")


def test_mode_drive_distort_silent_render_exits_refused_2(tmp_path):
    """Given a usable reference and a silent RS render, `distort` reports the
    estimator's own refusal as REFUSED, exit 2, with no traceback and no
    numeric result for the row that could not be measured (#1751)."""
    p = _mode_drive("distort", _tonal_corpus(tmp_path), bootstrap=SILENT_RS_RENDER)
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stderr[-400:])
    lines = p.stdout.splitlines()
    assert REFUSED_LINE in lines, p.stdout[-400:]
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-400:]
    # The refusal came from INSIDE the first row, not from the reference load
    # the silent-reference controls above already cover, and that row printed
    # no result: the table header is the last thing before REFUSED.
    at = lines.index(REFUSED_LINE)
    assert at == len(lines) - 1, "nothing may follow the refusal: " + p.stdout[-400:]
    assert lines[at - 1].lstrip().startswith("setting"), "a data row precedes the refusal: " + p.stdout[-400:]


def test_mode_drive_distort_unrelated_assertion_is_an_execution_error(tmp_path):
    """A plain AssertionError from the same render path still exits 1, names
    the injected fault in its traceback and prints no REFUSED line: adding
    `InsufficientEvidence` to the handler must not widen it to every
    AssertionError (tools/refusal.py's "inverse defect")."""
    p = _mode_drive("distort", _tonal_corpus(tmp_path), bootstrap=FAULTING_RS_RENDER)
    assert p.returncode == 1, (p.returncode, p.stdout[-200:], p.stderr[-400:])
    assert "Traceback" in p.stderr, p.stderr[-400:]
    assert f"AssertionError: {RENDER_FAULT}" in p.stderr, p.stderr[-400:]
    assert "REFUSED" not in p.stdout, "an AssertionError must not be relabelled a refusal"
