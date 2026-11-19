#!/usr/bin/env python3
"""Render the Mini V4 reference suite on an operator Mac (#1050).

    python3 tools/render_miniv4_suite.py --list
    python3 tools/render_miniv4_suite.py --render M1B --out docs/scorecard/miniv4-suite/renders \
        --licence-established "serial activated, verified in Arturia Software Center on this Mac"

Mini V4 ships only for macOS/Windows, so **this script never runs its render
on a fleet worker** -- there is nothing to run it on. `--list` (the dry run)
works anywhere: it prints every case in `docs/scorecard/miniv4-suite/suite.json`
with its render settings and exits 0, having attempted nothing.

Outcomes, per this repository's exit contract (tools/exit_contract.py):

    exit 0  PASS     the case rendered and its record was written
    exit 1  FAIL     the case rendered but a measurement of it failed
                     (demo-artefact detector fired on the calibration note)
    exit 2  REFUSED  a precondition of the apparatus is unmet, so nothing was
                     measured: wrong plugin version, unstated licence, no
                     plugin host, a rig that renders exact silence, or a rig
                     that does not sound the pitch that was commanded

REFUSED is first-class and distinct from FAIL on purpose (docs/failure-modes.md):
a wrong plugin version or an unlicensed bundle is a correct instrument in a
wrong state, and its output looks exactly like data. Every refusal names the
unmet precondition.

The three lessons this script asserts rather than assumes, each one a
precondition that was once silently wrong here:

1.  **Version.** The bundle must be exactly Mini V4 4.0.4.6366. A plugin
    update is a change of instrument; the suite it feeds must not change
    silently underneath the expectations (tools/reference_suites.py).
2.  **Licence.** There is no licence probe for any plugin in this repository,
    so the operator STATES how the licence was established and the statement
    is recorded in the environment tuple -- the same discipline
    `refprofile.environment_tuple` applies. An unlicensed Arturia plugin
    inserts artefacts, as the unlicensed Diva did for a whole session.
3.  **Pitch and level.** The calibration note must sound (not exact silence --
    the Model D lesson) and within 50 cents of the commanded pitch (Mini V3's
    Range default was an octave down; Model D's is too). Assert what defaults
    wrongly, every time.

Every render's record carries the #123 environment tuple -- host, host
version, plugin identity, block, sample rate, licence, preset -- plus the
top-level `identity` block (`name`, `version`, `bundle_id`), which is
what `reference_suites.require_suite` reads at the scoring boundary; the
`suite` stamp is a convenience for humans reading the file, not what the
gate checks.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "audition"))

import audio_measure as am                                          # noqa: E402
import dsp                                                          # noqa: E402
import reference_suites as rs                                       # noqa: E402
import refprofile as rp                                             # noqa: E402

OK, FAIL, REFUSED = rp.OK, rp.FAIL, rp.REFUSED

SR = 48000
BLOCK = 16
VELOCITY = 100

#: Mini V4's macOS bundle path, beside the V3 path the rig layer already uses.
VST3 = "/Library/Audio/Plug-Ins/VST3"
PATH_MINIV4 = f"{VST3}/Mini V4.vst3"

#: The calibration note and its tolerance. MIDI 60 -> 261.63 Hz. An octave
#: default is 1200 cents away; 50 cents is far inside any real tuning and
#: far outside every wrong-default seen here.
CAL_NOTE = 60
CAL_CENTS_LIMIT = 50.0

#: The demo-artefact detector's calibration-note window: the detector looks
#: for isolated broadband transients against a stationary background, the
#: shape both Arturia's and u-he's demo modes take (model/reference_integrity.py).
#: Long enough that the pitched path holds >= 1 s of analysis after its
#: 0.5 s attack skip.
CAL_RENDER_S = 2.5


class Refused(rs.Refused):
    """A stated precondition of the render apparatus is unmet."""


# ===========================================================================
# the rig: the smallest plugin host this suite needs, and no more
# ===========================================================================
class _Rig:
    """One dawdreamer engine holding one Mini V4.

    Deliberately smaller than `reference_rigs._Plugin`: a rig class there
    claims capabilities (waveforms, envelope timing, cutoff reading) and every
    capability entry in `refprofile.RIG_VERDICTS` is `None` until measured.
    This suite needs exactly three things -- load, hold a note, read the
    audio back -- and refuses everything it has not measured."""

    name = "miniv4"
    host = "dawdreamer"

    def __init__(self, path: str = PATH_MINIV4, block: int = BLOCK, engine=None):
        try:
            import dawdreamer  # noqa: F401
        except ImportError as exc:
            raise Refused(f"no dawdreamer on this machine ({exc}); "
                          f"Mini V4 renders need the plugin host installed")
        import dawdreamer as daw
        self.block = block
        self.eng = engine if engine is not None else daw.RenderEngine(SR, block)
        try:
            self.p = self.eng.make_plugin_processor(self.name, path)
        except Exception as exc:
            raise Refused(f"the host could not load Mini V4 at {path}: {exc}")
        self.eng.load_graph([(self.p, [])])

    def identity(self) -> dict:
        return rp.plugin_identity(PATH_MINIV4)

    def set_parameter(self, idx: int, value: float) -> None:
        self.p.set_parameter(int(idx), float(value))

    def render_note(self, note: int, seconds: float,
                    velocity: int = VELOCITY, note_at: float = 0.02) -> np.ndarray:
        self.p.clear_midi()
        self.p.add_note(note, velocity, note_at, seconds)
        self.eng.render(seconds)
        a = self.eng.get_audio()
        y = np.asarray(a).astype(np.float64)
        if y.ndim == 2:
            y = y[0]
        am.require_finite(y, f"{self.name}'s {seconds:.3f} s render")
        lat = self.p.get_latency_samples()
        return y[lat:] if lat else y


# ===========================================================================
# preconditions, each asserted at the point of use
# ===========================================================================
def check_plugin_version(path: str = PATH_MINIV4) -> dict:
    """The bundle must exist, and claim exactly 4.0.4.6366. REFUSED otherwise."""
    ident = rp.plugin_identity(path)
    if not ident.get("present"):
        raise Refused(f"Mini V4 is not installed at {path} "
                      f"(it ships for macOS/Windows only; renders happen on "
                      f"an operator Mac, never on a fleet worker)")
    version = ident.get("bundle_version")
    if version != rs.V4_REQUIRED_VERSION:
        raise Refused(
            f"Mini V4 version found is {version!r}, but this suite baselines "
            f"exactly {rs.V4_REQUIRED_VERSION!r}. A plugin update is a change "
            f"of instrument: record the new identity in the suite registry "
            f"first, never render over it")
    return ident


def check_licence(licence_established: str | None) -> str:
    """The operator states HOW the licence position was established, and the
    statement is recorded verbatim. There is no licence probe here; an empty
    statement refuses, because an unlicensed plugin renders artefacts and
    calls them reference audio."""
    if not licence_established or not licence_established.strip():
        raise Refused(
            "no licence statement: pass --licence-established '<how it was "
            "established>'. There is no licence probe for any plugin in this "
            "repository, so the position must be stated and recorded, not "
            "assumed")
    return licence_established.strip()


def check_calibration(rig: _Rig) -> dict:
    """The rig sounds, and sounds the pitch that was commanded.

    Renders one calibration note and asserts BOTH halves of the assertion:
    not exact silence (a host that returns no signal answers no question --
    the Model D lesson) and within CAL_CENTS_LIMIT of the commanded pitch
    (Mini V3's Range default was an octave down; the check that exists
    because a defaulted knob once passed silently)."""
    y = rig.render_note(CAL_NOTE, CAL_RENDER_S)
    peak = float(np.max(np.abs(y))) if len(y) else 0.0
    if peak == 0.0:
        raise Refused(
            "the rig rendered exact silence -- peak 0.0 over the whole "
            "calibration note. An apparatus that returns no signal answers "
            "no question (the Model D lesson); fix the rig before rendering")
    probe = am.dominant_frequency(y[int(0.1 * SR):], 100.0, 600.0, SR)
    if not probe.ok:
        raise Refused(f"calibration pitch unreadable: {probe.reason}")
    expected = dsp.note_hz(CAL_NOTE)
    cents = 1200.0 * math.log2(float(probe.value) / expected)
    if abs(cents) > CAL_CENTS_LIMIT:
        raise Refused(
            f"calibration note sounds {cents:+.0f} cents from the commanded "
            f"MIDI {CAL_NOTE} ({float(probe.value):.2f} Hz vs {expected:.2f} Hz). "
            f"A defaulted octave is 1200 cents away; this is the assert-what-"
            f"defaults-wrongly precondition")
    demo = _demo_artefact_scan(y, float(probe.value))
    return {"peak": peak, "measured_hz": float(probe.value), "cents_error": cents,
            "demo_artefact_scan": demo}


def _demo_artefact_scan(y: np.ndarray, f0_hz: float) -> dict:
    """Sparse impulsive transients against a stationary background -- the
    shape both Arturia's and u-he's demo modes take. The pitched path is the
    one already ground-truthed in model/reference_integrity.py (#225): a saw
    puts one broadband burst per period, so the block is whole periods of the
    measured f0 and a clean note reports ~no events. A clean scan is NOT
    proof of licence (stated limitation); it is one detector, and a fired
    scan FAILs (exit 1) rather than refusing, because the render did happen
    and was measured."""
    from reference_integrity import transient_report
    try:
        return transient_report(y, SR, f0_hz=f0_hz)
    except am.InsufficientEvidence as exc:
        raise Refused(
            f"the demo-artefact detector cannot run on this calibration "
            f"note: {exc}. Lengthen the note rather than trust a scan that "
            f"did not happen")


# ===========================================================================
# the render itself
# ===========================================================================
def environment_tuple(ident: dict, licence: str, host_version: str) -> dict:
    """The #123 tuple, every field required. A field nobody established is
    the literal string 'unstated' -- a tuple with a field missing must never
    look like a tuple whose field was never asked about."""
    return {
        "host": rig_host(),
        "host_version": host_version or "unstated",
        "plugin": ident,
        "block": BLOCK,
        "sr": SR,
        "licence": {"stated_by_operator": licence},
        "preset": "suite default patch (per-case patch fields in suite.json); no vendor preset loaded",
    }


def rig_host() -> str:
    return _Rig.host


def host_version() -> str:
    try:
        return importlib.metadata.version("dawdreamer")
    except importlib.metadata.PackageNotFoundError:
        return "unstated"


def render_case(case: dict, rig: _Rig, licence: str, out_dir: pathlib.Path) -> dict:
    """Render one case from the suite definition and write its record.

    The record carries the environment tuple and the top-level `identity`
    block -- the block `reference_suites.require_suite` reads at the
    scoring boundary. Before rendering, the identity the record will carry
    is checked through that same gate, so the writer and the reader cannot
    drift apart: a record no scorer can classify refuses here, having
    measured nothing. The `suite` stamp is for humans, not for the gate.
    Raw float32 WAV, no normalisation, complete release inside the stated
    phrase length."""
    plugin = rig.identity()
    identity = {
        "name": rs.V4_PLUGIN_NAME,
        "version": plugin.get("bundle_version"),
        "bundle_id": plugin.get("bundle_id"),
    }
    try:
        rs.require_suite({"identity": identity}, rs.SUITE_V4)
    except rs.Refused as exc:
        raise Refused(f"this render's identity is not classifiable as "
                      f"{rs.SUITE_V4}: {exc}") from exc
    phrase_s = float(case["phrase_s"])
    note = case["base_midi_note"]
    if note is None:
        raise Refused(
            f"{case['case_id']} is a sealed holdout: its note set is fixed "
            f"and recorded at the attended session before any tuning read "
            f"({case.get('sealing', 'no sealing statement')})")
    y = rig.render_note(int(note), phrase_s)
    out_dir.mkdir(parents=True, exist_ok=True)
    wav_path = out_dir / f"{case['case_id']}-miniv4-raw.wav"
    _write_float32_wav(wav_path, y)
    record = {
        "schema": 1,
        "case_id": case["case_id"],
        "suite": rs.SUITE_V4,
        "identity": identity,
        "suite_manifest": "docs/scorecard/miniv4-suite/suite.json",
        "plugin_required_version": rs.V4_REQUIRED_VERSION,
        "environment": environment_tuple(plugin, licence, host_version()),
        "audio": {
            "file": wav_path.name,
            "sample_rate_hz": SR,
            "channels": 1,
            "format": "IEEE float32 WAV",
            "samples": int(len(y)),
            "sha256": _sha256(wav_path),
        },
        "case_spec": {k: case[k] for k in
                      ("subject", "split", "batch", "base_midi_note",
                       "patch", "phrase_s", "velocity")},
    }
    rec_path = out_dir / f"{case['case_id']}-miniv4-manifest.json"
    rec_path.write_text(json.dumps(record, indent=1) + "\n")
    return record


def _write_float32_wav(path: pathlib.Path, y: np.ndarray) -> None:
    from scipy.io import wavfile
    wavfile.write(path, SR, y.astype(np.float32))


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ===========================================================================
# CLI
# ===========================================================================
def _list_cases(suite: dict) -> int:
    print(f"suite {suite['suite_id']}  plugin {suite['plugin']['name']} "
          f"{suite['plugin']['required_version']}  state: {suite['state']}")
    print(f"render: {suite['render']['sample_rate_hz']} Hz, "
          f"{suite['render']['bit_depth']}, velocity {suite['render']['velocity']}")
    for c in suite["cases"]:
        note = c["base_midi_note"] if c["base_midi_note"] is not None else "SEALED"
        ref = c["reference_before_v4"] or "none"
        print(f"  {c['case_id']:4s} {c['split']:12s} note {str(note):6s} "
              f"len {c['phrase_s']:4.1f}s vel {c['velocity']} | {c['subject']}"
              f" | pre-V4 reference: {ref}")
    waiting = [c["case_id"] for c in suite["cases"]
               if c["render_state"] == "awaiting-render"]
    print(f"{len(waiting)} of {len(suite['cases'])} cases awaiting a render: "
          f"{' '.join(waiting)}")
    return OK


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true",
                    help="dry run: list every case and its settings; no plugin needed")
    ap.add_argument("--render", metavar="CASE", action="append", default=[],
                    help="render one case id from the suite (repeatable)")
    ap.add_argument("--out", default="docs/scorecard/miniv4-suite/renders",
                    metavar="PATH", help="output directory for render records")
    ap.add_argument("--licence-established", default=None, metavar="STATEMENT",
                    help="HOW the licence position was established, recorded verbatim")
    ap.add_argument("--json", default=None, metavar="PATH",
                    help="write the run record (all outcomes) as JSON here")
    args = ap.parse_args(argv)

    try:
        suite = rs.load_v4_suite()
    except rs.Refused as why:
        print(f"REFUSED  {why}")
        return REFUSED

    if args.list and not args.render:
        return _list_cases(suite)

    run: dict = {"suite": rs.SUITE_V4, "requested": list(args.render),
                 "outcomes": {}, "records": {}}
    out_dir = ROOT / args.out
    try:
        ident = check_plugin_version(PATH_MINIV4)
        licence = check_licence(args.licence_established)
        rig = _Rig(PATH_MINIV4)
        cal = check_calibration(rig)
        run["calibration"] = cal
        code = OK
    except Refused as why:
        print(f"REFUSED  {why}")
        run["outcome"] = "REFUSED"
        run["refusal"] = str(why)
        _write_run_record(run, args.json)
        return REFUSED

    for cid in args.render:
        case = next((c for c in suite["cases"] if c["case_id"] == cid), None)
        if case is None:
            print(f"REFUSED  {cid} is not a case in the suite manifest")
            run["outcomes"][cid] = "REFUSED"
            code = REFUSED
            continue
        try:
            rec = render_case(case, rig, licence, out_dir)
            run["records"][cid] = rec
            run["outcomes"][cid] = "PASS"
            print(f"PASS     {cid} rendered -> {out_dir / rec['audio']['file']}")
        except Refused as why:
            print(f"REFUSED  {cid}: {why}")
            run["outcomes"][cid] = "REFUSED"
            code = max(code, REFUSED)
            continue

    scan = run.get("calibration", {}).get("demo_artefact_scan", {})
    if scan and int(scan.get("n_events", 0)) > 0:
        print(f"FAIL     the calibration note carries {scan['n_events']} "
              f"impulsive transient event(s) -- the demo-artefact detector "
              f"fired. A licensed plugin should come back clean; investigate "
              f"before trusting any render")
        run["outcome"] = "FAIL"
        code = FAIL
    elif code == OK:
        run["outcome"] = "PASS"
    _write_run_record(run, args.json)
    return code


def _write_run_record(run: dict, json_path: str | None) -> None:
    if not json_path:
        return
    p = pathlib.Path(json_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(run, indent=1) + "\n")


if __name__ == "__main__":
    sys.exit(main())
