"""Mutation record for fpga/scripts/pnr_report.py (issue #565, Judge on PR #594).

Applies each mutant in turn, runs fpga/test_pnr_report.py, restores the file,
and prints KILLED/SURVIVED. Usage: python fpga/testdata/mutate_pnr_report.py
[--python PY]. "J-" names are the Judge's mutants; "rc is None" is a known
EQUIVALENT mutant (still REFUSED via the rc != 0 path) and is expected to survive.
"""
import subprocess, sys
from pathlib import Path
WT = Path(__file__).resolve().parents[2]
PY = sys.argv[sys.argv.index("--python") + 1] if "--python" in sys.argv else sys.executable
SRC = WT / "fpga/scripts/pnr_report.py"
ORIG = SRC.read_text()
UNLINK = '''    for name in (*TARGETS[a.target]["stale"], log_p.name, st_p.name, console.name,
                 f"{a.target}_refused.txt", f"{a.target}_nofit.txt"):
        (build / name).unlink(missing_ok=True)
    log_p.write_text("")
'''
M = {
 "J-survivor: remove unlink loop + log truncation": [(UNLINK, "")],
 "remove unlink loop only (keep truncation)": [(UNLINK, '    log_p.write_text("")\n')],
 "J-survivor: disable evaluate bitstream check": [("if not (bit_p.is_file() and bit_p.stat().st_size > 0):", "if False:")],
 "J-survivor: disable util[0] > r order check": [("if util and util[0] > r:", "if False:")],
 "J-equivalent: disable rc is None": [('if rc is None:\n', 'if False:\n')],
 "always write --out": [("if v.state == ROUTED or (v.state == NOFIT and a.allow_no_fit):\n        Path(a.out)", "if True:\n        Path(a.out)")],
 "ignore error lines at rc 0": [("    if errors:\n        return Verdict(REFUSED", "    if False:\n        return Verdict(REFUSED")],
 "missing status treated as OK": [("    if status is None:\n        return", "    if status is None:\n        status = {'nextpnr': {'returncode': 0}, 'after': []}\n    if False:\n        return")],
 "ignore --after failures": [("if st.get(\"error\") or st.get(\"signal\") or st.get(\"returncode\") != 0:", "if False:")],
 "skip running --after": [("        for s in a.after:", "        for s in []:")],
 "any error counts as no-fit": [("fit = [l for l in errors if FIT_ERRORS.search(l)]", "fit = errors")],
 "allow-no-fit accepts REFUSED": [("if v.state == ROUTED or (v.state == NOFIT and allow_no_fit):", "if v.state == ROUTED or allow_no_fit:")],
 "non-zero rc treated as no-fit": [("return Verdict(REFUSED, why + [f\"nextpnr exited {rc} without", "return Verdict(NOFIT, why + [f\"nextpnr exited {rc} without")],
 "new: render Fmax on unrouted reports": [("if mf and verdict.state != ROUTED:", "if False:")],
}
bad = []
try:
    for name, subs in M.items():
        s = ORIG
        for a, b in subs:
            assert s.count(a) == 1, (name, a)
            s = s.replace(a, b)
        SRC.write_text(s)
        r = subprocess.run([PY, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider",
                            str(WT / "fpga/test_pnr_report.py")], capture_output=True, text=True, cwd=WT)
        fails = [l.split(" - ")[0] for l in r.stdout.splitlines() if l.startswith("FAILED")]
        print(f"{'KILLED ' if r.returncode else 'SURVIVED'} {name}" + (f"  <- {fails[0]}" if fails else ""))
        if r.returncode == 0 and not name.startswith("J-equivalent"):
            bad.append(name)
finally:
    SRC.write_text(ORIG)
if bad:
    print("UNEXPECTED SURVIVORS: " + "; ".join(bad))
sys.exit(1 if bad else 0)
