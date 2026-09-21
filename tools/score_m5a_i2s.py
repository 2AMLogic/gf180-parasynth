#!/usr/bin/env python3
"""Score a complete decoded SPI-to-I2S M5A phrase against the frozen reference."""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
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


def validate_integration_report(report_text: str) -> None:
    required = (
        "M5A path verified from SPI pins through the production voice and I2S pins",
        "selected 2x saw + causal 2x filter candidate",
        "phrase and complete release",
        "PASS --",
    )
    if any(token not in report_text for token in required) or "smoke" in report_text:
        raise ValueError("report is not a passing full filter-candidate I2S run")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wav", required=True, help="complete decoded int16 M5A I2S phrase")
    ap.add_argument("--verification", required=True,
                    help="captured stdout/stderr of the matching full --m5a --filter2x run")
    ap.add_argument("--out", default="docs/scorecard/results/M5A.json")
    ap.add_argument("--audio", default="docs/scorecard/mono-m5a-miniv3/filter2x-i2s.wav")
    a = ap.parse_args(argv)
    wav = pathlib.Path(a.wav).resolve()
    verification = pathlib.Path(a.verification).resolve()
    if not wav.is_file() or not verification.is_file():
        print("score_m5a_i2s: REFUSED -- WAV or verification report is missing")
        return 2
    report_text = verification.read_text()
    try:
        validate_integration_report(report_text)
    except ValueError as exc:
        print(f"score_m5a_i2s: REFUSED -- {exc}")
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
            pulse_shape="pulse479", voice_factory=_candidate_factory,
            model_label="causal-reconstructed-2x-filter-headroom-pulse479-decoded-i2s",
            output_path=candidate_audio, candidate_wav=wav)
    except (m5a.Refused, OSError, ValueError) as exc:
        print(f"score_m5a_i2s: REFUSED -- {exc}")
        return 2
    case = next(c for c in run_case.load_cases() if c["case_id"] == "M5A")
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                            check=True, capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                                check=True, capture_output=True, text=True).stdout.strip())
    source_files = ("tools/score_m5a_i2s.py", "tools/mono_m5a_score.py",
                    "model/filter_rate_chain.py", "model/voice_fx.py")
    provenance = run_case.provenance(
        run_case.model_input_hashes({
            "frozen:M5A:audio": "sha256:" + measured["reference_sha256"],
            "frozen:M5A:manifest": "sha256:" + measured["manifest_sha256"],
            "decoded:I2S": "sha256:" + hashlib.sha256(wav.read_bytes()).hexdigest(),
            "verification:SPI-I2S": "sha256:" + hashlib.sha256(verification.read_bytes()).hexdigest()}),
        {"ours": str(candidate_audio.relative_to(ROOT)),
         "spi_i2s": str(verification.relative_to(ROOT))},
        {"oscillator_config": "2x saw candidate",
         "filter_config": "causal reconstructed 2x, headroom preserved",
         "pulse_shape": "pulse479", "candidate_wav_sha256": measured["candidate_i2s_sha256"],
         "measurement_source_sha256": {
             rel: hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
             for rel in source_files}})
    provenance["engine"] = "integrated-rtl"
    record = {
        "engine": "integrated-rtl",
        "case_id": "M5A", "subject": case["subject"],
        "source_commit": commit,
        "source_dirty": dirty,
        "analysis_version": measured["analysis_version"],
        "reference_profile": "Mini V3 3.12.0.3422; frozen mono 48 kHz reference",
        "render_run": "full phrase decoded from SPI-driven I2S; causal reconstructed 2x filter; pulse479",
        "audio": str(candidate_audio.relative_to(ROOT)),
        "tolerance_policy": m5a.TOLERANCES,
        "metrics": measured["metrics"],
        "diagnostics": {
            "reference_sha256": measured["reference_sha256"],
            "reference_manifest_sha256": measured["manifest_sha256"],
            "decoded_i2s_sha256": hashlib.sha256(wav.read_bytes()).hexdigest(),
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
    print(f"score_m5a_i2s: valid {verdict['state']} M5A comparison; seven properties measured from decoded I2S")
    print(f"score_m5a_i2s: report {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
