#!/usr/bin/env python3
"""Score one drum case from the I2S pins of the integrated chip.

    tools/score_drum_i2s.py --case D02A --wav <decoded window> --verification <transcript>

Every Drums row on `docs/scorecard/BOARD.md` describes `model/drums_fx.py` --
the fixed-point model -- and not the chip. This scores the SAME case, through
the SAME estimator chain (`run_case.drum_measurements`, which `run_case` itself
calls), against the SAME frozen Fischer reference, from samples that left the
chip's I2S pins: `rtl-sketch/verify_synth_top.py --drum-solo` sends the kit and
the strike over the SPI PINS and decodes BCLK/LRCLK/SDATA the way a DAC does.

WHAT IT REFUSES TO SCORE, because each is a way this record starts lying about
where its numbers came from:

  * a transcript that is not a PASS of the whole-chip comparison. A wire that
    does not equal the model is a broken chip, not a measurement;
  * a transcript from a mutation run (`INJECT_BUG_`). An injected defect is a
    control, never evidence;
  * a transcript that does not BIND the WAV it is offered -- the hash printed
    by the run has to be this file's hash, or the transcript is describing some
    other audio;
  * a window that is not the case's own render length. `--drum-seconds`
    exists for smoke runs and a smoke run is not the case;
  * a run whose decoded window was not bit-exact against the fixed model struck
    in the SAME frame. That is the controlled comparison: if it failed, the
    difference is the chip and belongs in a bug report, not on the board as a
    quietly worse number.

The engine is named `integrated-rtl`, and `tools/compare_drum_i2s_candidate.py`
rejects anything else, so a fixed-model result cannot be filed as chip evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys

import numpy as np
from scipy.io import wavfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "tools"))
import run_case  # noqa: E402
import scorecard  # noqa: E402

ENGINE = "integrated-rtl"
SR = 48000
ANALYSIS_SCHEMA = "drum-i2s-score-v1"

# The sources that decide what this number is. Hashed into the record so a
# reader can tell a re-measurement from a changed measurement.
MEASUREMENT_SOURCES = (
    "tools/score_drum_i2s.py", "tools/run_case.py", "tools/scorecard.py",
    "model/drums_fx.py", "model/modal_fixed.py", "model/audio_measure.py",
    "model/synth_top_model.py", "rtl-sketch/verify_synth_top.py",
    "rtl-sketch/tb_top_bx.v", "rtl-sketch/synth_top.v", "rtl-sketch/spi_ctl.v",
    "rtl-sketch/drum_regs.v", "rtl-sketch/drum_kit.v", "rtl-sketch/drum_dp.v",
    "rtl-sketch/modal_dp.v", "rtl-sketch/i2s_tx.v",
)

RE_SOLO = re.compile(
    r"drum solo (?P<sound>\w+) \(circuit (?P<stop>\d+) of (?P<stops>\d+)\) at accent "
    r"(?P<accent>[\d.]+), (?P<seconds>[\d.]+) s / (?P<frames>\d+) frames, "
    r"both drum buses 0\.45, the voice silent; ROUTE = 0")
RE_STRIKE = re.compile(
    r"drum solo (?P<sound>\w+) struck in frame (?P<strike>\d+) at accent register "
    r"(?P<accent_reg>\d+); wire lag (?P<lag>\d+) period\(s\) behind the core; scored window "
    r"\[(?P<start>\d+), (?P<end>\d+)\) of (?P<periods>\d+) decoded periods")
RE_CONTROLLED = re.compile(
    r"decoded window against the fixed model struck in the SAME frame \((?P<strike>\d+)\): "
    r"(?P<differing>\d+) of (?P<frames>\d+) samples differ, max \|difference\| "
    r"(?P<maxdiff>\d+) LSB; bit-exact (?P<exact>True|False)")
RE_PHASE = re.compile(
    r"decoded window against run_case\.render_drum_solo \(strike in frame (?P<hit>\d+), "
    r"the free-running noise LFSR at a different phase\): (?P<differing>\d+) of "
    r"(?P<frames>\d+) samples differ, max \|difference\| (?P<maxdiff>\d+) LSB")
RE_DEFINES = re.compile(r"compile defines: (?P<defines>.+)")
RE_BUILT = re.compile(r"built from (?P<commit>\S+), outdir")


class Refused(Exception):
    """A precondition of the measurement failed, so there is no measurement."""


def validate_transcript(text: str, wav_sha256: str, voice: str, frames: int) -> dict:
    """The transcript's own claims, checked rather than read.

    It has to be a PASS of the whole-chip comparison, for THIS WAV, for THIS
    sound, at the case's own render length, with the controlled comparison
    against the fixed model bit-exact."""
    if "INJECT_BUG_" in text:
        raise Refused("mutation runs cannot provide chip evidence")
    if "PASS --" not in text or "FAIL --" in text or "REFUSED --" in text:
        raise Refused("the transcript is not a clean PASS of the whole-chip comparison")
    verified = (f"{voice} drum path verified from SPI pins through the production "
                f"drum engine and I2S pins")
    if verified not in text:
        raise Refused(f"the transcript does not verify the {voice} drum path at the pins")
    if f"decoded I2S WAV sha256 {wav_sha256}" not in text:
        raise Refused("the transcript does not bind this decoded I2S WAV")
    solo = RE_SOLO.search(text)
    strike = RE_STRIKE.search(text)
    controlled = RE_CONTROLLED.search(text)
    phase = RE_PHASE.search(text)
    if not (solo and strike and controlled and phase):
        raise Refused("the transcript omits the drum-solo stimulus, strike, or comparison report")
    if solo.group("sound") != voice or strike.group("sound") != voice:
        raise Refused(f"the transcript is a {solo.group('sound')} run, not a {voice} run")
    if int(solo.group("frames")) != frames or int(controlled.group("frames")) != frames:
        raise Refused(f"the transcript rendered {solo.group('frames')} frames, "
                      f"{frames} is this case's own render length")
    if int(strike.group("end")) - int(strike.group("start")) != frames:
        raise Refused("the transcript's scored window is not the render length")
    if float(solo.group("accent")) != 1.0:
        raise Refused(f"the scorecard render is accent 1.0, the transcript is {solo.group('accent')}")
    if controlled.group("exact") != "True" or int(controlled.group("differing")) != 0:
        raise Refused(f"the decoded window is not bit-exact against the fixed model struck in the "
                      f"same frame ({controlled.group('differing')} of {frames} samples differ, "
                      f"max {controlled.group('maxdiff')} LSB): that difference is the chip and "
                      f"is a defect report, not a board row")
    simulator = next((name for name in ("verilator", "iverilog")
                      if f"simulator backend {name}" in text), None)
    if simulator is None:
        raise Refused("the transcript omits the simulator backend")
    built = RE_BUILT.search(text)
    defines = RE_DEFINES.search(text)
    return {
        "simulator": simulator,
        "rtl_build": built.group("commit") if built else None,
        "compile_defines": defines.group("defines").strip() if defines else None,
        "sound": voice,
        "circuit": f"{solo.group('stop')} of {solo.group('stops')}",
        "accent": float(solo.group("accent")),
        "seconds": float(solo.group("seconds")),
        "render_frames": frames,
        "strike_frame": int(strike.group("strike")),
        "accent_register_at_strike": int(strike.group("accent_reg")),
        "wire_lag_periods": int(strike.group("lag")),
        "decoded_periods": int(strike.group("periods")),
        "window": [int(strike.group("start")), int(strike.group("end"))],
        "fixed_model_at_realised_strike": {
            "differing_samples": int(controlled.group("differing")),
            "max_abs_difference": int(controlled.group("maxdiff")),
            "identical": True},
        "fixed_model_scorecard_render": {
            "strike_frame": int(phase.group("hit")),
            "differing_samples": int(phase.group("differing")),
            "max_abs_difference": int(phase.group("maxdiff")),
            "identical": int(phase.group("differing")) == 0},
    }


def load_window(path: pathlib.Path, frames: int) -> np.ndarray:
    sr, x = wavfile.read(path)
    if sr != SR:
        raise Refused(f"the decoded window must be {SR} Hz (saw {sr})")
    x = np.asarray(x)
    if x.ndim != 1:
        raise Refused("the decoded window must be mono")
    if x.dtype != np.int16:
        raise Refused(f"the decoded window must be int16 (saw {x.dtype})")
    if len(x) != frames:
        raise Refused(f"the decoded window is {len(x)} frames, this case renders {frames}")
    return np.asarray(x, dtype=np.float64) / 32768.0


def build_record(case: dict, voice: str, ours_x, ref_x, ref_sr: int, rel: str,
                 setting: str, refdir: pathlib.Path, run: dict,
                 wav_sha256: str, transcript_sha256: str, audio_rel: str) -> dict:
    required = [m.strip() for m in case["required_measurements"].split(";") if m.strip()]
    metrics, windowing = run_case.drum_measurements(
        voice, ours_x, SR, ref_x, ref_sr, rel, required)
    import drums_fx as dx
    provenance = run_case.provenance(
        run_case.model_input_hashes({
            f"reference:{rel}": run_case._file_sha(refdir / rel),
            "decoded:I2S": "sha256:" + wav_sha256,
            "verification:SPI-I2S": "sha256:" + transcript_sha256}),
        {"ours": audio_rel, "reference": str(refdir / rel)},
        dict(voice=voice, refs=str(refdir), inject=None,
             render_seconds=run["seconds"], accent=run["accent"],
             bus_gain=0.45, level_matched=True,
             simulator=run["simulator"], rtl_build=run["rtl_build"],
             compile_defines=run["compile_defines"],
             strike_frame=run["strike_frame"], wire_lag_periods=run["wire_lag_periods"],
             scored_window=run["window"],
             measurement_source_sha256={
                 rel_: hashlib.sha256((ROOT / rel_).read_bytes()).hexdigest()
                 for rel_ in MEASUREMENT_SOURCES}))
    provenance["engine"] = ENGINE
    return {
        "engine": ENGINE,
        "case_id": case["case_id"],
        "subject": case["subject"],
        "source_commit": run_case.source_commit(),
        "source_dirty": bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                                            check=True, capture_output=True,
                                            text=True).stdout.strip()),
        "analysis_version": ANALYSIS_SCHEMA,
        "analysis_run": run_case.analysis_run(),
        "reference_profile": f"fischer-tr808-103852:{rel} ({setting})",
        "reference_identity": run_case.REF_ID,
        "render_run": (f"synth_top.v {run['rtl_build']} under {run['simulator']}: one hit of "
                       f"{voice} from the shipped kit, solo, at accent {run['accent']:.1f}, "
                       f"{run['seconds']:.2f} s, both drum buses 0.45, {SR} Hz, circuit "
                       f"{run['circuit']}; every register write sent over the SPI PINS and "
                       f"every sample decoded from the I2S pins (BCLK/LRCLK/SDATA), never "
                       f"read out of the model"),
        "audio": f"reference {rel}; ours {audio_rel}",
        "tolerance_policy": run_case.TOLERANCE_POLICY,
        "windowing": windowing,
        "metrics": metrics,
        "diagnostics": {
            "ours_peak_fs": round(float(np.abs(ours_x).max()), 6),
            "ours_rms_fs": round(float(np.sqrt(np.mean(np.square(ours_x)))), 6),
            "reference_peak_fs": round(float(np.abs(ref_x).max()), 6),
            "reference_rate_hz": ref_sr, "ours_rate_hz": SR,
            "level_matched": True,
            "decoded_i2s_sha256": wav_sha256,
            "spi_i2s_report_sha256": transcript_sha256,
            "integration": {k: run[k] for k in (
                "simulator", "rtl_build", "compile_defines", "strike_frame",
                "accent_register_at_strike", "wire_lag_periods", "decoded_periods",
                "window", "fixed_model_at_realised_strike",
                "fixed_model_scorecard_render")},
            "note": ("levels are not compared: the Fischer set pinned LEVEL at maximum for "
                     "every voice, so its inter-voice levels are not the machine's. The "
                     "decoded window is bit-exact against the fixed model struck in the SAME "
                     "frame; it is NOT the same waveform as run_case.render_drum_solo, whose "
                     "strike is in frame "
                     f"{run['fixed_model_scorecard_render']['strike_frame']}, because the drum "
                     "noise LFSR free-runs. That phase difference is reported above and is "
                     "the reason a metric here can differ from the fixed-model twin without "
                     "either engine being wrong."),
        },
        "provenance": provenance,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--case", required=True, help="a Drums case id, e.g. D02A")
    ap.add_argument("--wav", required=True,
                    help="the decoded I2S window written by verify_synth_top --drum-solo --wav-out")
    ap.add_argument("--verification", required=True,
                    help="captured stdout/stderr of the matching --drum-solo run")
    ap.add_argument("--refs", default=None, help="the Fischer corpus (default: run_case's)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--audio", default=None,
                    help="where the scored window is kept inside the repository")
    a = ap.parse_args(argv)

    try:
        case = next((c for c in run_case.load_cases() if c["case_id"] == a.case), None)
        if case is None:
            raise Refused(f"{a.case} is not a case in docs/scorecard/cases.csv")
        if case["family"] != "Drums":
            raise Refused(f"{a.case} is a {case['family']} case, not a Drums case")
        voice = run_case.DRUM_CASE_VOICE.get(a.case)
        if voice is None or voice not in run_case.DRUM_PLAN:
            raise Refused(f"{a.case} has no drum measurement plan")
        if voice not in run_case.REF_MAIN:
            raise Refused(f"the Fischer corpus has no reference recording mapped for {voice}")
        frames = int(run_case.SOLO_SECONDS.get(voice, 2.2) * SR)

        wav = pathlib.Path(a.wav).resolve()
        verification = pathlib.Path(a.verification).resolve()
        if not wav.is_file():
            raise Refused(f"no decoded I2S window at {wav}")
        if not verification.is_file():
            raise Refused(f"no verification transcript at {verification}")
        wav_sha256 = hashlib.sha256(wav.read_bytes()).hexdigest()
        transcript_sha256 = hashlib.sha256(verification.read_bytes()).hexdigest()
        run = validate_transcript(verification.read_text(), wav_sha256, voice, frames)
        ours_x = load_window(wav, frames)

        refdir = pathlib.Path(a.refs) if a.refs else run_case.configured_refs()
        ref_x, ref_sr, rel, setting = run_case.load_reference(voice, refdir, "")

        audio = pathlib.Path(a.audio or f"docs/scorecard/drum-{a.case.lower()}-i2s/"
                                        f"{voice.lower()}-solo-i2s.wav")
        if not audio.is_absolute():
            audio = ROOT / audio
        audio = audio.resolve()
        if not audio.is_relative_to(ROOT):
            raise Refused("the scored audio must stay inside the repository for provenance")
        audio.parent.mkdir(parents=True, exist_ok=True)
        if audio != wav:
            audio.write_bytes(wav.read_bytes())
        audio_rel = str(audio.relative_to(ROOT))

        record = build_record(case, voice, ours_x, ref_x, ref_sr, rel, setting, refdir,
                              run, wav_sha256, transcript_sha256, audio_rel)
    except (Refused, run_case.Refused) as exc:
        print(f"score_drum_i2s: REFUSED -- {exc}")
        return 2
    except (OSError, ValueError, KeyError) as exc:
        print(f"score_drum_i2s: REFUSED -- {exc}")
        return 2

    verdict = scorecard.evaluate(case, record)
    if verdict["state"] not in (scorecard.PASS, scorecard.FAIL):
        print(f"score_drum_i2s: REFUSED -- the measurement is not scoreable: {verdict['why']}")
        return 2
    record["provenance"]["outcome_code"] = 0 if verdict["state"] == scorecard.PASS else 1
    record["scorecard_state"] = verdict["state"]
    record["scorecard_reason"] = verdict["why"]

    out = pathlib.Path(a.out or f"docs/scorecard/results/{a.case}.json")
    if not out.is_absolute():
        out = ROOT / out
    run_case.carry_rubric_history(case, out, record)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2, sort_keys=False) + "\n")
    print(f"score_drum_i2s: valid {verdict['state']} {a.case} ({voice}) on {ENGINE}; "
          f"{len(record['metrics'])} properties measured from the decoded I2S window")
    print(f"score_drum_i2s: report {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
