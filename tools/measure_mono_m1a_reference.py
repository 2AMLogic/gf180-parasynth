#!/usr/bin/env python3
"""Freeze one measured Mini V3 round-bass patch for M1A; no model verdict."""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import subprocess
import sys
import numpy as np
from scipy.io import wavfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import measure_mono_m5a_reference as ref

EVENTS = ({"note": 36, "on_s": .1, "gate_s": .6},
          {"note": 43, "on_s": 2.1, "gate_s": .6},
          {"note": 36, "on_s": 4.1, "gate_s": .6})
SECONDS = 7.5
# At MIDI 36 the lead's 5 ms RMS window spans only 0.33 cycles and crosses
# release thresholds on carrier troughs. Qualify the bass basis separately.
ENVELOPE_WINDOW_MS = 40.0
PATCH = {
    "osc1_level": (15, "Level Osc1", .70),
    "osc2_level": (16, "Level Osc2", .30),
    "osc2_enable": (73, "Osc2", 1.0),
    "osc2_range": (46, "Range Osc2", .7417),
    "osc2_wave": (49, "Wave Osc2", .4083),
    "filter_cutoff": (23, "CutOff", .50),
    "filter_emphasis": (24, "Emphasis", .05),
    "filter_contour": (25, "Amount", .30),
    "filter_attack": (26, "VCF Attack", .005),
    "filter_decay": (27, "VCF Decay", .15),
    "filter_sustain": (28, "VCF Sustain", .0),
    "amp_attack": (29, "VCA Attack", .02),
    "amp_decay": (30, "VCA Decay", .25),
    "amp_sustain": (31, "VCA Sustain", .75),
}


def render(overrides=None, events=EVENTS, seconds=SECONDS):
    rig = ref.rr.MiniV3Rig(block=ref.BLOCK)
    ref._apply_patch(rig, "saw", decay=ref.PATCH["amp_decay"][2])
    settings = {**ref.PATCH, **PATCH}
    for key, value in (overrides or {}).items():
        i, name, _ = settings[key]; settings[key] = (i, name, value)
    for i, _, value in settings.values():
        rig.set(i, value)
    rig.pb.set_data(np.zeros((2, int(.05 * ref.SR)), dtype=np.float32))
    rig.p.clear_midi(); rig.eng.render(.05)

    def readbacks():
        result = {}
        for key, (i, name, value) in settings.items():
            expected = {"osc1_range": "8'", "osc2_range": "4'", "osc2_wave": "sawtooth"}.get(key, value)
            result[key] = ref._readback(rig.p, i, name, expected)
        bad = rig.check_pins()
        if bad:
            raise ref.Refused(f"reference pins changed: {bad}")
        return result

    before = readbacks()
    rig.pb.set_data(np.zeros((2, int(seconds * ref.SR)), dtype=np.float32))
    rig.p.clear_midi()
    for e in events:
        rig.p.add_midi_note(e["note"], ref.VELOCITY, e["on_s"], e["gate_s"])
    rig.eng.render(seconds)
    audio = np.asarray(rig.eng.get_audio()[0], dtype=np.float64)
    latency = int(rig.p.get_latency_samples())
    audio = audio[latency:] if latency else audio
    if len(audio) < int((seconds - .01) * ref.SR) or not np.isfinite(audio).all():
        raise ref.Refused("truncated or non-finite reference")
    peak = float(np.max(np.abs(audio)))
    if not 1e-5 < peak < .999:
        raise ref.Refused(f"silent or clipped reference: peak {peak}")
    return audio, {"before": before, "after": readbacks(), "latency_removed": latency,
                   "peak": peak, "settings": settings}


