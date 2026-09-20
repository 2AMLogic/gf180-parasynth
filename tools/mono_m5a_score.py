"""Measure the frozen Mini V3 M5A case against the selected integer voice model.

The Mini V3 recording is a software-synth reference, not a physical Model D.
This module refuses if the frozen audio or its manifest has changed.
"""
from __future__ import annotations

import hashlib
import json
import math
import pathlib
import sys

import numpy as np
from scipy.io import wavfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "tools"))
import audio_measure as am
import voice_fx as vf
from measure_mono_m5a_reference import envelope_timing

SR = 48_000
MANIFEST = ROOT / "docs/scorecard/mono-m5a-miniv3/manifest.json"
TOLERANCES = {
    "Pitch": (1.0, "cents; fixed screening limit for this frozen software-synth patch"),
    "Harmonic shape": (1.0, "dB per measured partial; fixed screening limit"),
    "Foldback energy": (3.0, "dB; half-power convention"),
    "Envelope attack": (5.0, "ms; fixed limit above the 5 ms RMS measurement hop"),
    "Envelope release": (125.0, "ms; fixed 10% of the measured reference T20"),
    "Gain": (3.0, "dB; fixed half/double amplitude screening range"),
    "Clipping": (0.01, "% samples at full-scale rail"),
}


class Refused(RuntimeError):
    pass


def _metric(name, ours, ref, units, tolerance, basis):
    if not all(math.isfinite(float(x)) for x in (ours, ref)):
        raise Refused(f"{name} is not finite")
    return {"value": round(float(ours), 5), "reference": round(float(ref), 5),
            "error": round(float(ours) - float(ref), 5), "units": units,
            "tolerance": tolerance, "valid": True, "tolerance_basis": basis}


def _voice_patch(manifest):
    first = manifest["timeline"]["segments"][0]["measurements"][0]
    env = first["envelope"]
    return dict(
        waves=("saw", "saw", "saw"), detune=(0.0, 0.0, 0.0), mix=(1.0, 0.0, 0.0),
        noise=0.0,
        cutoff=(int(round(manifest["patch"]["cutoff_measurement"]["f0_hz"])),
                int(round(manifest["patch"]["cutoff_measurement"]["f0_hz"]))),
        q=0.0, drive=1.0,
        amp=(env["attack_10_90_ms"] / 1000.0 / 0.8, 0.25, 1.0,
             env["release_t20_ms"] / 1000.0 * 4.0 / math.log(10.0)),
        fenv=(0.004, 0.30, 1.0, 0.10), track=0.0, vol=0.45,
        mod_mix=0.0, mod_wheel=0.0, osc_mod=False, filt_mod=False)


def _patch_for_wave(patch, wave):
    if wave not in ("saw", "pulse"):
        raise Refused(f"unsupported M5A waveform {wave!r}")
    # Model the register writes that select one waveform for all oscillators;
    # only oscillator 1 is mixed in this patch.
    return {**patch, "waves": (wave, wave, wave)}


