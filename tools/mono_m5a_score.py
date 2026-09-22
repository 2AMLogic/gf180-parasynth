"""Measure frozen Mini V3 M5A/M5B cases against the selected integer voice model.

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
ANALYSIS_VERSION = "m5a-score-v3"
M5A_PULSE_WAVE = "pulse29"
MANIFEST = ROOT / "docs/scorecard/mono-m5a-miniv3/manifest.json"
MANIFESTS = {
    "M5A": MANIFEST,
    "M5B": ROOT / "docs/scorecard/mono-m5b-miniv3/manifest.json",
}
ANALYSIS_VERSIONS = {"M5A": ANALYSIS_VERSION, "M5B": "m5b-score-v1"}
MODEL_NOTE_MUTATIONS = {"MONO_PITCH_UP_25_CENTS": 0.25}
ENGINE_PROFILES = {
    "selected": {
        "name": "selected-m5a-reconstructed-filter2x",
        "oscillator_oversample_2x": True,
        "filter_rate_converted": True,
        "filter_preserve_headroom": True,
        "filter_causal": True,
        "pulse479_filter_candidate": True,
        "filter_g_exact": False,
        "filter_k_comp": True,
        "filter_oversample_factor": 2,
        "ladder_coefficient_oversample": 2,
        "filter_drive": 0.75,
        "pulse_control_label": "pulse29",
        "pulse_effective_waveform": "pulse479",
        "pulse_effective_duty_percent": 100.0 * vf.DUTY["pulse479"] / vf.CYCLE,
        "saw_cutoff_hz": 20_000,
        "saw_volume_correction_db": -0.45428,
    },
    "legacy": {
        "name": "legacy-osc2x-base-rate-filter",
        "oscillator_oversample_2x": True,
        "filter_rate_converted": False,
        "filter_preserve_headroom": False,
        "filter_causal": False,
        "pulse479_filter_candidate": False,
        "filter_g_exact": False,
        "filter_k_comp": True,
        "filter_oversample_factor": 1,
        "ladder_coefficient_oversample": 2,
        "filter_drive": 0.75,
        "pulse_control_label": "pulse29",
        "pulse_effective_waveform": "pulse29",
        "pulse_effective_duty_percent": 100.0 * vf.DUTY["pulse29"] / vf.CYCLE,
        "saw_cutoff_hz": None,
        "saw_volume_correction_db": 0.0,
    },
}
TOLERANCES = {
    "Pitch": (1.0, "cents; fixed screening limit for this frozen software-synth patch"),
    "Harmonic shape": (1.0, "dB per measured partial; fixed screening limit"),
    "Foldback energy": (3.0, "dB excess alias energy above the reference; cleaner output is not penalized"),
    "Envelope attack": (5.0, "ms; fixed limit above the 5 ms RMS measurement hop"),
    "Envelope release": (125.0, "ms; fixed 10% of the measured reference T20"),
    "Gain": (3.0, "dB; fixed half/double amplitude screening range"),
    "Clipping": (0.01, "% samples at full-scale rail"),
}


class Refused(RuntimeError):
    pass


def engine_configuration(name="selected"):
    """Return the explicit shared M5A/M5B engine profile or refuse."""
    try:
        return dict(ENGINE_PROFILES[name])
    except KeyError as exc:
        raise Refused(f"unsupported Mono engine configuration {name!r}") from exc


def _voice_for_engine(profile):
    ladder_cfg = {**vf.LADDER_CFG,
                  "oversample": profile["ladder_coefficient_oversample"]}
    return vf.VoiceFx(
        oversample_2x=profile["oscillator_oversample_2x"],
        ladder_cfg=ladder_cfg,
        rate_converted_ladder=profile["filter_rate_converted"],
        preserve_filter_headroom=profile["filter_preserve_headroom"],
        causal_filter=profile["filter_causal"],
        pulse479_filter_candidate=profile["pulse479_filter_candidate"],
    )


def _cents_error(measured_hz: float, expected_hz: float) -> float:
    """Signed pitch deviation in cents (1200 cents per octave)."""
    if measured_hz <= 0 or expected_hz <= 0:
        raise Refused("pitch frequencies must be positive")
    return 1200.0 * math.log2(measured_hz / expected_hz)


def _harmonic_error(model: dict, reference: dict, k: int) -> tuple[float | None, str]:
    """Return a measured or floor-bounded model-minus-reference partial error.

    `None` in harmonic_signature means the component fell below that signal's
    measured floor. When only one side is measurable, use the other side's
    floor as a conservative bound instead of silently dropping the partial.
    """
    key = f"h{k}"
    model_h, reference_h = model.get(key), reference.get(key)
    if model_h is not None and reference_h is not None:
        return float(model_h) - float(reference_h), "measured"
    if model_h is None and reference_h is None:
        return None, "both_below_floor"
    if model_h is None:
        floor = model.get(f"floor{k}")
        if floor is None or not math.isfinite(float(floor)):
            raise Refused(f"h{k} absent in model without a measurable noise floor")
        return float(floor) - float(reference_h), "model_below_floor_bound"
    floor = reference.get(f"floor{k}")
    if floor is None or not math.isfinite(float(floor)):
        raise Refused(f"h{k} absent in reference without a measurable noise floor")
    return float(model_h) - float(floor), "reference_below_floor_bound"


def _excess_alias_db(model_db: float, reference_db: float) -> float:
    """Only alias energy above the reference is a regression."""
    return max(0.0, float(model_db) - float(reference_db))


def _load_i2s_candidate(path, required_samples: int):
    """Load only a complete mono signed-int16 I2S phrase at the target rate."""
    candidate_path = pathlib.Path(path)
    try:
        digest = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
        sr, candidate = wavfile.read(candidate_path)
    except OSError as exc:
        raise Refused(f"decoded I2S WAV is unavailable: {exc}") from exc
    if sr != SR or candidate.dtype != np.int16 or candidate.ndim != 1:
        raise Refused("decoded I2S WAV must be mono signed-int16 at 48 kHz")
    if len(candidate) < int(required_samples):
        raise Refused(f"decoded I2S WAV is short ({len(candidate)} < {required_samples} samples)")
    if not np.isfinite(candidate).all():
        raise Refused("decoded I2S WAV contains non-finite samples")
    return candidate[:required_samples].astype(np.float64) / 32768.0, digest


def _metric(name, ours, ref, units, tolerance, basis):
    if not all(math.isfinite(float(x)) for x in (ours, ref)):
        raise Refused(f"{name} is not finite")
    return {"value": round(float(ours), 5), "reference": round(float(ref), 5),
            "error": round(float(ours) - float(ref), 5), "units": units,
            "tolerance": tolerance, "valid": True, "tolerance_basis": basis}


def _stage_diagnostics(trace, output, start: int, stop: int, f0: float) -> dict:
    """Absolute stage vectors for localizing changes without calling a stage a reference."""
    inputs = {
        "oscillator": np.asarray(trace["osc"][0], dtype=np.float64) / 32768.0,
        "mixer": np.asarray(trace["mixed"], dtype=np.float64) / 32768.0,
        "ladder": np.asarray(trace["ladder"], dtype=np.float64) / 32768.0,
        "output": np.asarray(output, dtype=np.float64) / 32768.0,
    }
    result = {}
    for name, samples in inputs.items():
        x = samples[start:stop]
        signature = am.harmonic_signature(x, SR, f0=f0, kmax=12)
        alias = am.foldback_alias_db(x, f0, SR)
        if not alias.ok:
            raise Refused(f"{name} stage foldback estimator refused: {alias.reason}")
        result[name] = {
            "harmonics_db": {f"h{k}": signature.get(f"h{k}") for k in range(1, 13)},
            "foldback_db": round(float(alias.value), 5),
            "alias_band_power_dbfs": round(float(alias.detail["alias_band_power_dbfs"]), 5),
            "total_signal_power_dbfs": round(float(alias.detail["total_signal_power_dbfs"]), 5),
            "rms_dbfs": round(20.0 * math.log10(max(float(am.rms(x)), 1e-15)), 5),
        }
    return result


def _voice_patch(manifest):
    first = manifest["timeline"]["segments"][0]["measurements"][0]
    env = first["envelope"]
    return dict(
        waves=("saw", "saw", "saw"), detune=(0.0, 0.0, 0.0), mix=(1.0, 0.0, 0.0),
        noise=0.0,
        cutoff=(int(round(manifest["patch"]["cutoff_measurement"]["f0_hz"])),
                int(round(manifest["patch"]["cutoff_measurement"]["f0_hz"]))),
        q=0.0, drive=0.75,
        amp=(env["attack_10_90_ms"] / 1000.0 / 0.8, 0.25, 1.0,
             env["release_t20_ms"] / 1000.0 * 4.0 / math.log(10.0)),
        fenv=(0.004, 0.30, 1.0, 0.10), track=0.0, vol=0.45,
        mod_mix=0.0, mod_wheel=0.0, osc_mod=False, filt_mod=False)


def _patch_for_wave(patch, wave, pulse_shape=M5A_PULSE_WAVE):
    if wave not in ("saw", "pulse"):
        raise Refused(f"unsupported Mono waveform {wave!r}")
    if pulse_shape not in ("pulse29", "pulse479"):
        raise Refused(f"unsupported M5A model pulse candidate {pulse_shape!r}")
    # This is an explicit candidate choice, supported by the frozen-reference
    # duty sweep. pulse479 is model-only because the RTL has no waveform code.
    model_wave = pulse_shape if wave == "pulse" else "saw"
    return {**patch, "waves": (model_wave, model_wave, model_wave)}


def _model_note(note: float, injection: str = "") -> float:
    """Apply only the declared live-control mutation to the rendered model."""
    if not injection:
        return note
    if injection not in MODEL_NOTE_MUTATIONS:
        raise Refused(f"unsupported Mono model injection {injection!r}")
    shifted = float(note) + MODEL_NOTE_MUTATIONS[injection]
    if not 0 <= shifted <= 127:
        raise Refused("injected model pitch lies outside MIDI range")
    return shifted


def measure(*, pulse_shape=M5A_PULSE_WAVE, voice_factory=None,
            model_label=None, engine="selected", output_path=None,
            candidate_wav=None, saw_cutoff_override=None,
            saw_volume_correction_db=None, case_id="M5A", inject=""):
    if case_id not in MANIFESTS:
        raise Refused(f"unsupported Mono scorecard case {case_id!r}")
    engine_profile = engine_configuration(engine)
    if candidate_wav is None and saw_cutoff_override is None:
        saw_cutoff_override = engine_profile["saw_cutoff_hz"]
    if saw_volume_correction_db is None and candidate_wav is None:
        saw_volume_correction_db = engine_profile["saw_volume_correction_db"]
    elif saw_volume_correction_db is None:
        saw_volume_correction_db = 0.0
    if model_label is None:
        model_label = engine_profile["name"]
    if inject == "REF_MISSING":
        reference_override = MANIFESTS[case_id].parent / "no-such-file.wav"
    else:
        reference_override = None
    if inject and inject not in MODEL_NOTE_MUTATIONS and inject != "REF_MISSING":
        raise Refused(f"unsupported Mono model injection {inject!r}")
    manifest_path = MANIFESTS[case_id]
    if case_id == "M5A" and MANIFEST != ROOT / "docs/scorecard/mono-m5a-miniv3/manifest.json":
        # Preserve the test/CLI override used to exercise corrupt M5A references.
        manifest_path = MANIFEST
    manifest = json.loads(manifest_path.read_text())
    manifest_digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    if manifest.get("case_id") != case_id:
        raise Refused(f"frozen manifest identifies as {manifest.get('case_id')!r}, expected {case_id}")
    audio_meta = manifest["audio"]
    ref_path = reference_override or (manifest_path.parent / audio_meta["file"])
    try:
        digest = hashlib.sha256(ref_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise Refused(f"cannot read frozen {case_id} audio {ref_path.name}: {exc}") from exc
    if digest != audio_meta["sha256"]:
        raise Refused(f"frozen {case_id} Mini V3 audio hash mismatch")
    sr, ref_pcm = wavfile.read(ref_path)
    if sr != SR or ref_pcm.dtype != np.float32 or ref_pcm.ndim != 1:
        raise Refused(f"{case_id} reference WAV is not mono float32 at 48 kHz")
    ref_pcm = ref_pcm.astype(np.float64)
    if len(ref_pcm) != audio_meta["samples"] or not np.isfinite(ref_pcm).all():
        raise Refused("reference sample count or finite-sample precondition failed")

    candidate_pcm = None
    candidate_sha256 = None
    if candidate_wav is not None:
        if inject:
            raise Refused("Mono model/reference injection cannot alter supplied decoded I2S audio")
        required_samples = int(round(manifest["timeline"]["audio_duration_s"] * SR))
        candidate_pcm, candidate_sha256 = _load_i2s_candidate(candidate_wav, required_samples)

    # Integrated scoring must measure the samples that arrived from I2S. The
    # software voice is only needed for the model-only comparison path.
    patch = _voice_patch(manifest) if candidate_pcm is None else None
    if saw_cutoff_override is not None:
        if candidate_pcm is not None:
            raise Refused("saw cutoff override applies only to model renders")
        if isinstance(saw_cutoff_override, bool) or not isinstance(saw_cutoff_override, int):
            raise Refused("saw cutoff override must be an integer Hz value")
        if not vf.CUT_MIN <= saw_cutoff_override <= vf.CUT_MAX:
            raise Refused(f"saw cutoff override outside [{vf.CUT_MIN}, {vf.CUT_MAX}]")
    if (not isinstance(saw_volume_correction_db, (int, float))
            or isinstance(saw_volume_correction_db, bool)
            or not math.isfinite(saw_volume_correction_db)
            or not -12.0 <= saw_volume_correction_db <= 12.0):
        raise Refused("saw volume correction must be finite and within [-12, 12] dB")
    if candidate_pcm is not None and saw_volume_correction_db != 0.0:
        raise Refused("saw volume correction applies only to model renders")
    tolerance = TOLERANCES
    observed = {name: [] for name in tolerance}
    event_diagnostics = []
    renders = []
    executed_voice_flags = None
    model_parts = []
    silence = int(round(manifest["timeline"]["segment_silence_s"] * SR))
    for seg in manifest["timeline"]["segments"]:
        wave = seg["wave"]
        offset = int(seg["offset_samples"])
        if candidate_pcm is None:
            seq = []
            segment_patch = _patch_for_wave(patch, wave, pulse_shape)
            if wave == "saw" and saw_cutoff_override is not None:
                segment_patch = {**segment_patch,
                                 "cutoff": (saw_cutoff_override, saw_cutoff_override)}
            if wave == "saw" and saw_volume_correction_db != 0.0:
                base_volume = float(segment_patch["vol"])
                corrected_volume = base_volume * 10.0 ** (saw_volume_correction_db / 20.0)
                if not 0.0 <= corrected_volume <= 1.0:
                    raise Refused("saw volume correction would leave the supported 0..1 range")
                segment_patch = {**segment_patch, "vol": corrected_volume}
            for ev in seg["midi_events"]:
                seq.append((float(ev["on_s"]), _model_note(int(ev["note"]), inject), float(ev["gate_s"]),
                            {**segment_patch, "gate": float(ev["gate_s"])}))
            duration = float(seg["duration_s"])
            voice = (_voice_for_engine(engine_profile) if voice_factory is None
                     else voice_factory())
            flags = {
                "oscillator_oversample_2x": bool(voice.oversample_2x),
                "oscillator_pulse_oversample_2x": bool(voice.oversample_pulse_2x),
                "filter_rate_converted": bool(voice.rate_converted_ladder),
                "filter_preserve_headroom": bool(voice.preserve_filter_headroom),
                "filter_causal": bool(voice.causal_filter),
                "pulse479_filter_candidate": bool(voice.pulse479_filter_candidate),
                "filter_g_exact": bool(voice.g_exact),
                "filter_k_comp": bool(voice.k_comp),
                "filter_oversample_factor": (int(voice.ladder_cfg.get("oversample", 2))
                                             if voice.rate_converted_ladder else 1),
                "ladder_coefficient_oversample": int(voice.ladder_cfg.get("oversample", 2)),
            }
            if executed_voice_flags is not None and flags != executed_voice_flags:
                raise Refused("Mono phrase segments used inconsistent engine flags")
            executed_voice_flags = flags
            pcm = vf.render_mono_fx(seq, duration, voice)
            ours = np.asarray(pcm, dtype=np.float64) / 32768.0
            samples_before_trim = len(pcm)
        else:
            end = offset + int(seg["samples"])
            if end > len(candidate_pcm):
                raise Refused(f"decoded I2S WAV does not cover {wave} segment {offset}..{end}")
            ours = candidate_pcm[offset:end]
            samples_before_trim = len(ours)
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
                        "trimmed_candidate_tail_samples": max(0, samples_before_trim - common)})
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
            model_cents = _cents_error(eo.value, f0)
            reference_cents = _cents_error(er.value, f0)
            observed["Pitch"].append((model_cents - reference_cents, 0.0))
            so = am.harmonic_signature(xo, SR, f0=eo.value, kmax=12)
            sr_ = am.harmonic_signature(xr, SR, f0=er.value, kmax=12)
            harmonic_results = {f"h{k}": _harmonic_error(so, sr_, k)
                                for k in range(2, 13)
                                if k * max(eo.value, er.value) < SR / 2}
            diffs = [error for error, _status in harmonic_results.values()
                     if error is not None]
            if len(diffs) < 2:
                raise Refused(f"{wave} MIDI {note} has fewer than two comparable partials")
            harmonic_diffs = {key: round(float(error), 4)
                              for key, (error, _status) in harmonic_results.items()
                              if error is not None}
            harmonic_status = {key: status for key, (_error, status)
                               in harmonic_results.items()}
            # Store maximum absolute partial error as the case value/reference pair.
            observed["Harmonic shape"].append((max(abs(x) for x in diffs), 0.0))
            fo, fr = am.foldback_alias_db(xo, eo.value, SR), am.foldback_alias_db(xr, er.value, SR)
            if not fo.ok or not fr.ok:
                raise Refused(f"{wave} MIDI {note} foldback estimator refused")
            observed["Foldback energy"].append((_excess_alias_db(fo.value, fr.value), 0.0))
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
                "model_midi": _model_note(note, inject),
                "stages": (None if candidate_sha256 else
                           _stage_diagnostics(voice.trace, pcm, a, b, eo.value)),
                "filter_reconstruction": (None if candidate_sha256 else
                                          voice.trace.get("filter_reconstruction")),
                "filter_decimation": (None if candidate_sha256 else
                                      voice.trace.get("filter_decimation")),
                "pitch_cents_from_midi": {"model": round(model_cents, 5),
                                          "reference": round(reference_cents, 5),
                                          "model_minus_reference": round(model_cents - reference_cents, 5)},
                "harmonic_error_db_model_minus_reference": harmonic_diffs,
                "harmonic_comparison": harmonic_status,
                "foldback_db": {"model": round(float(fo.value), 4),
                                "reference": round(float(fr.value), 4),
                                "excess_over_reference_db": round(
                                    _excess_alias_db(fo.value, fr.value), 4)},
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
    out = ROOT / f"build/scorecard/{case_id}-model.wav" if output_path is None else pathlib.Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    pcm_out = np.clip(combined * 32768.0, -32768, 32767).astype("<i2")
    wavfile.write(out, SR, pcm_out)
    try:
        audio_path = str(out.relative_to(ROOT))
    except ValueError:
        audio_path = str(out)
    return {"analysis_version": ANALYSIS_VERSIONS[case_id],
            "metrics": metrics, "audio": audio_path,
            "model_configuration": {
                **engine_profile,
                **(executed_voice_flags or {}),
                "label": model_label,
                "engine_profile": ("custom" if voice_factory is not None and candidate_pcm is None
                                    else engine),
                "control_defaults_profile": engine,
                "pulse_shape_input": pulse_shape,
                "pulse_effective_waveform": (
                    engine_profile["pulse_effective_waveform"]
                    if pulse_shape == engine_profile["pulse_control_label"]
                    else pulse_shape),
                "pulse_effective_duty_percent": (
                    engine_profile["pulse_effective_duty_percent"]
                    if pulse_shape == engine_profile["pulse_control_label"]
                    else 100.0 * vf.DUTY[pulse_shape] / vf.CYCLE),
                "envelope_calibration_source": f"frozen {case_id} Mini V3 measurements",
                "injection": inject or None,
                "saw_cutoff_override_hz": saw_cutoff_override,
                "saw_volume_correction_db": float(saw_volume_correction_db)},
            "reference_sha256": digest, "manifest_sha256": manifest_digest,
            "cutoff_calibration": manifest["patch"]["cutoff_measurement"],
            "model_segments": renders,
            "event_diagnostics": event_diagnostics,
            "wrong_then_right": manifest["qualification"]["wrong_then_right"],
            "duration_s": len(combined) / SR,
        "candidate_i2s_sha256": candidate_sha256,
            "note": ("Metrics use decoded SPI-to-I2S samples." if candidate_sha256 else
                 ("Fixed integer model comparison; M5A also has a separate SPI-to-I2S smoke."
                  if case_id == "M5A" else
                  "Fixed integer model comparison only; no M5B SPI-to-I2S evidence is claimed."))}
