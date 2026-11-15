"""Tests for summarize.py's duplicate-metric guard (issue #835, OpenROAD#12303).

The grounding distinction `docs/failure-modes.md` is about: the committed
artifact is the fixture, not a file invented here.
`pnr/orfs/evidence/synth_top/joined-d1e5068/6_report.json` really does carry
`finish__design__instance__area` twice with differing values (the fill-
inclusive census total, then the filler-excluded active area), and
`5_2_route.json` in the same archived run carries
`detailedroute__route__drc_errors` twice (the klayout-tools#2089 family). A
guard that cannot be shown to fire on the real artifact does not count
(`docs/verification-rules.md` rule 8) -- so the first test asserts the
artifact itself, and the order-swapped variant is derived from it, never
hand-written.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import area_provenance as ap  # noqa: E402
import summarize as sm  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent

#: The committed evidence the issue is about. If this file is absent the
#: apparatus is in the wrong state and these tests must say so, not pass.
JOINED = ROOT / "pnr/orfs/evidence/synth_top/joined-d1e5068"
JOINED_6_REPORT = JOINED / "6_report.json"

AREA_KEY = "finish__design__instance__area"
#: The two serialised meanings, in emission order: fill-inclusive census
#: total first, filler-excluded active area second. From the file, not from
#: the issue text -- this assertion is what makes the guard's fire real.
JOINED_AREA_VALUES = [3382070.0, 2033110.0]


# ---------------------------------------------------------------- apparatus

def test_the_committed_artifact_is_in_the_asserted_state():
    """Precondition of every other test here (asserted, not assumed)."""
    assert JOINED_6_REPORT.is_file(), f"{JOINED_6_REPORT} is missing"
    _, dups = sm.load_metrics(JOINED_6_REPORT)
    assert dups[AREA_KEY] == JOINED_AREA_VALUES
    log = (JOINED / "6_report.log").read_text()
    assert "Total" in log and "report_design_area" in log


# ------------------------------------------------------------ the strict read

def test_the_committed_artifact_makes_the_guard_fire():
    """The differing duplicate is a metric the report presents -> refusal."""
    _, dups = sm.load_metrics(JOINED_6_REPORT)
    refusals = dict(sm.duplicate_refusals(dups))
    assert refusals[AREA_KEY] == JOINED_AREA_VALUES
    # The benign twin: same key name, identical values -- recorded, not refused.
    assert dups["finish__design__instance__count"] == [154246, 154246]
    assert "finish__design__instance__count" not in refusals


def test_the_order_swapped_variant_does_not_silently_pass():
    """The whole point: the guard must not depend on the accident of order.

    Built from the committed artifact by transposing the two textual
    occurrences -- a last-wins reader of this file would get the OTHER value,
    and the guard must refuse it exactly the same.
    """
    raw = JOINED_6_REPORT.read_text()
    first = f'"{AREA_KEY}": 3.38207e+06'
    second = f'"{AREA_KEY}": 2.03311e+06'
    assert raw.count(first) == 1 and raw.count(second) == 1, "artifact format moved"
    swapped = raw.replace(first, "@@A@@").replace(second, first).replace("@@A@@", second)
    with tempfile.TemporaryDirectory() as tmp:
        p = pathlib.Path(tmp) / "6_report.json"
        p.write_text(swapped)
        _, dups = sm.load_metrics(p)
        refusals = dict(sm.duplicate_refusals(dups))
    assert refusals[AREA_KEY] == list(reversed(JOINED_AREA_VALUES))


def test_a_duplicated_required_key_with_identical_values_is_not_a_refusal():
    dups = {"finish__design__instance__count": [154246, 154246]}
    assert sm.duplicate_refusals(dups) == []


def test_a_differing_non_presented_key_is_recorded_not_refused():
    # A per-class key pick() can never return (the ':' rule), and a stage key
    # this report does not present: neither is this report's number to refuse.
    assert sm.duplicate_refusals({
        "finish__design__instance__area__class:fill": [1.0, 2.0],
        "cts__design__instance__displacement__max": [3.0, 9.0],
    }) == []
    assert sm.duplicate_refusals({"finish__design__instance__area": [1.0, 2.0]})


def test_a_file_with_no_duplicates_behaves_exactly_as_today():
    data, dups = sm.load_metrics(JOINED_6_REPORT)
    assert dups.keys(), "the committed artifact must carry duplicates for this control"
    with tempfile.TemporaryDirectory() as tmp:
        p = pathlib.Path(tmp) / "clean.json"
        json.dump(data, open(p, "w"))  # the last-wins view is duplicate-free
        data2, dups2 = sm.load_metrics(p)
    assert dups2 == {}
    assert data2 == data


# ----------------------------------------------------------------- the CLI

def _stage(tmp_path, source_6_report=None, dedup_all=False):
    """An ORFS-shaped work tree from the archived fixed-die run (never the
    live tree -- `make controls` runs these in parallel)."""
    work = ap.stage(tmp_path / "work", None)
    logs = work / "logs/gf180/synth_top/base"
    if source_6_report is not None:
        shutil.copyfile(source_6_report, logs / "6_report.json")
    if dedup_all:
        for f in logs.glob("*.json"):
            data, _ = sm.load_metrics(f)
            if data:
                json.dump(data, open(f, "w"))  # the last-wins view, deduplicated
    return work


def _run(work):
    return subprocess.run([sys.executable, str(HERE / "summarize.py"),
                           "synth_top", "base", "--work", str(work)],
                          capture_output=True, text=True)


def test_the_shipped_cli_refuses_on_the_committed_artifact_and_keeps_numbers(tmp_path):
    """Test-plan check 1: the guard fires on the real artifact, and the
    published std-cell area it prints is the same number it printed before
    the guard existed -- refusal moves no published number."""
    work = _stage(tmp_path, JOINED_6_REPORT)
    done = _run(work)
    assert done.returncode == 2
    assert "duplicate-metric" in done.stdout
    assert f"`{AREA_KEY}`" in done.stdout
    assert "2,033,110" in done.stdout, "the last-wins std-cell area must still print, unchanged"


def test_the_shipped_cli_refuses_on_the_order_swapped_artifact(tmp_path):
    """Test-plan check 2: transposed occurrences must refuse, not silently
    publish the other value."""
    raw = JOINED_6_REPORT.read_text()
    first = f'"{AREA_KEY}": 3.38207e+06'
    second = f'"{AREA_KEY}": 2.03311e+06'
    swapped = raw.replace(first, "@@A@@").replace(second, first).replace("@@A@@", second)
    work = _stage(tmp_path)
    (work / "logs/gf180/synth_top/base/6_report.json").write_text(swapped)
    done = _run(work)
    assert done.returncode == 2
    assert "duplicate-metric" in done.stdout
    assert str(list(reversed(JOINED_AREA_VALUES))).replace("'", "") in done.stdout


def test_the_shipped_cli_is_green_on_a_duplicate_free_tree(tmp_path):
    """A run whose metrics carry no duplicates must behave exactly as before
    the guard: exit 0, ratio printed, no duplicate block at all. This is what
    a fixed OpenROAD's output looks like."""
    work = _stage(tmp_path, dedup_all=True)
    done = _run(work)
    out = done.stdout + done.stderr
    assert done.returncode == 0, out[-800:]
    assert "duplicate-metric" not in out
    assert "duplicate metric keys" not in out
    assert ap.RATIO_CLAIM.search(out), "the die ratio is still attributed"
