#!/usr/bin/env python3
"""Permanent injected-defect controls for `tools/manifest.py`.

Every defect listed here was **shipped** in the first version of the
render/analyse/accept scheme and found in review (PR #260). A bug is not closed
until it is an injection (`docs/verification-rules.md` rule 5), so each one is
reintroduced here and the test that is supposed to catch it must turn red. A
gate that stays green under its own defect is not a gate -- and every one of
them is a measurement-apparatus defect, the class this repository keeps
re-shipping: an identity hash that collided, a retained artefact overwritten in
place, a ledger that reported changes that had not happened, a render stage that
accepted silence, NaN and hard clipping without a word, and an id that silently
nested a directory.

Three outcomes, and the third is the point:

    exit 0   every control fired (its test went red under the injection)
    exit 1   a control did NOT fire -- that test cannot detect its own defect
    exit 2   REFUSED: nothing was measured, either because an injection did not
             apply at all (the code moved) or because the interpreter did not
             actually load it. A control that did not inject is worse than no
             control, because its output looks exactly like a pass.

Runs against a COPY of the tree in a temp dir, never the live worktree: this is
one job in `make controls`, which runs jobs in parallel, and a control that
edits `tools/manifest.py` in place could turn a concurrent job red.

    python3 tools/inject_manifest_defects.py [-v]

------------------------------------------------------------------------------
THE CONTROL'S OWN SHIPPED DEFECT, and why the three mechanisms below exist
------------------------------------------------------------------------------
The first version of this file checked that the injected text was on DISK
(`old in clean`) and never that the interpreter had COMPILED it -- the
precondition asserted one level away from the point of use. Four of the
injections add exactly the ten characters `"False and "`, so they produce a
`tools/manifest.py` of *identical size* (32576 bytes at the time); CPython's pyc
invalidation key is `(source mtime truncated to whole seconds, source size)`;
and the pytest subprocesses run sequentially in ONE staged tree. Two
same-size injections landing in the same wall-clock second therefore ran the
PREVIOUS injection's bytecode, with the guard under test still live -- so the
control printed `GREEN (MISSED)` for a defect it had never actually injected.

It was machine-speed dependent, which is the worst property a control can
have: ~1.2 s per pytest on a laptop usually crosses a second boundary and
reports 8/8, ~0.2 s in CI does not and reported 7/8. "8/8 fired locally" was
not evidence. Three mechanisms, each doing a different job:

1. **Make the interpreter see the injection.** `-B` on the child interpreter
   (never writes a pyc) plus `PYTHONDONTWRITEBYTECODE=1` in its environment
   (which `-B` alone does not pass to grandchildren), and `__pycache__` is
   purged from the staged tree before every run (`-B` stops writing, not
   reading).
2. **Make the collision deterministic instead of hoping for it.** Every
   injected file's mtime is pinned to one fixed whole second, so the
   `(mtime, size)` key is identical across those four same-size injections on
   every machine, fast or slow. If mechanism 1 is ever removed, the masking
   happens on *every* run rather than only on a fast one: a flake becomes a
   permanent, reproducible red.
3. **Assert it at the point of use, and require positive evidence.** The
   staged tree gets a `conftest.py` (`GUARD_CONFTEST`) that runs INSIDE the
   pytest process, imports the module the tests are about to import, and
   writes what it actually loaded to a receipt file. This harness REFUSES if
   the receipt is missing or disagrees -- a guard that can silently not run is
   not a guard.

`tools/test_manifest.py::test_the_injection_harness_refuses_when_the_interpreter_loaded_stale_bytecode`
is the permanent control for all three: it reproduces the collision
adversarially with mechanism 1 switched off and requires this harness to
REFUSE rather than report a verdict.
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
TESTS = "tools/test_manifest.py"

#: Appended to an injected Python source so the guard below can tell WHICH
#: injection the interpreter actually loaded. Every tag is the same length, so
#: injections that collided in size before still collide -- the marker is a
#: detector, not an accidental mitigation of the thing being detected.
MARKER = "_INJECTED_BY_CONTROL"

#: One fixed whole second for every injected file (mechanism 2 above).
PINNED_MTIME = 1_700_000_000

#: Where the in-process guard records what it loaded, relative to the tree.
RECEIPT_NAME = ".injection-receipt"

GUARD_CONFTEST = '''\
"""Control on the control -- staged by `tools/inject_manifest_defects.py` into
its temporary tree ONLY. The repository deliberately has no `tools/conftest.py`;
this file exists for the length of one injection.

