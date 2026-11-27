#!/usr/bin/env python3
"""verify_modal.py must honour a RELATIVE --outdir from any working directory.

#1199: the bench launches iverilog/vvp with cwd=HERE while passing --outdir
through unnormalised -- the same shape that broke verify_drums (#1126) and
verify_ladder (#1199). From any cwd other than rtl-sketch/ a relative outdir
resolves against TWO directories: the script process writes the vectors under
the caller's cwd, the simulator runs under rtl-sketch/ and cannot find the
same paths (`+vec=`/`+out=` mismatch, or iverilog cannot open its `-o`). The
fix is `a.outdir = os.path.abspath(a.outdir)` right after parse_args().

The test drives the REAL script as a subprocess from a scratch cwd with a
relative --outdir and requires exit 0 AND the simulator's output file
(modal_rtl_out.txt) under that directory. A run that merely refuses, or that
writes its vectors and then fails to simulate, does not pass: both were green
paths for a check that only looked at the vectors or accepted a nonzero exit.

Toolchain: the real iverilog/vvp when present (the same run `make verify`
makes); otherwise a controlled double that records the paths it was handed
and emulates a bit-exact simulator by copying the model's expected file to
+out=. The double case runs in EVERY environment (PATH prepended, real tools
shadowed), so the double itself is regression-tested against the defect it
exists to catch -- on the unfixed script its iverilog cannot open the -o path
and the run exits 2, which is the pre-fix failure recorded in #1199's PR.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "verify_modal.py")
OUT_NAME = "modal_rtl_out.txt"

_DOUBLE_IVERILOG = """\
#!/usr/bin/env python3
import os, sys
with open(os.environ["SIM_DOUBLE_LOG"], "a") as fh:
    fh.write("iverilog cwd=%s argv=%s\\n" % (os.getcwd(), " ".join(sys.argv[1:])))
out = sys.argv[sys.argv.index("-o") + 1]
open(out, "w").write("stub compiled vvp image\\n")
"""

_DOUBLE_VVP = """\
#!/usr/bin/env python3
import glob, os, sys
with open(os.environ["SIM_DOUBLE_LOG"], "a") as fh:
    fh.write("vvp cwd=%s argv=%s\\n" % (os.getcwd(), " ".join(sys.argv[1:])))
args = sys.argv[1:]
vec = next(a for a in args if a.startswith("+vec="))[5:]
out = next(a for a in args if a.startswith("+out="))[5:]
exp = sorted(glob.glob(os.path.join(os.path.dirname(vec), "*_expected.txt")))
if not exp:
    sys.stderr.write("double: no *_expected.txt beside %s\\n" % vec)
    sys.exit(1)
open(out, "w").write(open(exp[0]).read())
"""


def _double_env(tmp_path):
    """PATH with iverilog/vvp doubles prepended; they log to SIM_DOUBLE_LOG."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, src in (("iverilog", _DOUBLE_IVERILOG), ("vvp", _DOUBLE_VVP)):
        p = bin_dir / name
        p.write_text(src)
        p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([str(bin_dir), env.get("PATH", "")])
    env["SIM_DOUBLE_LOG"] = str(tmp_path / "double.log")
    return env


def _double_log(tmp_path):
    p = tmp_path / "double.log"
    return p.read_text() if p.exists() else ""


@pytest.mark.parametrize("toolchain", ["real", "double"])
def test_relative_outdir_from_another_cwd(tmp_path, toolchain):
    if toolchain == "real":
        if not (shutil.which("iverilog") and shutil.which("vvp")):
            pytest.skip("iverilog/vvp not on PATH; the double case covers the path logic")
        env = dict(os.environ)
    else:
        env = _double_env(tmp_path)

    work = tmp_path / "caller"
    work.mkdir()
    outdir = "out-rel"                    # relative: the caller's intent
    r = subprocess.run([sys.executable, SCRIPT, "--outdir", outdir],
                       cwd=work, capture_output=True, text=True,
                       env=env, timeout=900)
    out_file = work / outdir / OUT_NAME
    assert r.returncode == 0, (
        f"exit {r.returncode} from a foreign cwd with a relative --outdir;\n"
        f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}\n"
        f"simulator invocations:\n{_double_log(tmp_path)}")
    assert out_file.is_file(), (
        f"exit 0 but no simulator output under the caller's directory:\n"
        f"simulator invocations:\n{_double_log(tmp_path)}")
    assert (work / outdir / "modal_vectors.hex").is_file(), \
        "the model's vectors must land under the caller's directory too"
