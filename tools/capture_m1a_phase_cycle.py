#!/usr/bin/env python3
"""Capture the Mini V3 through one held MIDI 36 note, for the phase-cycle question.

    /opt/homebrew/anaconda3/bin/python3 tools/capture_m1a_phase_cycle.py

A thin wrapper around the EXISTING M1A reference renderer
(`measure_mono_m1a_reference.render`, which already sets every parameter by
index, checks each one's NAME and READBACK before and after the render, and
checks the rig's pinned settings). This file adds only the held-note events,
the extra preconditions below, and the write-out. It never touches the frozen
reference: output goes to docs/scorecard/mono-m1a-miniv3/phase-cycle-capture/.

Preconditions, each asserted where it is used; any failure REFUSES (exit 2,
`refused.json`) and nothing is reported as data:

  1. plugin bundle version and binary hash equal the frozen manifest's
  2. the frozen reference phrase AND its two isolated controls re-render
     bit-exactly in this host -- the host state matches the frozen one
  3. the existing reference-integrity qualification (40 s held note, zero
     unprompted transients, injected-click control detected)
  4. per take: finite, non-silent, unclipped; zero transient events in the held
     sustain AND the injected-click control detected in the same take; no run
     of >= 1 ms exact digital zeros inside the gate; isolated takes. 4-period RMS
     flat within 0.5 dB over the sustain (no dropouts)
  5. each condition's settings equal the frozen manifest's recorded settings
  6. octave: oscillator 1 within 5 c of MIDI 36, oscillator 2 within 10 c of
     twice that (Mini V3 has defaulted to a sub-audio range before)
  7. 48 kHz, 16-sample host blocks, as the frozen manifest
  8. both oscillators with the filter open equal the sum of the isolated takes
     (residual <= -40 dB): psi measured from separate instances describes the
     full-patch take's oscillators
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import subprocess
import sys

import numpy as np
from scipy.io import wavfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import measure_mono_m1a_reference as m1a  # noqa: E402

ref = m1a.ref
am = ref.am
sys.path.insert(0, str(ROOT / "model"))
import reference_integrity as ri  # noqa: E402

FROZEN = ROOT / "docs/scorecard/mono-m1a-miniv3"
OUT = FROZEN / "phase-cycle-capture"
NOTE = 36
GATE_S = 12.0
TAIL_S = 1.0
TAKES = {"A": .1, "B": 2.0}          # note-on, seconds after instantiation
OPEN = {"filter_cutoff": 1., "filter_contour": 0.}
CONDITIONS = {"full": {},
              "osc1_open": {"osc2_level": 0., **OPEN},
              "osc2_open": {"osc1_level": 0., **OPEN},
              "both_open": dict(OPEN)}          # precondition 8 only
SUSTAIN_FROM_S = 1.0                 # after note-on; the analysis region starts here
TRANSIENT_SKIP_S = .5                # after note-on; the attack is not a click
# The MAD transient detector compares short-block high-band levels. At MIDI 36
# a 5 ms block holds a saw's reset edge in one block of three, so the levels
# are bimodal and EVERY period reads as a click (found by the synthetic test,
# wrong-then-right). Blocks of exactly four MIDI 36 periods (eight of
# oscillator 2) hold the same number of edges each, so a held note is
# stationary again; the injected-click control in every take proves the
# longer block still sees clicks.
F36 = 440 * 2 ** ((NOTE - 69) / 12)
TRANSIENT_BLOCK_MS = 4000 / F36
ZERO_RUN_S = .001
FLAT_DB = .5
SUM_RESIDUAL_DB = -40.
OSC1_CENTS, OSC2_CENTS = 5., 10.
TOOL_FILES = ("tools/capture_m1a_phase_cycle.py", "tools/measure_mono_m1a_reference.py",
              "tools/measure_mono_m5a_reference.py", "model/reference_rigs.py",
              "model/reference_integrity.py", "model/audio_measure.py")


class Refused(ref.Refused):
    pass


# ------------------------------------------------------------ pure checks
def events_for(on_s):
    return ({"note": NOTE, "on_s": on_s, "gate_s": GATE_S},)


def seconds_for(on_s):
    return on_s + GATE_S + TAIL_S


def expected_settings(manifest, condition):
    """The frozen manifest's recorded settings for a condition, as tuples."""
    full = {k: tuple(v) for k, v in manifest["renders"][0]["apparatus"]["settings"].items()}
    if condition == "full":
        return full
    if condition in ("osc1_open", "osc2_open"):
        return {k: tuple(v) for k, v in manifest["controls"][condition]["apparatus"]["settings"].items()}
    out = dict(full)
    for key, value in CONDITIONS[condition].items():
        i, name, _ = out[key]
        out[key] = (i, name, value)
    return out


