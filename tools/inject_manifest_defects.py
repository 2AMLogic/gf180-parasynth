#!/usr/bin/env python3
"""Permanent injected-defect controls for `tools/manifest.py`.

Every defect listed here was **shipped** in the first version of the
render/analyse/accept scheme and found in review (PR #260). A bug is not closed
until it is an injection (`docs/verification-rules.md` rule 5), so each one is
reintroduced here and the test that is supposed to catch it must turn red. A
gate that stays green under its own defect is not a gate -- and these eight are
all measurement-apparatus defects, the class this repository keeps re-shipping:
an identity hash that collided, a retained artefact overwritten in place, a
ledger that reported changes that had not happened, a render stage that
accepted silence, NaN and hard clipping without a word.

Three outcomes, and the third is the point:

    exit 0   every control fired (its test went red under the injection)
    exit 1   a control did NOT fire -- that test cannot detect its own defect
    exit 2   REFUSED: an injection did not apply at all (the code moved), so
             nothing was measured. A control that did not inject is worse than
             no control, because its output looks exactly like a pass.

Runs against a COPY of the tree in a temp dir, never the live worktree: this is
one job in `make controls`, which runs jobs in parallel, and a control that
edits `tools/manifest.py` in place could turn a concurrent job red.

    python3 tools/inject_manifest_defects.py [-v]
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
TESTS = "tools/test_manifest.py"

# (label, file relative to the tree, exact text to replace, replacement,
#  the test(s) that must go red)
INJECTIONS = [
    ("hash identity: default=str (elides array middles; repr carries an address)",
     "tools/manifest.py",
     '    return hashlib.sha256(\n'
     '        json.dumps(_canonical(obj), sort_keys=True).encode()).hexdigest()[:12]',
     '    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str)'
     '.encode()).hexdigest()[:12]',
     [f"{TESTS}::test_hash_does_not_collide_on_arrays_that_differ_only_in_the_middle",
      f"{TESTS}::test_hash_refuses_a_config_value_with_no_reproducible_identity"]),

    ("render() overwrites an existing runs/<render_id>/ silently",
     "tools/manifest.py",
     "        if prior_path.exists():", "        if False and prior_path.exists():",
     [f"{TESTS}::test_render_refuses_to_overwrite_a_retained_wav_with_different_audio"]),

    ("bound ledger keyed by metric name alone (fabricates bound changes)",
     "tools/manifest.py",
     "        prev = history.get(case_id, {}).get(name)",
     "        prev = history.get(name)",
     [f"{TESTS}::test_the_bound_ledger_is_keyed_by_case_and_metric_not_by_metric_alone"]),

    ("WAV quantisation truncates instead of rounding (half-LSB bias)",
     "tools/manifest.py",
     '    y = np.rint(np.clip(scaled, -32768, 32767)).astype("<i2")',
     '    y = np.clip(scaled, -32768, 32767).astype("<i2")',
     [f"{TESTS}::test_wav_quantisation_rounds_rather_than_truncating"]),

    ("render() accepts non-finite samples",
     "tools/manifest.py",
     "    if n_bad:", "    if False and n_bad:",
     [f"{TESTS}::test_render_refuses_non_finite_samples"]),

    ("render() accepts exact silence",
     "tools/manifest.py",
     "    if requested_peak == 0.0 and not allow_silence:",
     "    if False and requested_peak == 0.0 and not allow_silence:",
     [f"{TESTS}::test_render_refuses_exact_silence_unless_the_call_says_it_is_intended"]),

    ("render() hard-clips silently",
     "tools/manifest.py",
     '        if wav_stats["clipped_samples"] and not allow_clipping:',
     '        if False and wav_stats["clipped_samples"] and not allow_clipping:',
     [f"{TESTS}::test_render_refuses_a_clipped_render_and_records_the_peak_when_allowed"]),

    ("the suite is dropped from the one make target CI invokes",
     "Makefile",
     "tools/test_run_all.py tools/test_manifest.py", "tools/test_run_all.py",
     [f"{TESTS}::test_this_suite_is_enumerated_in_a_target_a_ci_job_actually_runs"]),
]


def _stage(dest: pathlib.Path) -> None:
    """The subset of the tree these tests read: the module, its suite, and the
    two files the CI-wiring test reads."""
    shutil.copytree(ROOT / "tools", dest / "tools",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(ROOT / "Makefile", dest / "Makefile")
    (dest / ".github/workflows").mkdir(parents=True)
    shutil.copy2(ROOT / ".github/workflows/rungs.yml", dest / ".github/workflows/rungs.yml")


def main(argv: list[str]) -> int:
    verbose = "-v" in argv
    rows = []
    with tempfile.TemporaryDirectory(prefix="manifest-controls-") as tmp:
        tree = pathlib.Path(tmp) / "tree"
        _stage(tree)
        for label, rel, old, new, tests in INJECTIONS:
            path = tree / rel
            clean = path.read_text()
            if old not in clean:
                print(f"REFUSED: injection target for {label!r} is no longer present "
                      f"in {rel} -- the code moved, so nothing was measured. Update "
                      f"this control (or delete it, deliberately), do not ignore it.")
                return 2
            try:
                path.write_text(clean.replace(old, new, 1))
                r = subprocess.run([sys.executable, "-m", "pytest", *tests, "-q"],
                                   cwd=tree, capture_output=True, text=True)
                tail = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "(no output)"
                if verbose:
                    print(r.stdout)
                rows.append((label, r.returncode != 0, tail))
            finally:
                path.write_text(clean)

    width = max(len(r[0]) for r in rows)
    fired = 0
    for label, red, tail in rows:
        fired += red
        print(f"{'RED (caught)' if red else 'GREEN (MISSED)':<15} {label:<{width}}  {tail}")
    print(f"\n{fired}/{len(rows)} controls fired")
    return 0 if fired == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
