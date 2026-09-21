#!/usr/bin/env python3
"""Score a complete decoded SPI-to-I2S M5A phrase against the frozen reference."""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "tools"))
import mono_m5a_score as m5a
import scorecard
import run_case
import voice_fx as vf


def _candidate_factory():
    cfg = {**vf.LADDER_CFG, "oversample": 2}
    return vf.VoiceFx(oversample_2x=True, ladder_cfg=cfg,
                      rate_converted_ladder=True,
                      preserve_filter_headroom=True, causal_filter=True)


def validate_integration_report(report_text: str, wav_sha256: str, *,
                               pulse_shape: str | None = None,
                               case_id: str = "M5A", pulse_2x: bool | None = None,
                               saw_cutoff_hz: int | None = None,
                               saw_volume_correction_db: float | None = None) -> dict:
    if case_id not in m5a.MANIFESTS:
        raise ValueError("unsupported Mono case")
    if "INJECT_BUG_" in report_text:
        raise ValueError("mutation runs cannot provide sound evidence")
    required = (
        f"{case_id} path verified from SPI pins through the production voice and I2S pins",
        "selected 2x saw + causal 2x filter candidate",
        "phrase and complete release",
        "PASS --",
    )
    if any(token not in report_text for token in required) or "smoke" in report_text:
        raise ValueError("report is not a passing full filter-candidate I2S run")
    if f"decoded I2S WAV sha256 {wav_sha256}" not in report_text:
        raise ValueError("report does not bind the decoded I2S WAV hash")
    match = re.search(
        rf"{case_id} controls: pulse=(\w+);(?: effective pulse=(\w+) "
        r"\((\d+(?:\.\d+)?)% duty\);)? saw cutoff=(\d+) Hz; "
        r"saw volume correction=([+-]?\d+(?:\.\d+)?) dB", report_text)
    if not match:
        raise ValueError("report omits the M5A sound controls")
    controls = {"pulse_shape": match.group(1), "saw_cutoff_hz": int(match.group(4)),
                "saw_volume_correction_db": float(match.group(5))}
    define_line = next((line for line in report_text.splitlines()
                        if "compile defines:" in line), "")
    if "VOICE_FILTER_2X" not in define_line:
        raise ValueError("report omits VOICE_FILTER_2X, needed to identify effective pulse duty")
    pulse_selected = "VOICE_PULSE_2X" in define_line
    if pulse_2x is not None and pulse_selected != pulse_2x:
        raise ValueError("report has a different pulse 2x configuration")
    controls["oscillator_pulse_oversample_2x"] = pulse_selected
    effective_shape = ("pulse479" if controls["pulse_shape"] == "pulse29" else
                       controls["pulse_shape"])
    effective_duty = vf.DUTY[effective_shape] / vf.CYCLE
    if match.group(2) and (match.group(2) != effective_shape
                           or abs(float(match.group(3)) - 100.0 * effective_duty) > 0.01):
        raise ValueError("report effective pulse does not match the registered width and VOICE_FILTER_2X")
    controls["pulse_control_label"] = controls["pulse_shape"]
    controls["pulse_effective_waveform"] = effective_shape
    controls["pulse_effective_duty_fraction"] = effective_duty
    controls["pulse_effective_duty_percent"] = 100.0 * controls["pulse_effective_duty_fraction"]
    if pulse_shape is not None and controls["pulse_shape"] != pulse_shape:
        raise ValueError("report has a different pulse shape")
    if saw_cutoff_hz is not None and controls["saw_cutoff_hz"] != saw_cutoff_hz:
        raise ValueError("report has a different saw cutoff")
    if (saw_volume_correction_db is not None
            and abs(controls["saw_volume_correction_db"] - saw_volume_correction_db) > 0.00001):
        raise ValueError("report has a different saw volume correction")
    return controls


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", choices=tuple(m5a.MANIFESTS), default="M5A")
    ap.add_argument("--pulse2x", action="store_true", default=None)
    ap.add_argument("--wav", required=True, help="complete decoded int16 M5A I2S phrase")
    ap.add_argument("--verification", required=True,
                    help="captured stdout/stderr of the matching full --m5a --filter2x run")
    ap.add_argument("--out", default="docs/scorecard/results/M5A.json")
    ap.add_argument("--audio", default="docs/scorecard/mono-m5a-miniv3/filter2x-i2s.wav")
    ap.add_argument("--pulse-shape", choices=("square", "pulse15", "pulse25", "pulse29"),
                    default=None, help="expected SPI-selected pulse shape; defaults to report value")
    ap.add_argument("--saw-cutoff-hz", type=int, default=None,
                    help="expected saw cutoff; defaults to report value")
    ap.add_argument("--saw-volume-correction-db", type=float, default=None,
                    help="expected saw-only volume correction; defaults to report value")
    a = ap.parse_args(argv)
    wav = pathlib.Path(a.wav).resolve()
    verification = pathlib.Path(a.verification).resolve()
    if not wav.is_file() or not verification.is_file():
        print("score_m5a_i2s: REFUSED -- WAV or verification report is missing")
        return 2
    report_text = verification.read_text()
    try:
        wav_sha256 = hashlib.sha256(wav.read_bytes()).hexdigest()
        controls = validate_integration_report(
            report_text, wav_sha256, pulse_shape=a.pulse_shape,
            case_id=a.case, pulse_2x=a.pulse2x,
            saw_cutoff_hz=a.saw_cutoff_hz,
            saw_volume_correction_db=a.saw_volume_correction_db)
    except ValueError as exc:
        print(f"score_m5a_i2s: REFUSED -- {exc}")
        return 2
    simulator = next((name for name in ("verilator", "iverilog")
                      if f"simulator backend {name}" in report_text), None)
    if simulator is None:
        print("score_m5a_i2s: REFUSED -- report omits the simulator backend")
        return 2

    out = pathlib.Path(a.out)
    if not out.is_absolute():
        out = ROOT / out
    candidate_audio = pathlib.Path(a.audio)
    if not candidate_audio.is_absolute():
        candidate_audio = ROOT / candidate_audio
    candidate_audio = candidate_audio.resolve()
    if not candidate_audio.is_relative_to(ROOT):
        print("score_m5a_i2s: REFUSED -- output audio must remain inside the repository for provenance")
        return 2
    try:
        measured = m5a.measure(
            pulse_shape=controls["pulse_shape"], voice_factory=_candidate_factory,
            model_label=f"causal-reconstructed-2x-filter-headroom-{controls['pulse_shape']}-decoded-i2s",
            output_path=candidate_audio, candidate_wav=wav, case_id=a.case)
    except (m5a.Refused, OSError, ValueError) as exc:
        print(f"score_m5a_i2s: REFUSED -- {exc}")
        return 2
    if f"reference sha256 {measured['reference_sha256']}" not in report_text:
        print("score_m5a_i2s: REFUSED -- transcript reference differs from scoring reference")
        return 2
    case = next(c for c in run_case.load_cases() if c["case_id"] == a.case)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                            check=True, capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                                check=True, capture_output=True, text=True).stdout.strip())
    source_files = (
        "tools/score_m5a_i2s.py", "tools/verify_m5a_filter2x_i2s.py",
        "tools/mono_m5a_score.py", "model/filter_rate_chain.py",
        "model/synth_top_model.py", "model/voice_fx.py",
        "rtl-sketch/verify_synth_top.py", "rtl-sketch/tb_top_bx.v",
        "rtl-sketch/synth_top.v", "rtl-sketch/spi_ctl.v",
        "rtl-sketch/voice_dp.v", "rtl-sketch/ladder_dp_n.v",
        "rtl-sketch/rate_conv_2x.v", "rtl-sketch/i2s_tx.v",
        "rtl-sketch/osc_2x_saw_bank.v", "rtl-sketch/osc_2x_saw_path.v",
        "rtl-sketch/polyblep_saw_pair.v", "rtl-sketch/decimate_2x_tm_sym.v",
        "rtl-sketch/osc_substep_pair.v")
    engine = m5a.engine_configuration("selected")
    provenance = run_case.provenance(
        run_case.model_input_hashes({
            f"frozen:{a.case}:audio": "sha256:" + measured["reference_sha256"],
            f"frozen:{a.case}:manifest": "sha256:" + measured["manifest_sha256"],
            "decoded:I2S": "sha256:" + wav_sha256,
            "verification:SPI-I2S": "sha256:" + hashlib.sha256(verification.read_bytes()).hexdigest()}),
        {"ours": str(candidate_audio.relative_to(ROOT)),
         "spi_i2s": str(verification.relative_to(ROOT))},
        {"engine_profile": engine["name"],
         "oscillator_oversample_2x": engine["oscillator_oversample_2x"],
         "filter_rate_converted": engine["filter_rate_converted"],
         "filter_preserve_headroom": engine["filter_preserve_headroom"],
         "filter_causal": engine["filter_causal"],
         "pulse479_filter_candidate": engine["pulse479_filter_candidate"],
         "filter_g_exact": engine["filter_g_exact"],
         "filter_k_comp": engine["filter_k_comp"],
         "filter_drive": engine["filter_drive"],
         "filter_oversample_factor": engine["filter_oversample_factor"],
         "ladder_coefficient_oversample": engine["ladder_coefficient_oversample"],
         "oscillator_config": ("2x saw and pulse candidate" if controls["oscillator_pulse_oversample_2x"] else "2x saw candidate"),
         "oscillator_pulse_oversample_2x": controls["oscillator_pulse_oversample_2x"],
         "filter_config": "causal reconstructed 2x, headroom preserved",
         "simulator": simulator,
         "pulse_shape": controls["pulse_shape"],
         "pulse_control_label": controls["pulse_control_label"],
         "pulse_effective_waveform": controls["pulse_effective_waveform"],
         "pulse_effective_duty_fraction": controls["pulse_effective_duty_fraction"],
         "pulse_effective_duty_percent": controls["pulse_effective_duty_percent"],
         "saw_cutoff_hz": controls["saw_cutoff_hz"],
         "saw_volume_correction_db": controls["saw_volume_correction_db"],
         "candidate_wav_sha256": measured["candidate_i2s_sha256"],
         "scored_audio_artifact_sha256": hashlib.sha256(candidate_audio.read_bytes()).hexdigest(),
         "measurement_source_sha256": {
             rel: hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
             for rel in source_files}})
    provenance["engine"] = "integrated-rtl"
    record = {
        "engine": "integrated-rtl",
        "case_id": a.case, "subject": case["subject"],
        "source_commit": commit,
        "source_dirty": dirty,
        "analysis_version": measured["analysis_version"],
        "reference_profile": "Mini V3 3.12.0.3422; frozen mono 48 kHz reference",
        "pulse_mapping": {
            "control_label": controls["pulse_control_label"],
            "effective_waveform": controls["pulse_effective_waveform"],
            "effective_duty_fraction": controls["pulse_effective_duty_fraction"],
            "effective_duty_percent": controls["pulse_effective_duty_percent"],
            "selection": "VOICE_FILTER_2X DUTY_WIDE encoding",
        },
        "render_run": ("full phrase decoded from SPI-driven I2S; causal reconstructed 2x filter; "
                       f"pulse control={controls['pulse_control_label']}; "
                       f"effective pulse={controls['pulse_effective_waveform']} "
                       f"({controls['pulse_effective_duty_percent']:.2f}% duty); "
                       f"saw_cutoff={controls['saw_cutoff_hz']} Hz; "
                       f"saw_volume_correction={controls['saw_volume_correction_db']:+.5f} dB"),
        "audio": str(candidate_audio.relative_to(ROOT)),
        "tolerance_policy": m5a.TOLERANCES,
        "metrics": measured["metrics"],
        "diagnostics": {
            "reference_sha256": measured["reference_sha256"],
            "reference_manifest_sha256": measured["manifest_sha256"],
            "decoded_i2s_sha256": wav_sha256,
            "scored_audio_artifact_sha256": hashlib.sha256(candidate_audio.read_bytes()).hexdigest(),
            "spi_i2s_report_sha256": hashlib.sha256(verification.read_bytes()).hexdigest(),
            "events": measured["event_diagnostics"],
            "model_segments": measured["model_segments"],
            "duration_s": measured["duration_s"],
            "note": "Metrics are computed from the decoded I2S samples; the RTL stream is bit-exact against the causal fixed-point model.",
        },
        "provenance": provenance,
    }
    verdict = scorecard.evaluate(case, record)
    if verdict["state"] not in (scorecard.PASS, scorecard.FAIL):
        print(f"score_m5a_i2s: REFUSED -- measurement is not scoreable: {verdict['why']}")
        return 2
    record["provenance"]["outcome_code"] = 0 if verdict["state"] == scorecard.PASS else 1
    record["scorecard_state"] = verdict["state"]
    record["scorecard_reason"] = verdict["why"]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2) + "\n")
    print(f"score_m5a_i2s: valid {verdict['state']} {a.case} comparison; seven properties measured from decoded I2S")
    print(f"score_m5a_i2s: report {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
