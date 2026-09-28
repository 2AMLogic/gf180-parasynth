#!/usr/bin/env python3
"""Does check_arty_evidence_binding's live gate see the constraint file? (#421, #436)

    python3 tools/probe_arty_constraint_scope.py     # 0 every arm as expected

Drives each arm of the control WITHOUT pytest and without a matcher, and
prints the gate's own exit code, so an arm that FAILS TO FIRE prints
`GREEN(0)` where a red was required and is visibly a defect. A control you can
only read through `pytest.raises` tells you an exception was raised; this tells
you what the gate said.

WHAT IT RECORDS, AND WHY IT IS COMMITTED RATHER THAN RUN ONCE. Arm 3 is the
defect #421 was filed for, and it is the arm that stays GREEN on purpose:
revert fpga/boards/arty-a7-100.xdc to bytes the tree has not had since
383f10b, and the default rung -- the one `make verify` runs -- still exits 0.
Before #421 EVERY arm below behaved like arm 3, because there was no other
scope to ask. The pair (arm 2 red, arm 3 green, same reverted bytes) is the
evidence that publication scope carries constraint information and that the
default was deliberately left alone rather than forgotten.

EVERY ARM NOW RUNS AGAINST THE REAL COMMITTED RECORDS (#436). Until the
constraint bench existed, arms 1, 2 and 4 had to bind a SYNTHESISED record
carrying a key no committed record had -- which made the satisfiability arm a
statement about a file this probe had just written, not about the repository.
fpga/reports/arty/xdc-binding/binding.json is now real evidence, so arm 1 is
the real gate on the real tree.

Arm 4 is the false-positive control: a moved RTL source must go red WITHOUT
naming the constraint file, or "red when the XDC moves" is just "red".

Arms 5-8 are the new machinery's own controls. Publication scope is only worth
anything if the record behind it has to BE evidence: an injected run, a run
against other constraint bytes and an edited transcript must each REFUSE, and
a file no bound record answers for must REFUSE rather than pass unasked.

Arm 9 is the start-red, on a defect that shipped: the pre-#315 constraint file
that R0 and R1 were routed against must fail the constraint bench by name.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
XDC_REL = "fpga/boards/arty-a7-100.xdc"
R0_PUBLICATION = ROOT / "fpga/reports/arty/integrated-baseline-2025.1/publication.json"
PRE_315 = "383f10b^"

_spec = importlib.util.spec_from_file_location(
    "check_arty_evidence_binding", ROOT / "tools/check_arty_evidence_binding.py")
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

sys.path.insert(0, str(ROOT / "fpga"))
import build_arty as build  # noqa: E402
import publish_arty  # noqa: E402
import verify_xdc_binding as vxb  # noqa: E402

_REAL_SHA = build.sha


def _capture(argv, entry=None):
    """(exit code, stdout) of one gate run."""
    import io
    import contextlib
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = (entry or gate.main)(argv)
    return code, buffer.getvalue()


def _revert(rel):
    """Present the whole process -- this probe AND the gate, which hashes
    through build_arty.sha -- with `rel` reverted to the bytes R0 published."""
    published = json.loads(R0_PUBLICATION.read_text())["source_sha256"][rel]
    target = (ROOT / rel).resolve()

    def sha(path):
        return published if Path(path).resolve() == target else _REAL_SHA(path)

    build.sha = sha


REAL_RECORD = Path(vxb.CONSTRAINT_BY_WRAPPER[vxb.WRAPPER])


def _mutated_record(directory, **changes):
    """A copy of the REAL constraint record with one field changed, bound in
    place of it. Always derived from REAL_RECORD, never from whatever the
    previous arm bound -- an arm built on the previous arm's mutant refuses
    for the previous arm's reason and looks like a working control."""
    source = REAL_RECORD
    record = json.loads(source.read_text())
    record.update(changes)
    path = Path(directory) / vxb.RECORD
    path.write_text(json.dumps(record, indent=2) + "\n")
    shutil.copyfile(source.with_name(vxb.TRANSCRIPT),
                    path.with_name(vxb.TRANSCRIPT))
    return path


def arm(label, argv, want, want_names=(), want_absent=(), entry=None):
    """Run one arm and report. `want` is the required exit code; naming it
    here rather than deriving it from the run is what makes this a control."""
    code, out = _capture(argv, entry)
    verdict = {0: "GREEN", 1: "RED-STALE", 2: "RED-REFUSED"}.get(code, f"?{code}")
    problems = []
    if code != want:
        problems.append(f"exit {code}, wanted {want}")
    problems += [f"does not name {n}" for n in want_names if n not in out]
    problems += [f"wrongly names {n}" for n in want_absent if n in out]
    status = "ok" if not problems else "DEFECT: " + "; ".join(problems)
    print(f"  {label:<58s} {verdict}({code})  {status}")
    return not problems


