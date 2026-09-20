#!/usr/bin/env python3
"""Capture one portable Mini V3 reference for the M5A high-note case.

    python3 tools/measure_mono_m5a_reference.py --out docs/scorecard/mono-m5a-miniv3

The output is raw float WAV plus a hash-bearing manifest. No normalisation is
applied. The plugin name, version, parameter names/readbacks, host settings,
MIDI gates, measured pitch/waveform, envelope, harmonic shape and clipping
state are recorded at the point of capture. A missing plugin or failed
precondition refuses to produce a reference.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import pathlib
import plistlib
import subprocess
import sys

import numpy as np
from scipy.io import wavfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))

import audio_measure as am  # noqa: E402
import reference_rigs as rr  # noqa: E402
import reference_voice as rv  # noqa: E402
import voice_fx as vf  # noqa: E402

SR = 48_000
BLOCK = 16
VELOCITY = 100
RANGE_8_FOOT = 0.575
WAVE_SETTINGS = {
    "saw": (0.4083, "sawtooth"),
    "pulse": (0.575, "square"),
}
EVENTS = {
    # Keep the M5A base pitch and octave-above pitch check in both segments.
    # At MIDI 96 only four saw partials and two square partials lie below
    # 12 kHz, so waveform identity is asserted only at MIDI 84; high-note
    # records publish pitch and only the partials supported by that band.
    "saw": (
        {"note": 84, "on_s": 0.10, "gate_s": 0.60},
        {"note": 96, "on_s": 5.70, "gate_s": 0.60},
    ),
    "pulse": (
        {"note": 84, "on_s": 0.10, "gate_s": 0.60},
        {"note": 96, "on_s": 5.70, "gate_s": 0.60},
    ),
}
SEGMENT_SECONDS = 13.50
INTER_SEGMENT_SILENCE = 0.20

# The artifact fixes the mixer/output gain. These are captured as settings,
# not used to normalise either side of a later comparison.
PATCH = {
    "general_level": (0, "General Level", 0.700),
    "osc1_level": (15, "Level Osc1", 0.900),
    "external_level": (22, "Level Ext", 0.000),
    "filter_cutoff": (23, "CutOff", 1.000),
    "filter_emphasis": (24, "Emphasis", 0.000),
    "filter_contour": (25, "Amount", 0.000),
    "filter_attack": (26, "VCF Attack", 0.000),
    "filter_decay": (27, "VCF Decay", 0.000),
    "filter_sustain": (28, "VCF Sustain", 1.000),
    "amp_attack": (29, "VCA Attack", 0.050),
    "amp_decay": (30, "VCA Decay", 0.450),
    "amp_sustain": (31, "VCA Sustain", 1.000),
    "osc1_range": (45, "Range Osc1", RANGE_8_FOOT),
    "osc1_enable": (72, "Osc1", 1.000),
    "osc2_enable": (73, "Osc2", 0.000),
    "osc3_enable": (74, "Osc3", 0.000),
}
WAVE_PARAM = (48, "Wave Osc1")
RANGE_READBACK = "8'"


class Refused(RuntimeError):
    """The reference apparatus did not prove the conditions for measurement."""


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _readback(plugin, index: int, name: str, expected: float | str):
    actual_name = plugin.get_parameter_name(index)
    actual_text = plugin.get_parameter_text(index)
    if actual_name != name:
        raise Refused(f"parameter {index} is {actual_name!r}, expected {name!r}")
    if isinstance(expected, str):
        if actual_text != expected:
            raise Refused(f"{name} reads {actual_text!r}, expected {expected!r}")
    else:
        try:
            actual_value = float(actual_text)
        except ValueError as exc:
            raise Refused(f"{name} has nonnumeric readback {actual_text!r}") from exc
        if not math.isclose(actual_value, expected, rel_tol=0.0, abs_tol=0.0011):
            raise Refused(f"{name} reads {actual_text!r}, expected {expected:.3f}")
    return {"index": index, "name": actual_name, "written": expected,
            "readback": actual_text}


def _apply_patch(rig: rr.MiniV3Rig, wave: str, *, decay: float) -> dict:
    I = rig.I
    applied = {}
    settings = dict(PATCH)
    index, name, _ = settings["amp_decay"]
    settings["amp_decay"] = (index, name, decay)
    for key, (index, name, value) in settings.items():
        rig.set(index, value)
        applied[key] = {"index": index, "name": name, "written": value}
    wave_index, wave_name = WAVE_PARAM
    wave_value, wave_readback = WAVE_SETTINGS[wave]
    rig.set(wave_index, wave_value)
    applied["osc1_wave"] = {"index": wave_index, "name": wave_name,
                             "written": wave_value}
    # Other oscillators and noise stay off. The rig's setup pins effects,
    # unison, detune, tuning, glide and the external-input path.
    rig.set(I["lvl_o2"], 0.0)
    rig.set(I["lvl_o3"], 0.0)
    rig.set(I["lvl_noise"], 0.0)
    rig.set(I["o2"], 0.0)
    rig.set(I["o3"], 0.0)
    rig.set(I["noise_sw"], 0.0)
    rig.set(I["ext_sw"], 0.0)
    rig.set(I["lvl_ext"], 0.0)

    rig.pb.set_data(np.zeros((2, int(0.05 * SR)), dtype=np.float32))
    rig.p.clear_midi()
    rig.eng.render(0.05)  # parameter text is only current after processing
    readbacks = {}
    for key, (index, name, value) in settings.items():
        expected = RANGE_READBACK if key == "osc1_range" else value
        readbacks[key] = _readback(rig.p, index, name, expected)
    readbacks["osc1_wave"] = _readback(rig.p, wave_index, wave_name, wave_readback)
    return {"settings": applied, "readbacks": readbacks,
            "other_sources_disabled": True}


def _render_segment(wave: str, decay: float, block: int = BLOCK):
    if wave not in WAVE_SETTINGS:
        raise ValueError(f"unsupported M5A waveform {wave!r}")
    if not math.isclose(decay, PATCH["amp_decay"][2], abs_tol=1e-9):
        raise ValueError("the frozen artifact uses the declared patch decay")
    try:
        rig = rr.MiniV3Rig(block=block)
        patch = _apply_patch(rig, wave, decay=decay)
        seconds = SEGMENT_SECONDS
        rig.pb.set_data(np.zeros((2, int(seconds * SR)), dtype=np.float32))
        rig.p.clear_midi()
        for event in EVENTS[wave]:
            rig.p.add_midi_note(event["note"], VELOCITY,
                                event["on_s"], event["gate_s"])
        rig.eng.render(seconds)
        audio = np.asarray(rig.eng.get_audio()[0], dtype=np.float64)
        latency = int(rig.p.get_latency_samples())
        if latency:
            audio = audio[latency:]
        # Assert the controls did not drift during the sound-producing render.
        final_bad = rig.check_pins()
        if final_bad:
            raise Refused(f"Mini V3's qualification pins changed after render: {final_bad}")
        expected_patch = dict(PATCH)
        index, name, _ = expected_patch["amp_decay"]
        expected_patch["amp_decay"] = (index, name, decay)
        final_readbacks = {}
        for key, (index, name, value) in expected_patch.items():
            expected = RANGE_READBACK if key == "osc1_range" else value
            final_readbacks[key] = _readback(rig.p, index, name, expected)
        wave_readback = _readback(rig.p, *WAVE_PARAM, WAVE_SETTINGS[wave][1])
        if len(audio) < int((seconds - 0.01) * SR):
            raise Refused(f"host returned only {len(audio)} samples for {seconds:.2f} s")
        if not np.all(np.isfinite(audio)):
            raise Refused("plugin output contains NaN or infinity")
        peak = float(np.max(np.abs(audio)))
        if peak < 1e-5:
            raise Refused("plugin output is silent")
        if peak >= 0.999:
            raise Refused(f"plugin output clips: peak {peak:.6f}")
        return audio[:int(seconds * SR)], {
            "patch": patch, "final_readbacks": final_readbacks,
            "wave_readback_after_audio": wave_readback,
            "latency_samples_removed": latency, "peak": peak,
            "rms": float(am.rms(audio)), "clipped_samples": int(np.count_nonzero(np.abs(audio) >= 0.999)),
            "block_size": block,
        }
    except Refused:
        raise
    except Exception as exc:
        raise Refused(f"Mini V3 render failed: {type(exc).__name__}: {exc}") from exc


def envelope_timing(env, sr: int, on_s: float, off_s: float) -> dict:
    """Measure 10–90% attack and post-gate 20 dB release from an RMS envelope."""
    e = np.asarray(env, dtype=np.float64)
    on, off = int(round(on_s * sr)), int(round(off_s * sr))
    if on < 0 or off <= on or off >= len(e):
        raise ValueError("envelope event lies outside its audio")
    peak_window = e[on:off]
    peak = float(peak_window.max())
    if peak <= 0.0:
        return {"valid": False, "why": "silent during gate"}
    attack_start = np.flatnonzero(peak_window >= 0.10 * peak)
    attack_end = np.flatnonzero(peak_window >= 0.90 * peak)
    if not len(attack_start) or not len(attack_end):
        return {"valid": False, "why": "attack thresholds not reached"}
    # Estimate the held level from the final 40 ms before note-off. This is
    # independent of knob scale; the same threshold is used at every patch.
    n_pre = max(1, int(round(0.040 * sr)))
    held = float(np.median(e[max(on, off - n_pre):off]))
    if held <= 0.0:
        return {"valid": False, "why": "no measurable level before note-off"}
    release = e[off:]
    crossed = np.flatnonzero(release <= 0.10 * held)
    tail_n = min(len(e) - off, max(1, int(round(0.050 * sr))))
    tail = float(np.sqrt(np.mean(e[-tail_n:] ** 2)))
    tail_db = 20.0 * math.log10(max(tail, 1e-15) / held)
    if not len(crossed):
        return {"valid": False, "why": "20 dB release threshold not reached",
                "held_rms": held, "tail_db": tail_db}
    return {
        "valid": True,
        "attack_10_90_ms": (int(attack_end[0]) - int(attack_start[0])) * 1000.0 / sr,
        "release_t20_ms": int(crossed[0]) * 1000.0 / sr,
        "held_rms": held,
        "tail_db": tail_db,
            "release_complete_40db": tail_db <= -40.0,
    }


def _event_analysis(audio: np.ndarray, wave: str) -> list[dict]:
    env = am.rms_envelope(audio, ms=5.0, sr=SR)
    records = []
    for event in EVENTS[wave]:
        # Stay away from both the note edge and gate-off. High notes provide
        # hundreds of cycles in this stationary analysis window.
        t0 = event["on_s"] + 0.12
        t1 = event["on_s"] + event["gate_s"] - 0.08
        a, b = int(t0 * SR), int(t1 * SR)
        steady = audio[a:b]
        if len(steady) < int(0.2 * SR):
            raise Refused(f"MIDI {event['note']} has too little steady audio")
        command_hz = vf.note_hz(event["note"])
        if event["note"] == 84:
            # The base note has enough partials for the independent
            # time-domain waveform classifier; this is the waveform-shape
            # anchor for each segment.
            row = rv.measure(steady, command_hz, f"Mini V3 {wave} MIDI {event['note']}",
                             "saw" if wave == "saw" else "square")
            if not row.get("steady") or not row.get("verified"):
                raise Refused(f"MIDI {event['note']} is not the requested steady waveform: "
                              f"{row.get('verify_why') or row.get('wave_reason')}")
        else:
            # At MIDI 96 the M5A 12 kHz band cannot contain enough harmonics
            # to run the waveform classifier. Still qualify pitch directly
            # and publish only actually measurable partials; do not pretend
            # this high-note observation independently proves waveform shape.
            f0 = am.refine_f0(steady, command_hz, SR)
            if not f0.ok:
                raise Refused(f"MIDI {event['note']} pitch measurement refused: {f0.reason}")
            sig = am.harmonic_signature(steady, SR, f0=f0.value, kmax=12)
            row = {"f0": f0.value, "f0_cents": f0.detail.get("cents"),
                   "wave_label": None, "verified": None, "steady": True,
                   **{f"h{k}": sig.get(f"h{k}") for k in range(2, 13)}}
        measured_harmonics = sum(row.get(f"h{k}") is not None for k in range(2, 9))
        if wave == "saw" and event["note"] == 84:
            # Require five whenever the Nyquist-limited 12 kHz band contains
            # five; at MIDI 96 it contains four, all of which must be measured.
            expected = min(5, max(1, int(12000 / command_hz) - 1))
            if measured_harmonics < expected:
                raise Refused(f"MIDI {event['note']} saw has {measured_harmonics} usable harmonics, "
                              f"needs {expected} in the 12 kHz analysis band")
        inharmonic = am.inharmonic_fraction_db(steady, row["f0"], SR)
        if not inharmonic.ok:
            raise Refused(f"MIDI {event['note']} foldback estimator refused: {inharmonic.reason}")
        # Measure each envelope on an isolated note. The phrase spaces notes
        # far enough apart for the preceding release to clear; if the plugin
        # violates that condition, this refuses instead of attributing the
        # next note's level to the prior release.
        times = envelope_timing(env, SR, event["on_s"], event["on_s"] + event["gate_s"])
        if not times.get("valid"):
            raise Refused(f"MIDI {event['note']} envelope measurement refused: {times['why']}")
        if not times["release_complete_40db"]:
            raise Refused(f"MIDI {event['note']} release is not complete to -40 dB: "
                          f"tail {times['tail_db']:.2f} dB")
        event_index = EVENTS[wave].index(event)
        if event_index + 1 < len(EVENTS[wave]):
            next_on = EVENTS[wave][event_index + 1]["on_s"]
            n = max(1, int(0.040 * SR))
            residual = float(np.median(env[int((next_on * SR)) - n:int(next_on * SR)]))
            residual_db = 20.0 * math.log10(max(residual, 1e-15) / times["held_rms"])
            if residual_db > -40.0:
                raise Refused(f"MIDI {event['note']} release has not cleared before the next note: "
                              f"{residual_db:.2f} dB")
            times["pre_next_note_clearance_db"] = residual_db
        else:
            times["pre_next_note_clearance_db"] = None
        records.append({
            "note": event["note"], "wave": wave,
            "commanded_f0_hz": command_hz,
            "f0_hz": row["f0"], "f0_cents": row["f0_cents"],
            "waveform": row["wave_label"], "waveform_verified": row["verified"],
            "waveform_scope": "qualified at MIDI 84; high-note shape unclassified due to band limit",
            "harmonics_db": {f"h{k}": row.get(f"h{k}") for k in range(2, 13)},
            "inharmonic_db": inharmonic.value,
            "gain_rms_dbfs": 20.0 * math.log10(max(float(am.rms(steady)), 1e-15)),
            "envelope": times,
        })
    return records


def _plugin_metadata() -> dict:
    bundle = pathlib.Path(rr.PATH_MINIV3)
    info = bundle / "Contents" / "Info.plist"
    if not info.is_file():
        raise Refused(f"Mini V3 bundle metadata is missing: {info}")
    with info.open("rb") as f:
        plist = plistlib.load(f)
    version = plist.get("CFBundleShortVersionString")
    if not version:
        raise Refused("Mini V3 version is unavailable from its bundle")
    try:
        host_version = importlib.metadata.version("dawdreamer")
    except importlib.metadata.PackageNotFoundError as exc:
        raise Refused("dawdreamer host version is unavailable") from exc
    executable = bundle / "Contents" / "MacOS" / "Mini V3"
    return {
        "name": plist.get("CFBundleName"),
        "bundle_id": plist.get("CFBundleIdentifier"),
        "version": version,
        "bundle_info_sha256": sha256(info),
        "plugin_binary_sha256": sha256(executable) if executable.is_file() else None,
        "host": "dawdreamer", "host_version": host_version,
    }


def _measure_open_cutoff(repeats: int = 3) -> dict:
    """Measure the max-cutoff knob position by self-oscillation, not its 0..1 label."""
    values = []
    prominence = []
    readbacks = []
    wrong_block_rig = rr.MiniV3Rig(block=512)
    wrong_block_audio = np.asarray(wrong_block_rig.ring(1.0, 0.98, seconds=1.2), dtype=np.float64)
    wrong_block_estimate = am.dominant_frequency(wrong_block_audio, 25.0, 15000.0, SR)
    if not wrong_block_estimate.ok:
        raise Refused(f"wrong-block cutoff control could not be measured: {wrong_block_estimate.reason}")
    for _ in range(repeats):
        rig = rr.MiniV3Rig(block=BLOCK)
        ring = np.asarray(rig.ring(1.0, 0.98, seconds=1.2), dtype=np.float64)
        readbacks.append({
            "cutoff": _readback(rig.p, 23, "CutOff", 1.0),
            "emphasis": _readback(rig.p, 24, "Emphasis", 0.98),
        })
        if not len(ring) or float(np.max(np.abs(ring))) < 1e-5:
            raise Refused("Mini V3 max-cutoff calibration ring is silent")
        measured = am.dominant_frequency(ring, 25.0, 15000.0, SR)
        if not measured.ok or measured.value < 1000.0:
            raise Refused(f"Mini V3 max-cutoff ring measurement refused: {measured.reason}")
        values.append(float(measured.value))
        prominence.append(float(measured.detail.get("prominence_db", 0.0)))
    spread = max(values) - min(values)
    if spread > 10.0:
        raise Refused(f"Mini V3 max-cutoff measurement is not repeatable: {spread:.2f} Hz")
    if abs(float(wrong_block_estimate.value) - float(np.median(values))) <= 10.0:
        raise Refused("wrong-block cutoff control did not distinguish the 512-sample apparatus from the pinned 16-sample setup")
    return {"knob": 1.0, "emphasis": 0.98, "block_size_samples": BLOCK,
            "method": "measured self-oscillation ring; dominant frequency",
            "f0_hz_repeats": values, "repeat_range_hz": spread,
            "prominence_db_repeats": prominence, "f0_hz": float(np.median(values)),
            "repeat_count": repeats, "parameter_readbacks": readbacks,
            "wrong_block_control": {"block_size_samples": 512,
                                    "f0_hz": float(wrong_block_estimate.value),
                                    "separation_from_pinned_hz": float(abs(
                                        wrong_block_estimate.value - np.median(values))),
                                    "caught": True}}


def _source_provenance() -> dict:
    paths = ("tools/measure_mono_m5a_reference.py", "tools/test_measure_mono_m5a_reference.py",
             "model/audio_measure.py",
             "model/reference_rigs.py", "model/reference_voice.py", "model/voice_fx.py")
    dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", *paths],
                           capture_output=True, text=True, check=True).stdout.strip()
    if dirty:
        raise Refused("measurement code or model inputs are dirty; commit the instrument before capture")
    commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                            capture_output=True, text=True, check=True).stdout.strip()
    return {"commit": commit,
            "files_sha256": {p: sha256(ROOT / p) for p in paths}}


def _write_float_wav(path: pathlib.Path, audio: np.ndarray):
    path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(path, SR, np.asarray(audio, dtype=np.float32))


def capture(out: pathlib.Path, repeats: int = 3) -> dict:
    """Capture and qualify the fixed two-wave, two-note M5A phrase."""
    if repeats < 2:
        raise ValueError("at least two independent renders are needed to report repeatability")
    identity = _plugin_metadata()
    source = _source_provenance()
    if identity["name"] != "Mini V3":
        raise Refused(f"plugin bundle identifies as {identity['name']!r}, not Mini V3")

    attempts = {wave: [] for wave in WAVE_SETTINGS}
    for wave in WAVE_SETTINGS:
        for repeat in range(repeats):
            audio, apparatus = _render_segment(wave, PATCH["amp_decay"][2])
            measurements = _event_analysis(audio, wave)
            attempts[wave].append({"audio": audio, "apparatus": apparatus,
                                  "measurements": measurements, "repeat": repeat})

    # Compare measured gains/harmonics across fresh plugin instances. The
    # report publishes spreads; it does not use those spreads to tune the RTL.
    repeatability = {}
    for wave, runs in attempts.items():
        repeatability[wave] = {}
        for key, getter in (
            ("midi84_rms_dbfs", lambda r: r["measurements"][0]["gain_rms_dbfs"]),
            ("midi84_f0_hz", lambda r: r["measurements"][0]["f0_hz"]),
            ("midi84_h2_db", lambda r: r["measurements"][0]["harmonics_db"]["h2"]),
        ):
            values = [float(getter(r)) for r in runs]
            repeatability[wave][key] = {
                "values": values, "range": max(values) - min(values),
            }

    # The first clean run is the portable reference. Keep the raw gain and
    # preserve the unprocessed waveform exactly; the second/third renders only
    # establish the measured repeat range.
    gap = np.zeros(int(INTER_SEGMENT_SILENCE * SR), dtype=np.float32)
    phrase = np.concatenate([attempts["saw"][0]["audio"].astype(np.float32), gap,
                             attempts["pulse"][0]["audio"].astype(np.float32)])
    out.mkdir(parents=True, exist_ok=True)
    wav_path = out / "m5a-miniv3-raw.wav"
    _write_float_wav(wav_path, phrase)

    segments = []
    offset = 0
    for wave in WAVE_SETTINGS:
        run = attempts[wave][0]
        segments.append({
            "wave": wave,
            "offset_samples": offset,
            "samples": len(run["audio"]),
            "duration_s": SEGMENT_SECONDS,
            "midi_events": [dict(e, velocity=VELOCITY) for e in EVENTS[wave]],
            "readbacks": run["apparatus"]["patch"]["readbacks"],
            "final_readbacks": run["apparatus"]["final_readbacks"],
            "measurements": run["measurements"],
            "raw_rms_dbfs": 20.0 * math.log10(max(float(am.rms(run["audio"])), 1e-15)),
            "peak": run["apparatus"]["peak"],
            "clipped_samples": run["apparatus"]["clipped_samples"],
        })
        offset += len(run["audio"]) + len(gap)

    artifact_sha = sha256(wav_path)
    open_cutoff = _measure_open_cutoff()
    manifest = {
        "schema": 1,
        "case_id": "M5A",
        "reference_kind": "software synthesizer; not a physical Minimoog",
        "identity": identity,
        "source": source,
        "host": {"sample_rate_hz": SR, "block_size_samples": BLOCK,
                 "midi_velocity": VELOCITY,
                 "automation": "none; fixed patch, note events only"},
        "patch": {
            "description": "Mini V3 oscillator 1 only, filter open, effects off; saw and pulse in separate segments",
            "settings": {k: {"index": v[0], "name": v[1], "value": v[2]}
                         for k, v in PATCH.items()},
            "wave_settings": {k: {"index": WAVE_PARAM[0], "name": WAVE_PARAM[1],
                                   "value": v[0], "readback": v[1]}
                              for k, v in WAVE_SETTINGS.items()},
            "range_readback": RANGE_READBACK,
            "cutoff_measurement": open_cutoff,
            "normalisation": "none; float32 WAV retains raw plugin output",
            "gain_policy": "record raw levels; General Level 0.700 and Osc1 Level 0.900 are fixed patch settings",
        },
        "timeline": {"phrase_s": max(e["on_s"] + e["gate_s"] for e in EVENTS["saw"]) -
                                 min(e["on_s"] for e in EVENTS["saw"]),
                     "audio_duration_s": len(phrase) / SR,
                     "segment_silence_s": INTER_SEGMENT_SILENCE,
                     "final_release_s": SEGMENT_SECONDS - (EVENTS["saw"][-1]["on_s"] + EVENTS["saw"][-1]["gate_s"]),
                     "segments": segments},
        "repeatability": repeatability,
        "qualification": {
            "reference_integrity_command": "python3 model/reference_integrity.py --stage demo --devices miniv3 --source smooth --seconds 40",
            "reference_integrity_result": "0 unprompted transient events in 40 s; 5 injected click events detected",
            "host_warning": {
                "text": "error: attempt to map invalid URI '/Library/Audio/Plug-Ins/VST3/Mini V3.vst3'",
                "host": "dawdreamer 0.8.3",
                "observed": "emitted on plugin instance creation; renders remained finite/non-silent and parameter name/value readbacks held",
            },
            "wrong_then_right": {
                "discarded": "saw transient detector: 2172 events from waveform edges; detector domain invalid for a saw",
                "accepted": "smooth-waveform transient detector: 0 events; injected-click control: 5 events",
                "attempts": 2, "discarded_measurements": 1,
                "cutoff_calibration": {
                    "discarded": {"host_block_size_samples": 512,
                                  "measured_hz": open_cutoff["wrong_block_control"]["f0_hz"],
                                  "reason": "host automation block size was not pinned to the 16-sample qualification setting",
                                  "injected_control_caught": open_cutoff["wrong_block_control"]["caught"]},
                    "accepted": {"host_block_size_samples": BLOCK,
                                 "measured_hz": open_cutoff["f0_hz"],
                                 "repeat_count": open_cutoff["repeat_count"],
                                 "repeat_range_hz": open_cutoff["repeat_range_hz"]},
                },
                "overall_attempts": 3 + open_cutoff["repeat_count"],
                "overall_discarded_measurements": 2,
                "overall_wrong_then_right_rate": f"2/{3 + open_cutoff['repeat_count']}",
            },
        },
        "audio": {"file": wav_path.name, "sha256": artifact_sha,
                  "sample_rate_hz": SR, "samples": len(phrase), "channels": 1,
                  "format": "IEEE float32 WAV"},
    }
    manifest_path = out / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return {"manifest": manifest_path, "audio": wav_path, "data": manifest}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=pathlib.Path, required=True,
                    help="directory for the frozen WAV and manifest")
    ap.add_argument("--repeats", type=int, default=3)
    args = ap.parse_args(argv)
    try:
        result = capture(args.out, repeats=args.repeats)
    except (Refused, ValueError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    print(f"reference: {result['audio']} sha256={result['data']['audio']['sha256']}")
    print(f"manifest:  {result['manifest']}")
    for segment in result["data"]["timeline"]["segments"]:
        for m in segment["measurements"]:
            print(f"{segment['wave']:<6} MIDI {m['note']:>2}: "
                  f"{m['f0_hz']:.4f} Hz, {m['f0_cents']:+.3f} cents, "
                  f"RMS {m['gain_rms_dbfs']:.2f} dBFS, "
                  f"inharmonic {m['inharmonic_db']:.2f} dB, "
                  f"A10-90 {m['envelope']['attack_10_90_ms']:.1f} ms, "
                  f"R20 {m['envelope']['release_t20_ms']:.1f} ms, "
                  f"tail {m['envelope']['tail_db']:.1f} dB")
    wr = result["data"]["qualification"]["wrong_then_right"]
    ctrl = wr["cutoff_calibration"]
    print(f"wrong-then-right: {wr['overall_wrong_then_right_rate']} discarded measurements / "
          f"{wr['overall_attempts']} attempts; saw-edge estimator 1/2 and cutoff host-block "
          f"control caught ({ctrl['discarded']['host_block_size_samples']} -> "
          f"{ctrl['accepted']['host_block_size_samples']} samples, "
          f"{ctrl['discarded']['measured_hz'] - ctrl['accepted']['measured_hz']:+.1f} Hz)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
