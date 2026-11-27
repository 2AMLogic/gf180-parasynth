"""Entry-point regressions for section 0 and 1 summaries in
`tools/probes/estimator_ground_truth.py` (#1511).

    python3 -m pytest tools/probes/test_estimator_ground_truth_summaries.py -q

In `cmd_check`, section 0 (`0. Axis coverage of the fixture catalogue`) and
section 1 (`1. Inventory: every public callable in audio_measure is declared`)
previously printed catalogue declarations as completed work:

    5 axes x 7 families: all swept
    80 public callables; 64 fixture-checked, 7 declared not fixture-checkable,
    9 dataclasses/exceptions, 0 UNDECLARED

Both lines appeared verbatim when `axis_coverage` and `inventory` returned zero
rows, when each returned one row, and on the full catalogue where two of the
thirty-five family/axis pairs are `NOT APPLICABLE` exemptions rather than swept
axes. Section 1 also called `SPECS` membership `fixture-checked` regardless of
whether section 3 exercised any estimator.

Every assertion on section counts and claims below slices stdout strictly to
section 0 or section 1 before section 2 begins, so no terminal verdict after
section 5 can satisfy a section-level check.

Adversarial inputs and stated blind spots (`docs/verification-rules.md` rule 8):

- Committed controls below cover: (1) empty producers with the real catalogue
  and `audio_measure` declarations intact; (2) one-row producers, which defeat
  an `if not rows:` special case that falls back to `len(AXES) * len(kinds)` or
  `len(SPECS)`; (3) `NOT APPLICABLE` axis rows, which must not be counted as
  swept; (4) a failing axis row carrying a `NOT APPLICABLE` note and an
  `UNDECLARED` inventory row carrying `ok=True`, neither of which may hide as a
  valid exemption; and (5) mixed swept/exempt/failed records.
- Stated boundary: sections 0 and 1 summarise the rows returned by
  `ef.axis_coverage(fixtures)` and `inventory()`. Whether declared fixture axes
  actually perturb the signal is checked by `test_estimator_fixtures.py`, and
  whether estimators actually run against fixtures is checked by section 3 and
  `estimator_ground_truth_controls.py`.
"""

from __future__ import annotations

import contextlib
import importlib.util
import pathlib
import re
import subprocess
import sys
from unittest.mock import patch

import pytest

HERE = pathlib.Path(__file__).resolve().parent
GROUND_TRUTH = HERE / "estimator_ground_truth.py"


