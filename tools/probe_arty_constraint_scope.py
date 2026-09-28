#!/usr/bin/env python3
"""Does check_arty_evidence_binding's live gate see the constraint file? (#421)

    python3 tools/probe_arty_constraint_scope.py     # 0 every arm as expected

Drives each arm of the control WITHOUT pytest and without a matcher, and
prints the gate's own exit code, so an arm that FAILS TO FIRE prints
`GREEN(0)` where a red was required and is visibly a defect. A control you can
only read through `pytest.raises` tells you an exception was raised; this tells
you what the gate said.

WHAT IT RECORDS, AND WHY IT IS COMMITTED RATHER THAN RUN ONCE. Arm 3 is the
defect #421 was filed for, and it is the arm that stays GREEN on purpose:
revert fpga/boards/arty-a7-100.xdc to bytes the tree has not had since
383f10b, and the default rung -- the one `make verify` runs at Makefile:73 --
still exits 0. Before this change EVERY arm below behaved like arm 3, because
there was no other scope to ask. The pair (arm 2 red, arm 3 green, same
reverted bytes) is the evidence that publication scope carries constraint
information and that the default was deliberately left alone rather than
forgotten.

Arm 4 is the false-positive control: a moved RTL source must go red WITHOUT
naming the constraint file, or "red when the XDC moves" is just "red".

Arm 1 is the satisfiability arm. Without it a green-by-construction red arm
would be indistinguishable from a working control.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
XDC_REL = "fpga/boards/arty-a7-100.xdc"
R0_PUBLICATION = ROOT / "fpga/reports/arty/integrated-baseline-2025.1/publication.json"

_spec = importlib.util.spec_from_file_location(
    "check_arty_evidence_binding", ROOT / "tools/check_arty_evidence_binding.py")
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

sys.path.insert(0, str(ROOT / "fpga"))
import build_arty as build  # noqa: E402
import publish_arty  # noqa: E402

_REAL_SHA = build.sha


def _capture(argv):
    """(exit code, stdout) of one gate run."""
    import io
    import contextlib
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = gate.main(argv)
    return code, buffer.getvalue()


def _revert(rel):
    """Present the whole process -- this probe AND the gate, which hashes
    through build_arty.sha -- with `rel` reverted to the bytes R0 published."""
    published = json.loads(R0_PUBLICATION.read_text())["source_sha256"][rel]
    target = (ROOT / rel).resolve()

    def sha(path):
        return published if Path(path).resolve() == target else _REAL_SHA(path)

    build.sha = sha


def _record_plus_xdc(directory, xdc_sha):
    """The bound record plus the one key no verification record carries."""
    record = json.loads(gate.bound_bindings()[0][1].read_text())
    record["source_sha256"][XDC_REL] = xdc_sha
    path = Path(directory) / "verification.json"
    path.write_text(json.dumps(record, indent=2) + "\n")
    return path


def arm(label, argv, want, want_names=(), want_absent=()):
    """Run one arm and report. `want` is the required exit code; naming it
    here rather than deriving it from the run is what makes this a control."""
    code, out = _capture(argv)
    verdict = {0: "GREEN", 1: "RED-STALE", 2: "RED-REFUSED"}.get(code, f"?{code}")
    problems = []
    if code != want:
        problems.append(f"exit {code}, wanted {want}")
    problems += [f"does not name {n}" for n in want_names if n not in out]
    problems += [f"wrongly names {n}" for n in want_absent if n in out]
    status = "ok" if not problems else "DEFECT: " + "; ".join(problems)
    print(f"  {label:<58s} {verdict}({code})  {status}")
    return not problems


def main():
    import tempfile
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        covering = _record_plus_xdc(tmp, _REAL_SHA(build.XDC))
        saved = dict(publish_arty.VERIFICATION_BY_WRAPPER)

        print("the real tree, real binding:")
        ok &= arm("0. default scope (Makefile:73 rung)", [], 0,
                  want_names=["BOUND", XDC_REL, gate.NOT_COVERED])
        ok &= arm("0b. publication scope: record never hashed the XDC",
                  ["--scope", "publication"], 2,
                  want_names=["REFUSED", XDC_REL, gate.NOT_COVERED],
                  want_absent=[gate.DIFFERS])

        try:
            publish_arty.VERIFICATION_BY_WRAPPER.clear()
            publish_arty.VERIFICATION_BY_WRAPPER["arty_a7_top"] = covering
            print("\na synthesised record that DOES cover the XDC:")
            ok &= arm("1. satisfiability: live XDC, publication scope",
                      ["--scope", "publication"], 0, want_names=["BOUND"])

            _revert(XDC_REL)
            print(f"\n{XDC_REL} reverted to R0's published bytes:")
            ok &= arm("2. publication scope must go red, by name",
                      ["--scope", "publication"], 1,
                      want_names=["STALE", XDC_REL, gate.DIFFERS])
            ok &= arm("3. default scope stays GREEN -- the #421 gap, kept",
                      [], 0, want_names=["BOUND", XDC_REL])
            build.sha = _REAL_SHA

            _revert("rtl-sketch/voice_dp.v")
            print("\nrtl-sketch/voice_dp.v reverted instead (false-positive control):")
            ok &= arm("4. red for the source, silent about the XDC",
                      ["--scope", "publication"], 1,
                      want_names=["rtl-sketch/voice_dp.v"],
                      want_absent=[XDC_REL])
            build.sha = _REAL_SHA
        finally:
            build.sha = _REAL_SHA
            publish_arty.VERIFICATION_BY_WRAPPER.clear()
            publish_arty.VERIFICATION_BY_WRAPPER.update(saved)

    print("\nALL ARMS AS EXPECTED" if ok else "\nAN ARM DID NOT BEHAVE AS NAMED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
