#!/usr/bin/env python3
"""Shared body for the "every acceptance test declares its claim status and
names the estimator's ground truth" gate (issue #222, generalising #45 item 1
beyond `test_808_acceptance.py`).

WHY THIS EXISTS. `test_808_acceptance.py` wrote a meta-test
(`test_meta_every_test_declares_status_and_ground_truth`) that walks its own
module, and requires every `test_*` function to open its docstring with a
claim-status tag and -- unless it is itself a meta-test -- to name the
`test_audio_measure.py` function that backs the estimator it uses. It found
real gaps the day it was written. That check was coupled to one module: `mod`
was hardcoded to `test_808_acceptance`, so nothing enforced the same
discipline anywhere else that calls into `model/audio_measure.py`.

`assert_ground_truth_gate` is the checking logic factored out so a second (or
third, or Nth) acceptance suite can reuse it without copy-pasting the body --
each suite still writes its OWN `test_meta_*` function (so pytest reports a
per-suite failure, not one shared test that blames the wrong file) and that
function's entire job is to call this with its own module.

CLAIM STATUS. Every test's docstring must open with one of:

    [source-verified: ...]   a primary source states it; asserted at the
                             reference's own tolerance
    [source-inferred: ...]   the reference derived it; asserted as a justified
                             provisional range, wider than a verified claim
    [hardware-measured: ...] measured off real hardware and written up
                             somewhere citable; a different claim again from
                             either of the above
    [measured-here: ...]     a property this suite established by measurement,
                             with no source behind the number
    [defect: ...]            a specific tracked implementation defect
    [method] / [meta]        a guard on how something is measured, or on the
                             suite itself

GROUND TRUTH. Every non-meta test must also name, on a line starting
"Ground truth:", the ground-truth test(s) that back the estimator(s) it uses --
`module_name.function_name`, for any module registered in `ground_truth_mods`
-- and each one named must actually exist. "Verified in a source" and
"validated by our own measurement" are different claims, and a suite that
blurs them is how an inference becomes a fact. A test whose function name
starts with `meta_prefix` (default `"test_meta_"`) is exempt from the
ground-truth requirement, but NOT from the claim-status tag: it still has to
say what kind of guard it is.
"""
from __future__ import annotations

import re

CLAIM_STATUS_TAGS = (
    "[source-verified:", "[source-inferred:", "[hardware-measured:",
    "[measured-here:", "[defect:", "[method]", "[meta]",
)


def assert_ground_truth_gate(mod, ground_truth_mods, *, meta_prefix="test_meta_",
                              not_asserted=None, known_defects=None):
    """The shared meta-test body. Call this from a `test_meta_*` function in
    the suite being gated -- never call it directly as a test, since a shared
    test name would blame whichever suite pytest happened to collect it from.

    `mod`                the acceptance-suite module being gated (e.g. the
                         caller's own module, `import ... as mod`)
    `ground_truth_mods`  a module, or a {name: module} mapping of modules whose
                         `test_*` members are valid ground-truth references.
                         A single module is accepted directly and keyed by its
                         own `__name__`. A `Ground truth:` line may name
                         `<name>.<function>` for any `name` present here --
                         typically `test_audio_measure` plus, for a suite that
                         validates its own local estimators the same way
                         `test_audio_measure.py` validates the shared ones,
                         the suite's own module (a self-reference).
    `meta_prefix`        test names starting with this are exempt from the
                         ground-truth requirement (but still need a tag).
    `not_asserted`       optional: this suite's "established by no source, so
                         asserted by nothing here" table. If given (not None),
                         it must be non-empty -- mirrors
                         `test_808_acceptance.py`'s NOT_ASSERTED escape hatch,
                         "the could-not-establish list must stay in this
                         file." Pass None if the suite has no such table.
    `known_defects`      optional: this suite's tracked-defect table (test
                         name -> reason). If given, every key must still name
                         a live test in `mod` -- a strict xfail whose entry
                         silently stops matching anything is a defect nobody
                         is tracking any more.
    """
    if not isinstance(ground_truth_mods, dict):
        ground_truth_mods = {ground_truth_mods.__name__: ground_truth_mods}
    known = {name: {n for n in vars(m) if n.startswith("test_")}
             for name, m in ground_truth_mods.items()}
    mod_alt = "|".join(re.escape(name) for name in known)
    ref_re = re.compile(rf"\b({mod_alt})\.(\w+)") if mod_alt else None

    missing_tag, missing_gt, unknown_gt = [], [], []
    for name, fn in sorted(vars(mod).items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        doc = (fn.__doc__ or "").lstrip()
        if not doc.startswith(CLAIM_STATUS_TAGS):
            missing_tag.append(name)
            continue
        if name.startswith(meta_prefix):
            continue
        m = re.search(r"Ground truth:\s*(.+)$", doc, re.S)
        if not m:
            missing_gt.append(name)
            continue
        # A `Ground truth:` line naming no registered module (e.g. "none
        # needed -- register read-back and arithmetic") is a deliberate,
        # explicit escape hatch, not a missing reference -- the docstring
        # still has to SAY it needs none, which is what the `if not m` branch
        # above catches; a line that says so is left alone here.
        refs = ref_re.findall(m.group(1)) if ref_re else []
        for mod_name, fn_name in refs:
            if fn_name not in known[mod_name]:
                unknown_gt.append(f"{name} -> {mod_name}.{fn_name}")

    assert not missing_tag, f"tests without a claim-status tag: {missing_tag}"
    assert not missing_gt, f"tests that name no estimator ground truth: {missing_gt}"
    assert not unknown_gt, f"ground-truth tests that do not exist: {unknown_gt}"

    if not_asserted is not None:
        assert not_asserted, "the could-not-establish list must stay in this file"
    if known_defects is not None:
        live = {n for n in vars(mod) if n.startswith("test_")}
        stale = [n for n in known_defects if n not in live]
        assert not stale, f"KNOWN_DEFECTS names tests that no longer exist: {stale}"
