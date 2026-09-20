#!/usr/bin/env python3
"""Measure one externally grounded mono voice segment.

This is the first qualified slice of scorecard M5A (bright high lead): one
MIDI-84 saw oscillator, filter and envelopes excluded by the frozen stimulus.
The reference is Surge XT's Classic oscillator, rendered through the already
qualified :class:`model.reference_rigs.SurgeRig`; the DUT is the integer
PolyBLEP oscillator used by ``model/voice_fx.py``.  It is deliberately a
component result, not a claim that the full M5A patch is qualified.

The command writes the two WAVs and a JSON record.  It returns 0 for a valid
comparison, 1 for a measured mismatch, and 2 for REFUSED (the apparatus is
not available or a signal fails its qualification preconditions).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sys
import wave

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "audition"))

import audio_measure as am  # noqa: E402
import reference_rigs as rr  # noqa: E402
import voice_fx as vf  # noqa: E402
import oversampled_osc as os2  # noqa: E402

NOTE = 84
SECONDS = 0.70
WAVE = "saw"
SR = rr.SR
REFUSED = 2


class Refused(RuntimeError):
    pass


def _sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _write_wav(path: pathlib.Path, x: np.ndarray) -> None:
    y = np.clip(np.round(np.asarray(x, dtype=float) * 32767.0), -32768, 32767)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SR)
        out.writeframes(y.astype("<i2").tobytes())


def _apparatus() -> dict:
    """Assert the external instrument and its qualified setup are usable."""
    if not pathlib.Path(rr.PATH_SURGE).exists():
        raise Refused(f"Surge XT bundle missing: {rr.PATH_SURGE}")
    try:
        import dawdreamer  # noqa: F401
    except Exception as exc:  # pragma: no cover - host-specific
        raise Refused(f"dawdreamer unavailable: {exc}") from exc
    try:
        rig = rr.SurgeRig("Type 2")
        # osc_tone calls select_osc, which renders and checks oscillator type,
        # shape, width and unison readbacks before returning audio.
        ref = rig.osc_tone(WAVE, NOTE, SECONDS)
    except Exception as exc:  # pragma: no cover - host-specific
        raise Refused(f"qualified Surge apparatus refused: {exc}") from exc
    if len(ref) < int(0.5 * SR) or not np.isfinite(ref).all() or np.max(np.abs(ref)) < 1e-5:
        raise Refused("Surge returned short, non-finite, or silent reference audio")
    return {"rig": rig, "reference": np.asarray(ref, dtype=np.float64)}


def _measure(x: np.ndarray, f0_cmd: float) -> dict:
    x = np.asarray(x, dtype=np.float64)
    if not np.isfinite(x).all() or np.max(np.abs(x)) < 1e-5:
        raise Refused("signal is silent or non-finite")
    f = am.refine_f0(x, f0_cmd, SR)
    if not f.ok:
        raise Refused(f"fundamental qualification failed: {f.reason}")
    sig = am.harmonic_signature(x, SR, f0=f.value, kmax=12)
    bad = [k for k in range(2, 13) if sig.get(f"h{k}") is not None and sig[f"h{k}"] > 0]
    if bad:
        raise Refused(f"harmonic qualification failed: harmonics above fundamental {bad}")
    alias = am.inharmonic_fraction_db(x, f.value, SR)
    if not alias.ok:
        raise Refused(f"alias estimator refused: {alias.reason}")
    rms = float(np.sqrt(np.mean(x * x)))
    return {
        "f0_hz": round(float(f.value), 6),
        "f0_cents": round(float(f.detail["cents"]), 6),
        "rms_dbfs": round(20.0 * float(np.log10(rms)), 6),
        "peak_dbfs": round(20.0 * float(np.log10(float(np.max(np.abs(x))))), 6),
        "harmonics_db": {f"h{k}": round(float(sig[f"h{k}"]), 6)
                         for k in range(2, 13) if sig.get(f"h{k}") is not None},
        "inharmonic_db": round(float(alias.value), 6),
        "estimator": alias.detail,
    }


def run(out: pathlib.Path) -> tuple[int, dict]:
    try:
        apparatus = _apparatus()
        ref = apparatus["reference"]
        inc = vf.phase_inc(vf.note_hz(NOTE))
        dut_raw = np.asarray(vf.OscFx(WAVE, smooth=False).render(int(SECONDS * SR), inc), dtype=np.float64) / 32768.0
        dut = np.asarray(vf.OscFx(WAVE, smooth=True).render(int(SECONDS * SR), inc), dtype=np.float64) / 32768.0
        dut_2x = np.asarray(os2.render_saw(int(SECONDS * SR), inc), dtype=np.float64) / 32768.0
        if len(dut) != len(ref):
            raise Refused(f"reference/DUT length mismatch: {len(ref)} != {len(dut)}")
        ref_m = _measure(ref, vf.note_hz(NOTE))
        baseline_m = _measure(dut_raw, vf.note_hz(NOTE))
        dut_m = _measure(dut, vf.note_hz(NOTE))
        dut_2x_m = _measure(dut_2x, vf.note_hz(NOTE))
        out.mkdir(parents=True, exist_ok=True)
        ref_wav, dut_wav = out / "reference-surge.wav", out / "dut-polyblep.wav"
        dut_2x_wav = out / "dut-2x-decimated.wav"
        _write_wav(ref_wav, ref)
        _write_wav(dut_wav, dut)
        _write_wav(dut_2x_wav, dut_2x)
        report = {
            "status": "VALID",
            "case": "M5A-component",
            "scope": "M5A bright high lead: MIDI-84 saw oscillator segment only",
            "reference_identity": (
                "Surge XT Type 2 / Classic oscillator, qualified parameter readbacks; "
                "software reference, not a physical Model D"),
            "stimulus": {"midi_note": NOTE, "waveform": WAVE, "seconds": SECONDS,
                         "sample_rate": SR, "f0_command_hz": vf.note_hz(NOTE)},
            "reference": ref_m,
            "baseline_dut": baseline_m,
            "dut": dut_m,
            "dut_2x_reference": dut_2x_m,
            "difference": {
                "f0_cents_dut_minus_reference": round(dut_m["f0_cents"] - ref_m["f0_cents"], 6),
                "inharmonic_db_dut_minus_reference": round(
                    dut_m["inharmonic_db"] - ref_m["inharmonic_db"], 6),
                "inharmonic_db_filter_improvement": round(
                    dut_m["inharmonic_db"] - baseline_m["inharmonic_db"], 6),
                "f0_cents_2x_minus_reference": round(
                    dut_2x_m["f0_cents"] - ref_m["f0_cents"], 6),
                "rms_db_2x_minus_reference": round(
                    dut_2x_m["rms_dbfs"] - ref_m["rms_dbfs"], 6),
                "inharmonic_db_2x_minus_reference": round(
                    dut_2x_m["inharmonic_db"] - ref_m["inharmonic_db"], 6),
            },
            "diagnosis": (
                "The causal oscillator filter reduces the DUT's inharmonic energy by "
                f"{baseline_m['inharmonic_db'] - dut_m['inharmonic_db']:.3f} dB; "
                f"the true 2x path is {dut_2x_m['inharmonic_db'] - ref_m['inharmonic_db']:.3f} dB from Surge's inharmonic score, "
                f"with an RMS level difference of {dut_2x_m['rms_dbfs'] - ref_m['rms_dbfs']:+.3f} dB. "
                "This component comparison does not qualify absolute level or the full envelope/filter patch."),
            "audio": {
                # The report lives beside these files, so portable records keep
                # artifact names relative instead of freezing the render path.
                "reference": ref_wav.name, "dut": dut_wav.name, "dut_2x": dut_2x_wav.name,
                "reference_sha256": _sha256(ref_wav), "dut_sha256": _sha256(dut_wav),
                "dut_2x_sha256": _sha256(dut_2x_wav),
            },
        }
        (out / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return 0, report
    except Refused as exc:
        out.mkdir(parents=True, exist_ok=True)
        report = {"status": "REFUSED", "case": "M5A-component", "reason": str(exc)}
        (out / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return REFUSED, report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=pathlib.Path, default=ROOT / "build" / "mono-m5a")
    args = ap.parse_args(argv)
    code, report = run(args.out)
    print(report["status"], report.get("reason", report.get("diagnosis", "")))
    if code == 0:
        print(f"alias DUT-reference: {report['difference']['inharmonic_db_dut_minus_reference']:+.3f} dB")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
