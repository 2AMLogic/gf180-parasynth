#!/usr/bin/env python3
"""The #1576 trace plan: every `candidate-escape` row the four-root scanner
added, each run through its caller.

`tools/refusal_sibling_scan.py` at `origin/main` `ebba993d` reports 90
`candidate-escape` rows: 53 that #1183's census already held, and the 37 this
file traces -- 23 in `tools/` and `model/` that a function-local import had
kept out of every earlier census, and 14 in `fpga/`, a root #1573 added. Each
entry names the site, the callee, a WORKING input and a KNOWN REFUSING input
for the entry point and, for a raise the caller's inputs cannot reach, the
boundary that excludes it. What each run then did is in
`docs/verification/refusal-candidate-trace.json`; this file only says what to
run. `expect` is what reading the caller predicts, kept beside the verdict so
a wrong prediction stays visible.

THREE KINDS OF REFUSING INPUT, and which is which matters:

  * a file the entry point reads by name, made refusing -- a corpus WAV cut
    into the strike, which `run_case.prepare` must refuse by its own contract;
  * a FRESH CLONE of the traced revision with one committed file altered --
    never the working tree -- for the callers whose refusing precondition is
    the state of the tree itself (a kit that no longer hashes to R1's digest,
    an XDC with an unstated query). A fresh clone is also, untouched, the
    known refusing input for `f1_rtl_filter_path.py`: the frozen reference
    audio is not committed, so a checkout that has not restored it has none;
  * PR #1574's control shape -- `uart_host.main` returns 1 -- where the only
    file-level input that makes a supported CLI command refuse (the kit) is
    refused earlier by the same derivation (`r1_candidate.commands` runs only
    after `kit_identity` has accepted the kit).

A boundary script calls the callee directly with the arguments that reach its
refusing arm, and with the arguments the caller actually passes. It shows the
arm is live and what it raises; on its own it establishes nothing about the
caller, which is why every such row also carries the entry-point run in which
the call at the site executed and returned.

Prerequisites, each REFUSED as an `unresolved` row rather than skipped: the
Fischer corpus (`GF180_TR808_REFS`, else `/tmp/tr808-ref`), pyserial for the
simulated UART session, and -- for the WORKING run of f1_rtl_filter_path only
-- the restored frozen reference audio (`tools/refprofile_restore.py`).
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import tempfile

import numpy as np
from scipy.io import wavfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
TRACED = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                        capture_output=True, text=True).stdout.strip()
_common = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--git-common-dir"],
                         capture_output=True, text=True).stdout.strip()
REPO = pathlib.Path(_common) if pathlib.Path(_common).is_absolute() else (ROOT / _common).resolve()

TOOLS_PATH = [str(ROOT / p) for p in ("tools", "model", "tools/probes", "audition")]
FPGA_PATH = [str(ROOT / p) for p in ("fpga", "fpga/release", "model", "tools", "audition", "rtl-sketch")]
MODEL_PATH = [str(ROOT / p) for p in ("model", "tools", "audition")]
ENV_REFS = "GF180_TR808_REFS"
#: where the private corpora and clones live; the runner points this at its
#: scratch dir, so a fixture is made once per session however many rows use it
SCRATCH = pathlib.Path(os.environ.get("REFUSAL_TRACE_SCRATCH") or tempfile.gettempdir())


def set_scratch(path) -> None:
    global SCRATCH
    SCRATCH = pathlib.Path(path)


# ---------------------------------------------------------------------------
# inputs
# ---------------------------------------------------------------------------
def _refs() -> pathlib.Path | None:
    r = pathlib.Path(os.environ.get(ENV_REFS) or "/tmp/tr808-ref")
    return r if (r / "bd8" / "BD5050.WAV").exists() else None


def struck(sr: int = 44100) -> tuple[int, np.ndarray]:
    """A decaying 80 Hz sine that begins AT its own peak: cut into the
    strike, with 0 samples of the lead `run_case.prepare` guarantees."""
    t = np.arange(int(0.5 * sr)) / sr
    x = 0.9 * np.cos(2 * np.pi * 80.0 * t) * np.exp(-t / 0.12)
    return sr, (x * 32767).astype("<i2")


def nonfinite(sr: int = 44100) -> tuple[int, np.ndarray]:
    """A float WAV with one NaN sample: finite-ness is asserted BEFORE the
    silence test, because NaN defeats it (#133, #134)."""
    _sr, x = struck(sr)
    y = np.concatenate([np.zeros(int(0.05 * sr), np.float32), x.astype(np.float32) / 32768.0])
    y[len(y) // 2] = np.nan
    return sr, y


def corpus(ws: pathlib.Path, **replace) -> dict:
    """A private copy of the Fischer corpus with named files replaced (one
    copy per set of replaced files): `{"refs": path}`, or `{"unresolved": ...}`
    when there is no corpus."""
    src = _refs()
    if src is None:
        return {"unresolved": f"no Fischer corpus: set {ENV_REFS} (this caller reads reference audio)"}
    dst = SCRATCH / "corpus" / _slug(",".join(sorted(replace)) or "as-cloned")
    if not dst.exists():
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".git", "_soundmeta"))
        for rel, (sr, x) in replace.items():
            (dst / rel).parent.mkdir(parents=True, exist_ok=True)
            wavfile.write(str(dst / rel), sr, x)
    return {"refs": dst}


