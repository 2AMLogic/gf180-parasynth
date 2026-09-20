"""Compile and run directed Q1.15 saturation checks for the shipping FIR."""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
RTL = ROOT / "rtl-sketch"


def main() -> int:
    iverilog, vvp = shutil.which("iverilog"), shutil.which("vvp")
    if not iverilog or not vvp:
        raise SystemExit("REFUSED: iverilog and vvp are required for the decimator saturation check")
    with tempfile.TemporaryDirectory(prefix="decimator-saturation-") as tmp:
        image = pathlib.Path(tmp) / "saturation.vvp"
        build = subprocess.run(
            [iverilog, "-g2012", "-o", str(image),
             str(RTL / "test_decimate_2x_tm_sym_saturation.v"),
             str(RTL / "decimate_2x_tm_sym.v")],
            capture_output=True, text=True,
        )
        if build.returncode:
            print(build.stdout, end="")
            print(build.stderr, end="")
            return build.returncode
        sim = subprocess.run([vvp, "-n", str(image)], capture_output=True, text=True)
        print(sim.stdout, end="")
        if sim.returncode:
            print(sim.stderr, end="")
            return sim.returncode
        return 0 if "PASS decimate_2x_tm_sym saturates both Q1.15 rails" in sim.stdout else 1


if __name__ == "__main__":
    raise SystemExit(main())
