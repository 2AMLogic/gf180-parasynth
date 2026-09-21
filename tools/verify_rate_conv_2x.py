#!/usr/bin/env python3
"""Bit-exact RTL-versus-Python check for the causal 2x FIR conversion pair."""
from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys
import tempfile

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))
import filter_rate_chain as frc


def _vectors(n: int = 512):
    rng = np.random.default_rng(0x2F17)
    x = rng.integers(-32768, 32768, n, dtype=np.int16)
    x[:12] = (32767, 32767, 0, 0, -32768, -32768, 0, 0,
              32767, -32768, 32767, -32768)
    interp = frc.CausalRateConverter(2).reconstruct(x)
    x_even, x_odd = interp[::2], interp[1::2]

    # Explicitly cover valid ladder Q4.15 values outside ordinary Q1.15.
    y_even = rng.integers(-180000, 180001, n, dtype=np.int32)
    y_odd = rng.integers(-180000, 180001, n, dtype=np.int32)
    y_even[:8] = (40000, -40000, 60000, -60000, 262143, -262144, 0, 0)
    y_high = np.empty(2 * n, dtype=np.int32)
    y_high[::2], y_high[1::2] = y_even, y_odd
    decim = frc.CausalRateConverter(2).decimate(y_high, output_bits=19)
    return x, x_even, x_odd, y_even, y_odd, decim


def run(*, inject_clamp: bool = False) -> int:
    iverilog, vvp = shutil.which("iverilog"), shutil.which("vvp")
    if not iverilog or not vvp:
        print("NO-VERDICT: iverilog/vvp unavailable")
        return 2
    module = ROOT / "rtl-sketch/rate_conv_2x.v"
    bench = ROOT / "rtl-sketch/tb_rate_conv_2x.v"
    if not module.is_file() or not bench.is_file():
        print("NO-VERDICT: causal rate-converter RTL or testbench is missing")
        return 2

    x, x0, x1, y0, y1, yout = _vectors()
    with tempfile.TemporaryDirectory(prefix="rate-conv-2x-") as temp_name:
        temp = pathlib.Path(temp_name)
        vectors = temp / "vectors.txt"
        with vectors.open("w") as f:
            for row in zip(x, x0, x1, y0, y1, yout):
                f.write(" ".join(str(int(v)) for v in row) + "\n")
        sim = temp / "rate_conv.vvp"
        compile_cmd = [iverilog, "-g2012", "-s", "tb_rate_conv_2x", "-o", str(sim)]
        if inject_clamp:
            compile_cmd.append("-DINJECT_BUG_RATE_CONV_2X_CLAMP")
        compile_cmd.extend((str(module), str(bench)))
        try:
            built = subprocess.run(compile_cmd, cwd=ROOT, text=True,
                                   capture_output=True, timeout=30)
            if built.returncode:
                print(f"NO-VERDICT: iverilog failed: {built.stderr[-2000:]}")
                return 2
            result = subprocess.run([vvp, str(sim), f"+vectors={vectors}"], cwd=ROOT,
                                    text=True, capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"NO-VERDICT: RTL simulator did not complete: {exc}")
            return 2
    print(result.stdout, end="")
    if result.returncode == 0 and "RATE_CONV_PASS" in result.stdout:
        return 0
    if result.returncode != 0 and "RATE_CONV_FAIL" in result.stdout:
        return 1
    print(f"NO-VERDICT: simulation lacked a clean completion marker (rc={result.returncode})")
    if result.stderr:
        print(result.stderr[-2000:])
    return 2


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inject-clamp", action="store_true",
                        help="negative control: clamp interpolator output to int16")
    parser.add_argument("--expect-fail", action="store_true",
                        help="control passes only when the injected defect is detected")
    args = parser.parse_args(argv)
    result = run(inject_clamp=args.inject_clamp)
    if args.expect_fail:
        if result == 1:
            print("verify_rate_conv_2x: PASS -- injected interpolation clamp was detected")
            return 0
        print(f"verify_rate_conv_2x: FAIL -- injected clamp outcome was {result}, expected mismatch")
        return 1
    print(f"verify_rate_conv_2x: {'PASS' if result == 0 else 'FAIL/NO-VERDICT'}")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
