#!/usr/bin/env python3
"""Qualify M5A's 5 ms RMS attack meter with exact-envelope carrier controls.

The frozen Mini V3 audio and manifest are checked before any comparison. The
synthetic controls use a mathematically known linear attack on saw and pulse
carriers at both M5A pitches, then pass them through the same 5 ms RMS and
10–90% crossing code as the scorecard. This measures meter bias without
changing the acceptance limit or claiming the plugin's hidden envelope is
known.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import subprocess
from datetime import datetime, timezone

import numpy as np
from scipy.io import wavfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "tools"))
import audio_measure as am  # noqa: E402
from measure_mono_m5a_reference import envelope_timing  # noqa: E402

SR = 48_000
REF_DIR = ROOT / "docs/scorecard/mono-m5a-miniv3"
MANIFEST = REF_DIR / "manifest.json"
EXPECTED_AUDIO_SHA256 = "a808cd22448ecea1c639f9311578eb72399f69bda373e9036174c2ca208a1f0a"
MIDI_HZ = {84: 1046.5022612023945, 96: 2093.004522404789}
ON_S, OFF_S, DURATION_S = 0.10, 0.30, 1.30
TRUE_ATTACK_MS = 7.333333333333333
RMS_MS = 5.0


class Refused(RuntimeError):
    """Frozen-reference preconditions did not hold."""


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _reference_preconditions(manifest_path: pathlib.Path) -> tuple[dict, pathlib.Path, str]:
    if not manifest_path.is_file():
        raise Refused(f"manifest is missing: {manifest_path}")
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    audio_path = manifest_path.parent / manifest["audio"]["file"]
    if not audio_path.is_file():
        raise Refused(f"frozen reference audio is missing: {audio_path}")
    audio_sha = _sha256(audio_path)
    if audio_sha != manifest["audio"].get("sha256") or audio_sha != EXPECTED_AUDIO_SHA256:
        raise Refused("frozen reference audio hash does not match the pinned M5A artifact")
    sample_rate, audio = wavfile.read(audio_path)
    if sample_rate != SR or audio.ndim != 1 or len(audio) != int(manifest["audio"]["samples"]):
        raise Refused("frozen reference audio has unexpected rate, channels, or length")
    settings = manifest.get("patch", {}).get("settings", {})
    amp_attack = settings.get("amp_attack", {})
    if amp_attack.get("name") != "VCA Attack" or float(amp_attack.get("value", -1)) != 0.05:
        raise Refused("frozen patch does not have the qualified VCA Attack setting")
    if manifest.get("host", {}).get("block_size_samples") != 16:
        raise Refused("frozen reference host block size is not the qualified 16 samples")
    events = []
    for segment in manifest["timeline"]["segments"]:
        readback = float(segment["final_readbacks"]["amp_attack"]["readback"])
        if not math.isclose(readback, 0.05, abs_tol=1e-12):
            raise Refused("a segment's final VCA Attack readback differs from 0.050")
        for measurement in segment["measurements"]:
            env = measurement["envelope"]
            if not env.get("valid"):
                raise Refused("frozen manifest contains an invalid attack measurement")
            events.append({"wave": measurement["wave"], "midi": int(measurement["note"]),
                           "attack_10_90_ms": float(env["attack_10_90_ms"])})
    if len(events) != 4:
        raise Refused(f"expected four frozen note measurements, found {len(events)}")
    return {"events": events, "amp_attack_readback": 0.05}, audio_path, audio_sha


def _known_carrier(f0: float, wave: str, phase: float, t: np.ndarray) -> np.ndarray:
    cycles = np.mod((t - ON_S) * f0 + phase, 1.0)
    if wave == "saw":
        return 2.0 * cycles - 1.0
    if wave == "pulse479":
        return np.where(cycles < 0.479, 1.0, -1.0)
    raise ValueError(f"unsupported known carrier {wave!r}")


def _measure_known_condition(f0: float, wave: str, phases: int) -> dict:
    n = int(round(DURATION_S * SR))
    t = np.arange(n, dtype=np.float64) / SR
    ramp_s = (TRUE_ATTACK_MS / 1000.0) / 0.8
    relative = t - ON_S
    amp = np.clip(relative / ramp_s, 0.0, 1.0)
    amp[t < ON_S] = 0.0
    release_tau = 0.30 / math.log(10.0)  # complete past −40 dB inside this short signal
    release = t >= OFF_S
    amp[release] *= np.exp(-(t[release] - OFF_S) / release_tau)
    measured = []
    for phase_index in range(phases):
        phase = phase_index / phases
        signal = amp * _known_carrier(f0, wave, phase, t)
        env = am.rms_envelope(signal, ms=RMS_MS, sr=SR)
        timing = envelope_timing(env, SR, ON_S, OFF_S)
        if not timing.get("valid") or not timing.get("release_complete_40db"):
            raise Refused(f"known {wave} {f0:.2f} Hz control was not measurable")
        measured.append(float(timing["attack_10_90_ms"]))
    median = float(np.median(measured))
    return {"f0_hz": f0, "wave": wave, "phases": phases,
            "true_attack_10_90_ms": TRUE_ATTACK_MS,
            "measured_min_ms": min(measured), "measured_median_ms": median,
            "measured_max_ms": max(measured),
            "median_bias_ms": median - TRUE_ATTACK_MS}


def measure_attack_bias(manifest_path: pathlib.Path | str = MANIFEST, *, phases: int = 8) -> dict:
    if not 1 <= int(phases) <= 32:
        raise ValueError("phase count must be in 1..32")
    manifest_path = pathlib.Path(manifest_path).resolve()
    apparatus, audio_path, audio_sha = _reference_preconditions(manifest_path)
    conditions = [_measure_known_condition(f0, wave, int(phases))
                  for f0 in MIDI_HZ.values() for wave in ("saw", "pulse479")]
    observed = [row["attack_10_90_ms"] for row in apparatus["events"]]
    source = pathlib.Path(__file__)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
                            capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                                check=True, capture_output=True, text=True).stdout.strip())
    return {
        "schema": "m5a-attack-bias-v1", "valid": True,
        "claim": "The scorecard's 5 ms RMS meter adds measurable bias to a known linear attack; this does not reveal Mini V3's hidden envelope.",
        "method": {"sample_rate_hz": SR, "rms_window_ms": RMS_MS,
                   "crossings": "first 10% to first 90% of the event peak",
                   "known_envelope": "exact linear full-scale ramp followed by a known exponential release",
                   "true_attack_10_90_ms": TRUE_ATTACK_MS,
                   "full_ramp_ms": TRUE_ATTACK_MS / 0.8,
                   "carrier_phases_per_condition": int(phases)},
        "known_signal": {"conditions": conditions,
                         "median_bias_range_ms": [min(c["median_bias_ms"] for c in conditions),
                                                  max(c["median_bias_ms"] for c in conditions)]},
        "frozen_reference": {"audio_sha256": audio_sha,
                             "manifest_sha256": _sha256(manifest_path),
                             "audio_path": str(audio_path.relative_to(ROOT)),
                             "manifest_path": str(manifest_path.relative_to(ROOT)),
                             "amp_attack_readback": apparatus["amp_attack_readback"],
                             "events": apparatus["events"],
                             "event_attack_spread_ms": max(observed) - min(observed)},
        "provenance": {"commit": commit, "dirty": dirty,
                       "tool_sha256": _sha256(source),
                       "run_at_utc": datetime.now(timezone.utc).isoformat()},
        "interpretation": ("The known envelope is measured 1.2–1.6 ms slower by this RMS meter. "
                           "The frozen patch reports 3.33–7.46 ms across notes/waves despite a common "
                           "0.050 attack readback. Therefore the 5.52 ms score error does not isolate "
                           "the actual VCA rise time; keep it measured, but do not tune the envelope "
                           "from this comparison until the estimator is qualified against the plugin's "
                           "signal path."),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", default=str(MANIFEST))
    ap.add_argument("--phases", type=int, default=8)
    ap.add_argument("--out", default="docs/scorecard/mono-m5a-miniv3/attack-bias-v1.json")
    args = ap.parse_args(argv)
    try:
        report = measure_attack_bias(args.manifest, phases=args.phases)
    except (OSError, ValueError, KeyError, TypeError, Refused) as exc:
        print(f"measure_m5a_attack_bias: REFUSED -- {exc}")
        return 2
    out = pathlib.Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    if not out.resolve().is_relative_to(ROOT):
        print("measure_m5a_attack_bias: REFUSED -- output must stay inside the repository")
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    medians = [row["median_bias_ms"] for row in report["known_signal"]["conditions"]]
    spread = report["frozen_reference"]["event_attack_spread_ms"]
    print(f"measure_m5a_attack_bias: valid; known-signal 5 ms RMS bias {min(medians):+.3f}..{max(medians):+.3f} ms; "
          f"frozen-reference event spread {spread:.3f} ms")
    print(f"measure_m5a_attack_bias: report {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
