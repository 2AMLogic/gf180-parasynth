#!/usr/bin/env python3
"""The PDK-neutral yosys cell counts the README quotes, reproducibly.

    python3 rtl-sketch/synth_count.py        (or rtl-sketch/synth_count.sh)

Generic `synth` (no liberty, no -booth), then `stat`. Needs yosys on PATH.

Outcomes (#568; the shell version could print blank counts and exit 0):
    0  three counts printed
    3  REFUSED: yosys is not on PATH
    4  REFUSED: a yosys run exited non-zero, or its log has no `N cells` line
A refusal prints nothing on stdout, so a blank never looks like a result.
"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXIT_NO_YOSYS = 3
EXIT_BAD_RUN = 4

FLOWS = [
    ("ladder_dp, 16-entry tanh ",
     "read_verilog ladder_dp.v; synth -top ladder_dp; stat"),
    ("ladder_dp, 256-entry tanh",
     'read_verilog ladder_dp.v; chparam -set TANH_LOG2N 8 '
     '-set ROM_FILE "tanh256.hex" ladder_dp; synth -top ladder_dp; stat'),
    ("modal_dp, 4 modes        ",
     "read_verilog modal_dp.v; synth -top modal_dp; stat"),
]
CELLS = re.compile(r"^\s+(\d+) +cells$")


class Refused(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


def parse_cells(log_text):
    """Last `N cells` line of a yosys log (the top-level total), or None."""
    found = None
    for line in log_text.splitlines():
        m = CELLS.match(line)
        if m:
            found = int(m.group(1))
    return found


def count(label, script, tmp):
    log = Path(tmp) / "yosys.log"
    log.unlink(missing_ok=True)
    r = subprocess.run(["yosys", "-q", "-l", str(log), "-p", script],
                       cwd=HERE, capture_output=True, text=True)
    if r.returncode != 0:
        raise Refused(EXIT_BAD_RUN, f"yosys exited {r.returncode} for "
                      f"{label.strip()!r}:\n{(r.stdout + r.stderr)[-2000:]}")
    text = log.read_text(errors="replace") if log.exists() else ""
    n = parse_cells(text)
    if n is None:
        raise Refused(EXIT_BAD_RUN, f"no `N cells` line in the yosys log for "
                      f"{label.strip()!r} (yosys stat format changed?)")
    return n


def main():
    try:
        if shutil.which("yosys") is None:
            raise Refused(EXIT_NO_YOSYS, "yosys is not on PATH")
        rows = []
        with tempfile.TemporaryDirectory() as tmp:
            for label, script in FLOWS:
                rows.append(f"{label} : {count(label, script, tmp)} cells")
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return e.code
    print("\n".join(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
