#!/usr/bin/env python3
"""Verify the no-DAC sigma-delta output (#406): in-band SNR, with bounds.

    python3 fpga/verify_sd_dac.py [--outdir build/sd-dac]

The bench (fpga/rtl/tb_sd_dac.v) runs the chip's own I2S transmitter into
fpga/rtl/i2s_rx.v into fpga/rtl/sd_dac.v and writes the one-bit stream. This
script decimates that stream -- an exact integer CIC (order 4, R = 64, to
192 kHz) then a 255-tap Kaiser FIR low-pass (20.5 kHz) and R = 4 to 48 kHz --
and compares it against the SOURCE FILE, zero-order held at 12.288 MHz, scaled
by the modulator's documented 7/8 and put through the identical decimator.
The error is therefore only what the wire path and the modulator added; the
16-bit quantisation of the source is common to both and cancels.

The estimator is checked before any RTL result is reported, against answers
known by construction and independent of the modulator; if either check fails
the run is REFUSED (exit 2), never reported:

  known_inband   the reference plus a 5 kHz tone 70 dB below it must read
                 70.0 +- 0.5 dB;
  known_oob      the reference plus a FULL-SCALE 1.507 MHz tone (not on a CIC
                 null) must read >= 110 dB: the decimator rejects the region
                 where the shaped noise lives.

WRONG-THEN-RIGHT, recorded here because it is how this estimator was made:
the first version ran the CIC in float64. At order 4 over ~600k samples the
integrator sums exceed 2^53 and lose precision; it reported 72 dB for the held
note, and at order 5 and 6 it read the known -70 dB tone as 30 dB and -4 dB.
known_inband caught it. The CIC is now exact wrapping int64 arithmetic, as a
hardware CIC is, and reads the same figure at orders 3, 4 and 5.

Cases and bounds (measured when the bounds were set, 2nd order vs 1st order,
from the Python prototype of the same loop):

  sine-6    -6 dBFS ~1 kHz sine            >= 95 dB   (104 / 70)
  held      the model's held note A2        >= 90 dB   (99.5 / 62), peaks
            (fixed patch, 1920 frames on,             -7.6 dBFS
            release to the end)
  square    full-scale 1 kHz square         >= 75 dB   (84): the overload case;
                                                        the integrator clamp
  silence   all-zero input: in-band noise   <= -110 dBFS

Negative controls (each must turn the named case red):

  SD_FIRST_ORDER   quantise the first integrator      sine-6, held
  SD_NO_SAT        integrators wrap instead of clamp  square
  I2S_RX_SHIFT     receiver captures one bit early    every signal case

Also asserted: the wire path's latency. The sample strobed in frame k reaches
the modulator LAG_CYCLES after frame k starts (i2s_tx's D = 1 period, then
the left slot's 16th BCLK rise at cycle 66, one clock into i2s_rx.sample and
one into sd_dac.pdm: 256 + 66 + 1 + 1 = 324). A receiver that latched late
would pass SNR at a different lag, so the lag is checked separately. (The
first derivation said 323 -- it forgot the pdm register -- and the search
measured 324; one cycle of misalignment costs ~40 dB, 65.7 against 104.)

Exit 0 clean and every control caught, 1 otherwise, 2 refused.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from scipy import signal as ss

ROOT = Path(__file__).resolve().parents[1]
OSR = 256                       # core clocks per 48 kHz frame
FB = 1 << 18                    # the modulator's +-8*FS feedback
CIC_R, CIC_N = 64, 4
FIR = ss.firwin(255, 20500, fs=192000, window=("kaiser", 12))
EDGE = 64                       # 48 kHz samples trimmed from each end
STROBE = 176                    # tb_sd_dac.v: the sample strobe cycle
LAG_CYCLES = 256 + 66 + 1 + 1   # D = 1 period, the 16th BCLK, i2s_rx.sample, sd_dac.pdm
LAG_WINDOW = range(256 + 40, 256 + 100)

SOURCES = [ROOT / "fpga/rtl/tb_sd_dac.v", ROOT / "rtl-sketch/i2s_tx.v",
           ROOT / "fpga/rtl/i2s_rx.v", ROOT / "fpga/rtl/sd_dac.v"]

BOUNDS = {"sine-6": ("snr_db", ">=", 95.0), "held": ("snr_db", ">=", 90.0),
          "square": ("snr_db", ">=", 75.0), "silence": ("noise_dbfs", "<=", -110.0)}
CONTROLS = {"SD_FIRST_ORDER": {"sine-6", "held"}, "SD_NO_SAT": {"square"},
            "I2S_RX_SHIFT": {"sine-6", "held", "square"}}


class Refused(Exception):
    pass


# ---- the estimator ---------------------------------------------------------
def decimate(stream) -> np.ndarray:
    """12.288 MHz integer stream -> 48 kHz float, exact integer CIC first."""
    s = np.asarray(stream, dtype=np.int64)
    with np.errstate(over="ignore"):             # wrapping is the CIC's arithmetic
        for _ in range(CIC_N):
            s = np.cumsum(s, dtype=np.int64)
        s = s[CIC_R - 1::CIC_R]
        for _ in range(CIC_N):
            s = np.diff(s, prepend=np.int64(0))
    return np.convolve(s.astype(np.float64) / CIC_R ** CIC_N, FIR, mode="same")[::4]


def reference(samples, n_cycles: int, lag: int) -> np.ndarray:
    """The ideal output in the modulator's units: 7*x, zero-order held, starting
    `lag` cycles into the stream (zero before the first sample arrives)."""
    held = np.repeat(7 * np.asarray(samples, dtype=np.int64), OSR)
    out = np.zeros(n_cycles, dtype=np.int64)
    m = min(len(held), n_cycles - lag)
    out[lag:lag + m] = held[:m]
    return out


def compare(out_stream, ref_stream) -> dict:
    do, dr = decimate(out_stream), decimate(ref_stream)
    e, d = (do - dr)[EDGE:-EDGE], dr[EDGE:-EDGE]
    pe, pd = float(np.mean(e ** 2)), float(np.mean(d ** 2))
    fs2 = float(FB) ** 2 / 2                      # a full-scale sine's power
    return {"snr_db": round(10 * np.log10(pd / pe), 2) if pe and pd else None,
            # an exactly zero error (silence: the idle pattern sits on a CIC
            # null) is recorded as such, never as a sentinel number
            "noise_dbfs": round(10 * np.log10(pe / fs2), 2) if pe else None,
            "exact_zero_error": pe == 0.0}


def check_estimator(samples) -> dict:
    ref = reference(samples, len(samples) * OSR, 0)
    n = np.arange(len(ref))
    rms = float(np.sqrt(np.mean(ref.astype(np.float64) ** 2)))
    scale = 1 << 8                                  # headroom so the tones are integers
    tone = np.round(scale * rms * np.sqrt(2) * 10 ** (-70 / 20)
                    * np.sin(2 * np.pi * 5000 * n / 12.288e6)).astype(np.int64)
    known_inband = compare(ref * scale + tone, ref * scale)["snr_db"]
    oob = np.round(scale * FB * np.sin(2 * np.pi * 1.507e6 * n / 12.288e6)).astype(np.int64)
    known_oob = compare(ref * scale + oob, ref * scale)["snr_db"]
    result = {"known_inband_db": known_inband, "known_inband_expected": "70.0 +- 0.5",
              "known_oob_db": known_oob, "known_oob_expected": ">= 110"}
    if known_inband is None or abs(known_inband - 70.0) > 0.5:
        raise Refused(f"estimator reads a known -70 dB in-band tone as {known_inband} dB")
    if known_oob is None or known_oob < 110:
        raise Refused(f"estimator lets a full-scale out-of-band tone through: {known_oob} dB")
    return result


# ---- stimuli ---------------------------------------------------------------
def held_note() -> np.ndarray:
    for p in ("fpga", "model", "rtl-sketch"):
        sys.path.insert(0, str(ROOT / p))
    import uart_host as uh
    import synth_top_model as stm
    import voice_fx as vf
    w = [(0, fl, s, a, d) for fl, s, a, d in
         uh.voice_image_writes(None) + uh.voice_mixer_writes(None)]
    for f, op, *args in vf.KeyHost().writes([(0, "on", 45), (1920, "off", 45)],
                                            vf.VoiceFx.patch_regs()):
        f += 2
        if op == "INC":
            w.append((f, 1 if args[2] else 0, 0, stm.A_INC + args[0], args[1]))
        elif op == "TRACK":
            w.append((f, 0, 0, stm.A_TRACK, args[0]))
        else:
            w.append((f, 0, 0, stm.A_GATE_ON if args[0] else stm.A_GATE_OFF, 0))
    m = stm.SynthTopModel(oversample_2x=True, filter_2x=True).run(w, 2400)
    return np.asarray(m["sample"], dtype=np.int64)


def stimuli() -> dict:
    t = np.arange(2048)
    sine = np.round(10 ** (-6 / 20) * 32768 * np.sin(2 * np.pi * t * 43 / 2048)).astype(np.int64)
    square = np.where((t // 24) % 2, 32767, -32768).astype(np.int64)
    held = held_note()
    if not np.any(held):
        raise Refused("the model rendered the held note as silence")
    return {"sine-6": sine, "held": held, "square": square,
            "silence": np.zeros(1024, dtype=np.int64)}


# ---- the RTL ---------------------------------------------------------------
def compile_bench(work: Path, inject: str | None) -> Path:
    exe = work / f"tb_{inject or 'clean'}.vvp"
    cmd = ["iverilog", "-g2012", "-s", "tb_sd_dac", "-o", str(exe)]
    if inject:
        cmd.append(f"-DINJECT_BUG_{inject}")
    subprocess.run(cmd + [str(p) for p in SOURCES], check=True, capture_output=True, text=True)
    return exe


def run_bench(exe: Path, work: Path, name: str, samples) -> np.ndarray:
    hexfile = work / f"{exe.stem}_{name}.hex"
    bits = work / f"{exe.stem}_{name}.bits"
    hexfile.write_text("".join(f"{int(v) & 0xFFFF:04x}\n" for v in samples))
    r = subprocess.run(["vvp", "-n", str(exe), f"+samples={hexfile}",
                        f"+frames={len(samples)}", f"+bits={bits}"],
                       capture_output=True, text=True, timeout=1800)
    if r.returncode != 0 or "TB_SD_DAC DONE" not in r.stdout:
        raise RuntimeError(f"bench failed: {r.stdout[-500:]} {r.stderr[-500:]}")
    raw = np.frombuffer(bits.read_bytes(), dtype=np.uint8)
    if len(raw) != (len(samples) + 2) * OSR or not np.all((raw == 48) | (raw == 49)):
        raise RuntimeError(f"bench stream is not {len(samples) + 2} frames of 0/1")
    return np.where(raw == 49, FB, -FB).astype(np.int64)


def lag_search(stream, samples) -> int:
    best = max(LAG_WINDOW, key=lambda lag: compare(
        stream, reference(samples, len(stream), lag))["snr_db"] or -1e9)
    return best


def measure(stream, samples) -> dict:
    return compare(stream, reference(samples, len(stream), LAG_CYCLES))


def verdict(case: str, m: dict) -> bool:
    key, op, bound = BOUNDS[case]
    v = m[key]
    if key == "noise_dbfs" and m["exact_zero_error"]:
        return True
    return bool(v is not None and (v >= bound if op == ">=" else v <= bound))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--outdir", type=Path, default=ROOT / "build/sd-dac")
    a = ap.parse_args(argv)
    a.outdir.mkdir(parents=True, exist_ok=True)
    record = {"state": "REFUSED", "bounds": {k: list(v) for k, v in BOUNDS.items()},
              "lag_cycles_expected": LAG_CYCLES,
              "source_sha256": {str(p.relative_to(ROOT)): sha(p) for p in SOURCES}}

    def save(code):
        (a.outdir / "verification.json").write_text(json.dumps(record, indent=2) + "\n")
        print(f"verify_sd_dac: {record['state']}"
              + (f" -- {record['reason']}" if "reason" in record else ""))
        return code

    try:
        for tool in ("iverilog", "vvp"):
            if shutil.which(tool) is None:
                raise Refused(f"{tool} not on PATH")
        stim = stimuli()
        record["estimator"] = check_estimator(stim["sine-6"])
    except Refused as exc:
        record["reason"] = str(exc)
        return save(2)

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        builds = [None] + list(CONTROLS)
        exes = {b: compile_bench(work, b) for b in builds}
        jobs = [(b, c) for b in builds for c in stim]
        with ThreadPoolExecutor(max_workers=4) as pool:
            streams = dict(zip(jobs, pool.map(
                lambda j: run_bench(exes[j[0]], work, j[1], stim[j[1]]), jobs)))

    ok = True
    lag = lag_search(streams[(None, "sine-6")], stim["sine-6"])
    record["lag_cycles_measured"] = lag
    if lag != LAG_CYCLES:
        ok = False
        print(f"FAIL wire latency: sine-6 aligns at {lag} cycles, expected {LAG_CYCLES}")
    clean = {}
    for case in stim:
        m = measure(streams[(None, case)], stim[case])
        m["pass"] = verdict(case, m)
        clean[case] = m
        ok &= m["pass"]
        key, op, bound = BOUNDS[case]
        print(f"{'PASS' if m['pass'] else 'FAIL'} {case:8s} {key} {m[key]} (bound {op} {bound})")
    record["clean"] = clean
    controls = {}
    for inject, must in CONTROLS.items():
        red = sorted(c for c in stim if not verdict(c, measure(streams[(inject, c)], stim[c])))
        caught = must <= set(red)
        controls[inject] = {"red": red, "required_red": sorted(must), "caught": caught}
        ok &= caught
        print(f"{'CAUGHT' if caught else 'MISSED'} {inject}: red {red}, required {sorted(must)}")
    record["controls"] = controls
    record["state"] = "PASS" if ok else "FAIL"
    return save(0 if ok else 1)


if __name__ == "__main__":
    raise SystemExit(main())
