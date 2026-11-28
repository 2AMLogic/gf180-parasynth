#!/usr/bin/env python3
"""Known-answer exit-status controls for tools/clap_final_strike_experiment.py
(#2046).

THE DEFECT, AS MEASURED ON main @ 616b8d63 (2026-11-28, red-first run of this
file's ten controls against the untouched tool): 3 red, 7 green. `main()`
handled `probe.Refused` alone, around `probe.basis`. `rc.load_reference` and
`rc.prepare` raise `run_case.Refused` (= `preparation_contract.Refused`), a
SIBLING of that class, so at :273 a non-finite reference left as a traceback
and exit 1:

    File "tools/clap_final_strike_experiment.py", line 273, in main
      rx, rsr, rel, _ = rc.load_reference("CP", refdir)
    File "tools/run_case.py", line 1628, in load_reference
      raise Refused(str(why)) from why
    preparation_contract.Refused: reference recording cp8/CP.WAV holds 1
    non-finite samples of 44100 (1 NaN, 0 Inf; first at index 1000) ...

and at :274 a reference cut into the strike left the same way. The seven
green were the controls of behaviour the fix must not touch (the declared
status, the module's own refusal, the plain-error controls, a well-formed
reference) and the pre-fix replay, which asserts the defect. This tool
declares no exit 1: it returns 0, or 2 for a refusal. So the traceback was a
crash, which a batch runner reports as FAIL where the same refusal handled is
NO-VERDICT (`tools/run_all.py`, `exit_contract.NO_EVIDENCE`) -- the inversion
`tools/exit_contract.py` exists to prevent.

WHAT STANDS IN FRONT OF THE SITES ON THAT TREE, as run, not read: two
handled refusals, so no input to the untouched CLI reaches :273 today.

  * the corpus pin. `probe.basis` checks the manifest and the corpus git
    HEAD (pinned 85fbecf) before anything is loaded and refuses as
    `probe.Refused` -- handled, exit 2. An altered CP.WAV passes it only
    when the manifest beside the corpus names that altered file.
  * the renderer precondition. With nothing but the pin bypassed and the
    three renders REAL, `main()` returned 2 in 20.1 s with one line,
    `REFUSED: the experiment renderer does not reproduce
    render_drum_solo('CP') bit for bit`. The kit now ships this experiment's
    own selected condition -- `CP_BURSTS = 3, CP_PERIOD = 511, CP_FINAL_TAU
    = 20 ms, CP_TAIL_TAU = 80 ms` (model/drums_fx.py:750) is "L2 final
    1.00" in docs/scorecard/clap-d12a/final-strike.json -- while its frozen
    B0 is the earlier baseline (`probe.DEFAULT`: 2 strikes, period 480, tail
    47 ms). Registers 0x60 and 0x66 differ, and with them 28101 of 105600
    samples. That is the precondition doing its job on a kit that moved. It
    is NOT pinned here: a test that reds on a re-frozen experiment reds on
    someone else's legitimate work.

So the defect is latent on that tree and live the moment either gate opens:
a corpus-side manifest naming an altered file, or the experiment re-based on
the kit that ships. The handler is missing either way, and this file proves
it by run at the sites.

THE KNOWN ANSWERS, independent of anything this repository models:

  * arithmetic on NaN is not audio, and a record that begins at its own
    peak was cut into the strike. `run_case` refuses both, by classes this
    module does not name.
  * a tool that measured nothing owes `exit_contract.REFUSED` (2) with its
    reason; an implementation error is not a precondition and owes exit 1.

WHAT THE TOOL OWES, and what the controls below assert:

  non-finite ref    a child interpreter: exit 2, the refusal's reason as the
                    last line on stdout, `REFUSED: <reason>`; no traceback,
                    no table, no record written
  cut-into-strike   the same, through `main(argv)` in this process, from
                    `prepare` at the next site
  its own refusal   `probe.basis` refusing still exits 2 with its reason: one
                    handler at the dispatch replaced the narrow one, and
                    nothing that one caught is lost
  other errors      a plain AssertionError at the same call exits 1 in a
                    child and propagates unchanged in process (RuntimeError
                    too): the handler catches `refusal.Refused` and nothing
                    wider (tools/refusal.py's "inverse defect")
  well-formed       an int16 record with lead passes both sites and the run
                    goes on into the pool, where this control stops it: the
                    refusals above are about the content they name
  pre-fix replay    the source at `PRE_FIX`, read out of this repository's
                    own history and run under the SAME seam, still exits 1
                    with the traceback above, at line 273: the injected-bug
                    control
  the census        `tools/refusal_sibling_scan.py`, which read every site
                    but `basis` as `candidate-escape`, reads the module
                    `covered(cli-base)`; the four sites of the issue are
                    still listed, by callee

THE SEAM, and what it bypasses (docs/verification-rules.md rule 8). One text,
`_SEAM`, run by the child interpreter and exec'd by the fixture, so no
control runs a seam the others do not:

  * `probe.basis` returns a dict. A fabricated corpus cannot carry the
    pinned git HEAD, and the pin is not what is under test.
  * the three precondition renders return one short finite record each.
    A real render costs 6.5 s, none is on the path to the sites, and on
    this tree they fail the precondition (above). The sha check they feed
    is satisfied by three equal arrays.

Everything else is real: a WAV on disk, `scipy.io.wavfile.read`,
`run_case.load_reference`, `run_case.prepare`, the classes and `main(argv)`.
What defeats this file: a `probe.basis` stub mistaken for production
reachability. It is not -- see the two gates above.

NOT REACHED BY RUN, stated rather than implied: the two rendered-side sites
(`late_event`, and the headroom row), both `rc.prepare` on this model's own
output. `render()` returns integer output over 32768 with 10 ms of true
silence before the strike, so `prepare` has nothing to refuse there -- not
non-finite, not cut into the strike -- and no input is known that changes
that. The one handler covers them and the census reads them
`covered(cli-base)`; they are not exercised here. Nor is the full live run
(exit 0, the table): it needs the pinned corpus and a kit the experiment
reproduces, this host has neither, and no control below claims it.

MUTANTS, run against the fixed tree with the handler edited three ways and
the tool restored byte for byte afterwards. Narrowed to `except
probe.Refused` (the original shape): the two reference controls red. Widened
to `except Exception`: the three plain-error controls red. Removed: those
two and the module's-own-refusal control red. None survived.

WRONG-THEN-RIGHT (3, all in the apparatus or the record, all caught before
anything was committed):

  1. The seam was planned as ONE bypass, the pin: with the pin alone
     bypassed, the real renders were expected to carry the run to :273. The
     first measurement said otherwise (`audio_sha` unequal, then the 20.1 s
     run above) and the seam has two parts. Written to the plan, every
     reference control would have been green on the untouched tool -- refused
     by the precondition, for a reason that is not the defect.
  2. The first draft of the non-finite control wrote the corpus directory
     twice, a finite file and then the NaN one. Harmless as run, since the
     second write won, and one slip from a control red for the wrong reason.
     Caught by rereading before the red run, not by a control.
  3. This record first said exit 1 is what the tool returns "when it has a
     finding" -- the holdout probe's contract, carried over by analogy. The
     tool declares no exit 1. Caught by reading its every `return` before the
     sentence stood; nothing ran wrong, a claim was about to.
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
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))

import clap_d12a_probe as probe                          # noqa: E402
import clap_final_strike_experiment as ex                # noqa: E402
import exit_contract                                     # noqa: E402
import refusal                                           # noqa: E402
import run_case as rc                                    # noqa: E402

TOOL = ROOT / "tools" / "clap_final_strike_experiment.py"
TOOL_REL = "tools/clap_final_strike_experiment.py"
SCAN = ROOT / "tools" / "refusal_sibling_scan.py"
PRE_FIX = "616b8d63"
SR = 44100                      # the Fischer corpus rate
REFUSED, FAIL = exit_contract.REFUSED, exit_contract.FAIL
TIMEOUT = 600

NAN_WAV = "reference recording cp8/CP.WAV holds 1 non-finite samples"
CUT_WAV = "the recording begins at or above 2 % of its own peak"

#: The one seam, as SOURCE: the child interpreter runs this text between the
#: load and the call, and the in-process fixture execs the same text, so no
#: control can run a seam the others do not.
_SEAM = (
    "import numpy as np\n"
    "import clap_d12a_probe as probe\n"
    "import run_case as rc\n"
    "_tiny = np.zeros(4800)\n"
    "_tiny[480] = 0.5\n"
    "probe.basis = lambda refdir, manifest: {'seam': 'corpus pin not under test'}\n"
    "v.render = lambda cond, accent=1.0, offset=0, force_subclass=False: "
    "(_tiny, 48000, [0.0], _tiny)\n"
    "rc.render_drum_solo = lambda sound, *a, **k: (_tiny, 48000)\n")

_HEAD = (f"import sys\n"
         f"sys.path.insert(0, {str(ROOT / 'model')!r})\n"
         f"sys.path.insert(0, {str(ROOT / 'tools')!r})\n")
_AS_MODULE = (f"import types\n"
              f"v = types.ModuleType('clap_final_strike_experiment')\n"
              f"v.__file__ = {str(TOOL)!r}\n"
              f"exec(compile(sys.stdin.read(), {str(TOOL)!r}, 'exec'), v.__dict__)\n")


def _argv(refdir: pathlib.Path, tmp_path: pathlib.Path) -> list[str]:
    return ["--refs", str(refdir), "--out", str(tmp_path / "out.json"), "--jobs", "1"]


def _main(argv, *, source=None, patch="") -> subprocess.CompletedProcess:
    """`main(argv)` in a child interpreter, as a batch runner sees it: the
    working tree's module, or `source` loaded as that module under the tool's
    own path so its ROOT resolves; then the seam, then `patch`, then the call."""
    load = _AS_MODULE if source is not None else "import clap_final_strike_experiment as v\n"
    code = _HEAD + load + _SEAM + patch + f"raise SystemExit(v.main({list(argv)!r}))\n"
    return subprocess.run([sys.executable, "-c", code], input=source or "",
                          capture_output=True, text=True, timeout=TIMEOUT, check=False,
                          cwd=str(ROOT))


def _pre_fix_source() -> str:
    """The tool before the fix, out of this repository's own history, pinned
    to the site the issue names. A `git show` that cannot run is NO VERDICT,
    which is red -- never a skip that reads like a control that ran."""
    r = subprocess.run(["git", "-C", str(ROOT), "show", f"{PRE_FIX}:{TOOL_REL}"],
                       capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 0, (
        f"NO VERDICT: git show {PRE_FIX}:{TOOL_REL} failed in this clone "
        f"({r.stderr.strip()[:160]}); the handler-free tool cannot be read back")
    lines = r.stdout.splitlines()
    assert lines[272].strip() == 'rx, rsr, rel, _ = rc.load_reference("CP", refdir)', (
        f"{PRE_FIX}:{TOOL_REL} line 273 is not the load_reference site this control replays: "
        f"{lines[272].strip()!r}")
    assert "except refusal.Refused" not in r.stdout, (
        f"{PRE_FIX}:{TOOL_REL} carries the base handler: it is not the pre-fix tool")
    assert "except probe.Refused as e:" in r.stdout, (
        f"{PRE_FIX}:{TOOL_REL} lacks the narrow handler this control exists to replay")
    return r.stdout


def _corpus(tmp_path: pathlib.Path, x, dtype) -> pathlib.Path:
    """A corpus directory holding one CP reference: `x` written as `dtype`, a
    real WAV on disk for the real `run_case.load_reference` to read."""
    refdir = tmp_path / "corpus"
    (refdir / "cp8").mkdir(parents=True)
    wavfile.write(refdir / "cp8" / "CP.WAV", SR, np.asarray(x, dtype=dtype))
    return refdir


def _decay(tau: float, n: int = SR) -> np.ndarray:
    return np.exp(-np.arange(n) / SR / tau)


def _non_finite() -> np.ndarray:
    """A float32 reference with ONE NaN at index 1000: arithmetic on it is
    not audio, whatever else the record holds."""
    x = np.zeros(SR, dtype=np.float32)
    x[:1001] = 0.5 * _decay(0.05)[:1001]
    x[1000] = np.nan
    return x


@pytest.fixture
def seam():
    """The seam in this process, undone afterwards. Nothing here may also be
    monkeypatched by a test: the restore is by assignment, not by undo."""
    saved = probe.basis, ex.render, rc.render_drum_solo
    exec(compile(_SEAM, "<seam>", "exec"), {"v": ex})
    yield
    probe.basis, ex.render, rc.render_drum_solo = saved


def test_the_status_is_a_declared_no_evidence_one_and_the_classes_are_siblings():
    assert exit_contract.REFUSED in exit_contract.NO_EVIDENCE
    assert exit_contract.REFUSED != exit_contract.FAIL
    assert rc.Refused is rc.prep_contract.Refused
    assert issubclass(rc.Refused, refusal.Refused) and issubclass(probe.Refused, refusal.Refused)
    assert not issubclass(rc.Refused, probe.Refused), (
        "rc.Refused under probe.Refused: the narrow handler would have caught it and the defect "
        "this file replays cannot exist")


def test_a_non_finite_reference_exits_refused_2_naming_the_reason(tmp_path):
    refdir = _corpus(tmp_path, _non_finite(), np.float32)
    p = _main(_argv(refdir, tmp_path))
    assert p.returncode == REFUSED, (p.returncode, p.stderr[-600:], p.stdout[-300:])
    lines = p.stdout.splitlines()
    assert lines[-1].startswith(f"REFUSED: {NAN_WAV}"), p.stdout[-600:]
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-600:]
    assert not any(l.startswith("condition") for l in lines), "a refused run printed a table row"
    assert not (tmp_path / "out.json").exists(), "a refused run wrote a record"


def test_a_reference_cut_into_the_strike_is_refused_at_prepare(seam, tmp_path, capsys):
    refdir = _corpus(tmp_path, (20000 * _decay(0.01)).astype(np.int16), np.int16)
    assert ex.main(_argv(refdir, tmp_path)) == REFUSED
    lines = capsys.readouterr().out.splitlines()
    assert lines[-1].startswith(f"REFUSED: {CUT_WAV}"), lines[-1]


def test_the_modules_own_refusal_is_still_rendered_the_same_way(tmp_path):
    why = "corpus at 0deadbeef, pinned 85fbecf"
    patch = (f"def _refusing(refdir, manifest):\n    raise probe.Refused({why!r})\n"
             "probe.basis = _refusing\n")
    p = _main(_argv(tmp_path / "nowhere", tmp_path), patch=patch)
    assert p.returncode == REFUSED, (p.returncode, p.stderr[-600:])
    assert p.stdout.splitlines()[-1] == f"REFUSED: {why}", p.stdout[-600:]
    assert "Traceback" not in p.stderr, p.stderr[-600:]


def test_pre_fix_source_under_the_same_seam_escapes_exit_1(tmp_path):
    """The injected-bug control: with the handler reverted (the pre-fix
    source), the seam the dead-reference control runs must still produce the
    defect -- so that control cannot be green without the handler."""
    refdir = _corpus(tmp_path, _non_finite(), np.float32)
    p = _main(_argv(refdir, tmp_path), source=_pre_fix_source())
    assert p.returncode == FAIL, (p.returncode, p.stderr[-600:])
    assert "Traceback (most recent call last)" in p.stderr, p.stderr[-600:]
    assert f"preparation_contract.Refused: {NAN_WAV}" in p.stderr, p.stderr[-600:]
    assert f"line 273, in main" in p.stderr, p.stderr[-600:]
    assert not any(l.startswith("REFUSED") for l in p.stdout.splitlines()), p.stdout[-600:]


def test_a_plain_error_at_the_same_call_still_exits_1(tmp_path):
    patch = ("def _fault(*a, **k):\n"
             "    raise AssertionError('plain fault at the load_reference call')\n"
             "rc.load_reference = _fault\n")
    p = _main(_argv(tmp_path / "nowhere", tmp_path), patch=patch)
    assert p.returncode == FAIL, (p.returncode, p.stderr[-600:])
    assert "Traceback (most recent call last)" in p.stderr, p.stderr[-600:]
    assert "AssertionError: plain fault at the load_reference call" in p.stderr, p.stderr[-600:]
    assert "REFUSED" not in p.stdout, p.stdout[-600:]


@pytest.mark.parametrize("exc_type", [RuntimeError, AssertionError])
def test_other_errors_propagate_unchanged(seam, monkeypatch, tmp_path, capsys, exc_type):
    fault = exc_type("unrelated fault at the load_reference call")

    def raising(*a, **k):
        raise fault

    monkeypatch.setattr(rc, "load_reference", raising)
    with pytest.raises(exc_type) as caught:
        ex.main(_argv(tmp_path / "nowhere", tmp_path))
    assert caught.value is fault
    assert "REFUSED" not in capsys.readouterr().out


def test_a_well_formed_reference_passes_both_sites(seam, monkeypatch, tmp_path):
    """The two refusals above are about the content they name: an int16 record
    with lead passes `load_reference` and `prepare` alike and the run goes on
    into the pool, which is where this control stops it."""
    class Reached(Exception):
        pass

    def run_set(names, offsets, ref, jobs):
        raise Reached

    monkeypatch.setattr(ex, "run_set", run_set)
    x = np.concatenate([np.zeros(int(0.005 * SR)), 20000 * _decay(0.05)]).astype(np.int16)
    with pytest.raises(Reached):
        ex.main(_argv(_corpus(tmp_path, x, np.int16), tmp_path))


def test_the_census_reads_the_module_covered_by_the_base_handler():
    """`tools/refusal_sibling_scan.py` read every site but `basis` as
    `candidate-escape`, guard `none`, on main. Covered by a BASE handler at
    the CLI boundary is the classification the fix owes: an exact one reads
    `covered(cli-exact)` and covers one family alone."""
    p = subprocess.run([sys.executable, str(SCAN), "--json"], capture_output=True, text=True,
                       timeout=600, check=False, cwd=str(ROOT))
    assert p.returncode == 0, p.stderr[-400:]
    sites = {s["site"]: s for s in json.loads(p.stdout)["sites"]
             if s["site"].startswith(TOOL_REL + ":")}
    at_issue = [s for s in sites.values() if s["callee"] in ("run_case.prepare", "run_case.load_reference")]
    assert len(at_issue) == 4, sites
    assert sites, "the census no longer lists this module at all"
    for site in sites.values():
        assert site["classification"] == "covered(cli-base)" and site["uncovered"] == [], site