def tree(ws: pathlib.Path, tag: str = "as-traced") -> dict:
    """A private clone of the traced revision: a real git tree, so the release
    checkers' `git show <frozen commit>:<path>` still resolves in it. One clone
    per `tag`, i.e. per alteration."""
    dst = SCRATCH / "trees" / _slug(tag)
    if not dst.exists():
        try:
            subprocess.run(["git", "clone", "--shared", "--no-checkout", "-q", str(REPO), str(dst)], check=True)
            subprocess.run(["git", "-C", str(dst), "checkout", "-q", "--detach", TRACED], check=True)
        except subprocess.CalledProcessError as exc:
            return {"unresolved": f"cannot clone the traced revision: {exc}"}
    return {"root": dst}


def pyserial() -> dict:
    if importlib.util.find_spec("serial") is None:
        return {"unresolved": "pyserial is not installed (the simulated session opens a pty through it)"}
    return {}


def quiet_host() -> dict:
    """midi_session.py's own precondition for a simulated board, asked before
    the run: the session keeps wall-clock time, so on a loaded host the tool
    refuses rather than measure the other load."""
    load = os.getloadavg()[0]
    if load > (os.cpu_count() or 1):
        return {"unresolved": f"midi_session.py refuses on this host: load average {load:.2f} on "
                              f"{os.cpu_count()} CPUs (its own REFUSED; re-run on a quiet host)"}
    return {}


def needs(prereq: dict, build, **kw) -> dict:
    if prereq.get("unresolved"):
        return prereq
    return build(**prereq, **kw)


def tampered(ws: pathlib.Path, change, build) -> dict:
    """Run `build` in a clone of the traced revision after `change(root)` --
    the clone is named after `change`, so one alteration is one clone."""
    return needs(tree(ws, getattr(change, "__name__", "unnamed")),
                 lambda root: (change(root), build(root))[1])


