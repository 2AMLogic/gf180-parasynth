#!/usr/bin/env python3
"""Mutation controls for the #1910 non-finite refusal controls.

    python3 tools/balance_line_shape_refusal_mutants.py     # needs pytest + numpy + scipy

`tools/test_balance_line_shape_refusal.py` records that the #1782 census row
for `tools/probes/balance_line_shape.py` is UNREACHABLE as an exit-1
traceback: a NaN or Inf leaving `run_case.load_reference` or `run_case.prepare`
is CONVERTED there to `run_case.Refused` (a `refusal.Refused`), which the
probe's dispatch handler catches. Those tests are green on the unmodified
tree, so on their own they cannot show they would notice the conversion
being removed -- which is the one thing they are for. Each mutant here
removes or widens ONE of those conversions (or narrows or widens the probe's
handler) in a scratch copy of the tree, never this checkout, and must turn
the controls RED at a RECORDED assertion. The exit status is 0 only when
all eight do.

The verdict is not decided here: every focused run goes through
`tools/probes/holdout_injected_controls.py` (`run_baseline`, `run_suite`,
`verdict`), as `bd_pitch_predeclaration_mutants.py` and the sound and
FPGA-record probes' runs do. `EXPECTED` records the test and the assertion
that caught each mutant on the real suite. A run that exits 1 for any other
reason -- a crash inside a test, an unrelated assertion, a lost report --
is REFUSED, not counted red (#1463).

Exit status, the shared probes' aggregate convention: 1 when any mutant
SURVIVED (`no-fire`: the suite stayed green with the conversion gone -- a
hole in the controls), else 2 when any REFUSED (nothing was measured; the
reason is on its line), else 0.

Inputs that defeat it (docs/verification-rules.md rule 8), REFUSED (exit 2)
before any mutant verdict line is printed:

  1. a mutant whose `old` text is not found EXACTLY once in its file -- the
     conversion moved and this control cannot fire; repair it, never skip it
  2. a baseline that is not green and complete on the UNMUTATED copy -- no
     red can mean anything
  3. a `verdict` of REFUSED: see holdout_injected_controls for the cases

NOT WIRED into `make controls`. It is the instrument behind the mutation
table in the test file's docstring and is run on demand; wiring it beside
`bd_pitch_predeclaration_mutants.py` is a follow-up, because that script's
history (#755) is that an unwired mutant script rots silently.
"""
from __future__ import annotations

import pathlib
import shutil
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "probes"))
import holdout_injected_controls as hic  # noqa: E402

SUITE = "tools/test_balance_line_shape_refusal.py"
RC = "tools/run_case.py"
BLS = "tools/probes/balance_line_shape.py"

_LOAD_TRY = ('    try:\n'
             '        am.require_finite(x, f"reference recording {rel}")\n')
_LOAD_CALL = '    am.require_finite(x, f"reference recording {rel}")\n'
_PREP_TRY = ('    try:\n'
             '        am.require_finite(x, side)\n')
_PREP_CALL = '    am.require_finite(x, side)\n'
_CONVERT = ('    except am.NonFiniteAudio as why:\n'
            '        raise Refused(str(why)) from why\n')
_WIDE = _CONVERT.replace("am.NonFiniteAudio", "AssertionError")
_HANDLER = '    except refusal.Refused as e:\n'

T = "test_records_nonfinite_reference_exits_refused_2_no_verdict"
P = "test_prepare_site_nonfinite_render_exits_refused_2_through_dispatch"
A = "test_plain_assertion_error_inside_the_converted_region_still_escapes"
S = "test_records_silent_reference_exits_refused_2_no_verdict"
U = "test_unreadable_reference_is_an_execution_error_not_a_refusal"

_EXIT2 = hic.Site("assert p.returncode == exit_contract.REFUSED", "AssertionError")
_MESSAGE_PIN = hic.Site('assert (f"{FIRST_REL} holds 1 non-finite samples of {FRAMES} "',
                        "AssertionError")
_ESCAPED_NONE = hic.Site("assert escaped is None", "AssertionError")
_IS_ASSERTION = hic.Site("assert isinstance(escaped, AssertionError)", "AssertionError")
_EXIT1 = hic.Site("assert p.returncode == 1", "AssertionError")

