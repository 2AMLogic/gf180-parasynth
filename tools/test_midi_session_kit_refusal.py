#!/usr/bin/env python3
"""Known-answer exit-status controls: a drifted R1 kit or sound table must
reach both MIDI session entry points as REFUSED (exit 2), never as an uncaught
exit-1 traceback (#1614).

THE DEFECT, AS MEASURED ON main @ 0a6b0861 (2026-11-28, red-first run of every
control in this file, before the fix): `fpga/midi_session.py main` builds
`MidiSession(...)` outside the `try` that turns `session.start()`'s
`uart_host.Refused` into `midi_session: REFUSED -- ...`, exit 2. The
constructor reads the image's kit (`uart_host.image_kit`) and its sound table
(`uart_host.image_sound_presets`), and each raises `drums_fx.KitRefused` when
`fpga/release/r1-kit.json` no longer hashes to R1's frozen values. KitRefused
is a RuntimeError, not a Refused, so no handler matched: the refusal left the
process as exit 1 with a traceback -- REFUSED rendered as FAIL, the inversion
tools/exit_contract.py exists to prevent (#804), and the class of #1160,
#1162, #1183 and #1569. `fpga/verify_live_midi.py` has the same construction
in `run_session`, and reads the same file again for its oracle
(`target_kit_and_presets`: `r1_candidate.frozen_kit`, which re-raises as its
own Refused, and `uart_host.image_sound_presets("r1")`).

THE KNOWN ANSWERS, independent of anything this repository models: an exit
status is 0, 1 or 2, and the exit contract says which a refusal is. Every
refusing input below was run by hand against the pre-fix tree first, and each
left exit 1 with the refusal class on the last traceback line
(drums_fx.KitRefused for the kit and for the sound table,
r1_candidate.Refused on the oracle side, uart_host.Refused for a sound table
that leaves a shared circuit in both of its sounds). The REASON each input
must be reported with is read from the raising function itself: the test calls
the constructor's two reads, in its order, on the same file in-process, and
requires the entry point's line to carry exactly that text, so a paraphrase
cannot pass. That in-process call is also what names the raise point: once the
fix lands the child prints no traceback, so which read refused is no longer
visible in its output.

WHAT THE TWO ENTRY POINTS OWE, and what these controls assert:

  CLI, `--image r1`   the kit drift and the sound-table drift each exit
                      `exit_contract.REFUSED` (2) with
                      `midi_session: REFUSED -- <the raiser's own reason>` on
                      stderr, once, no traceback, and no session started
  harness             the same drift at `run_session`'s construction (the
                      default run), and at the oracle's own reads
                      (`--start-red`: the stub session never reads the kit, so
                      there the oracle is first to refuse) -- exit 2, the
                      reason named, no verdict line, no record written, and a
                      prior record removed
  `--image r2`        is sent R1's frozen kit too, so it refuses the same way
  `--image tree`      reads the tree's model, not the frozen file: with the
                      file drifted it still plays (CLI) and still issues a
                      real verdict (harness) -- the handler must not refuse
                      what never read the kit
  unrelated errors    a corrupt kit file (json.JSONDecodeError, a ValueError),
                      a file without its `writes` key (KeyError) and a plain
                      RuntimeError injected at the same read each STILL exit 1
                      as execution errors: the handler names drums_fx.KitRefused
                      and catches neither its base class nor Exception, which
                      would relabel an execution error as a refusal
                      (tools/refusal.py's "inverse defect"). The plain
                      RuntimeError is the input that defeats a handler written
                      against KitRefused's base class (verification rule 8)

THE SAME SHAPE, FOUND WHILE WRITING THESE: `circuit_positions` raises
`uart_host.Refused` from inside the same constructor when the loaded kit
leaves a shared circuit in neither or both of its sounds, and that refusal
escaped the same way (exit 1, measured on the pre-fix tree with the tree's
sound table emptied). It is why the construction's handler catches
`uart_host.Refused` as well as KitRefused, and it has its own control below.

HOW THE ENTRY POINTS ARE DRIVEN: the real file, as `__main__`, in a child
interpreter (the pattern fpga/test_midi_session.py already uses for `--help`),
after ONE seam -- `uart_host.R1_KIT` is pointed at the fixture copy, which is
exactly what fpga/test_named_r1.py monkeypatches in-process. The child stands
in for pyserial's module when the port is `fake`: the CI workflows install
numpy, scipy and pytest only (as of 0a6b0861), and without pyserial
uart_host._require_serial exits 2 before the kit is ever read, so the defect
would be unreachable there. The stand-in is the port object, which
MidiSession.__init__ never touches before the kit read, and never the code
under test. `uart_host.Bridge.on_serial` doing no I/O at construction is what
that rests on. The happy-path control uses the real pyserial against the real
pty simulator and is skipped where pyserial is absent.

A LOADED HOST IS NO VERDICT, not a failure: `--port sim` runs on the wall
clock, and midi_session.py refuses it when the load average exceeds the CPU
count. The CLI controls skip when the CLI itself reports that, so a busy
machine cannot turn them red for a reason that is not the kit's.

WHAT THE CLI HAPPY-PATH CONTROL DOES NOT ASSERT: the exit status. On the real
port it is a wall-clock timing verdict (late events are FAIL, and NO-VERDICT
under load) that belongs to the session, not to this handler; the control
asserts what the handler alone could break -- the session got past the
construction, started, and was not refused.

WHAT THE CONTROLS SEE (a mutation matrix, run 2026-11-28 against the fix as
committed beside this file; verification rule 4 -- what a control is BLIND to
is listed, not only what it caught). Each mutant is one edit to a handler;
both files were restored afterwards and checked by digest:

  CLI       `except Exception`              corrupt file, missing key, injected RuntimeError
            `+ ValueError`                  corrupt file only
            `except RuntimeError` (base)    circuit-in-both, injected RuntimeError
            `dx.KitRefused` alone           circuit-in-both
            `return 1` / reason paraphrased kit, sound table, r2, circuit-in-both
  harness   `except Exception`              corrupt file (both paths), injected RuntimeError
            `refusal.Refused` + `RuntimeError`  injected RuntimeError
            `dx.KitRefused` alone           oracle-side kit drift
            `r1c.Refused` alone             session construction, oracle-side sound table, r2
            prior record left standing      the prior-record control, and nothing else
            `return 1`                      every harness refusal control
            reason paraphrased             every harness refusal control but the prior-record
                                           one, which asserts no reason

Two cells are why this file has the controls it has. The issue's example
controls (ValueError, KeyError) are BLIND to a handler written against
KitRefused's base class -- `except RuntimeError` left both green in each entry
point -- so the plain-RuntimeError control is what sees that mutation. And a
reason paraphrase is caught by every refusal control: an exit code alone is
satisfied by a crashed-and-caught-anything handler.

WRONG-THEN-RIGHT (3, all in the reporting of this run, none in a measurement
the controls rest on). The first read of the red run paired each failure with
the previous one's body (a regex split, off by one section); it was caught by
the count -- nine rows printed for ten failures -- and re-read per section
before anything was quoted. The first base-class mutant for the harness did
not build: it named `refusal.Refused`, and verify_live_midi.py never imports
`refusal`, so the except clause itself raised NameError and six refusal
controls went red. That is a mutant that failed to run, not a control that
fired (verification rule 5, condition 2); the matrix above is from the re-run
with a name that resolves, which turned exactly one. And the first probe of
uart_host.py's own CLI with a drifted kit read exit 0 and was about to be
recorded as "it never reads the frozen file": run as __main__, uart_host.py
is re-executed as a second module object, so the seam applied to the imported
module never reached the code that ran. Its source (image_kit called from
fixture_split) contradicted the reading; re-run through the imported module
it exits 1 with the KitRefused traceback. The two entry points this file
drives import uart_host instead of re-executing it, which is why the seam
reaches them -- their refusals name the drifted file's digest.

COMPARATOR, NOT A REPAIR: fpga/pads_rom.py and fpga/release/r1_candidate.py
already catch KitRefused by name (the issue's own evidence that the narrow
handler works); this fix copies them.
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in ("tools", "fpga", "fpga/release", "model"):
    if str(ROOT / _p) not in sys.path:
        sys.path.insert(0, str(ROOT / _p))

import exit_contract                                     # noqa: E402
import drums_fx as dx                                    # noqa: E402
import uart_host as uh                                   # noqa: E402
import r1_candidate as r1c                               # noqa: E402
import refusal                                           # noqa: E402

MIDI_SESSION = ROOT / "fpga/midi_session.py"
LIVE_MIDI = ROOT / "fpga/verify_live_midi.py"
DRIFT = ("kit", "presets")
CLI_ARGS = ("--port", "sim", "--midi-in", "scripted:coverage")

# The child: apply the one seam (and, for the controls, one named injection),
# then run the real file as __main__ with the caller's arguments.
_CHILD = """\
import runpy, sys, types
from pathlib import Path
script, kit, port, inject, *args = sys.argv[1:]
sys.path.insert(0, str(Path(script).resolve().parent))
import uart_host as uh
if kit != "-":
    uh.R1_KIT = Path(kit)
