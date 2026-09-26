#!/usr/bin/env python3
"""Score a scorecard ENSEMBLE case on the integrated RTL, from the I2S pins.

    tools/score_ensemble_i2s.py --case E1A

WHAT THIS IS FOR. Every Ensemble result on the board is `fixed-model`: the
integer models `model/voice_fx.py` and `model/drums_fx.py` summed in one Python
process. An ensemble case is the one place where the mono voice AND the drum
section carry signal at the same time, so it is the case whose `fixed-model`
answer says least about the chip -- the master mix, the drum handshake and the
I2S serialiser all sit between those two models and the pins, and none of them
exists in Python. This produces the same case's three properties from the
DECODED I2S WIRE of `rtl-sketch/synth_top.v`, driven over the SPI pins.

HOW THE PARTS WORK, and why there are twelve runs rather than one.
`run_case.render_ensemble` answers the case from ONE render by zeroing buses:
the final mix, the three per-bus stems, and each drum stop's row alone. A chip
cannot zero a bus, so each of those is a separate RTL run here, differing only
in the three Q0.15 gain words (and, for a per-stop part, in which hits are
sent). The mix and the three stems send an otherwise byte-identical write
stream, so their writes land in the same frames and "is the output the sum of
the stems" stays a question about the master mix's one rail.

WHAT IS MEASURED AGAINST WHAT:

  Event timing    each stop's row alone, against the frame that stop's A_STOPS
                  raise LANDED in according to the CS_N pin (plus contract 13's
                  one period of I2S delay). Not against the frame the groove
                  asked for: a host write takes about 1.45 frames on this link,
                  and folding the link's delivery latency into an onset error
                  would report a transport property as a mix property. The
                  latency is recorded separately, per part, in `diagnostics`.
  bus balance     the mix's RMS against the sum of the three stem runs'.
  output artifacts  samples of the mix on the rail.

THIS TOOL REFUSES RATHER THAN REPORTS when any part's run is not a clean,
complete, un-injected PASS whose report binds the exact WAV and schedule this
scorer read. `INJECT_BUG_` anywhere in a report is fatal: a mutation run cannot
provide sound evidence.

THE NEGATIVE CONTROL IS PART OF THE RESULT, not a separate chore. `--control`
(the default) also runs the mix part with `-DINJECT_BUG_DRUM_BUS_STALE` -- the
mutation that makes the master mix read the PREVIOUS frame's drum buses, the
hazard issues #82/#85 raised -- and requires it to turn the bench red. What it
measures, and says out loud in the record, is that the three case metrics are
nearly BLIND to that mutation while the bit-exact wire-versus-model comparison
the anchor rests on is not. A control that fires only where the evidence
actually comes from is still a control; one that was assumed to fire is not.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import pathlib
import re
import subprocess
import sys
import wave

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "rtl-sketch"))
import numpy as np
import run_case as rc
import scorecard
import verify_synth_top as vst

SR = 48000
STEM_PARTS = ("mix", "voice", "drum-mix", "body")
#: The mutation this anchor must be shown to be sensitive to, at the level the
#: anchor's evidence actually comes from. Already in `synth_top.v`; #82/#85.
CONTROL_INJECT = "DRUM_BUS_STALE"
#: Sources whose content decides what this number means. Hashed onto the record
#: so a later reader can tell a stale anchor from a current one.
MEASUREMENT_SOURCES = (
    "tools/score_ensemble_i2s.py", "tools/compare_ensemble_candidate.py",
    "tools/run_case.py", "tools/scorecard.py", "model/audio_measure.py",
    "model/drums_fx.py", "model/voice_fx.py", "model/synth_top_model.py",
    "audition/patches.py",
    "rtl-sketch/verify_synth_top.py", "rtl-sketch/tb_top_bx.v",
    "rtl-sketch/synth_top.v", "rtl-sketch/spi_ctl.v", "rtl-sketch/voice_dp.v",
    "rtl-sketch/drum_regs.v", "rtl-sketch/drum_kit.v", "rtl-sketch/drum_dp.v",
    "rtl-sketch/modal_dp.v", "rtl-sketch/ladder_dp_n.v", "rtl-sketch/i2s_tx.v",
)


class Refused(Exception):
    """This apparatus could not answer. Distinct from a pass and from a fail."""


def part_slug(part: str) -> str:
    return part.replace(":", "-")


def parts_for(case_id: str) -> list[str]:
    """Every part of the case's render, and therefore every RTL run it needs.

    The stop list is READ OUT OF THE STIMULUS rather than restated: the same
    `ensemble_script` that will be sent over the pins names which stops the
    case's groove strikes, so a groove edit cannot leave a stop unmeasured
    while this list still claims eight."""
    _, _, info = vst.ensemble_script(case_id, "mix")
    return list(STEM_PARTS) + [f"stop:{name}" for name in sorted(info["raises"])]


def part_paths(outdir: pathlib.Path, part: str) -> dict:
    slug = part_slug(part)
    return {"wav": outdir / f"{slug}.wav",
            "report": outdir / f"{slug}.txt",
            "schedule": outdir / f"{slug}-schedule.json",
            "simdir": outdir / f"rtl-{slug}"}


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_wav_int16(path: pathlib.Path) -> np.ndarray:
    """The decoded I2S words back as integers, mono, at 48 kHz, or Refused."""
    try:
        with wave.open(str(path), "rb") as fh:
            if fh.getnchannels() != 1 or fh.getsampwidth() != 2:
                raise Refused(f"{path.name} is not mono 16-bit")
            if fh.getframerate() != SR:
                raise Refused(f"{path.name} is not at {SR} Hz")
            raw = fh.readframes(fh.getnframes())
    except wave.Error as exc:
        raise Refused(f"{path.name} is not a readable WAV: {exc}") from exc
    return np.frombuffer(raw, dtype="<i2").astype(np.int64)


def run_part(case_id: str, part: str, outdir: pathlib.Path, *, simulator: str,
             inject: str | None = None, timeout_s: float = 5400.0,
             expect_fail: bool = False) -> dict:
    """One RTL run of one part. Returns the paths and the exit status; never
    interprets the run -- `validate_part` does that from the report text."""
    paths = part_paths(outdir, part)
    paths["simdir"].mkdir(parents=True, exist_ok=True)
    command = [sys.executable, str(ROOT / "rtl-sketch/verify_synth_top.py"),
               "--ensemble", case_id, "--ensemble-part", part,
               "--simulator", simulator,
               "--wav-out", str(paths["wav"]),
               "--schedule-out", str(paths["schedule"]),
               "--outdir", str(paths["simdir"])]
    if inject:
        command += ["--inject", inject]
    if expect_fail:
        command += ["--expect-fail"]
    try:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                timeout=timeout_s)
        text, status = result.stdout + result.stderr, result.returncode
    except subprocess.TimeoutExpired as exc:
        text = (exc.stdout or "") if isinstance(exc.stdout, str) else (exc.stdout or b"").decode(errors="replace")
        text += f"\nscore_ensemble_i2s: NO-VERDICT -- {part} timed out after {timeout_s}s\n"
        status = 2
    paths["report"].parent.mkdir(parents=True, exist_ok=True)
    paths["report"].write_text(text)
    return {"part": part, "status": status, "paths": paths}


_GAINS_RE = re.compile(r"ensemble gains: vol=(\d+), dvol=(\d+), bvol=(\d+)")
_LATENCY_RE = re.compile(r"worst host-to-register latency ([+-]?\d+) frame")
_PERIODS_RE = re.compile(r"PASS -- (\d+) I2S periods decoded from the wire")


def validate_part(case_id: str, part: str, report_text: str, wav_path: pathlib.Path,
                  schedule_path: pathlib.Path) -> dict:
    """The preconditions of using this part as evidence, asserted at the point
    of use. Raises Refused with the reason; never returns a partial verdict."""
    if "INJECT_BUG_" in report_text:
        raise Refused(f"{part}: a mutation run cannot provide sound evidence")
    required = (
        f"ENSEMBLE {case_id} part {part}:",
        f"{case_id} ensemble part {part} verified from SPI pins through the COMBINED "
        f"voice and drum paths to the I2S pins",
        "PASS -- ",
    )
    missing = [token for token in required if token not in report_text]
    if missing:
        raise Refused(f"{part}: the report is not a passing complete ensemble run")
    for bad in ("REFUSED", "FAIL --", "NO-VERDICT"):
        if bad in report_text:
            raise Refused(f"{part}: the report contains {bad}")
    simulator = next((name for name in ("verilator", "iverilog")
                      if f"simulator backend {name}" in report_text), None)
    if simulator is None:
        raise Refused(f"{part}: the report omits the simulator backend")
    gains = _GAINS_RE.search(report_text)
    if not gains:
        raise Refused(f"{part}: the report omits the three bus gain words")
    if tuple(int(g) for g in gains.groups()) != vst.ensemble_part_gains(part):
        raise Refused(f"{part}: the report's bus gains are not this part's")
    if not wav_path.is_file() or not schedule_path.is_file():
        raise Refused(f"{part}: the decoded WAV or the schedule is missing")
    wav_sha, schedule_sha = _sha256(wav_path), _sha256(schedule_path)
    if f"decoded I2S WAV sha256 {wav_sha}" not in report_text:
        raise Refused(f"{part}: the report does not bind this decoded WAV")
    if f"ensemble schedule sha256 {schedule_sha}" not in report_text:
        raise Refused(f"{part}: the report does not bind this schedule")
    try:
        schedule = json.loads(schedule_path.read_text())
    except json.JSONDecodeError as exc:
        raise Refused(f"{part}: the schedule is not readable: {exc}") from exc
    if schedule.get("case_id") != case_id or schedule.get("part") != part:
        raise Refused(f"{part}: the schedule names a different case or part")
    if schedule.get("i2s_delay_periods") != 1 or schedule.get("sample_rate_hz") != SR:
        raise Refused(f"{part}: the schedule does not declare contract 13's D = 1 at {SR} Hz")
    periods = _PERIODS_RE.search(report_text)
    if not periods:
        raise Refused(f"{part}: the report does not say how many periods were decoded")
    samples = read_wav_int16(wav_path)
    if len(samples) != int(periods.group(1)):
        raise Refused(f"{part}: the WAV holds {len(samples)} samples, the report decoded "
                      f"{periods.group(1)} periods")
    case_frames = int(schedule["case_frames"])
    if len(samples) < case_frames:
        raise Refused(f"{part}: the run is {case_frames - len(samples)} frame(s) short of "
                      f"{case_id}'s {case_frames}-frame phrase")
    latency = _LATENCY_RE.search(report_text)
    return {"part": part, "simulator": simulator, "schedule": schedule,
            "wav_sha256": wav_sha, "schedule_sha256": schedule_sha,
            "report_sha256": hashlib.sha256(report_text.encode()).hexdigest(),
            "periods": len(samples),
            # The case's own length, so every part is compared over the same
            # window: a part may run a frame or two long, never short.
            "samples": samples[:case_frames].astype(np.float64),
            "case_frames": case_frames,
            "worst_latency_frames": int(latency.group(1)) if latency else None}


def stop_schedule_s(part_evidence: dict, stop: str) -> list[float]:
    """The times the onsets of `stop` are measured against: the I2S period that
    carries the first sample of the frame its A_STOPS raise landed in."""
    rows = part_evidence["schedule"]["stops"].get(stop)
    if not rows:
        raise Refused(f"the {stop} part's schedule holds no raise for {stop}")
    return [int(row["i2s_period"]) / SR for row in rows]


def measure(case_id: str, evidence: dict) -> tuple[dict, dict]:
    """The case's three properties, from the decoded parts. `evidence` maps a
    part name to what `validate_part` returned for it."""
    for part in STEM_PARTS:
        if part not in evidence:
            raise Refused(f"no evidence for the {part} part")
    mix = evidence["mix"]["samples"]
    stems = {part: evidence[part]["samples"] for part in ("voice", "drum-mix", "body")}
    stem_sum = sum(stems.values())
    metrics: dict = {}

    # ---- Event timing: each stop's row alone, worst reported, never averaged --
    stops = sorted(name.split(":", 1)[1] for name in evidence if name.startswith("stop:"))
    if not stops:
        raise Refused("no per-stop part was measured: event timing cannot be asked")
    tol, basis = rc.tol_fixed(10.0, "event timing")(0.0, {})
    worst, worst_stop, refusal, outside_total = None, "", "", 0
    per_stop: dict = {}
    for stop in stops:
        part = evidence[f"stop:{stop}"]
        scheduled = stop_schedule_s(part, stop)
        est = rc.worst_event_offset_ms(part["samples"] / 32768.0, SR, scheduled)
        outside_total += int((est.detail or {}).get("onsets_outside_the_schedule", 0))
        per_stop[stop] = {"scheduled": len(scheduled), "valid": bool(est.ok),
                          "offset_ms": None if not est.ok else round(float(est.value), 4),
                          "why": "" if est.ok else f"{est.reason} {est.detail}",
                          "worst_host_latency_frames": part["worst_latency_frames"]}
        if not est.ok:
            refusal = refusal or f"{stop}: {est.reason} {est.detail}"
            continue
        if worst is None or est.value > worst:
            worst, worst_stop = float(est.value), stop
    if refusal or worst is None:
        metrics["Event timing"] = rc.invalid_metric("ms", refusal or "no stop to measure", tol)
    else:
        metrics["Event timing"] = {"value": round(worst, 4), "units": "ms", "reference": 0.0,
                                   "error": round(worst, 4), "tolerance": tol, "valid": True,
                                   "tolerance_basis": basis, "worst_stop": worst_stop}

    # ---- bus balance: does the master mix's one rail change the sum? ---------
    if rc.am.rms(stem_sum) <= 0 or rc.am.rms(mix) <= 0:
        metrics["bus balance"] = rc.invalid_metric("dB", "a silent run")
    else:
        value = 20.0 * math.log10(rc.am.rms(mix) / rc.am.rms(stem_sum))
        metrics["bus balance"] = {"value": round(value, 4), "units": "dB", "reference": 0.0,
                                  "error": round(value, 4), "tolerance": 0.5, "valid": True,
                                  "tolerance_basis": "bus sum"}

    # ---- output artifacts: samples of the final output on the rail -----------
    rail = 100.0 * rc.am.clipped_fraction(mix, 32767.0)
    metrics["output artifacts"] = {"value": round(rail, 6), "units": "% of samples",
                                  "reference": 0.0, "error": round(rail, 6),
                                  "tolerance": 0.01, "valid": True,
                                  "tolerance_basis": "rail"}

    schedule = evidence["mix"]["schedule"]
    diagnostics = {
        "peak_fs": round(float(np.abs(mix).max()) / 32768.0, 6),
        "stem_peaks_fs": {name: round(float(np.abs(s).max()) / 32768.0, 6)
                          for name, s in stems.items()},
        "scheduled_events": int(schedule["scheduled_hits"]),
        "stops_timed": stops,
        "onsets_outside_the_schedule": outside_total,
        "dc_offset_fs": round(float(mix.mean()) / 32768.0, 8),
        "patch": schedule["patch"], "bpm": schedule["bpm"],
        "case_frames": evidence["mix"]["case_frames"],
        "per_stop_event_timing": per_stop,
        "stem_sum_minus_mix_samples": int(np.count_nonzero(stem_sum != mix)),
        "stem_sum_minus_mix_max_abs": int(np.abs(stem_sum - mix).max()),
        "host_to_register_latency_frames": {
            name: part["worst_latency_frames"] for name, part in sorted(evidence.items())},
        "decoded_i2s_sha256": {name: part["wav_sha256"]
                               for name, part in sorted(evidence.items())},
        "schedule_sha256": {name: part["schedule_sha256"]
                            for name, part in sorted(evidence.items())},
        "simulator": sorted({part["simulator"] for part in evidence.values()}),
        "note": ("Every number here is computed from I2S words decoded from BCLK, LRCLK and "
                 "SDATA, and every part's wire was bit-identical to model/synth_top_model.py "
                 "over the whole phrase."),
    }
    return metrics, diagnostics


def control_report(case_id: str, clean_mix: np.ndarray, outdir: pathlib.Path,
                   *, simulator: str, timeout_s: float) -> dict:
    """The DRUM_BUS_STALE control, and what it is and is not sensitive to.

    The mutation makes the master mix read the previous frame's drum buses.
    Two separate questions, answered separately because they have different
    answers: does the bench's bit-exact wire-versus-model comparison catch it
    (it must, and that is the precondition every metric here rests on), and do
    the case's three metrics move (they largely do not -- a one-frame shift of
    both drum buses is 20.8 us against a 10 ms event-timing tolerance, and it
    moves the mix and the stems together). Saying the second out loud is the
    point: a control that is reported as protecting a metric it cannot see is
    the false green docs/verification-rules.md exists to prevent."""
    part = f"control-{CONTROL_INJECT}"
    run = run_part(case_id, "mix", outdir / part, simulator=simulator,
                   inject=CONTROL_INJECT, timeout_s=timeout_s, expect_fail=True)
    text = run["paths"]["report"].read_text()
    caught = (run["status"] == 0
              and f"negative control {CONTROL_INJECT} CAUGHT" in text
              and "FAIL -- of " in text)
    out = {"inject": CONTROL_INJECT, "part": "mix", "expect": "the bench turns red",
           "caught": bool(caught), "exit_status": run["status"],
           "report": str(run["paths"]["report"].relative_to(ROOT))
                     if run["paths"]["report"].is_relative_to(ROOT) else str(run["paths"]["report"]),
           "report_sha256": hashlib.sha256(text.encode()).hexdigest()}
    mism = re.search(r"FAIL -- of (\d+) decoded I2S periods: (\d+) differ from the model", text)
    if mism:
        out["periods"] = int(mism.group(1))
        out["periods_differing_from_the_model"] = int(mism.group(2))
    if run["paths"]["wav"].is_file():
        injected = read_wav_int16(run["paths"]["wav"]).astype(np.float64)
        n = min(len(injected), len(clean_mix))
        delta = injected[:n] - clean_mix[:n]
        out["audio_vs_clean_mix"] = {
            "samples_compared": int(n),
            "samples_differing": int(np.count_nonzero(delta)),
            "max_abs_difference_lsb": int(np.abs(delta).max()),
            "rail_percent_injected": round(100.0 * rc.am.clipped_fraction(injected[:n], 32767.0), 6),
            "rail_percent_clean": round(100.0 * rc.am.clipped_fraction(clean_mix[:n], 32767.0), 6),
            "rms_ratio_db": (round(20.0 * math.log10(rc.am.rms(injected[:n]) / rc.am.rms(clean_mix[:n])), 4)
                             if rc.am.rms(clean_mix[:n]) > 0 else None)}
    return out


def fixed_model_comparison(case_id: str, record: dict, twin_path: pathlib.Path) -> dict | None:
    """The `fixed-model` twin this anchor is about to stand next to, compared
    metric by metric and SAID OUT LOUD. Without this the RTL record simply
    replaces the model's at the same path and a disagreement between the two
    engines would leave no trace at all."""
    if not twin_path.is_file():
        return None
    try:
        twin = json.loads(twin_path.read_text())
    except json.JSONDecodeError:
        return None
    if twin.get("engine") == "integrated-rtl":
        return None                          # an earlier anchor, not the model twin
    case = next(c for c in rc.load_cases() if c["case_id"] == case_id)
    twin_verdict = scorecard.evaluate(case, twin)
    rtl_verdict = scorecard.evaluate(case, record)
    per_metric = {}
    for name, metric in sorted(record["metrics"].items()):
        other = (twin.get("metrics") or {}).get(name, {})
        per_metric[name] = {
            "integrated_rtl": metric.get("value") if metric.get("valid") else None,
            "fixed_model": other.get("value") if other.get("valid") else None,
            "units": metric.get("units"), "tolerance": metric.get("tolerance")}
    return {"twin_engine": twin.get("engine"),
            "twin_source_commit": twin.get("source_commit"),
            "twin_analysis_run": twin.get("analysis_run"),
            "twin_sha256": _sha256(twin_path),
            "fixed_model_state": twin_verdict["state"],
            "fixed_model_worst": (None if twin_verdict["worst"] is None
                                  else round(twin_verdict["worst"], 4)),
            "integrated_rtl_state": rtl_verdict["state"],
            "integrated_rtl_worst": (None if rtl_verdict["worst"] is None
                                     else round(rtl_verdict["worst"], 4)),
            "engines_agree_on_state": twin_verdict["state"] == rtl_verdict["state"],
            "metrics": per_metric}


def build_record(case_id: str, evidence: dict, metrics: dict, diagnostics: dict,
                 *, audio_rel: str, control: dict | None) -> dict:
    case = next(c for c in rc.load_cases() if c["case_id"] == case_id)
    commit = rc.source_commit()
    worktree = rc.worktree_state()
    schedule = evidence["mix"]["schedule"]
    inputs = rc.model_input_hashes({
        f"decoded:{case_id}:{name}": "sha256:" + part["wav_sha256"]
        for name, part in sorted(evidence.items())})
    inputs.update({f"schedule:{case_id}:{name}": "sha256:" + part["schedule_sha256"]
                   for name, part in sorted(evidence.items())})
    provenance = rc.provenance(
        inputs,
        {"mix": audio_rel,
         **{f"verification:{name}": str(part_paths(pathlib.Path("."), name)["report"])
            for name in sorted(evidence)}},
        {"patch": schedule["patch"], "dense": schedule["dense"], "bpm": schedule["bpm"],
         "seconds": schedule["seconds"], "bus_gain": schedule["bus_gain"],
         "limiter": False, "parts": sorted(evidence),
         "simulator": diagnostics["simulator"],
         "measurement_source_sha256": {
             rel: _sha256(ROOT / rel) for rel in MEASUREMENT_SOURCES
             if (ROOT / rel).is_file()}})
    provenance["engine"] = "integrated-rtl"
    record = {
        "engine": "integrated-rtl",
        "case_id": case_id, "subject": case["subject"],
        "source_commit": commit,
        "source_dirty": bool(worktree.get("dirty")),
        "analysis_run": rc.analysis_run(),
        "reference_profile": ("our own per-bus stems and the register schedule the CS_N pin "
                              "predicted; no external reference is involved in this case"),
        "render_run": (f"synth_top.v over the SPI pins: {schedule['patch']} bass line and the "
                       f"{'dense' if schedule['dense'] else 'sparse'} 808 groove, "
                       f"{schedule['bpm']:.0f} BPM, {schedule['bars']} bars, buses "
                       f"{schedule['bus_gain']}/{schedule['bus_gain']}, no limiter; "
                       f"{len(evidence)} parts decoded from the I2S pins"),
        "audio": audio_rel,
        "tolerance_policy": rc.TOLERANCE_POLICY,
        "metrics": metrics,
        "diagnostics": diagnostics,
        "provenance": provenance,
    }
    if control is not None:
        record["diagnostics"]["negative_control"] = control
    return record


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--case", default="E1A", help="an Ensemble case id (E1A, E1B, E2A)")
    ap.add_argument("--simulator", choices=("iverilog", "verilator"), default="verilator",
                    help="RTL event engine; Verilator is about three times faster on a "
                         "six-second phrase and is what this was measured with")
    ap.add_argument("--jobs", type=int, default=2,
                    help="parts to simulate at once (this repository's shared-host budget is 2)")
    ap.add_argument("--outdir", default=None, help="where the per-part runs go")
    ap.add_argument("--out", default=None, help="the result record")
    ap.add_argument("--audio", default=None, help="the scored mix WAV kept with the record")
    ap.add_argument("--twin", default=None,
                    help="where the fixed-model record being stood next to is copied")
    ap.add_argument("--reuse", action="store_true",
                    help="keep a part's existing run when its report, WAV and schedule still "
                         "validate together; re-run only what is missing or stale")
    ap.add_argument("--control", dest="control", action="store_true", default=True)
    ap.add_argument("--no-control", dest="control", action="store_false",
                    help="skip the DRUM_BUS_STALE run (the record then says so)")
    ap.add_argument("--timeout", type=float, default=5400.0)
    a = ap.parse_args(argv)

    slug = a.case.lower()
    outdir = pathlib.Path(a.outdir or ROOT / f"build/ensemble-{a.case}")
    out = pathlib.Path(a.out or ROOT / f"docs/scorecard/results/{a.case}.json")
    audio = pathlib.Path(a.audio or ROOT / f"docs/scorecard/ensemble-{slug}/rtl/{a.case}-mix-i2s.wav")
    twin = pathlib.Path(a.twin or ROOT / f"docs/scorecard/ensemble-{slug}/rtl/{a.case}-fixed-model-twin.json")
    for path in (outdir, out, audio, twin):
        if not pathlib.Path(path).is_absolute():
            path = ROOT / path
    outdir, out, audio, twin = (p if p.is_absolute() else ROOT / p
                                for p in (outdir, out, audio, twin))
    if not audio.resolve().is_relative_to(ROOT) or not out.resolve().is_relative_to(ROOT):
        print("score_ensemble_i2s: REFUSED -- outputs must remain inside the repository")
        return 2

    try:
        parts = parts_for(a.case)
    except (ValueError, KeyError, StopIteration) as exc:
        print(f"score_ensemble_i2s: REFUSED -- {exc}")
        return 2
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"score_ensemble_i2s: {a.case} needs {len(parts)} RTL runs "
          f"({', '.join(parts)}) on {a.simulator}")

    # ---- which parts already have usable evidence ----------------------------
    evidence: dict = {}
    todo = []
    for part in parts:
        paths = part_paths(outdir, part)
        if a.reuse and paths["report"].is_file():
            try:
                evidence[part] = validate_part(a.case, part, paths["report"].read_text(),
                                               paths["wav"], paths["schedule"])
                print(f"score_ensemble_i2s: reusing {part} ({evidence[part]['periods']} periods)")
                continue
            except Refused as exc:
                print(f"score_ensemble_i2s: re-running {part} -- {exc}")
        todo.append(part)

    # ---- run what is left, bounded ------------------------------------------
    if todo:
        workers = max(1, min(a.jobs, len(todo)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(run_part, a.case, part, outdir,
                                   simulator=a.simulator, timeout_s=a.timeout): part
                       for part in todo}
            for future in concurrent.futures.as_completed(futures):
                run = future.result()
                print(f"score_ensemble_i2s: {run['part']} exited {run['status']}")
    for part in todo:
        paths = part_paths(outdir, part)
        if not paths["report"].is_file():
            print(f"score_ensemble_i2s: REFUSED -- {part} produced no report")
            return 2
        try:
            evidence[part] = validate_part(a.case, part, paths["report"].read_text(),
                                           paths["wav"], paths["schedule"])
        except Refused as exc:
            print(f"score_ensemble_i2s: REFUSED -- {exc}")
            return 2

    # ---- the measurement ----------------------------------------------------
    try:
        metrics, diagnostics = measure(a.case, evidence)
    except Refused as exc:
        print(f"score_ensemble_i2s: REFUSED -- {exc}")
        return 2

    control = None
    if a.control:
        try:
            control = control_report(a.case, evidence["mix"]["samples"], outdir,
                                     simulator=a.simulator, timeout_s=a.timeout)
        except Refused as exc:
            print(f"score_ensemble_i2s: REFUSED -- the negative control did not run: {exc}")
            return 2
        if not control["caught"]:
            print(f"score_ensemble_i2s: REFUSED -- INJECT_BUG_{CONTROL_INJECT} did NOT turn the "
                  f"bench red (exit {control['exit_status']}). The anchor rests on that "
                  f"comparison being discriminating, so it is not evidence without it.")
            return 2
        print(f"score_ensemble_i2s: negative control {CONTROL_INJECT} CAUGHT: "
              f"{control.get('periods_differing_from_the_model')} of {control.get('periods')} "
              f"periods differ from the model; audio differs in "
              f"{control.get('audio_vs_clean_mix', {}).get('samples_differing')} samples, "
              f"worst {control.get('audio_vs_clean_mix', {}).get('max_abs_difference_lsb')} LSB")
    else:
        diagnostics["negative_control"] = {
            "inject": CONTROL_INJECT, "caught": None,
            "why": "--no-control: this record carries no evidence that the comparison it "
                   "rests on is discriminating"}

    # ---- the scored artefact, kept with the record --------------------------
    audio.parent.mkdir(parents=True, exist_ok=True)
    rc.write_wav16(audio, evidence["mix"]["samples"] / 32768.0, SR)
    audio_rel = str(audio.relative_to(ROOT))
    record = build_record(a.case, evidence, metrics, diagnostics,
                          audio_rel=audio_rel, control=control)
    record["provenance"]["config"]["scored_audio_artifact_sha256"] = _sha256(audio)

    # ---- the fixed-model twin, preserved and compared -----------------------
    comparison = fixed_model_comparison(a.case, record, out)
    if comparison is not None:
        twin.parent.mkdir(parents=True, exist_ok=True)
        twin.write_text(out.read_text())
        comparison["twin_kept_at"] = str(twin.relative_to(ROOT))
        record["fixed_model_comparison"] = comparison
        print(f"score_ensemble_i2s: the {comparison['twin_engine']} twin "
              f"({comparison['fixed_model_state']}, worst {comparison['fixed_model_worst']}) "
              f"is kept at {comparison['twin_kept_at']}")
    elif out.is_file():
        print(f"score_ensemble_i2s: {out.name} already holds an integrated-rtl record; "
              f"no fixed-model twin to preserve")

    verdict = scorecard.evaluate(next(c for c in rc.load_cases() if c["case_id"] == a.case),
                                record)
    if verdict["state"] not in (scorecard.PASS, scorecard.FAIL):
        print(f"score_ensemble_i2s: REFUSED -- the measurement is not scoreable: {verdict['why']}")
        return 2
    record["scorecard_state"] = verdict["state"]
    record["scorecard_reason"] = verdict["why"]
    record["provenance"]["outcome_code"] = 0 if verdict["state"] == scorecard.PASS else 1
    if comparison is not None:
        comparison["integrated_rtl_state"] = verdict["state"]
        comparison["integrated_rtl_worst"] = (None if verdict["worst"] is None
                                              else round(verdict["worst"], 4))
        comparison["engines_agree_on_state"] = (
            comparison["fixed_model_state"] == verdict["state"])
        if not comparison["engines_agree_on_state"]:
            print(f"score_ensemble_i2s: THE ENGINES DISAGREE -- fixed-model "
                  f"{comparison['fixed_model_state']} (worst {comparison['fixed_model_worst']}) "
                  f"vs integrated-rtl {verdict['state']} (worst "
                  f"{comparison['integrated_rtl_worst']}). The model twin is kept at "
                  f"{comparison['twin_kept_at']}; neither has been discarded.")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2) + "\n")
    print(f"score_ensemble_i2s: {verdict['state']} {a.case} on integrated-rtl, worst "
          f"{verdict['worst']:.4f}; three properties measured from decoded I2S over "
          f"{len(evidence)} parts")
    for name, metric in sorted(metrics.items()):
        print(f"  {name:<18}{metric.get('value')} {metric.get('units')} "
              f"(tolerance {metric.get('tolerance')})")
    print(f"score_ensemble_i2s: record {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