def longest_zero_run(x):
    z = np.concatenate(([0], (np.asarray(x) == 0).astype(np.int8), [0]))
    edges = np.flatnonzero(np.diff(z))
    return int(np.max(edges[1::2] - edges[::2])) if len(edges) else 0


def pitch_hz(x, sr, expect_hz, on_s):
    a = round((on_s + SUSTAIN_FROM_S) * sr)
    est = am.refine_f0(np.asarray(x[a:a + round(.5 * sr)], dtype=np.float64), expect_hz, sr)
    if not est.ok:
        raise Refused(f"pitch refused near {expect_hz:.2f} Hz: {est.reason}")
    return float(est.value)


def check_take(audio, sr, on_s, condition):
    """Precondition 4 and 6 on one take. Returns the evidence; raises Refused."""
    x = np.asarray(audio, dtype=np.float64)
    if not np.isfinite(x).all():
        raise Refused(f"{condition}: non-finite samples")
    peak = float(np.max(np.abs(x)))
    if not 1e-4 < peak < .999:
        raise Refused(f"{condition}: silent or clipped (peak {peak:.3g})")
    on, off = round(on_s * sr), round((on_s + GATE_S) * sr)
    gate = x[on:off]
    zrun = longest_zero_run(gate)
    if zrun >= ZERO_RUN_S * sr:
        raise Refused(f"{condition}: {zrun} consecutive exact zeros inside the gate (dropout)")
    held = x[round((on_s + TRANSIENT_SKIP_S) * sr):off]
    clean = ri.transient_report(held, sr, skip_s=0., block_ms=TRANSIENT_BLOCK_MS)
    if clean["n_events"]:
        raise Refused(f"{condition}: {clean['n_events']} transient events in the held sustain "
                      f"at {clean['event_times_s']} s -- demo noise or clicks")
    injected = ri.transient_report(ri.inject_clicks(held, sr, every_s=2.0), sr, skip_s=0.,
                                   block_ms=TRANSIENT_BLOCK_MS)
    want = int((len(held) / sr - 1e-9) // 2.0)
    if injected["n_events"] < want:
        raise Refused(f"{condition}: click detector control failed ({injected['n_events']} of {want})")
    sustain = x[round((on_s + SUSTAIN_FROM_S) * sr):round((on_s + GATE_S - .05) * sr)]
    env = am.rms_envelope(sustain, ms=TRANSIENT_BLOCK_MS, sr=sr)[round(.07 * sr):-round(.07 * sr)]
    env_db = 20 * np.log10(np.maximum(env, 1e-12))
    evidence = {"peak": peak, "longest_zero_run_in_gate": zrun,
                "transient_events": clean["n_events"],
                "transient_peak_over_median_db": clean["peak_over_median_db"],
                "injected_click_events": injected["n_events"], "injected_click_expected": want,
                "sustain_rms_dbfs": 20 * math.log10(am.rms(sustain)),
                "sustain_rms_4period_range_db": float(np.ptp(env_db))}
    if condition in ("osc1_open", "osc2_open") and evidence["sustain_rms_4period_range_db"] > FLAT_DB:
        raise Refused(f"{condition}: isolated oscillator level moves "
                      f"{evidence['sustain_rms_4period_range_db']:.2f} dB in the sustain (dropout?)")
    f36 = F36
    if condition == "osc1_open":
        f = pitch_hz(x, sr, f36, on_s)
        cents = 1200 * math.log2(f / f36)
        if abs(cents) > OSC1_CENTS:
            raise Refused(f"oscillator 1 is {cents:+.1f} c from MIDI {NOTE} (wrong octave/range?)")
        evidence.update(f0_hz=f, cents_from_midi=cents)
    if condition == "osc2_open":
        f = pitch_hz(x, sr, 2 * f36, on_s)
        cents = 1200 * math.log2(f / (2 * f36))
        if abs(cents) > OSC2_CENTS:
            raise Refused(f"oscillator 2 is {cents:+.1f} c from the octave (wrong range?)")
        evidence.update(f0_hz=f, cents_from_octave=cents)
    return evidence


def sum_residual_db(both, osc1, osc2, on_s, sr):
    a, b = round((on_s + SUSTAIN_FROM_S) * sr), round((on_s + GATE_S - .05) * sr)
    s = np.asarray(osc1[a:b], dtype=np.float64) + np.asarray(osc2[a:b], dtype=np.float64)
    r = np.asarray(both[a:b], dtype=np.float64) - s
    return 20 * math.log10(max(am.rms(r), 1e-30) / am.rms(s))


# ------------------------------------------------------------ apparatus
def _git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def reproduce_frozen(manifest):
    """Precondition 2: the frozen phrase and both isolated controls, bit-exact."""
    rows = []
    audio, _ = m1a.render()
    for r in manifest["renders"]:
        rate, frozen = wavfile.read(FROZEN / r["file"])
        same = rate == ref.SR and np.array_equal(np.asarray(audio, dtype=np.float32), frozen)
        rows.append({"file": r["file"], "bit_exact": bool(same)})
    for label, overrides in (("osc1_open", CONDITIONS["osc1_open"]), ("osc2_open", CONDITIONS["osc2_open"])):
        audio, _ = m1a.render(overrides)
        rate, frozen = wavfile.read(FROZEN / manifest["controls"][label]["file"])
        same = rate == ref.SR and np.array_equal(np.asarray(audio, dtype=np.float32), frozen)
        rows.append({"file": manifest["controls"][label]["file"], "bit_exact": bool(same)})
    bad = [r["file"] for r in rows if not r["bit_exact"]]
    if bad:
        raise Refused(f"frozen reference does not re-render bit-exactly: {bad}")
    return rows


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        dirty = _git("status", "--porcelain", "--", *TOOL_FILES)
        if dirty:
            raise Refused("commit the capture instrument before capturing:\n" + dirty)
        manifest = json.loads((FROZEN / "manifest.json").read_text())
        if manifest["sample_rate_hz"] != ref.SR or manifest["block_size_samples"] != ref.BLOCK:
            raise Refused("host rate/block differ from the frozen manifest")
        identity = ref._plugin_metadata()
        for key in ("bundle_id", "version", "plugin_binary_sha256", "bundle_info_sha256"):
            if identity[key] != manifest["identity"][key]:
                raise Refused(f"plugin {key} {identity[key]!r} differs from the frozen "
                              f"{manifest['identity'][key]!r}")
        print("identity matches the frozen manifest", flush=True)
        reproduction = reproduce_frozen(manifest)
        print("frozen phrase and controls re-render bit-exactly", flush=True)
        integrity = ref._qualify_reference_integrity()
        print("reference integrity: 0 unprompted transients, click control detected", flush=True)
        takes = {}
        for take, on_s in TAKES.items():
            audio_by = {}
            rows = {}
            for condition, overrides in CONDITIONS.items():
                audio, apparatus = m1a.render(overrides, events_for(on_s), seconds_for(on_s))
                got = {k: tuple(v) for k, v in apparatus["settings"].items()}
                want = expected_settings(manifest, condition)
                if got != want:
                    diff = {k: (got.get(k), want.get(k)) for k in set(got) | set(want)
                            if got.get(k) != want.get(k)}
                    raise Refused(f"{condition} settings differ from the frozen record: {diff}")
                evidence = check_take(audio, ref.SR, on_s, condition)
                audio_by[condition] = np.asarray(audio, dtype=np.float32)
                path = OUT / f"take{take}-{condition}.wav"
                wavfile.write(path, ref.SR, audio_by[condition])
                rows[condition] = {"file": path.name, "sha256": ref.sha256(path),
                                   "overrides": overrides, "checks": evidence,
                                   "apparatus": apparatus}
                print(f"take {take} {condition}: preconditions hold", flush=True)
            resid = sum_residual_db(audio_by["both_open"], audio_by["osc1_open"],
                                    audio_by["osc2_open"], on_s, ref.SR)
            if resid > SUM_RESIDUAL_DB:
                raise Refused(f"take {take}: both-open differs from the isolated sum by {resid:.1f} dB; "
                              "isolated takes do not describe the full take's oscillators")
            takes[take] = {"note_on_s": on_s, "gate_s": GATE_S, "seconds": seconds_for(on_s),
                           "events": events_for(on_s), "conditions": rows,
                           "isolated_sum_residual_db": resid}
        report = {"schema": "m1a-phase-cycle-capture-v1", "state": "CAPTURED",
                  "scope": "diagnostic only; the frozen M1A reference is untouched",
                  "identity": identity, "host_python": sys.executable,
                  "sample_rate_hz": ref.SR, "block_size_samples": ref.BLOCK,
                  "midi_velocity": ref.VELOCITY,
                  "provenance": {"commit": _git("rev-parse", "HEAD"),
                                 "files_sha256": {f: ref.sha256(ROOT / f) for f in TOOL_FILES},
                                 "frozen_manifest_sha256": ref.sha256(FROZEN / "manifest.json")},
                  "frozen_reproduction": reproduction, "integrity": integrity,
                  "thresholds": {"zero_run_s": ZERO_RUN_S, "isolated_flat_db": FLAT_DB,
                                 "sum_residual_db": SUM_RESIDUAL_DB, "osc1_cents": OSC1_CENTS,
                                 "osc2_cents": OSC2_CENTS},
                  "takes": takes}
        (OUT / "capture.json").write_text(json.dumps(report, indent=1) + "\n")
        (OUT / "refused.json").unlink(missing_ok=True)
        print("CAPTURED")
        return 0
    except (ref.Refused, OSError, ValueError, RuntimeError) as exc:
        (OUT / "refused.json").write_text(json.dumps({"state": "REFUSED", "reason": str(exc)}, indent=1) + "\n")
        print(f"REFUSED: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
