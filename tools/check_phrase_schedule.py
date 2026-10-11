#!/usr/bin/env python3
"""Applied versus intended note times for an M5A/M5B SPI-to-I2S phrase (#372).

A verdict, not a printout. Every GATE_ON and GATE_OFF the bench applied (its
own write log, `top_wrs_*.txt`: applied frame, flag, section, address, data,
pin-predicted frame) is compared with the frame the frozen manifest says it
belongs in, ABSOLUTELY -- no event is aligned away -- because the scorer
(`tools/mono_m5a_score.py`) cuts its windows at absolute positions in the
decoded WAV: segment `offset_samples` plus the event's `on_s`.

The frame contract the targets come from, stated once:

* the timing map is the scorer's: segment k starts at `offset_samples[k]`
  (NOT at the sum of `duration_s` and silence, which is 44 samples longer per
  segment because the reference was trimmed by its host latency); an event's
  sample is `offset_samples + round(on_s * 48000)`, round-to-nearest, and its
  release is `offset_samples + round((on_s + gate_s) * 48000)`;
* i2s_tx carries the sample of core frame f in LRCLK period f + 1 (contract
  13, D = 1), so a gate that must be heard at WAV sample s is applied in core
  frame s - 1. That is the frame the write log reports.

PERMITTED ERROR: 0 frames. It is declared from the link, not chosen from a
candidate: the bench's landing frame is the frame after the one CS_N's
acceptance falls in (DR 0007 section 5), which a host controls exactly by
when it starts the transaction, and frame quantisation is resolved by the
integer targets above. Any nonzero offset is therefore a scheduling error, and
the old relative-wait scheduler's 11-27 frames were exactly that.

REFUSED (exit 2), not judged: an inconsistent timing map, a missing, malformed,
unordered or ambiguous write log, a write whose applied frame differs from its
pin prediction or has none, and a gate sequence that is not on/off pairs in the
manifest's number. FAIL is exit 1, PASS exit 0.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import pathlib

A_GATE_ON, A_GATE_OFF = 0x20, 0x21
SR = 48_000
WIRE_D = 1                    # contract 13: period f + 1 carries frame f's sample
TOLERANCE_FRAMES = 0          # see the module docstring; not a tuning dial


class Refused(Exception):
    """The inputs cannot carry a verdict."""


def _frame(seconds: float) -> int:
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or not math.isfinite(seconds):
        raise Refused(f"non-finite or non-numeric time {seconds!r}")
    return int(round(float(seconds) * SR))


def targets(manifest: dict) -> list[dict]:
    """Per event, the core frame its GATE_ON and GATE_OFF must be applied in."""
    try:
        tl = manifest["timeline"]
        segs = tl["segments"]
        silence = _frame(tl["segment_silence_s"])
    except (KeyError, TypeError) as exc:
        raise Refused(f"manifest has no usable timeline: {exc}") from exc
    if not segs:
        raise Refused("manifest timeline has no segments")
    out, expect_offset, last_off = [], 0, -1
    for k, seg in enumerate(segs):
        try:
            offset, samples = int(seg["offset_samples"]), int(seg["samples"])
            events = seg["midi_events"]
        except (KeyError, TypeError, ValueError) as exc:
            raise Refused(f"segment {k} lacks its timing map: {exc}") from exc
        if offset != expect_offset:
            raise Refused(f"segment {k} offset_samples {offset} != {expect_offset} "
                          f"(previous offset + samples + segment silence)")
        expect_offset = offset + samples + silence
        for i, ev in enumerate(events):
            on = _frame(ev["on_s"])
            off = _frame(float(ev["on_s"]) + float(ev["gate_s"]))
            if not 0 <= on < off <= samples:
                raise Refused(f"segment {k} event {i}: on {on} / off {off} outside the "
                              f"segment's {samples} samples or not on < off")
            on_f, off_f = offset + on - WIRE_D, offset + off - WIRE_D
            if on_f <= last_off:
                raise Refused(f"segment {k} event {i} starts at or before the previous release")
            last_off = off_f
            out.append({"segment": k, "event": i, "wave": seg.get("wave"),
                        "note": ev.get("note"), "on_frame": on_f, "off_frame": off_f})
    return out


def read_log(outdir: pathlib.Path) -> list[list[int]]:
    files = sorted(glob.glob(str(pathlib.Path(outdir) / "top_wrs_*.txt")))
    if len(files) != 1:
        raise Refused(f"expected one write log in {outdir}, found {len(files)}")
    rows = []
    for n, line in enumerate(open(files[0]), 1):
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 6:
            raise Refused(f"write log line {n} has {len(parts)} fields, not 6")
        try:
            rows.append([int(p) for p in parts])
        except ValueError as exc:
            raise Refused(f"write log line {n} is not integers: {line.strip()!r}") from exc
    if not rows:
        raise Refused("write log is empty")
    for n, r in enumerate(rows):
        if r[5] < 0:
            raise Refused(f"write {n} has no pin prediction")
        if r[0] != r[5]:
            raise Refused(f"write {n} applied in frame {r[0]} but the CS_N pin predicted {r[5]}")
        if n and r[0] < rows[n - 1][0]:
            raise Refused(f"write {n} applied in frame {r[0]}, before write {n - 1} ({rows[n - 1][0]})")
    return rows


def judge(tgt: list[dict], rows: list[list[int]]) -> dict:
    gates = [(r[0], r[3]) for r in rows if r[3] in (A_GATE_ON, A_GATE_OFF)]
    want = [A_GATE_ON, A_GATE_OFF] * len(tgt)
    if [a for _, a in gates] != want:
        n_on = sum(a == A_GATE_ON for _, a in gates)
        raise Refused(f"gates are not {len(tgt)} on/off pairs in order "
                      f"({n_on} GATE_ON, {len(gates) - n_on} GATE_OFF)")
    on = [f for f, _ in gates[0::2]]
    off = [f for f, _ in gates[1::2]]
    d_on = [a - t["on_frame"] for a, t in zip(on, tgt)]
    d_off = [a - t["off_frame"] for a, t in zip(off, tgt)]
    violations = []
    for t, a, b in zip(tgt, d_on, d_off):
        for what, d in (("GATE_ON", a), ("GATE_OFF", b)):
            if abs(d) > TOLERANCE_FRAMES:
                violations.append(f"segment {t['segment']} event {t['event']} {what} "
                                  f"{'early' if d < 0 else 'late'} by {abs(d)} frame(s) ({d:+d})")
    every = d_on + d_off
    return {
        "verdict": "FAIL" if violations else "PASS",
        "tolerance_frames": TOLERANCE_FRAMES, "wire_delay_periods": WIRE_D,
        "events": len(tgt),
        "target_on_frames": [t["on_frame"] for t in tgt],
        "target_off_frames": [t["off_frame"] for t in tgt],
        "applied_on_frames": on, "applied_off_frames": off,
        "gate_on_offset_frames": d_on, "gate_off_offset_frames": d_off,
        # the three components, reported separately (they have different causes)
        "lead_in_frames": d_on[0],
        "gate_on_drift_frames": [d - d_on[0] for d in d_on],
        "gate_length_error_frames": [b - a for a, b in zip(d_on, d_off)],
        "worst_early_frames": min(0, min(every)), "worst_late_frames": max(0, max(every)),
        "violations": violations,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--manifest", type=pathlib.Path, required=True)
    ap.add_argument("runs", nargs="+", help="label=outdir (the directory holding top_wrs_*.txt)")
    ap.add_argument("--out", type=pathlib.Path, default=None)
    a = ap.parse_args(argv)
    res, status = {}, 0
    try:
        tgt = targets(json.loads(a.manifest.read_text()))
        for spec in a.runs:
            if "=" not in spec:
                raise Refused(f"run {spec!r} is not label=outdir")
            label, d = spec.split("=", 1)
            r = judge(tgt, read_log(pathlib.Path(d)))
            res[label] = r
            print(f"{label}: {r['verdict']} -- GATE_ON offsets {r['gate_on_offset_frames']}, "
                  f"GATE_OFF offsets {r['gate_off_offset_frames']} frames against the manifest "
                  f"(lead-in {r['lead_in_frames']:+d}, drift {r['gate_on_drift_frames']}, "
                  f"gate length error {r['gate_length_error_frames']}; permitted "
                  f"{TOLERANCE_FRAMES})")
            for v in r["violations"]:
                print(f"  {label}: {v}")
            if r["verdict"] != "PASS":
                status = 1
    except (Refused, OSError, json.JSONDecodeError) as exc:
        print(f"REFUSED: {exc}")
        return 2
    if a.out:
        a.out.write_text(json.dumps(res, indent=1) + "\n")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