def measure():
    manifest = json.loads(MANIFEST.read_text())
    manifest_digest = hashlib.sha256(MANIFEST.read_bytes()).hexdigest()
    audio_meta = manifest["audio"]
    ref_path = MANIFEST.parent / audio_meta["file"]
    digest = hashlib.sha256(ref_path.read_bytes()).hexdigest()
    if digest != audio_meta["sha256"]:
        raise Refused("frozen Mini V3 audio hash mismatch")
    sr, ref_pcm = wavfile.read(ref_path)
    if sr != SR or ref_pcm.dtype != np.float32 or ref_pcm.ndim != 1:
        raise Refused("reference WAV is not mono float32 at 48 kHz")
    ref_pcm = ref_pcm.astype(np.float64)
    if len(ref_pcm) != audio_meta["samples"] or not np.isfinite(ref_pcm).all():
        raise Refused("reference sample count or finite-sample precondition failed")

    patch = _voice_patch(manifest)
    tolerance = TOLERANCES
    observed = {name: [] for name in tolerance}
    event_diagnostics = []
    renders = []
    model_parts = []
    silence = int(round(manifest["timeline"]["segment_silence_s"] * SR))
    for seg in manifest["timeline"]["segments"]:
        wave = seg["wave"]
        seq = []
        segment_patch = _patch_for_wave(patch, wave)
        for ev in seg["midi_events"]:
            seq.append((float(ev["on_s"]), int(ev["note"]), float(ev["gate_s"]),
                        {**segment_patch, "gate": float(ev["gate_s"])}))
        duration = float(seg["duration_s"])
        pcm = vf.render_mono_fx(seq, duration, vf.VoiceFx(oversample_2x=True))
        ours = np.asarray(pcm, dtype=np.float64) / 32768.0
        offset = int(seg["offset_samples"])
        ref_segment = ref_pcm[offset:offset + int(seg["samples"])]
        # The host latency is removed from the frozen recording (44 samples
        # here). Compare the common timeline and trim only the candidate's
        # final silence to the manifest's recorded segment length.
        if abs(len(ref_segment) - len(ours)) > 128:
            raise Refused(f"{wave} model/reference segment lengths differ by more than 128 samples")
        common = min(len(ref_segment), len(ours))
        ours, ref_segment = ours[:common], ref_segment[:common]
        model_parts.append(ours)
        renders.append({"wave": wave, "samples": common, "offset_samples": offset,
                        "trimmed_candidate_tail_samples": max(0, len(pcm) - common)})
        ours_env = am.rms_envelope(ours, ms=5.0, sr=SR)
        ref_env = am.rms_envelope(ref_segment, ms=5.0, sr=SR)
        for ev in seg["midi_events"]:
            note, on, off = int(ev["note"]), float(ev["on_s"]), float(ev["on_s"] + ev["gate_s"])
            a, b = int((on + 0.12) * SR), int((off - 0.08) * SR)
            xo, xr = ours[a:b], ref_segment[a:b]
            f0 = vf.note_hz(note)
            eo, er = am.refine_f0(xo, f0, SR), am.refine_f0(xr, f0, SR)
            if not eo.ok or not er.ok:
                raise Refused(f"{wave} MIDI {note} pitch refused: {eo.reason if not eo.ok else er.reason}")
            model_cents = 100.0 * math.log2(eo.value / f0)
            reference_cents = 100.0 * math.log2(er.value / f0)
            observed["Pitch"].append((model_cents - reference_cents, 0.0))
            so = am.harmonic_signature(xo, SR, f0=eo.value, kmax=12)
            sr_ = am.harmonic_signature(xr, SR, f0=er.value, kmax=12)
            diffs = [float(so[f"h{k}"]) - float(sr_[f"h{k}"])
                     for k in range(2, 13)
                     if so.get(f"h{k}") is not None and sr_.get(f"h{k}") is not None]
            if len(diffs) < 2:
                raise Refused(f"{wave} MIDI {note} has fewer than two common measurable partials")
            harmonic_diffs = {f"h{k}": round(float(so[f"h{k}"]) - float(sr_[f"h{k}"]), 4)
                              for k in range(2, 13)
                              if so.get(f"h{k}") is not None and sr_.get(f"h{k}") is not None}
            # Store maximum absolute partial error as the case value/reference pair.
            observed["Harmonic shape"].append((max(abs(x) for x in diffs), 0.0))
            fo, fr = am.foldback_alias_db(xo, eo.value, SR), am.foldback_alias_db(xr, er.value, SR)
            if not fo.ok or not fr.ok:
                raise Refused(f"{wave} MIDI {note} foldback estimator refused")
            observed["Foldback energy"].append((fo.value, fr.value))
            to = envelope_timing(ours_env, SR, on, off)
            tr = envelope_timing(ref_env, SR, on, off)
            if not to.get("valid") or not tr.get("valid"):
                raise Refused(f"{wave} MIDI {note} envelope estimator refused")
            if not tr.get("release_complete_40db") or not to.get("release_complete_40db"):
                raise Refused(f"{wave} MIDI {note} release did not clear by the segment tail")
            observed["Envelope attack"].append((to["attack_10_90_ms"], tr["attack_10_90_ms"]))
            observed["Envelope release"].append((to["release_t20_ms"], tr["release_t20_ms"]))
            observed["Gain"].append((20 * math.log10(max(float(am.rms(xo)), 1e-15)),
                                      20 * math.log10(max(float(am.rms(xr)), 1e-15))))
            event_diagnostics.append({
                "wave": wave, "midi": note,
                "pitch_cents_from_midi": {"model": round(model_cents, 5),
                                          "reference": round(reference_cents, 5),
                                          "model_minus_reference": round(model_cents - reference_cents, 5)},
                "harmonic_error_db_model_minus_reference": harmonic_diffs,
                "foldback_db": {"model": round(float(fo.value), 4),
                                "reference": round(float(fr.value), 4)},
                "envelope_ms": {"attack_model": round(to["attack_10_90_ms"], 4),
                                "attack_reference": round(tr["attack_10_90_ms"], 4),
                                "release_model": round(to["release_t20_ms"], 4),
                                "release_reference": round(tr["release_t20_ms"], 4)},
                "gain_dbfs": {"model": round(20 * math.log10(max(float(am.rms(xo)), 1e-15)), 4),
                              "reference": round(20 * math.log10(max(float(am.rms(xr)), 1e-15)), 4)},
            })
        clip_ours = 100.0 * np.count_nonzero(np.abs(ours) >= 32767 / 32768) / len(ours)
        clip_ref = 100.0 * np.count_nonzero(np.abs(ref_segment) >= 0.999) / len(ref_segment)
        observed["Clipping"].append((clip_ours, clip_ref))

    metrics = {}
    for name, pairs in observed.items():
        limit, basis = tolerance[name]
        if name == "Harmonic shape":
            # Compare the single largest measured partial error against zero.
            value = max(o for o, _ in pairs)
            ref = 0.0
        elif name == "Pitch":
            value, ref = max(pairs, key=lambda p: abs(p[0]))
        else:
            value, ref = max(pairs, key=lambda p: abs(p[0] - p[1]))
        metrics[name] = _metric(name, value, ref, "dB" if name in
                                ("Harmonic shape", "Foldback energy", "Gain") else
                                ("cents" if name == "Pitch" else
                                 ("ms" if "Envelope" in name else "%")), limit, basis)

    combined = np.concatenate([part for i, part in enumerate(model_parts)
                               if i == 0] + [np.zeros(silence)] + model_parts[1:])
    out = ROOT / "build/scorecard/M5A-model.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    pcm_out = np.clip(combined * 32768.0, -32768, 32767).astype("<i2")
    wavfile.write(out, SR, pcm_out)
    return {"metrics": metrics, "audio": str(out.relative_to(ROOT)),
            "reference_sha256": digest, "manifest_sha256": manifest_digest,
            "cutoff_calibration": manifest["patch"]["cutoff_measurement"],
            "model_segments": renders,
            "event_diagnostics": event_diagnostics,
            "wrong_then_right": manifest["qualification"]["wrong_then_right"],
            "duration_s": len(combined) / SR,
            "note": ("Full sound comparison uses the fixed integer model; a separate "
                     "short M5A stimulus verifies the selected configuration through SPI to I2S.")}