if inject == "tree-presets-empty":
    import drums_fx as dx
    dx.preset_writes = lambda _sound: []
elif inject == "image-kit-runtime-error":
    def _raise(_image):
        raise RuntimeError("injected at the kit read: an execution error, not a refusal")
    uh.image_kit = _raise
if port == "fake":
    serial = types.ModuleType("serial")
    serial.Serial = lambda *_a, **_k: object()
    serial.SerialException = type("SerialException", (OSError,), {})
    sys.modules["serial"] = serial
sys.argv = [script, *args]
runpy.run_path(script, run_name="__main__")
"""


# ---------------------------------------------------------------------------
# fixtures: copies of R1's frozen kit file, each with ONE thing moved
# ---------------------------------------------------------------------------
def _drifted(tmp_path: pathlib.Path, what: str) -> pathlib.Path:
    """`kit`: one kit write flipped, which uart_host.r1_kit refuses by digest.
    `presets`: the first position write of one sound, which r1_kit never reads
    (the kit still hashes to R1's digest) and uart_host.image_sound_presets
    refuses by digest."""
    rec = json.loads(uh.R1_KIT.read_text())
    if what == "kit":
        a, v = rec["writes"][0]
        rec["writes"][0] = [a, v ^ 1]
    else:
        sound = next(s for s in sorted(rec["presets"]) if rec["presets"][s])
        a, v = rec["presets"][sound][0]
        rec["presets"][sound][0] = [a, v ^ 1]
    p = tmp_path / "r1-kit.json"
    p.write_text(json.dumps(rec))
    return p


def _corrupt(tmp_path: pathlib.Path) -> pathlib.Path:
    p = tmp_path / "r1-kit.json"
    p.write_bytes(b"this is not json at all, just text")
    return p


def _without_writes(tmp_path: pathlib.Path) -> pathlib.Path:
    p = tmp_path / "r1-kit.json"
    p.write_text(json.dumps({"presets": {}}))
    return p


# ---------------------------------------------------------------------------
# the two entry points, as a shell would run them
# ---------------------------------------------------------------------------
def _run(script: pathlib.Path, args, *, kit=None, port="fake", inject="none"):
    return subprocess.run(
        [sys.executable, "-c", _CHILD, str(script), str(kit or "-"), port, inject, *args],
        capture_output=True, text=True, timeout=300, cwd=str(ROOT))


def _cli(*args, port="fake", **kw):
    p = _run(MIDI_SESSION, args, port=port, **kw)
    loaded = "midi_session: REFUSED -- load average" in p.stderr \
        or "midi_session: NO-VERDICT" in p.stderr
    if loaded:
        pytest.skip("NO VERDICT: midi_session refused --port sim because this host is loaded")
    return p


def _assert_refused(p, stream: str) -> None:
    """Exit 2; REFUSED on one stream, once; no traceback anywhere."""
    said = p.stderr if stream == "stderr" else p.stdout
    other = p.stdout if stream == "stderr" else p.stderr
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stdout[-600:], p.stderr[-600:])
    assert "REFUSED" in said and said.count("REFUSED") == 1, said[-600:]
    assert "REFUSED" not in other, other[-600:]
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-600:]


def _assert_execution_error(p, exc: str) -> None:
    assert p.returncode == 1, (p.returncode, p.stdout[-600:], p.stderr[-600:])
    assert "Traceback (most recent call last):" in p.stderr and exc in p.stderr, p.stderr[-600:]
    assert "REFUSED" not in p.stdout and "REFUSED" not in p.stderr, \
        "an execution error must not be relabelled a refusal"


# ---------------------------------------------------------------------------
# the known answers, read from the raising functions on the same file
# ---------------------------------------------------------------------------
def _raiser_reason(monkeypatch, kit_file: pathlib.Path, what: str) -> str:
    """MidiSession.__init__'s two reads, in its order, on the fixture: which one
    refuses, and the reason it gives."""
    monkeypatch.setattr(uh, "R1_KIT", kit_file)
    if what == "kit":
        with pytest.raises(dx.KitRefused, match="not R1's frozen kit") as e:
            uh.image_kit("r1")
        return str(e.value)
    assert dx._kit_sha256(uh.image_kit("r1")) == uh.R1_KIT_SHA256, "the kit read must PASS"
    with pytest.raises(dx.KitRefused, match="not R1's frozen sound presets") as e:
        uh.image_sound_presets("r1")
    return str(e.value)


def _oracle_reason(monkeypatch, kit_file: pathlib.Path, what: str) -> str:
    """The oracle's own reads (target_kit_and_presets): r1_candidate.frozen_kit
    first, then the sound table."""
    monkeypatch.setattr(uh, "R1_KIT", kit_file)
    if what == "kit":
        with pytest.raises(r1c.Refused, match="not R1's frozen kit") as e:
            r1c.frozen_kit()
        return str(e.value)
    assert len(r1c.frozen_kit()) == r1c.KIT_R14_WRITES, "the kit read must PASS"
    with pytest.raises(dx.KitRefused, match="not R1's frozen sound presets") as e:
        uh.image_sound_presets("r1")
    return str(e.value)


# ---------------------------------------------------------------------------
# the class relations the fix rests on
# ---------------------------------------------------------------------------
def test_kit_refusals_are_not_refused_by_class():
    """Why `except uh.Refused` at the construction could not have been enough,
    and why the handler names each class rather than a base."""
    assert issubclass(dx.KitRefused, RuntimeError)
    assert not issubclass(dx.KitRefused, refusal.Refused)
    for klass in (uh.Refused, r1c.Refused):
        assert issubclass(klass, refusal.Refused), klass


# ---------------------------------------------------------------------------
# CLI: the drift must REFUSE by name, not traceback
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("what", DRIFT)
def test_cli_drifted_r1_kit_or_sound_table_exits_refused_2(what, tmp_path, monkeypatch):
    kit = _drifted(tmp_path, what)
    reason = _raiser_reason(monkeypatch, kit, what)
    p = _cli(*CLI_ARGS, "--image", "r1", kit=kit)
    _assert_refused(p, "stderr")
    assert f"midi_session: REFUSED -- {reason}" in p.stderr, p.stderr[-600:]
    assert "r1-kit.json" in reason, reason
    assert "playing --" not in p.stdout, "a refused run must not start a session"


def test_cli_r2_is_sent_r1s_frozen_kit_and_refuses_the_same_way(tmp_path, monkeypatch):
    kit = _drifted(tmp_path, "kit")
    reason = _raiser_reason(monkeypatch, kit, "kit")
    p = _cli(*CLI_ARGS, "--image", "r2", kit=kit)
    _assert_refused(p, "stderr")
    assert f"midi_session: REFUSED -- {reason}" in p.stderr, p.stderr[-600:]


def test_cli_a_sound_table_that_leaves_a_circuit_in_both_sounds_refuses_2(tmp_path):
    """uart_host.Refused from circuit_positions, inside the same constructor: a
    host that does not know its circuits' positions cannot switch them."""
    p = _cli(*CLI_ARGS, inject="tree-presets-empty")
    _assert_refused(p, "stderr")
    assert re.search(r"midi_session: REFUSED -- the loaded kit puts the \w+ circuit in "
                     r"\['\w+', '\w+'\] of \w+/\w+", p.stderr), p.stderr[-600:]


@pytest.mark.parametrize("image,drifted", [("r1", False), ("r2", False), ("tree", True)])
def test_cli_still_plays_where_no_frozen_kit_read_refuses(image, drifted, tmp_path):
    """The successful path through the new handler, on the real pyserial port
    against the real pty simulator: a clean r1 and r2 session play, and so does
    the tree image with R1's file drifted, which it never reads."""
    pytest.importorskip("serial")
    kit = _drifted(tmp_path, "kit") if drifted else None
    p = _cli(*CLI_ARGS, "--image", image, "--duration", "1", kit=kit, port="real")
    assert "midi_session: playing --" in p.stdout, (p.stdout[-600:], p.stderr[-600:])
    assert "REFUSED" not in p.stderr and "Traceback" not in p.stderr, p.stderr[-600:]


@pytest.mark.parametrize("label,make,inject,exc", [
    ("a_corrupt_kit_file", _corrupt, "none", "json.decoder.JSONDecodeError"),
    ("a_runtime_error_injected_at_the_kit_read", None, "image-kit-runtime-error",
     "RuntimeError: injected at the kit read"),
])
def test_cli_unrelated_errors_at_the_same_kit_read_still_exit_1(label, make, inject, exc, tmp_path):
    kit = make(tmp_path) if make else None
    p = _cli(*CLI_ARGS, "--image", "r1", kit=kit, inject=inject)
    _assert_execution_error(p, exc)


def test_cli_a_kit_file_without_its_writes_key_refuses_2(tmp_path):
    """#1621: this input was the `KeyError: 'writes'` row of the control above.
    uart_host.r1_kit read `rec["writes"]` before the digest check that refuses
    a drifted kit, so a partial kit file left as an execution error. The read
    is now guarded by the function's own declared refusal (KitRefused), which
    that handler names: a kit that cannot be read is REFUSED like a kit that
    does not hash, and the refusal names the key."""
    p = _cli(*CLI_ARGS, "--image", "r1", kit=_without_writes(tmp_path))
    _assert_refused(p, "stderr")
    assert "midi_session: REFUSED --" in p.stderr and "'writes'" in p.stderr, p.stderr[-600:]
    assert "playing --" not in p.stdout, "a refused run must not start a session"


# ---------------------------------------------------------------------------
# fpga/verify_live_midi.py: the session under test, then its oracle
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("what", DRIFT)
def test_harness_drifted_r1_kit_or_sound_table_refuses_2(what, tmp_path, monkeypatch):
    kit = _drifted(tmp_path, what)
    reason = _raiser_reason(monkeypatch, kit, what)
    out = tmp_path / "out"
    p = _run(LIVE_MIDI, ["--outdir", str(out)], kit=kit)
    _assert_refused(p, "stdout")
    assert f"live-midi: REFUSED -- {reason}" in p.stdout, p.stdout[-600:]
    assert "T-LIVE-MIDI:" not in p.stdout, "a refused run must not issue a verdict"
    assert not (out / "verification.json").exists()


def test_harness_refused_run_removes_a_prior_record(tmp_path, monkeypatch):
    """A PASS left by an earlier run must not stand in for this run's refusal:
    tools/r1_harvest.py lists T-LIVE-MIDI among the trials it harvests, and this
    is the rule fpga/test_verifier_summary_exit.py pins on the sibling verifiers."""
    kit = _drifted(tmp_path, "kit")
    out = tmp_path / "out"
    out.mkdir()
    (out / "verification.json").write_text(json.dumps({"verdict": "PASS"}))
    p = _run(LIVE_MIDI, ["--outdir", str(out)], kit=kit)
    _assert_refused(p, "stdout")
    assert not (out / "verification.json").exists()


@pytest.mark.parametrize("what", DRIFT)
def test_harness_oracle_side_reads_refuse_2(what, tmp_path, monkeypatch):
    """--start-red: the stub session never reads the kit, so target_kit_and_presets
    is first to refuse."""
    kit = _drifted(tmp_path, what)
    reason = _oracle_reason(monkeypatch, kit, what)
    out = tmp_path / "start-red"
    p = _run(LIVE_MIDI, ["--outdir", str(out), "--start-red"], kit=kit)
    _assert_refused(p, "stdout")
    assert f"live-midi: REFUSED -- {reason}" in p.stdout, p.stdout[-600:]
    assert "T-LIVE-MIDI:" not in p.stdout
    assert not (out / "start-red.json").exists()


def test_harness_r2_is_sent_r1s_frozen_kit_and_refuses_the_same_way(tmp_path, monkeypatch):
    kit = _drifted(tmp_path, "kit")
    reason = _raiser_reason(monkeypatch, kit, "kit")
    out = tmp_path / "out"
    p = _run(LIVE_MIDI, ["--outdir", str(out), "--image", "r2", "--control", "DROP_NOTE_OFF"],
             kit=kit)
    _assert_refused(p, "stdout")
    assert f"live-midi: REFUSED -- {reason}" in p.stdout, p.stdout[-600:]


def test_harness_tree_image_never_reads_the_frozen_kit_and_still_issues_a_verdict(
        tmp_path, monkeypatch):
    kit = _drifted(tmp_path, "kit")
    out = tmp_path / "out"
    p = _run(LIVE_MIDI, ["--outdir", str(out), "--image", "tree", "--control", "DROP_NOTE_OFF",
                         "--expect-fail"], kit=kit)
    assert p.returncode == 0, (p.returncode, p.stdout[-600:], p.stderr[-600:])
    assert "live-midi control DROP_NOTE_OFF: CAUGHT" in p.stdout, p.stdout[-600:]
    assert "REFUSED" not in p.stdout and "REFUSED" not in p.stderr
    rec = json.loads((out / "tree" / "verification.json").read_text())
    assert rec["verdict"] == "FAIL" and rec["control"]["caught"] is True


@pytest.mark.parametrize("flag", ["", "--start-red"], ids=["session-construction", "oracle-reads"])
def test_harness_corrupt_kit_file_is_an_execution_error_not_a_refusal(flag, tmp_path):
    kit = _corrupt(tmp_path)
    p = _run(LIVE_MIDI, ["--outdir", str(tmp_path / "out")] + ([flag] if flag else []), kit=kit)
    _assert_execution_error(p, "json.decoder.JSONDecodeError")
    assert "T-LIVE-MIDI:" not in p.stdout


def test_harness_a_runtime_error_at_the_kit_read_still_exits_1(tmp_path):
    p = _run(LIVE_MIDI, ["--outdir", str(tmp_path / "out")], inject="image-kit-runtime-error")
    _assert_execution_error(p, "RuntimeError: injected at the kit read")