# name: (file, old, new)
MUTANTS = {
    "load_reference-conversion-removed":       (RC, _LOAD_TRY + _CONVERT, _LOAD_CALL),
    "load_reference-finiteness-assert-removed": (RC, _LOAD_TRY + _CONVERT, ""),
    "load_reference-conversion-widened":       (RC, _LOAD_TRY + _CONVERT, _LOAD_TRY + _WIDE),
    "prepare-conversion-removed":              (RC, _PREP_TRY + _CONVERT, _PREP_CALL),
    "prepare-conversion-widened":              (RC, _PREP_TRY + _CONVERT, _PREP_TRY + _WIDE),
    "handler-narrowed-to-local-Refused":       (BLS, _HANDLER, '    except Refused as e:\n'),
    "handler-widened-to-AssertionError":       (BLS, _HANDLER, '    except AssertionError as e:\n'),
    "handler-widened-to-Exception":            (BLS, _HANDLER, '    except Exception as e:\n'),
}

# name: {test: the assertion that must fail}
EXPECTED = {
    "load_reference-conversion-removed": {f"{T}[{i}]": _EXIT2 for i in ("nan", "pos-inf", "neg-inf")},
    # With the assert gone the WIDTH guard refuses the same float32 file, so
    # the three NaN/Inf controls still see exit 2 and go red on their message
    # pin instead -- which is why that pin exists.
    "load_reference-finiteness-assert-removed": {
        **{f"{T}[{i}]": _MESSAGE_PIN for i in ("nan", "pos-inf", "neg-inf")},
        f"{A}[load_reference]": _IS_ASSERTION,
    },
    "load_reference-conversion-widened": {f"{A}[load_reference]": _IS_ASSERTION},
    "prepare-conversion-removed": {f"{P}[{i}]": _ESCAPED_NONE for i in ("nan", "inf")},
    "prepare-conversion-widened": {f"{A}[prepare]": _IS_ASSERTION},
    "handler-narrowed-to-local-Refused": {
        S: _EXIT2,
        **{f"{T}[{i}]": _EXIT2 for i in ("nan", "pos-inf", "neg-inf")},
        **{f"{P}[{i}]": _ESCAPED_NONE for i in ("nan", "inf")},
    },
    "handler-widened-to-AssertionError": {f"{A}[{i}]": _IS_ASSERTION for i in ("load_reference", "prepare")},
    "handler-widened-to-Exception": {
        U: _EXIT1,
        **{f"{A}[{i}]": _IS_ASSERTION for i in ("load_reference", "prepare")},
    },
}

RANK = {hic.FIRED: 0, hic.NO_FIRE: 1, hic.REFUSED: 2}


def report(results: dict[str, hic.Result]) -> int:
    for name, r in results.items():
        line = f"{r.outcome:9s} {name}: {r.reason}"
        if r.failed:
            line += f"  [{'; '.join(r.failed)}]"
        print(line)
        if r.tail:
            print(r.tail.rstrip())
    worst = max(RANK[r.outcome] for r in results.values())
    if worst == 0:
        print(f"all {len(results)} mutants red at the recorded assertion")
    return worst


def main() -> int:
    files = sorted({rel for rel, _, _ in MUTANTS.values()})
    with tempfile.TemporaryDirectory(prefix="bls-mutants-") as tmp:
        dst = pathlib.Path(tmp) / "tree"
        shutil.copytree(ROOT, dst, symlinks=True, ignore=shutil.ignore_patterns(
            ".git", ".loom", "build", ".venv", "__pycache__", ".pytest_cache", "node_modules"))
        originals = {rel: (dst / rel).read_text() for rel in files}
        # Precondition 1: every mutant still aims at the code it names.
        for name, (rel, old, _) in MUTANTS.items():
            n = originals[rel].count(old)
            if n != 1:
                print(f"REFUSED: mutant {name}: target text found {n} times in {rel}")
                return 2
        # Precondition 2: the controls are green on the UNMUTATED copy.
        base = hic.run_baseline(dst, SUITE)
        if base.report is None:
            print(f"REFUSED: baseline -- {base.reason}")
            print(base.tail.rstrip())
            return 2
        print(f"baseline: {len(base.report.passed)} test(s) green on the UNMUTATED tree")
        results = {}
        for name, (rel, old, new) in MUTANTS.items():
            for r, src in originals.items():              # restore every file
                (dst / r).write_text(src)
            (dst / rel).write_text(originals[rel].replace(old, new))
            rc, run, _stdout = hic.run_suite(dst, SUITE)
            results[name] = hic.verdict(rc, run, EXPECTED.get(name, {}),
                                         suite=SUITE, baseline=base.report)
        return report(results)


if __name__ == "__main__":
    sys.exit(main())