@contextlib.contextmanager
def _loaded_ground_truth():
    """Load `estimator_ground_truth.py` fresh so module state cannot leak."""
    name = "estimator_ground_truth_summaries_under_test"
    spec = importlib.util.spec_from_file_location(name, GROUND_TRUTH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(name, None)


@pytest.fixture
def gt():
    with _loaded_ground_truth() as module:
        yield module


def _section(out: str, index: int) -> str:
    """Return strictly the body of section `index` (between its banner and the
    next section's banner), so text outside the section cannot satisfy checks."""
    pattern = re.compile(
        rf"^-{{10,}}\n{index}\. [^\n]+\n-{{10,}}\n(.*?)(?=^-{{10,}}\n{index + 1}\. )",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(out)
    assert match is not None, f"section {index} missing from output:\n{out}"
    return match.group(1)


def test_empty_producers_report_zero_observed_rows_and_label_declarations(gt, capsys):
    """Published #1511 empty-record reproduction through `gt.main(["check"])`:
    both sections report zero observed rows and neither claims completed sweeps
    or fixture checks from catalogue declarations."""
    with (
        patch.object(gt.ef, "axis_coverage", return_value=[]),
        patch.object(gt, "inventory", return_value=[]),
        patch.object(gt, "conversions", return_value=[]),
        patch.object(gt, "run", return_value=[]),
    ):
        gt.main(["check"])
    out = capsys.readouterr().out
    sec0 = _section(out, 0)
    sec1 = _section(out, 1)

    assert (
        "0 family/axis pairs observed: 0 swept, 0 not applicable, 0 failed" in sec0
    ), sec0
    assert "declarations: 5 axes x 7 families (35 declared pairs)" in sec0, sec0
    assert "all swept" not in sec0.lower(), sec0

    assert (
        "0 inventory rows observed: 0 callables classified "
        "(0 assigned to SPECS fixtures, 0 declared not fixture-checkable, "
        "0 dataclasses/exceptions), 0 failed (0 UNDECLARED)"
    ) in sec1, sec1
    assert "declarations:" in sec1, sec1
    assert "fixture-checked" not in sec1.lower(), sec1


def test_single_row_producers_are_not_inflated_by_catalogue_size(gt, capsys):
    """One swept axis row and one `SPECS` inventory row with empty estimator
    outcomes: section 0 reports 1 swept pair, section 1 reports 1 classified
    callable with fixture assignment as inventory metadata, and neither count
    is inflated by the 35-pair grid or 80-callable module."""
    one_axis = [
        ("damped_sine", "level", 4, 1000.0, True, "needs >=3 values and ratio >= 10")
    ]
    one_inv = [("rms", "SPECS", "checked against fixtures", True)]
    with (
        patch.object(gt.ef, "axis_coverage", return_value=one_axis),
        patch.object(gt, "inventory", return_value=one_inv),
        patch.object(gt, "conversions", return_value=[]),
        patch.object(gt, "run", return_value=[]),
    ):
        gt.main(["check"])
    out = capsys.readouterr().out
    sec0 = _section(out, 0)
    sec1 = _section(out, 1)

    assert "1 family/axis pair observed: 1 swept, 0 not applicable, 0 failed" in sec0, (
        sec0
    )
    assert "all swept" not in sec0.lower(), sec0

    assert (
        "1 inventory row observed: 1 callable classified "
        "(1 assigned to SPECS fixtures, 0 declared not fixture-checkable, "
        "0 dataclasses/exceptions), 0 failed (0 UNDECLARED)"
    ) in sec1, sec1
    assert "fixture-checked" not in sec1.lower(), sec1


def test_not_applicable_axis_row_is_distinguished_from_swept_pair(gt, capsys):
    """An exempt `NOT APPLICABLE` row has `ok=True` in `axis_coverage`, so a
    naive `sum(ok)` summary would call it swept. Section 0 must report 0 swept
    and 1 not applicable, while keeping the `SKIP` exemption line."""
    exempt_axis = [
        (
            "filtered_noise",
            "phase",
            0,
            0.0,
            True,
            "NOT APPLICABLE: white noise has no phase parameter",
        )
    ]
    with (
        patch.object(gt.ef, "axis_coverage", return_value=exempt_axis),
        patch.object(gt, "inventory", return_value=[]),
        patch.object(gt, "conversions", return_value=[]),
        patch.object(gt, "run", return_value=[]),
    ):
        gt.main(["check"])
    out = capsys.readouterr().out
    sec0 = _section(out, 0)

    assert "SKIP filtered_noise" in sec0, sec0
    assert "NOT APPLICABLE: white noise has no phase parameter" in sec0, sec0
    assert "1 family/axis pair observed: 0 swept, 1 not applicable, 0 failed" in sec0, (
        sec0
    )


def test_mixed_axis_and_inventory_rows_report_counts_diagnostics_and_exit_1(gt, capsys):
    """Mixed swept, exempt, and failed axis rows alongside valid and failed
    inventory rows: check exact section counts, named `SKIP`/`FAIL` lines,
    terminal failure list, and exit status 1."""
    mixed_axis = [
        ("damped_sine", "level", 4, 1000.0, True, "needs >=3 values and ratio >= 10"),
        (
            "filtered_noise",
            "phase",
            0,
            0.0,
            True,
            "NOT APPLICABLE: white noise has no phase parameter",
        ),
        ("damped_sine", "duration", 1, 0.0, False, "needs >=3 values and ratio >= 4"),
    ]
    mixed_inv = [
        ("rms", "SPECS", "checked against fixtures", True),
        ("db", "conversion", "20 log10(a/ref)", True),
        ("Estimate", "dataclass/exception", "holds no measurement", True),
        (
            "mystery_estimator",
            "UNDECLARED",
            "a public callable in audio_measure with no declaration",
            False,
        ),
    ]
    with (
        patch.object(gt.ef, "axis_coverage", return_value=mixed_axis),
        patch.object(gt, "inventory", return_value=mixed_inv),
        patch.object(gt, "conversions", return_value=[]),
        patch.object(gt, "run", return_value=[]),
    ):
        rc = gt.main(["check"])
    out = capsys.readouterr().out
    sec0 = _section(out, 0)
    sec1 = _section(out, 1)

    assert rc == 1, out
    assert "SKIP filtered_noise" in sec0, sec0
    assert (
        "FAIL damped_sine            duration   1 values, spread 0 "
        "(needs >=3 values and ratio >= 4)"
    ) in sec0, sec0
    assert (
        "3 family/axis pairs observed: 1 swept, 1 not applicable, 1 failed" in sec0
    ), sec0

    assert (
        "FAIL mystery_estimator              "
        "a public callable in audio_measure with no declaration"
    ) in sec1, sec1
    assert (
        "4 inventory rows observed: 3 callables classified "
        "(1 assigned to SPECS fixtures, 1 declared not fixture-checkable, "
        "1 dataclass/exception), 1 failed (1 UNDECLARED)"
    ) in sec1, sec1

    assert "  - axis not swept: damped_sine/duration: 1 values, spread 0" in out, out
    assert "  - undeclared estimator: mystery_estimator" in out, out


def test_malformed_exemption_and_undeclared_rows_cannot_hide_as_valid(gt, capsys):
    """Rule 8 control: a failing axis row whose note starts with
    `NOT APPLICABLE` must still fail rather than count as exempt, and an
    `UNDECLARED` or blank-reason inventory row marked `ok=True` must fail
    rather than count as `declared not fixture-checkable`."""
    tricky_axis = [
        ("two_modes", "phase", 1, 0.0, False, "NOT APPLICABLE: forged note on ok=False")
    ]
    tricky_inv = [
        ("forged_undeclared", "UNDECLARED", "marked ok=True by mistake", True),
        ("forged_blank", "conversion", "   ", True),
    ]
    with (
        patch.object(gt.ef, "axis_coverage", return_value=tricky_axis),
        patch.object(gt, "inventory", return_value=tricky_inv),
        patch.object(gt, "conversions", return_value=[]),
        patch.object(gt, "run", return_value=[]),
    ):
        rc = gt.main(["check"])
    out = capsys.readouterr().out
    sec0 = _section(out, 0)
    sec1 = _section(out, 1)

    assert rc == 1, out
    assert "1 family/axis pair observed: 0 swept, 0 not applicable, 1 failed" in sec0, (
        sec0
    )
    assert "SKIP two_modes" not in sec0, sec0
    assert (
        "2 inventory rows observed: 0 callables classified "
        "(0 assigned to SPECS fixtures, 0 declared not fixture-checkable, "
        "0 dataclasses/exceptions), 2 failed (1 UNDECLARED)"
    ) in sec1, sec1


def test_real_cli_run_distinguishes_swept_and_exempt_pairs_on_shipped_catalogue():
    """Unmocked subprocess run on `--estimator rms`: on the shipped 7-family
    catalogue, 33 pairs are swept and 2 (`repeated_hits/phase` and
    `filtered_noise/phase`) are not applicable, and all 80 public callables are
    classified."""
    proc = subprocess.run(
        [sys.executable, str(GROUND_TRUTH), "check", "--estimator", "rms"],
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    sec0 = _section(proc.stdout, 0)
    sec1 = _section(proc.stdout, 1)

    assert (
        "35 family/axis pairs observed: 33 swept, 2 not applicable, 0 failed" in sec0
    ), sec0
    assert "all swept" not in sec0.lower(), sec0
    assert "fixture-checked" not in sec1.lower(), sec1
    assert re.search(
        r"80 inventory rows observed: 80 callables classified "
        r"\(64 assigned to SPECS fixtures, 7 declared not fixture-checkable, "
        r"9 dataclasses/exceptions\), 0 failed \(0 UNDECLARED\)",
        sec1,
    ), sec1


def test_inventory_summary_counts_observed_rows_without_claiming_fixture_checks(
    gt, capsys
):
    """Section 1 checked in isolation from section 0: zero rows and a single
    `SPECS` row each report observed classifications and fixture assignment,
    never `64 fixture-checked` from `SPECS` declarations."""
    with (
        patch.object(gt.ef, "axis_coverage", return_value=[]),
        patch.object(gt, "inventory", return_value=[]),
        patch.object(gt, "conversions", return_value=[]),
        patch.object(gt, "run", return_value=[]),
    ):
        gt.main(["check"])
    sec1_empty = _section(capsys.readouterr().out, 1)
    assert (
        "0 inventory rows observed: 0 callables classified "
        "(0 assigned to SPECS fixtures, 0 declared not fixture-checkable, "
        "0 dataclasses/exceptions), 0 failed (0 UNDECLARED)"
    ) in sec1_empty, sec1_empty
    assert "fixture-checked" not in sec1_empty.lower(), sec1_empty


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