def event_measurements(audio):
    envelope = ref.am.rms_envelope(audio, ms=ENVELOPE_WINDOW_MS, sr=ref.SR)
    rows = []
    for e in EVENTS:
        hz = ref.vf.note_hz(e["note"])
        steady = audio[round((e["on_s"] + .3) * ref.SR):round((e["on_s"] + .55) * ref.SR)]
        pitch = ref.am.refine_f0(steady, hz, ref.SR)
        if not pitch.ok or abs(1200 * math.log2(pitch.value / hz)) > 5:
            raise ref.Refused(f"bass pitch did not follow MIDI {e['note']}")
        env = ref.envelope_timing(envelope, ref.SR, e["on_s"], e["on_s"] + e["gate_s"])
        if not env.get("valid") or not env.get("release_complete_40db"):
            raise ref.Refused(f"incomplete bass envelope: {env}")
        shape = ref.am.harmonic_signature(steady, ref.SR, f0=pitch.value, kmax=12)
        trajectory = []
        for offset in (.02, .16, .30):
            clip = audio[round((e["on_s"] + offset) * ref.SR):round((e["on_s"] + offset + .25) * ref.SR)]
            sig = ref.am.harmonic_signature(clip, ref.SR, f0=pitch.value, kmax=4)
            trajectory.append({"offset_s": offset, "h4_db": sig.get("h4")})
        rows.append({**e, "f0_hz": pitch.value, "pitch_cents": 1200 * math.log2(pitch.value / hz),
                     "harmonics": shape, "envelope": env,
                     "rms_dbfs": 20 * math.log10(ref.am.rms(steady)),
                     "h4_trajectory": trajectory})
    return rows