`pytest_configure` runs inside the pytest process, before any test module is
imported, so the module it imports here is the same module object the tests
will use. It records what it actually loaded to a receipt the harness requires
to exist, and refuses loudly when the module the interpreter loaded is not the
one just written to disk (a stale `__pycache__` entry: pyc invalidation is
keyed on `(mtime-to-the-second, size)`, and several injections have identical
sizes). Nothing measured is not a pass and not a failure.
"""
import os
import pathlib
import sys

MARKER = "_INJECTED_BY_CONTROL"


def pytest_configure(config):
    tag = os.environ.get("LOOM_INJECTION_TAG")
    modname = os.environ.get("LOOM_INJECTION_MODULE")
    receipt = os.environ.get("LOOM_INJECTION_RECEIPT")
    if not (tag and modname and receipt):
        raise RuntimeError(
            "CONTROL-REFUSED: the injection guard was not configured "
            "(LOOM_INJECTION_TAG / _MODULE / _RECEIPT unset) -- refusing to let "
            "this run report a verdict, because then nothing would have checked "
            "that the injection reached the interpreter")
    if modname == "-":
        # The injection target is not a Python module (the Makefile), so there
        # is no bytecode cache that could go stale. Recorded, not skipped.
        pathlib.Path(receipt).write_text("- -")
        return
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    module = __import__(modname)
    seen = getattr(module, MARKER, "<absent>")
    pathlib.Path(receipt).write_text(modname + " " + str(seen))
    if seen != tag:
        raise RuntimeError(
            "CONTROL-REFUSED: " + modname + " on disk carries injection " + tag
            + " but the module the interpreter LOADED carries " + repr(seen)
            + " -- almost certainly a stale __pycache__ entry. The injected "
            "defect was never in effect, so this run measured nothing.")
'''


class ControlRefused(Exception):
    """Nothing was measured. Distinct from both a pass and a failure."""


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

    ("case_id reaches a path component unchecked (runs/SD/01-...)",
     "tools/manifest.py",
     '    _single_path_component("case_id", case_id)',
     '    pass  # _single_path_component("case_id", case_id)',
     [f"{TESTS}::test_render_refuses_a_case_id_that_is_not_one_path_component"]),

    ("the suite is dropped from the one make target CI invokes",
     "Makefile",
     "tools/test_run_all.py tools/test_manifest.py", "tools/test_run_all.py",
     [f"{TESTS}::test_this_suite_is_enumerated_in_a_target_a_ci_job_actually_runs"]),
]


def tag_for(label: str) -> str:
    """A fixed-length identity for one injection. Fixed length on purpose: see
    `MARKER`."""
    return hashlib.sha256(label.encode()).hexdigest()[:12]


def _stage(dest: pathlib.Path) -> None:
    """The subset of the tree these tests read: the module, its suite, and the
    two files the CI-wiring test reads, plus the in-process guard."""
    shutil.copytree(ROOT / "tools", dest / "tools",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(ROOT / "Makefile", dest / "Makefile")
    (dest / ".github/workflows").mkdir(parents=True)
    shutil.copy2(ROOT / ".github/workflows/rungs.yml", dest / ".github/workflows/rungs.yml")
    assert not (ROOT / "tools/conftest.py").exists(), (
        "the repository has grown a tools/conftest.py -- merge it with "
        "GUARD_CONFTEST rather than letting this overwrite it")
    (dest / "tools/conftest.py").write_text(GUARD_CONFTEST)


def _purge_pycache(tree: pathlib.Path) -> None:
    for cache in tree.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)


def run_one(tree: pathlib.Path, injection, *, verbose: bool = False,
            no_bytecode_cache: bool = True, purge_pycache: bool = True
            ) -> tuple[bool, str]:
    """Apply one injection in `tree`, run its test(s), restore the file.

    Returns `(went_red, last_line_of_output)`. Raises `ControlRefused` when
    nothing was measured -- the injection did not apply, or the interpreter did
    not load it.

    `no_bytecode_cache` / `purge_pycache` are not tuning knobs: they are the
    two halves of mechanism 1 in this module's docstring, and they exist as
    parameters **only** so the adversarial self-test in `tools/test_manifest.py`
    can switch them off and prove this function REFUSES instead of reporting a
    verdict. Nothing in `main()` passes anything but the defaults.
    """
    label, rel, old, new, tests = injection
    path = tree / rel
    clean = path.read_text()
    if old not in clean:
        raise ControlRefused(
            f"injection target for {label!r} is no longer present in {rel} -- the "
            f"code moved, so nothing was measured. Update this control (or delete "
            f"it, deliberately), do not ignore it.")
    tag = tag_for(label)
    is_module = rel.endswith(".py")
    injected = clean.replace(old, new, 1)
    if is_module:
        injected += f'\n{MARKER} = "{tag}"\n'
    receipt = tree / RECEIPT_NAME
    receipt.unlink(missing_ok=True)

    env = dict(os.environ)
    env["LOOM_INJECTION_TAG"] = tag
    env["LOOM_INJECTION_MODULE"] = pathlib.PurePosixPath(rel).stem if is_module else "-"
    env["LOOM_INJECTION_RECEIPT"] = str(receipt)
    argv = [sys.executable]
    if no_bytecode_cache:
        argv.append("-B")
        env["PYTHONDONTWRITEBYTECODE"] = "1"
    else:
        env.pop("PYTHONDONTWRITEBYTECODE", None)

    try:
        path.write_text(injected)
        # Mechanism 2: one fixed second for every injection, so the
        # (mtime, size) pyc key collides deterministically rather than by luck.
        os.utime(path, (PINNED_MTIME, PINNED_MTIME))
        if purge_pycache:
            _purge_pycache(tree)
        r = subprocess.run([*argv, "-m", "pytest", *tests, "-q"],
                           cwd=tree, capture_output=True, text=True, env=env)
        if verbose:
            print(r.stdout)
            if r.stderr.strip():
                print(r.stderr)
        # Mechanism 3, read BEFORE the red/green verdict: a verdict from a run
        # that did not load the injection is not a verdict.
        expected_receipt = f"{env['LOOM_INJECTION_MODULE']} {tag if is_module else '-'}"
        if not receipt.exists():
            raise ControlRefused(
                f"the in-process guard left no receipt for {label!r} -- it did "
                f"not run, so nothing checked that the interpreter loaded the "
                f"injection. pytest said: {r.stdout.strip().splitlines()[-1:] or r.stderr.strip()[-400:]}")
        seen = receipt.read_text().strip()
        if seen != expected_receipt:
            raise ControlRefused(
                f"the module the interpreter LOADED is not the one this control "
                f"wrote for {label!r}: receipt says {seen!r}, expected "
                f"{expected_receipt!r} -- stale bytecode (pyc invalidation is "
                f"keyed on (mtime-to-the-second, size), and several injections "
                f"have identical sizes). Nothing was measured.")
        tail = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "(no output)"
        return r.returncode != 0, tail
    finally:
        path.write_text(clean)
        os.utime(path, (PINNED_MTIME, PINNED_MTIME))


def main(argv: list[str]) -> int:
    verbose = "-v" in argv
    rows = []
    with tempfile.TemporaryDirectory(prefix="manifest-controls-") as tmp:
        tree = pathlib.Path(tmp) / "tree"
        _stage(tree)
        for injection in INJECTIONS:
            try:
                red, tail = run_one(tree, injection, verbose=verbose)
            except ControlRefused as exc:
                print(f"REFUSED: {exc}")
                return 2
            rows.append((injection[0], red, tail))

    width = max(len(r[0]) for r in rows)
    fired = 0
    for label, red, tail in rows:
        fired += red
        print(f"{'RED (caught)' if red else 'GREEN (MISSED)':<15} {label:<{width}}  {tail}")
    print(f"\n{fired}/{len(rows)} controls fired")
    return 0 if fired == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
