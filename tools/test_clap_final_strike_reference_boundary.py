#!/usr/bin/env python3
"""Known-answer exit-status controls for the CP final-strike experiment's
reference boundary -- the trace of one row of the #1782 estimator-refusal
census (#1887).

UPDATE AT MERGE: the handler gap this file was written to trace was closed on
main by #2046 (`main()` now catches `refusal.Refused`; its controls live in
`tools/test_clap_final_strike_refusal.py`). The strict xfail below was removed
as its own marker instructed ("when a handler lands, remove the marker"); the
dispatch control now passes. The text that follows is the measurement as made
BEFORE #2046, kept as the record: at that time NO HANDLER CHANGED and on
`origin/main` every injection that got past the corpus check was stopped, exit
2, by an earlier gate that no input controls (read at `560a1580`, the same at
`8f3d8ed2`).

THE ROW. `tools/estimator_refusal_census.py` lists
`tools/clap_final_strike_experiment.py`: `run_case.prepare` and
`run_case.load_reference` call `audio_measure.require_finite`, and the only
refusal handler the module defines names `probe.Refused` (`main`). An estimator
refusal would pass every handler it defines. What that refusal is, by run:

  cp8/CP.WAV digitally silent    preparation_contract.Refused "reference
                                 recording cp8/CP.WAV is silent"
                                 (`load_reference`)
  cp8/CP.WAV float32, all NaN    preparation_contract.Refused "... holds 4410
                                 non-finite samples ..." (`load_reference`)
  cp8/CP.WAV cut into the strike preparation_contract.Refused "the recording
                                 begins at or above 2 % of its own peak"
                                 (`prepare`)

THE CENSUS LABEL IS A MISLABEL, which is how the other twenty `via run_case`
rows of that census should be read: `NonFiniteAudio` never leaves either
function. Both catch it and re-raise `Refused`, which is
`preparation_contract.Refused` -- `run_case.Refused` -- a SIBLING of
`probe.Refused` on `refusal.Refused` (the #1162 shape of
`tools/test_rs_guard_band_refusal.py`). The class a handler would need is
`rc.Refused`; `NonFiniteAudio` survives only as the `__cause__`.

WHAT STOPPED IT -- not `probe.basis`. The corpus check is passable: a manifest
that hashes the defective `cp8/CP.WAV` and a checkout whose HEAD begins with
the pin (both built below; `test_the_corpus_check_passes_the_fixture` pins
it). Of the injections here it stops only an absent listed file and an absent
root. Every injection that passes it is stopped instead by the renderer
precondition in `main` -- the two `render({}, ...)` calls against
`render_drum_solo('CP')` -- which refuses EVERY run on this tree:

    REFUSED: the experiment renderer does not reproduce render_drum_solo('CP') bit for bit

because the shipped kit's CP envelope is now the L2 condition the experiment
selected (`drums_fx.CP_BURSTS` 3, `CP_PERIOD` 511, `CP_FINAL_TAU` 20 ms,
`CP_TAIL_TAU` 80 ms; 5124f48d), while the experiment's B0 renders
`clap_d12a_probe.DEFAULT` -- the pre-L2 envelope (bursts 2, period 480, tail
tau 47 ms, no final strike). That gate reads no input, so from the
CLI `load_reference` is never called and the flagged sites are unreachable.
Observed on the unmodified tree, each through the real CLI with the real
environment (a corpus root named by both of `run_case`'s variables):

    injection                            corpus check   exit   stopped by
    cp8/CP.WAV silent                    passes          2    renderer precondition
    cp8/CP.WAV float32 NaN               passes          2    renderer precondition
    cp8/CP.WAV cut into the strike       passes          2    renderer precondition
    cp8/CP.WAV text scipy cannot read    passes          2    renderer precondition
    cp8/CP.WAV absent, listed            REFUSED         2    the manifest (probe.Refused)
    corpus root absent                   REFUSED         2    manifest missing

No traceback and no `--out` record in any row. The text-file row matters most:
an EXECUTION ERROR is masked the same way, so no exit-1 control of any kind can
be observed through the CLI today. The handler gap is real and masked; `main`
is left as it is, as the issue directs for a candidate no input reaches.

WHAT THESE CONTROLS ASSERT, and what turns each red:

  CLI, silent CP       exit `exit_contract.REFUSED` (2), `REFUSED` on stdout,
                       no traceback, no record, and NOT a corpus-check reason:
                       the run got past `probe.basis`. This is the tripwire --
                       re-baselining the renderer on the shipped kit without a
                       handler for `rc.Refused` makes it exit 1 and turns red.
  CLI, absent root     the corpus check's own two refusals, exit 2, preserved.
       / absent file
  in process           the three defective recordings leave the loader as
                       `rc.Refused` and never as `NonFiniteAudio`; the two
                       `Refused` classes are siblings on the base; the fixture
                       satisfies the corpus check.
  dispatch, renderer   the renderer precondition is STUBBED to hold, because on
  STUBBED to hold      main it does not: these are statements about `main`'s
                       dispatch, not about what the CLI reaches today. A plain
                       `AssertionError` at the `load_reference` boundary
                       propagates -- never REFUSED (red under any
                       `except Exception` / `AssertionError`). The contract
                       itself is xfail(strict, raises=rc.Refused): each
                       defective recording must one day leave `main` as
                       REFUSED; when a handler lands, the strict xfail turns
                       red and its marker is removed.

WRONG THEN RIGHT (fixture, caught by `git` rather than by a control): the
first mined commit carried raw nonce bytes and `git hash-object` refused it
(`nulInCommit`); the nonce is hex. `hash-object` takes the object's CONTENT and
prepends `commit <len>\\0` itself.
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import subprocess
import sys

import numpy as np
import pytest
from scipy.io import wavfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))

import audio_measure as am                     # noqa: E402
import clap_d12a_probe as probe                # noqa: E402
import clap_final_strike_experiment as ex      # noqa: E402
import exit_contract                           # noqa: E402
import preparation_contract                    # noqa: E402
import refusal                                 # noqa: E402
import run_case as rc                          # noqa: E402

TOOL = ROOT / "tools" / "clap_final_strike_experiment.py"
SR = 44100
FRAMES = 4410                 # 100 ms: the rs_guard_band fixture spec
REL = "cp8/CP.WAV"            # drum_verify.REF_MAIN["CP"]; asserted below
MANIFEST_NAME = "tr808-fischer-85fbecf.sha256"    # clap_d12a_probe's pinned name
#: the three reasons `probe.basis` can give; none means the run got past it
CORPUS_REASONS = ("reference manifest missing", "reference files differ from", ", pinned ")

#: A commit whose id begins with the corpus pin. `verify_manifest` reads
#: `git -C <refs> rev-parse HEAD` and `basis` requires `PINNED_COMMIT` as a
#: prefix; the real corpus commit is not in this repository and a unit test
#: must not clone it. Mined once (28 bits, 34 s on two cores).
MINED_COMMIT = (b"tree 4b825dc642cb6eb9a060e54bf8d69288fbee4904\n"
                b"author Fixture <fixture@example.invalid> 0 +0000\n"
                b"committer Fixture <fixture@example.invalid> 0 +0000\n"
                b"\n"
                b"corpus fixture nonce 000000000662eba0\n")
MINED_OID = "85fbecf5b92372d8883dc0c2f6221b53f9957f28"


def _git(*argv: str, **kw) -> str:
    return subprocess.run(["git", *argv], check=True, capture_output=True, **kw).stdout.decode().strip()


def _write_recording(path: pathlib.Path, kind: str) -> None:
    if kind == "absent":
        return
    if kind == "silent":
        wavfile.write(str(path), SR, np.zeros(FRAMES, dtype="<i2"))
    elif kind == "nan":
        wavfile.write(str(path), SR, np.full(FRAMES, np.nan, dtype=np.float32))
    elif kind == "cut":
        # a strike captured from its first sample: sample 0 IS the peak
        t = np.arange(int(0.3 * SR)) / SR
        x = np.round(20000 * np.exp(-t / 0.05) * np.cos(2 * np.pi * 800 * t)).astype("<i2")
        wavfile.write(str(path), SR, x)
    else:
        raise ValueError(kind)


def _corpus(home: pathlib.Path, kind: str) -> tuple[pathlib.Path, pathlib.Path]:
    """A corpus root holding one `cp8/CP.WAV` of `kind`, a manifest that
    hashes it, and a HEAD at the pin. Returns (refdir, the default manifest).

    `home` stands in for `$HOME` so that `clap_d12a_probe.MANIFEST_DEFAULT`
    resolves INSIDE the fixture on every host: on a box with `~/dev/refs`
    present the module would otherwise read the real manifest and refuse
    every row for a reason of its own. The module's fallback location, next
    to the corpus root, holds the same bytes."""
    refdir = home / "refs" / "corpus"
    cp = refdir / REL
    cp.parent.mkdir(parents=True)
    _write_recording(cp, kind)
    digest = hashlib.sha256(cp.read_bytes()).hexdigest() if cp.exists() else "0" * 64
    text = f"{digest}  ./{REL}\n"
    manifest = home / "dev" / "refs" / MANIFEST_NAME
    manifest.parent.mkdir(parents=True)
    manifest.write_text(text)
    (refdir.parent / MANIFEST_NAME).write_text(text)
    _pin(refdir)
    return refdir, manifest


def _pin(refdir: pathlib.Path) -> None:
    """A checkout at `refdir` whose HEAD resolves to MINED_OID, asserted at
    the point of use: the fixture must satisfy the module's pin as it stands,
    and `rev-parse HEAD` -- what the module reads -- must return it."""
    _git("init", "-q", str(refdir))
    oid = _git("-C", str(refdir), "hash-object", "-w", "-t", "commit", "--stdin", input=MINED_COMMIT)
    _git("-C", str(refdir), "symbolic-ref", "HEAD", "refs/heads/corpus")
    _git("-C", str(refdir), "update-ref", "refs/heads/corpus", oid)
    assert oid == MINED_OID, oid
    assert MINED_OID.startswith(probe.PINNED_COMMIT), (MINED_OID, probe.PINNED_COMMIT)
    assert _git("-C", str(refdir), "rev-parse", "HEAD") == MINED_OID


def _cli(refs: pathlib.Path, home: pathlib.Path) -> tuple[subprocess.CompletedProcess, pathlib.Path]:
    """The real interpreter and the real environment a batch runner sees: both
    of `run_case`'s corpus variables name the root (their NAMES come from
    run_case), and `$HOME` is the fixture's. Returns the run and the `--out`
    path the run was told to write."""
    drop = (rc.REFS_ENV, rc.REFS_ALIAS_ENV)
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env[rc.REFS_ENV] = env[rc.REFS_ALIAS_ENV] = str(refs)
    env["HOME"] = str(home)
    out = home / "final-strike.json"
    # Asserted at the point of use: with a FILE as `home`, `out.exists()` is
    # False whatever the child did, so every "no record written" check below
    # would be unfalsifiable.
    assert home.is_dir() and out.parent.is_dir(), ("`home` must be a directory", home)
    p = subprocess.run(
        [sys.executable, str(TOOL), "--refs", str(refs), "--out", str(out), "--jobs", "1"],
        capture_output=True, text=True, timeout=300, env=env, cwd=str(ROOT))
    return p, out


# ---------------------------------------------------------------------------
# the class relations the trace rests on, and the fixture's own preconditions
# ---------------------------------------------------------------------------
def test_the_two_refused_classes_are_siblings_on_the_base():
    assert rc.Refused is preparation_contract.Refused
    assert probe.Refused is not rc.Refused
    for a, b in ((probe.Refused, rc.Refused), (rc.Refused, probe.Refused)):
        assert not issubclass(a, b), (a, b)
    for klass in (probe.Refused, rc.Refused):
        assert issubclass(klass, refusal.Refused), klass
        assert not issubclass(klass, am.InsufficientEvidence), klass


def test_the_fixture_spells_the_modules_own_paths():
    assert rc.REF_MAIN["CP"][0] == REL
    assert probe.MANIFEST_DEFAULT.name == MANIFEST_NAME
    assert probe.MANIFEST_DEFAULT.relative_to(pathlib.Path.home()) == pathlib.Path("dev") / "refs" / MANIFEST_NAME


# ---------------------------------------------------------------------------
# in process: what the loader raises, and what the corpus check does with it
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("kind, boundary, words, cause", [
    ("silent", "load_reference", "is silent", None),
    ("nan", "load_reference", "non-finite samples", am.NonFiniteAudio),
    ("cut", "prepare", "begins at or above 2 % of its own peak", None),
])
def test_defective_recording_leaves_the_loader_as_run_case_refused(tmp_path, kind, boundary, words, cause):
    refdir, _ = _corpus(tmp_path, kind)
    loaded = []
    with pytest.raises(rc.Refused) as ei:
        x, sr, rel, _ = rc.load_reference("CP", refdir)
        loaded.append(rel)
        rc.prepare(x, sr)
    assert loaded == ([REL] if boundary == "prepare" else []), boundary
    assert not isinstance(ei.value, am.NonFiniteAudio), "the census's class must not be what leaves"
    assert words in str(ei.value)
    if cause:
        assert isinstance(ei.value.__cause__, cause), "the census's class is the CAUSE, not what leaves"
    else:
        assert ei.value.__cause__ is None


def test_the_corpus_check_passes_the_fixture(tmp_path):
    refdir, manifest = _corpus(tmp_path, "silent")
    r = probe.basis(refdir, manifest)["reference"]
    assert r["files_checked"] == 1 and r["files_mismatched"] == 0
    assert r["corpus_git_head"] == MINED_OID


def test_the_corpus_check_stops_an_absent_listed_file(tmp_path):
    refdir, manifest = _corpus(tmp_path, "absent")
    with pytest.raises(probe.Refused) as ei:
        probe.basis(refdir, manifest)
    assert "1 of 1 reference files differ from" in str(ei.value)


# ---------------------------------------------------------------------------
# the CLI: every injection is REFUSED (2), with no traceback and no record
# ---------------------------------------------------------------------------
def test_cli_silent_reference_exits_refused_2_past_the_corpus_check(tmp_path):
    """The tripwire. On main @ 8f3d8ed2 the reason printed is the renderer
    precondition's ("does not reproduce render_drum_solo('CP') bit for
    bit"); it is deliberately not pinned, so that re-baselining the renderer
    on the shipped kit together with an `rc.Refused` handler stays green,
    while the re-baseline alone (exit 1, a `preparation_contract.Refused`
    traceback) turns this red."""
    refdir, _ = _corpus(tmp_path, "silent")
    p, out = _cli(refdir, tmp_path)
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stderr[-400:])
    assert "REFUSED" in p.stdout, p.stdout[-400:]
    assert not any(r in p.stdout for r in CORPUS_REASONS), ("stopped by the corpus check", p.stdout[-400:])
    # Either legitimate stop past the corpus check -- the renderer precondition
    # or the loader's silent-CP refusal -- and no other: an unrelated REFUSED
    # must turn this red.
    assert ("does not reproduce render_drum_solo('CP') bit for bit" in p.stdout
            or "is silent" in p.stdout), p.stdout[-400:]
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-400:]
    assert "condition" not in p.stdout, "a refused run must not start its table"
    assert not out.exists(), "a refused run must not write a record"


def test_cli_absent_corpus_root_keeps_its_refused_2(tmp_path):
    p, out = _cli(tmp_path / "no-such-refs", tmp_path)
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stderr[-400:])
    assert "REFUSED" in p.stdout and "Traceback" not in p.stderr, (p.stdout[-400:], p.stderr[-400:])
    assert "reference manifest missing" in p.stdout, p.stdout[-400:]
    assert not out.exists()


def test_cli_absent_listed_file_is_stopped_by_the_manifest(tmp_path):
    refdir, _ = _corpus(tmp_path, "absent")
    p, out = _cli(refdir, tmp_path)
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stderr[-400:])
    assert "REFUSED" in p.stdout and "1 of 1 reference files differ from" in p.stdout, p.stdout[-400:]
    assert "Traceback" not in p.stderr and not out.exists()


# ---------------------------------------------------------------------------
# the dispatch with the renderer precondition STUBBED to hold
# ---------------------------------------------------------------------------
def _renderer_precondition_holds(monkeypatch) -> None:
    """`main` hashes `render({}, ...)` twice against `render_drum_solo('CP')`;
    one constant on both sides makes that check pass without a render."""
    x = np.linspace(-0.5, 0.5, 64, dtype=np.float64)
    monkeypatch.setattr(ex, "render", lambda *_, **__: (x, SR, [], None))
    monkeypatch.setattr(rc, "render_drum_solo", lambda *_, **__: (x, SR))


def _argv(refdir: pathlib.Path, home: pathlib.Path) -> list[str]:
    return ["--refs", str(refdir), "--out", str(home / "final-strike.json"), "--jobs", "1"]


def test_the_stubbed_dispatch_reaches_the_loader(tmp_path, monkeypatch):
    """The apparatus of the three tests below: with the renderer stubbed and
    the default manifest pointed at the fixture, `main` gets past the corpus
    check AND the renderer precondition and calls `rc.load_reference`."""
    _renderer_precondition_holds(monkeypatch)
    refdir, manifest = _corpus(tmp_path, "silent")
    monkeypatch.setattr(probe, "MANIFEST_DEFAULT", manifest)

    def reached(*_, **__):
        raise RuntimeError("reached run_case.load_reference")

    monkeypatch.setattr(rc, "load_reference", reached)
    with pytest.raises(RuntimeError, match="reached run_case.load_reference"):
        ex.main(_argv(refdir, tmp_path))


@pytest.mark.parametrize("kind", ["silent", "nan", "cut"])
def test_dispatch_refuses_a_defective_reference_when_the_renderer_precondition_holds(tmp_path, monkeypatch, kind):
    _renderer_precondition_holds(monkeypatch)
    refdir, manifest = _corpus(tmp_path, kind)
    monkeypatch.setattr(probe, "MANIFEST_DEFAULT", manifest)
    assert ex.main(_argv(refdir, tmp_path)) == exit_contract.REFUSED
    assert not (tmp_path / "final-strike.json").exists()


def test_a_plain_assertion_error_at_the_loader_boundary_is_never_a_refusal(tmp_path, monkeypatch):
    """The inverse defect (tools/refusal.py): whatever handler is one day
    added for `rc.Refused` must not take an `AssertionError` with it."""
    _renderer_precondition_holds(monkeypatch)
    refdir, manifest = _corpus(tmp_path, "silent")
    monkeypatch.setattr(probe, "MANIFEST_DEFAULT", manifest)

    def injected(*_, **__):
        raise AssertionError("injected at the run_case.load_reference boundary")

    monkeypatch.setattr(rc, "load_reference", injected)
    with pytest.raises(AssertionError, match="injected at the run_case.load_reference boundary"):
        ex.main(_argv(refdir, tmp_path))