def qualify_envelope_basis():
    """Independent mathematical release; retain the failed lead-window control."""
    t = np.arange(round(SECONDS * ref.SR)) / ref.SR
    audio = np.zeros_like(t)
    for event in EVENTS:
        dt = t - event["on_s"]
        envelope = np.clip(dt / .03, 0, 1)
        off = dt >= event["gate_s"]
        envelope[off] = np.exp(-(dt[off] - event["gate_s"]) / .1)
        hz = 440 * 2 ** ((event["note"] - 69) / 12)
        audio += .1 * envelope * np.sin(2 * np.pi * hz * t)
    rows = {}
    expected = 100 * math.log(10)
    for label, window in (("lead_window", 5.), ("bass_window", ENVELOPE_WINDOW_MS)):
        measured = ref.am.rms_envelope(audio, ms=window, sr=ref.SR)
        values = [ref.envelope_timing(measured, ref.SR, e["on_s"], e["on_s"] + e["gate_s"])["release_t20_ms"] for e in EVENTS]
        rows[label] = {"window_ms": window, "release_ms": values,
                       "max_error_ms": max(abs(v - expected) for v in values)}
    if rows["bass_window"]["max_error_ms"] >= 10 or rows["lead_window"]["max_error_ms"] <= 10:
        raise ref.Refused("bass envelope qualification or wrong-window control failed")
    return {"known_release_ms": expected, **rows, "spectral_window_s": .25,
            "scope": "40 ms audio-envelope window; not internal VCA timing",
            "initial_failures": "100 ms spectral window refused below 12 periods; 5 ms envelope window failed known release; early h4 window could not resolve the short filter transient"}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=ROOT / "docs/scorecard/mono-m1a-miniv3")
    a = ap.parse_args(argv); a.out.mkdir(parents=True, exist_ok=True)
    try:
        if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip():
            raise ref.Refused("commit instrument before capture")
        provenance = ref._source_provenance()
        provenance["files_sha256"]["tools/measure_mono_m1a_reference.py"] = ref.sha256(Path(__file__))
        qualification = qualify_envelope_basis()
        identity = ref._plugin_metadata()
        integrity = ref._qualify_reference_integrity()
        renders = []
        for repeat in range(3):
            audio, apparatus = render()
            rows = event_measurements(audio)
            path = a.out / f"m1a-repeat-{repeat}.wav"
            wavfile.write(path, ref.SR, np.asarray(audio, dtype=np.float32))
            renders.append({"file": path.name, "sha256": ref.sha256(path),
                            "apparatus": apparatus, "events": rows})
            print(f"M1A repeat {repeat}: measured three bass notes", flush=True)
        controls = {}
        for label, overrides, note in (
            ("osc1_open", {"osc2_level": 0., "filter_cutoff": 1., "filter_contour": 0.}, 36),
            ("osc2_open", {"osc1_level": 0., "filter_cutoff": 1., "filter_contour": 0.}, 48)):
            audio, apparatus = render(overrides)
            steady = audio[int(.3 * ref.SR):int(.6 * ref.SR)]
            measured = ref.rv.measure(steady, ref.vf.note_hz(note), label, "saw")
            if not measured.get("verified") or not measured.get("steady"):
                raise ref.Refused(f"{label} oscillator/pitch qualification failed: {measured}")
            path = a.out / f"control-{label}.wav"
            wavfile.write(path, ref.SR, np.asarray(audio, dtype=np.float32))
            controls[label] = {"file": path.name, "sha256": ref.sha256(path), "measurement": measured, "rms_dbfs": 20 * math.log10(ref.am.rms(steady)),
                               "apparatus": apparatus}
        if controls["osc2_open"]["rms_dbfs"] >= controls["osc1_open"]["rms_dbfs"] - 3:
            raise ref.Refused("octave oscillator is not measurably quieter")
        audio, apparatus = render({"filter_contour": 0.})
        controls["filter_envelope_disabled"] = {"events": event_measurements(audio), "apparatus": apparatus}
        path = a.out / "control-filter-envelope-disabled.wav"
        wavfile.write(path, ref.SR, np.asarray(audio, dtype=np.float32))
        disabled = controls["filter_envelope_disabled"]
        disabled.update(file=path.name, sha256=ref.sha256(path))
        deltas = []
        for event in range(len(EVENTS)):
            values = [r["events"][event]["h4_trajectory"][0]["h4_db"] for r in renders]
            off = disabled["events"][event]["h4_trajectory"][0]["h4_db"]
            if off is None or any(value is None for value in values):
                raise ref.Refused("filter-envelope control has an unmeasurable fourth partial")
            deltas.append({"midi": EVENTS[event]["note"], "baseline_median_db": float(np.median(values)),
                           "disabled_db": off, "difference_db": float(np.median(values) - off),
                           "repeat_range_db": float(np.ptp(values))})
        disabled["early_h4_comparison"] = deltas
        # A 250 ms harmonic window cannot resolve a very short filter attack.
        # Use the causal sample difference, with independently repeated clean
        # audio as its noise floor, and keep the insensitive spectral result.
        baseline = wavfile.read(a.out / renders[0]["file"])[1].astype(np.float64)
        comparison = np.asarray(audio, dtype=np.float32).astype(np.float64)
        repeat_peak = max(float(np.max(np.abs(baseline - wavfile.read(a.out / r["file"])[1]))) for r in renders)
        threshold = max(1e-5, 100 * repeat_peak)
        responses = []
        for event in EVENTS:
            start = round(event["on_s"] * ref.SR)
            end = round((event["on_s"] + event["gate_s"]) * ref.SR)
            difference = np.abs(baseline[start:end] - comparison[start:end])
            changed = np.flatnonzero(difference > threshold)
            if not len(changed):
                raise ref.Refused("disabled filter envelope caused no change above repeated-render variation")
            duration = float(changed[-1] * 1000 / ref.SR)
            if duration > 250:
                raise ref.Refused("filter-envelope audio effect did not settle within 250 ms")
            responses.append({"midi": event["note"], "peak_difference": float(difference.max()),
                              "last_difference_ms": duration, "threshold": threshold})
        disabled["audio_response"] = {"events": responses, "repeat_peak_difference": repeat_peak,
                                      "scope": "duration of audible-output difference, not internal cutoff-envelope timing"}
        rings = []
        for _ in range(3):
            rig = ref.rr.MiniV3Rig(block=ref.BLOCK)
            y = rig.ring(PATCH["filter_cutoff"][2], .98, seconds=1.2)
            rings.append(ref.am.dominant_frequency(y, 25, 15000, ref.SR).require("bass cutoff ring"))
        if np.ptp(rings) > 10:
            raise ref.Refused("bass cutoff ring is not repeatable")
        report = {"schema": "mono-m1a-reference-v1", "case_id": "M1A", "valid": True,
                  "scope": "frozen Mini V3 reference; no model comparison or Model D cross-check yet",
                  "identity": identity, "integrity": integrity, "provenance": provenance,
                  "sample_rate_hz": ref.SR, "block_size_samples": ref.BLOCK,
                  "envelope_window_ms": ENVELOPE_WINDOW_MS,
                  "measurement_qualification": qualification,
                  "midi_velocity": ref.VELOCITY, "events": EVENTS, "duration_s": SECONDS,
                  "filter_rest_ring_hz": rings, "renders": renders, "controls": controls,
                  "wrong_then_right": {"corrected_measurements": 0, "phrase_repeats": 3}}
        (a.out / "manifest.json").write_text(json.dumps(report, indent=2) + "\n")
        print("M1A reference captured; scorecard coverage unchanged")
        return 0
    except (ref.Refused, OSError, ValueError, RuntimeError) as exc:
        (a.out / "refused.json").write_text(json.dumps({"state": "REFUSED", "reason": str(exc)}, indent=2))
        print(f"REFUSED: {exc}"); return 2


if __name__ == "__main__":
    raise SystemExit(main())