def _slug(text: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-." else "_" for ch in text)


def _at_traced(rel: str) -> bytes:
    return subprocess.run(["git", "-C", str(REPO), "show", f"{TRACED}:{rel}"], capture_output=True,
                          check=True).stdout


def edit_json(path: pathlib.Path, change) -> None:
    rec = json.loads(path.read_text())
    change(rec)
    path.write_text(json.dumps(rec, indent=1) + "\n")


def drop_kit_writes(root: pathlib.Path) -> None:
    """R1's frozen kit, drifted by one register value: no longer R1's digest."""
    path = root / "fpga/release/r1-kit.json"
    if path.read_bytes() == _at_traced("fpga/release/r1-kit.json"):
        edit_json(path, lambda rec: rec["writes"][0].__setitem__(1, rec["writes"][0][1] + 1))


def drop_kit_presets(root: pathlib.Path) -> None:
    """The kit writes untouched; its sound table drifted: r1_kit() still
    accepts the kit, image_sound_presets() refuses the presets."""
    path = root / "fpga/release/r1-kit.json"
    if path.read_bytes() == _at_traced("fpga/release/r1-kit.json"):
        def change(rec):
            first = next(k for k in sorted(rec["presets"]) if rec["presets"][k])
            rec["presets"][first][0][1] += 1
        edit_json(path, change)


def append_xdc_unstated_query(root: pathlib.Path) -> None:
    """A constraint on cells nothing states a required count for."""
    _append_xdc(root, "set_property ASYNC_REG TRUE [get_cells -hier -regexp {u_extra.*}]\n")


def append_xdc_multicycle(root: pathlib.Path) -> None:
    """A timing exception kind expected_exceptions() does not model."""
    _append_xdc(root, "set_multicycle_path 2 -setup -from [get_pins a] -to [get_pins b]\n")


def _append_xdc(root: pathlib.Path, line: str) -> None:
    p = root / "fpga/boards/arty-a7-100.xdc"
    if line not in p.read_text():
        p.write_text(p.read_text() + line)


def alter_m5a_audio(root: pathlib.Path) -> None:
    """The frozen M5A reference audio with its last four bytes changed: they
    no longer hash to what the committed manifest states."""
    d = root / "docs/scorecard/mono-m5a-miniv3"
    manifest = json.loads((d / "manifest.json").read_text())
    p = d / manifest["audio"]["file"]
    p.write_bytes(p.read_bytes()[:-4] + b"\x01\x02\x03\x04")


# ---------------------------------------------------------------------------
# run builders
# ---------------------------------------------------------------------------
def run(entry: str, argv: list, *, root: pathlib.Path = ROOT, env: dict | None = None,
        expect: str | None = None, note: str | None = None, timeout: int = 900,
        share: str | None = None, path: list[str] | None = None,
        prelude: str | None = None) -> dict:
    return {"entry": str(root / entry), "argv": [str(a) for a in argv], "root": str(root),
            "cwd": str(root), "env": env or {}, "expect": expect, "note": note,
            "timeout": timeout, "share": share, "path": path or [], "prelude": prelude}


def script(ws: pathlib.Path, name: str, source: str, path: list[str], note: str | None = None,
           root: pathlib.Path = ROOT) -> dict:
    """A boundary script: the callee called with the arguments that reach its
    refusing arm, and with the arguments the caller passes."""
    p = ws / f"{name}.py"
    p.write_text(source)
    return {"entry": str(p), "argv": [], "root": str(root), "cwd": str(root), "path": path,
            "note": note, "timeout": 600}


REFUSE_MAIN = ("import sys\nimport uart_host as uh\n"
               "def _refused(argv):\n"
               "    print('uart_host: unsupported command', file=sys.stderr)\n"
               "    return 1\n"
               "uh.main = _refused\n")

PREPARE_ARMS = """\
import numpy as np
import run_case as rc
y, sr = rc.render_drum_solo("LT")
onset = int(np.argmax(np.abs(y) > rc.ONSET_FRAC * np.abs(y).max()))
print("render_drum_solo('LT') ->", y.shape, "first sample", float(y[0]), "peak", round(float(np.abs(y).max()), 6),
      "onset index", onset, "(its lead is", onset, "samples of digital silence)")
rc.prepare(y, sr)
print("prepare(render) returned")
rc.prepare(y[onset - 1:], sr)
print("prepare(render minus all but one sample of lead) returned: that sample is still below the onset threshold")
try:
    rc.prepare(y[onset:], sr)
    print("NO RAISE")
except Exception as e:
    print("prepare(render from its onset) raised %s.%s -- %s" % (type(e).__module__, type(e).__name__, str(e)[:90]))
"""

SOLO_ARMS = """\
import run_case as rc
for kw in (dict(), dict(inject="CB_GATE_THE_SUM"), dict(hit_frame=10 ** 9), dict(frames=100)):
    try:
        rc.render_drum_solo("LT", **kw)
        print("render_drum_solo('LT', **%r) returned" % kw)
    except Exception as e:
        print("render_drum_solo('LT', **%r) raised %s.%s -- %s" % (kw, type(e).__module__, type(e).__name__, str(e)[:80]))
try:
    rc.render_drum_solo("NO-SUCH-SOUND")
    print("NO RAISE")
except Exception as e:
    print("render_drum_solo('NO-SUCH-SOUND') raised %s.%s -- %s" % (type(e).__module__, type(e).__name__, str(e)[:60]))
"""

M5A_ARMS = """\
import json
import mono_m5a_score as score
print("engine_configuration('selected') ->", sorted(score.engine_configuration("selected"))[:4])
try:
    score.engine_configuration("no-such-engine")
    print("NO RAISE")
except Exception as e:
    print("engine_configuration('no-such-engine') raised %s.%s -- %s" % (type(e).__module__, type(e).__name__, str(e)[:70]))
patch = score._voice_patch(json.loads(score.MANIFEST.read_text()))
for wave in ("saw", "pulse"):
    score._patch_for_wave(patch, wave, score.M5A_PULSE_WAVE)
    print("_patch_for_wave(patch, %r, %r) returned" % (wave, score.M5A_PULSE_WAVE))
for args in (("triangle", score.M5A_PULSE_WAVE), ("pulse", "pulse999")):
    try:
        score._patch_for_wave(patch, *args)
        print("NO RAISE")
    except Exception as e:
        print("_patch_for_wave(patch, *%r) raised %s.%s -- %s" % (args, type(e).__module__, type(e).__name__, str(e)[:70]))
"""

ADF = "tools/probes/audio_distance_floor.py"
HH5 = "tools/probes/hihat/hh_probe5.py"
KIT = ["uart_host.r1_kit", "uart_host.image_sound_presets"]
RENDERS = "the argument is an integer-model render of one strike: digital silence until the hit, " \
          "so its onset is never at index 0 and every sample is finite; `prepare` refuses only a " \
          "record cut into the strike or a non-finite one"
TRACES: list[dict] = []


def vlm(ws: pathlib.Path) -> dict:
    return run("fpga/verify_live_midi.py", ["--outdir", str(SCRATCH / "out" / "vlm-working"), "--sustained-s", "1"],
               timeout=1500)


def vlm_kit_drift(ws: pathlib.Path) -> dict:
    return tampered(ws, drop_kit_writes, lambda root: run(
        "fpga/verify_live_midi.py", ["--outdir", str(SCRATCH / "out" / "vlm-kit-drift"), "--sustained-s", "1"],
        root=root, timeout=1500, share="vlm-kit-drift",
        expect="escape, but not from here: MidiSession.__init__ reads the kit first (midi_session.py:352)"))


def vlm_presets_drift(ws: pathlib.Path) -> dict:
    return tampered(ws, drop_kit_presets, lambda root: run(
        "fpga/verify_live_midi.py", ["--outdir", str(SCRATCH / "out" / "vlm-presets-drift"), "--sustained-s", "1"],
        root=root, timeout=1500, share="vlm-presets-drift",
        expect="escape from midi_session.py:355: the session under test reads the sound table before "
               "the oracle does"))


def midi_sim(ws: pathlib.Path) -> dict:
    return needs(pyserial(), lambda: needs(quiet_host(), lambda: run(
        "fpga/midi_session.py", ["--port", "sim", "--midi-in", "scripted:coverage", "--duration", "3"],
        timeout=300)))


def midi_sim_r1_presets(ws: pathlib.Path) -> dict:
    return needs(pyserial(), lambda: needs(quiet_host(), lambda: tampered(ws, drop_kit_presets, lambda root: run(
        "fpga/midi_session.py", ["--port", "sim", "--midi-in", "scripted:coverage", "--duration", "3",
                                 "--image", "r1"], root=root, timeout=300, share="midi-r1-presets",
        expect="escape: MidiSession(...) at main:1166 is outside the try that guards session.start()"))))


def r1c_check(ws: pathlib.Path) -> dict:
    return run("fpga/release/r1_candidate.py", ["check"], timeout=1200, share="r1c-working")


def frozen_kit_arms(ws: pathlib.Path) -> dict:
    return needs(tree(ws, "drop_kit_writes"), lambda root: (drop_kit_writes(root), script(ws, "frozen-kit-arms", """import uart_host as uh
import r1_candidate as r1c
for name, fn in (("uh.r1_kit()", uh.r1_kit), ("r1c.frozen_kit()", r1c.frozen_kit)):
    try:
        fn()
        print(name, "returned")
    except Exception as e:
        print("%s raised %s.%s -- %s" % (name, type(e).__module__, type(e).__name__, str(e)[:70]))
""", [str(root / p) for p in ("fpga", "fpga/release", "model", "tools", "audition", "rtl-sketch")],
        root=root, note="in a clone whose r1-kit.json has drifted"))[1])


def adf(ws: pathlib.Path, ref: str, **replace) -> dict:
    return needs(corpus(ws, **replace),
                 lambda refs: run(ADF, ["--ref", ref], env={ENV_REFS: str(refs)}))


def hh5(ws: pathlib.Path, **replace) -> dict:
    return needs(corpus(ws, **replace),
                 lambda refs: run(HH5, ["--refs", str(refs), "--out", str(ws / "out"), "--quick"],
                                  env={ENV_REFS: str(refs)}))


for _line in (441, 446, 467, 489, 490, 540):
    TRACES.append(dict(
        id=f"{ADF}:{_line}", site=f"{ADF}:{_line}", callee="run_case.prepare", wrap=["run_case.prepare"],
        working=lambda ws: adf(ws, "bd8/BD2550.WAV"), refusing=None,
        share={"working": "adf-working"},
        boundary=[lambda ws: script(ws, "prepare-arms", PREPARE_ARMS, TOOLS_PATH)],
        invariant=RENDERS))

TRACES += [
    dict(id=f"{ADF}:611", site=f"{ADF}:611", callee="run_case.prepare", wrap=["run_case.prepare"],
         working=lambda ws: adf(ws, "bd8/BD2550.WAV"),
         refusing=[lambda ws: adf(ws, "bd8/BD2550.WAV", **{"bd8/BD5050.WAV": struck()})],
         share={"working": "adf-working", "refusing": "adf-cut-bd5050"}),
    dict(id=f"{ADF}:612", site=f"{ADF}:612", callee="run_case.prepare", wrap=["run_case.prepare"],
         working=lambda ws: adf(ws, "bd8/BD2550.WAV"),
         refusing=[lambda ws: adf(ws, "bd8/BD2550.WAV", **{"sd8/SD5050.WAV": struck()})],
         share={"working": "adf-working", "refusing": "adf-cut-sd5050"}),
    dict(id=f"{ADF}:640", site=f"{ADF}:640", callee="run_case.prepare", wrap=["run_case.prepare"],
         working=lambda ws: adf(ws, "bd8/BD2550.WAV"),
         refusing=[lambda ws: adf(ws, "bd8/BD2550.WAV", **{"bd8/BD5050.WAV": struck()})],
         share={"working": "adf-working", "refusing": "adf-cut-bd5050"},
         shadowed_by=f"{ADF}:611: e9 prepares the same file e7 does, three experiments later"),
    dict(id=f"{ADF}:687", site=f"{ADF}:687", callee="run_case.prepare", wrap=["run_case.prepare"],
         working=lambda ws: adf(ws, "bd8/BD2550.WAV"),
         refusing=[lambda ws: adf(ws, "cut/BD.WAV", **{"cut/BD.WAV": struck()}),
                   lambda ws: adf(ws, "nan/BD.WAV", **{"nan/BD.WAV": nonfinite()})],
         share={"working": "adf-working"}),
    dict(id=f"{ADF}:705", site=f"{ADF}:705", callee="run_case.prepare", wrap=["run_case.prepare"],
         working=lambda ws: adf(ws, "bd8/BD5050.WAV"),
         refusing=[lambda ws: adf(ws, "bd8/BD5050.WAV", **{"bd8/AA-cut.WAV": struck()})],
         share={"refusing": "adf-t20-cut"},
         note="the working run is itself a REFUSED one by design -- bd8/BD5050.WAV has no usable T20, "
              "which is the branch :705 lives in"),
    dict(id=f"{HH5}:531", site=f"{HH5}:531", callee="run_case.prepare", wrap=["run_case.prepare"],
         working=lambda ws: hh5(ws),
         refusing=[lambda ws: hh5(ws, **{"cy8/CY5025.WAV": struck()})],
         share={"working": "hh5-working", "refusing": "hh5-cut-dev"}),
    dict(id=f"{HH5}:532", site=f"{HH5}:532", callee="run_case.prepare", wrap=["run_case.prepare"],
         working=lambda ws: hh5(ws), refusing=None, share={"working": "hh5-working"},
         boundary=[lambda ws: script(ws, "hh5-render-arms", """\
import numpy as np
import run_case as rc
import hh_probe5 as hh
x, _info = hh.render(hh.dx.kit_with_sounds("CY"), 2.0, "2pole")
y = x / 32768.0
onset = int(np.argmax(np.abs(y) > rc.ONSET_FRAC * np.abs(y).max()))
print("hh_probe5.render(CY) first sample", float(y[0]), "peak", round(float(np.abs(y).max()), 6), "onset index", onset)
rc.prepare(y, hh.SR)
print("prepare(our CY render) returned")
try:
    rc.prepare(y[onset:], hh.SR)
    print("NO RAISE")
except Exception as e:
    print("prepare(render from its onset) raised %s.%s -- %s" % (type(e).__module__, type(e).__name__, str(e)[:80]))
""", TOOLS_PATH + [str(ROOT / "tools/probes/hihat")])],
         invariant="the argument is the probe's own integer-model CY render, " + RENDERS.split(": ", 1)[1]),
]

for _entry, _line, _argv, _literal in (
        ("tools/probes/tom_conga_gate.py", 446,
         ["run", "--refs", "{refs}", "--out", "{out}", "--sounds", "LT", "--codes", "50"],
         "LT and HC are literals"),
        ("tools/probes/tom_distortion.py", 1579, ["baseline", "--refs", "{refs}", "--out", "{out}"],
         "every sound is a member of tom_conga_gate.FAMILY, a literal tuple"),
        ("tools/probes/tom_pitch_drop_fischer.py", 771,
         ["measure", "--refs", "{refs}", "--split", "development", "--out", "{out}"],
         "every sound is a member of TOMS, a literal tuple")):
    TRACES.append(dict(
        id=f"{_entry}:{_line}", site=f"{_entry}:{_line}", callee="run_case.render_drum_solo",
        wrap=["run_case.render_drum_solo"],
        working=lambda ws, e=_entry, a=_argv: needs(
            corpus(ws), lambda refs: run(e, [s.format(refs=refs, out=ws / "out") for s in a],
                                         env={ENV_REFS: str(refs)})),
        refusing=None,
        boundary=[lambda ws: script(ws, "solo-arms", SOLO_ARMS, TOOLS_PATH)],
        invariant=_literal + "; no inject, hit_frame or frames is passed, and render_drum_solo refuses "
                    "an unknown sound, a strike outside the render, and the cowbell control on any other sound"))

TRACES += [
    dict(id="tools/f1_rtl_filter_path.py:449", site="tools/f1_rtl_filter_path.py:449",
         callee="run_case.load_filter_reference", wrap=["run_case.load_filter_reference"],
         working=lambda ws: ({"unresolved": "the frozen reference audio is not restored in this tree "
                                           "(python tools/refprofile_restore.py)"}
                             if not (ROOT / "refprofile/cache").is_dir() else
                             run("tools/f1_rtl_filter_path.py", ["--frames", "0", "--json", str(ws / "curve.json")],
                                 timeout=2400, note="the whole F1 train: the tool's own default, --frames 20000, is "
                                                    "shorter than its first analysis window and is refused after :449 ran")),
         refusing=[lambda ws: needs(tree(ws), lambda root: run(
             "tools/f1_rtl_filter_path.py", ["--json", str(ws / "curve.json")], root=root,
             expect="escape: a fresh checkout has no frozen reference audio, and refprofile.Refused is "
                    "a sibling of the local handler"))]),
    dict(id="tools/measure_pulse2x_headroom.py:191", site="tools/measure_pulse2x_headroom.py:191",
         callee="mono_artifact_probe.measure_point", wrap=["mono_artifact_probe.measure_point"],
         working=lambda ws: run("tools/measure_pulse2x_headroom.py",
                                ["probe", "--gains", "0.85", "--out", str(ws / "probe.json")]),
         refusing=None,
         boundary=[lambda ws: script(ws, "measure-point-arms", """\
import mono_artifact_probe as mp
r = mp.measure_point("pulse2x", 60, mp.held_patch(waves=("square",) * 3))
print("measure_point('pulse2x', 60, held_patch) -> keys", sorted(r)[:4])
try:
    mp.measure_point("pulse2x", 60, mp.held_patch(waves=("square",) * 3), inject="no-such-injection")
    print("NO RAISE")
except Exception as e:
    print("measure_point(..., inject='no-such-injection') raised %s.%s -- %s" % (type(e).__module__, type(e).__name__, str(e)[:70]))
""", TOOLS_PATH)],
         invariant="the probe passes no `inject` and the engine 'pulse2x' is a literal; measure_point "
                   "refuses only an unknown injection"),
    dict(id="tools/performance_suite.py:629", site="tools/performance_suite.py:629",
         callee="mono_artifact_probe.make_voice", wrap=["mono_artifact_probe.make_voice"],
         working=lambda ws: run("tools/performance_suite.py",
                                ["--backend", "model", "--phrase", "bass", "--out", str(ws / "suite.json")],
                                timeout=3000, note="the shortest phrase that has notes: dense_drums has none, "
                                                   "so model_render never makes a voice for it"),
         refusing=None,
         boundary=[lambda ws: run("tools/performance_suite.py", ["--backend", "model", "--engine", "r2"],
                                  expect="REFUSED, exit 2: --engine selects the synthtop backend only"),
                   lambda ws: script(ws, "make-voice-arms", """\
import mono_artifact_probe as mp
print("make_voice('r1') ->", type(mp.make_voice("r1")).__name__)
try:
    mp.make_voice("no-such-engine")
    print("NO RAISE")
except Exception as e:
    print("make_voice('no-such-engine') raised %s.%s -- %s" % (type(e).__module__, type(e).__name__, str(e)[:70]))
""", TOOLS_PATH)],
         invariant="model_render's `engine` defaults to the literal 'r1' and the CLI refuses --engine "
                   "for every backend but synthtop"),
    dict(id="tools/measure_pulse2x_headroom.py:264", site="tools/measure_pulse2x_headroom.py:264",
         callee="mono_m5a_score.measure", wrap=["mono_m5a_score.measure"],
         working=lambda ws: run("tools/measure_pulse2x_headroom.py",
                                ["phrases", "--gains", "0.84", "--out", str(ws / "phrases.json")]),
         refusing=[lambda ws: run("tools/measure_pulse2x_headroom.py",
                                  ["phrases", "--gains", "0.0", "--out", str(ws / "phrases0.json")],
                                  expect="? a zero candidate gain silences the pulse segments; whether the "
                                         "pitch estimator refuses them is what this run asks"),
                   lambda ws: tampered(ws, alter_m5a_audio, lambda root: run(
                       "tools/measure_pulse2x_headroom.py",
                       ["phrases", "--gains", "0.84", "--out", str(ws / "phrases.json")], root=root,
                       expect="escape: the frozen M5A reference audio no longer hashes, and this tool "
                              "has no handler at all"))]),
    dict(id="tools/verify_m5a_filter2x_i2s.py:50", site="tools/verify_m5a_filter2x_i2s.py:50",
         callee="mono_m5a_score.engine_configuration", wrap=["mono_m5a_score.engine_configuration"],
         working=lambda ws: run("tools/verify_m5a_filter2x_i2s.py", [
             "--pulse2x", "--timeout", "45", "--outdir", "build/m5a-filter2x-i2s-trace",
             "--verification", "build/verification/M5A-filter2x-i2s-trace.txt",
             "--wav", "build/scorecard/M5A-filter2x-i2s-trace.wav",
             "--record", "build/scorecard/M5A-filter2x-i2s-trace.json",
             "--audio", "build/scorecard/M5A-filter2x-i2s-trace-measured.wav"], timeout=600,
             note="the call at :50 executes before the RTL child is launched; the child is then cut "
                  "off by the tool's own --timeout (its NO-VERDICT): the full RTL phrase is a build-box run"),
         refusing=None,
         boundary=[lambda ws: script(ws, "m5a-arms", M5A_ARMS, TOOLS_PATH)],
         invariant="the name 'selected' is a literal and a key of mono_m5a_score.ENGINE_PROFILES"),
]

for _line, _callee, _inv in (
        (26, "mono_m5a_score.engine_configuration",
         "the name 'selected' is a literal and a key of mono_m5a_score.ENGINE_PROFILES"),
        (28, "mono_m5a_score._patch_for_wave",
         "the wave is 'pulse' or 'saw' by the preset name, an argparse choice, and the pulse shape is "
         "the module default")):
    TRACES.append(dict(
        id=f"fpga/selected_preset.py:{_line}", site=f"fpga/selected_preset.py:{_line}", callee=_callee,
        wrap=["mono_m5a_score.engine_configuration", "mono_m5a_score._patch_for_wave"],
        working=lambda ws: [run("fpga/selected_preset.py", [n, "--out", str(ws / f"{n}.json")])
                            for n in ("m5a-saw", "m5a-pulse")],
        refusing=None,
        boundary=[lambda ws: script(ws, "m5a-arms", M5A_ARMS, TOOLS_PATH)],
        invariant=_inv))

TRACES += [
    dict(id="model/bd_pedestal.py:483", site="model/bd_pedestal.py:483",
         callee="bd_excitation_probe.calibrate_amp", wrap=["bd_excitation_probe.calibrate_amp"],
         working=lambda ws: run("model/bd_pedestal.py", ["--out", str(ws / "pedestal.json")]),
         refusing=None,
         boundary=[lambda ws: script(ws, "calibrate-arms", """\
import modal_fixed as mf
import bd_excitation_probe as bx
print("calibrate_amp(mf.BP, 1.0, 0) ->", bx.calibrate_amp(mf.BP, 1.0, 0))
try:
    bx.calibrate_amp(mf.BP, 1.0, 0, probe_amp=0.0)
    print("NO RAISE")
except Exception as e:
    print("calibrate_amp(mf.BP, 1.0, 0, probe_amp=0.0) raised %s.%s -- %s" % (type(e).__module__, type(e).__name__, str(e)[:70]))
""", MODEL_PATH)],
         invariant="the calibration render is a BD strike through the band-pass excitation at probe "
                   "amp 0.02; calibrate_amp refuses only a silent render"),
    dict(id="model/res_tuning.py:442", site="model/res_tuning.py:442", callee="sensitivity.runs",
         wrap=["sensitivity.runs"],
         working=lambda ws: needs(tree(ws), lambda root: run("model/res_tuning.py", ["sweep"], root=root,
                                                            timeout=2400,
                                                            note="in a clone: sweep writes docs/res-tuning/sweeps.*")),
         refusing=None,
         boundary=[lambda ws: script(ws, "runs-arms", """\
import sensitivity
print("runs([(1, 5.0), (2, 5.1)], 0.02) ->", sensitivity.runs([(1.0, 5.0), (2.0, 5.1)], 0.02))
for pts in ([(1.0, 0.0), (2.0, 1.0)], [(1.0, -1.0), (2.0, 1.0)]):
    try:
        sensitivity.runs(pts, 0.02)
        print("NO RAISE")
    except Exception as e:
        print("runs(%r, 0.02) raised %s.%s -- %s" % (pts, type(e).__module__, type(e).__name__, str(e)[:80]))
""", [str(ROOT / "tools")])],
         invariant="the plan's two sweeps select on worst_abs_cents (a magnitude) and "
                   "mean_offset_travel_cents; runs() refuses a spread that touches or crosses zero "
                   "unless the column is an exact plateau"),
    dict(id="fpga/midi_session.py:355", site="fpga/midi_session.py:355", callee="uart_host.image_sound_presets",
         wrap=KIT,
         working=[midi_sim, vlm],
         refusing=[midi_sim_r1_presets, vlm_presets_drift],
         note="two entry points reach it: the session CLI itself, and fpga/verify_live_midi.py, "
              "which builds the session under test before its oracle"),
    dict(id="fpga/pads_rom.py:110", site="fpga/pads_rom.py:110", callee="uart_host.r1_kit", wrap=KIT,
         working=lambda ws: run("fpga/pads_rom.py", [], expect="BOUND, exit 0"),
         refusing=[lambda ws: tampered(ws, drop_kit_writes, lambda root: run(
             "fpga/pads_rom.py", [], root=root,
             expect="handled at the boundary: except (Refused, dx.KitRefused)"))]),
    dict(id="fpga/release/r1_candidate.py:138", site="fpga/release/r1_candidate.py:138",
         callee="uart_host.r1_kit", wrap=KIT,
         working=r1c_check,
         refusing=[lambda ws: tampered(ws, drop_kit_writes, lambda root: run(
                       "fpga/release/r1_candidate.py", ["check"], root=root, timeout=1200,
                       expect="NOT-REACHED, exit 2: kit_identity() refuses the drifted kit first")),
                   vlm_kit_drift],
         boundary=[frozen_kit_arms],
         share={"working": "r1c-working"},
         shadowed_by="at every entry point a drifted kit is refused by an earlier uart_host.image_kit "
                      "read (r1_candidate.kit_identity at :262, MidiSession.__init__ at :352) before "
                      "frozen_kit() runs; and frozen_kit() catches dx.KitRefused itself at :139",
         note="the two refusing runs are the kit-drift input through each entry point that calls "
              "frozen_kit(): neither reaches :138 with it"),
    dict(id="fpga/verify_live_midi.py:107", site="fpga/verify_live_midi.py:107", callee="r1_candidate.frozen_kit",
         wrap=KIT + ["r1_candidate.frozen_kit"],
         working=vlm,
         refusing=[vlm_kit_drift],
         shadowed_by="fpga/midi_session.py:352 (uart_host.image_kit): run_session builds the session "
                      "under test before check() builds the oracle, so a drifted kit refuses there first",
         share={}),
    dict(id="fpga/verify_live_midi.py:107#2", site="fpga/verify_live_midi.py:107",
         callee="uart_host.image_sound_presets", wrap=KIT + ["r1_candidate.frozen_kit"],
         working=vlm,
         refusing=[vlm_presets_drift],
         shadowed_by="fpga/midi_session.py:355: the session under test reads the same sound table "
                      "before the oracle does",
         share={"working": "vlm-working"}),
]

for _line, _callee, _change in (
        (116, "xdc_bindings.object_queries", append_xdc_unstated_query),
        (123, "xdc_bindings.expected_exceptions", append_xdc_multicycle)):
    TRACES.append(dict(
        id=f"fpga/publish_binding_cases.py:{_line}", site=f"fpga/publish_binding_cases.py:{_line}",
        callee=_callee, wrap=["xdc_bindings.object_queries", "xdc_bindings.expected_exceptions"],
        working=lambda ws: run("fpga/publish_binding_cases.py", ["--case", "baseline"], expect="baseline PUBLISHED, exit 0",
                               timeout=900, share="pubcases-working"),
        refusing=[lambda ws, c=_change: tampered(ws, c, lambda root: run(
            "fpga/publish_binding_cases.py", ["--case", "baseline"], root=root, timeout=900,
            expect="escape: run_case() calls the builder outside its try"))]))

TRACES += [
    dict(id="fpga/release/glide_boundary.py:242", site="fpga/release/glide_boundary.py:242",
         callee="uart_host.plan_show", wrap=["uart_host.plan_show"],
         working=lambda ws: run("fpga/release/glide_boundary.py",
                                ["--case", "accept-default", "--level", "wrapper", "--outdir", str(ws / "glide")],
                                timeout=1500),
         refusing=None,
         boundary=[lambda ws: script(ws, "plan-show-arms", """\
import synth_top_model as stm
import uart_host as uh
gate_on = ("write", 0, 0, stm.A_GATE_ON, 0)
off = ("gate-off", 0, 0, stm.A_GATE_OFF, 0)
def show(name, cmds, **kw):
    try:
        print("plan_show(%s%s) -> %d rows" % (name, kw or "", len(uh.plan_show(cmds, **kw))))
    except Exception as e:
        print("plan_show(%s%s) raised %s.%s -- %s" % (name, kw or "", type(e).__module__, type(e).__name__, str(e)[:80]))
show("gate-on write, no marker: what wrapper_capture passes", [gate_on])
show("gate-on write, one marker, hold 2000", [gate_on, off], hold_frames=2000)
show("one marker, hold 0", [gate_on, off])
show("two markers", [gate_on, off, off], hold_frames=2000)
show("a marker with no gate-on write", [off], hold_frames=2000)
show("a write after the marker", [gate_on, off, ("write", 0, 0, stm.A_VOL, 5)], hold_frames=2000)
""", FPGA_PATH)],
         invariant="wrapper_capture plans each case's writes as frame-0 live writes and device events "
                   "one packet apart: no `gate-off` marker command and no hold_frames, and every "
                   "refusing arm of plan_show needs one of the two"),
    dict(id="fpga/release/r1_candidate.py:279", site="fpga/release/r1_candidate.py:279",
         callee="release_manifest._capture", wrap=["release_manifest._capture"],
         working=r1c_check,
         refusing=[lambda ws: tampered(ws, drop_kit_writes, lambda root: run(
                       "fpga/release/r1_candidate.py", ["check"], root=root, timeout=1200,
                       expect="NOT-REACHED: kit_identity() refuses the drifted kit before commands() runs")),
                   lambda ws: run("fpga/release/r1_candidate.py", ["check"], prelude=REFUSE_MAIN, path=FPGA_PATH,
                                  timeout=1200,
                                  expect="escape: release_manifest.Refused is a sibling of the except "
                                         "dx.KitRefused here and of check()'s except Refused",
                                  note="PR #1574's control shape: uart_host.main returns 1")],
         share={"working": "r1c-working"}),
]

def drift_publication_state(root: pathlib.Path) -> None:
    edit_json(root / "fpga/reports/arty/r2-2025.1/publication.json",
              lambda rec: rec.__setitem__("state", "drifted"))


def drift_ext_io_dcp(root: pathlib.Path) -> None:
    edit_json(root / "fpga/reports/arty/r2-2025.1/ext-io/ext-io-extract.json",
              lambda rec: rec.__setitem__("dcp_sha256", "0" * 64))


for _line, _callee, _change, _expect in (
        (110, "r1_release.image_identity", drift_publication_state,
         "handled in check(): except Refused is an exact alias of r1_release.Refused"),
        (302, "r1_release.external_io", drift_ext_io_dcp,
         "handled in check(): except Refused is an exact alias of r1_release.Refused")):
    TRACES.append(dict(
        id=f"fpga/release/r2_release.py:{_line}", site=f"fpga/release/r2_release.py:{_line}", callee=_callee,
        wrap=["r1_release.image_identity", "r1_release.external_io"],
        working=lambda ws: run("fpga/release/r2_release.py", ["check"], expect="BOUND, exit 0", timeout=1200,
                               share="r2-working"),
        refusing=[lambda ws, c=_change, e=_expect: tampered(ws, c, lambda root: run(
            "fpga/release/r2_release.py", ["check"], root=root, timeout=1200, expect=e))]))

TRACES.append(dict(
    id="fpga/release/r2_release.py:372", site="fpga/release/r2_release.py:372", callee="release_manifest._capture",
    wrap=["release_manifest._capture"],
    working=lambda ws: run("fpga/release/r2_release.py", ["stale-control", "host", "--out", str(ws / "stale-host")],
                           timeout=1200),
    refusing=[lambda ws: run("fpga/release/r2_release.py", ["stale-control", "host", "--out", str(ws / "stale-host")],
                             prelude=REFUSE_MAIN, path=FPGA_PATH, timeout=1200,
                             expect="escape on this revision, #1569's known defect; PR #1574 repairs it",
                             note="PR #1574's control shape: uart_host.main returns 1")]))

assert len({t["id"] for t in TRACES}) == len(TRACES) == 37, len(TRACES)   # 37 scanner rows; :107 is two calls