def start_red_arm():
    """The pre-#315 constraint file, read from this repository's own history,
    must fail the constraint bench by name. A `git show` that cannot run is
    NO VERDICT, which is red -- never a silent skip."""
    result = subprocess.run(["git", "-C", str(ROOT), "show", f"{PRE_315}:{XDC_REL}"],
                            capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  {'9. start red: the pre-#315 XDC R0/R1 shipped':<58s} "
              f"NO-VERDICT  DEFECT: git show {PRE_315}:{XDC_REL} failed: "
              f"{result.stderr.strip()[:120]}")
        return False
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".xdc", delete=False) as handle:
        handle.write(result.stdout)
        path = handle.name
    return arm("9. start red: the pre-#315 XDC R0/R1 shipped",
               ["--xdc", path], 1,
               want_names=["hier_separators", "generate block", "#315"],
               entry=vxb.main)


def main():
    import tempfile
    ok = True
    saved_verification = dict(publish_arty.VERIFICATION_BY_WRAPPER)
    saved_constraint = dict(vxb.CONSTRAINT_BY_WRAPPER)
    try:
        print("the real tree, the real committed records:")
        ok &= arm("0. default scope (the Makefile rung)", [], 0,
                  want_names=["BOUND", XDC_REL, gate.NOT_COVERED])
        ok &= arm("1. satisfiability: publication scope is answerable",
                  ["--scope", "publication"], 0,
                  want_names=["BOUND", XDC_REL, "xdc-binding"],
                  want_absent=[gate.DIFFERS, gate.NOT_COVERED])

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

        print("\nthe constraint record itself is not usable evidence:")
        with tempfile.TemporaryDirectory() as tmp:
            for index, (label, changes, names) in enumerate((
                    ("an INJECTED run", {"inject": "UART_SLASH_JOIN"},
                     ["INJECTED", "UART_SLASH_JOIN"]),
                    ("a run against other constraint bytes",
                     {"xdc_override": "/tmp/pre315.xdc"}, ["/tmp/pre315.xdc"]),
                    ("a PASS that records a failed property",
                     {"properties": dict(json.loads(REAL_RECORD.read_text())
                                         ["properties"],
                                         hier_separators=["edited in by hand"])},
                     ["property with problems"])), start=5):
                directory = Path(tmp) / f"arm{index}"
                directory.mkdir()
                vxb.CONSTRAINT_BY_WRAPPER[vxb.WRAPPER] = _mutated_record(
                    directory, **changes)
                ok &= arm(f"{index}. {label}", ["--scope", "publication"], 2,
                          want_names=["REFUSED"] + names)
            vxb.CONSTRAINT_BY_WRAPPER.clear()
            vxb.CONSTRAINT_BY_WRAPPER.update(saved_constraint)

        print("\na bench that stopped reading the ROMs leaves them unasked:")
        real_evidence = gate.bound_evidence

        def narrowed(scope=gate.VERIFICATION_SCOPE):
            # exactly what a bench narrowing its own read set looks like from
            # here: the digital record stops answering for the ROM files, and
            # nothing else answers for them either
            return [item if item.validate is not None else
                    item._replace(files=[f for f in item.files
                                         if f.suffix != ".hex"])
                    for item in real_evidence(scope)]

        gate.bound_evidence = narrowed
        try:
            ok &= arm("8. unasked is REFUSED, not passed over",
                      ["--scope", "publication"], 2,
                      want_names=["REFUSED", "no bound record answers", ".hex"])
        finally:
            gate.bound_evidence = real_evidence

        print("\nthe constraint bench's own start-red:")
        ok &= start_red_arm()
    finally:
        build.sha = _REAL_SHA
        publish_arty.VERIFICATION_BY_WRAPPER.clear()
        publish_arty.VERIFICATION_BY_WRAPPER.update(saved_verification)
        vxb.CONSTRAINT_BY_WRAPPER.clear()
        vxb.CONSTRAINT_BY_WRAPPER.update(saved_constraint)

    print("\nALL ARMS AS EXPECTED" if ok else "\nAN ARM DID NOT BEHAVE AS NAMED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
