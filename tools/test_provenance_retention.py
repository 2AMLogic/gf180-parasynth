#!/usr/bin/env python3
"""Retention is a property of the RECORD, and this is the suite that keeps it
one.

The failure being guarded against is specific and quiet: **the WAV a bound was
derived from expiring on the same seven-day clock as a per-push smoke render.**
Nothing goes red when that happens. The bound keeps issuing verdicts; it simply
becomes a number that can only be re-trusted, never re-derived -- which is the
state issue #68 exists to get out of.

So the three things here are (1) the number in the workflow file is the number
in `RETENTION_DAYS`, asserted by reading the YAML rather than by everyone
remembering; (2) a record that would be silently dropped from the bundle is
REFUSED instead; and (3) a fixture really does outlive a smoke render.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import provenance_retention as pr                                     # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "provenance.yml"


def _store(root: pathlib.Path, **classes) -> pathlib.Path:
    """A minimal store: `{sub}/{id}` directories with one file each, marked (or
    deliberately not) with a retention class."""
    for ident, (sub, cls) in classes.items():
        d = root / sub / ident
        d.mkdir(parents=True, exist_ok=True)
        (d / "manifest.json").write_text(json.dumps({"id": ident}))
        if cls is not None:
            pr.mark(d, cls)
    return root


# ---------------------------------------------------------------------------
# 1. the number lives in one place, and the workflow agrees with it
# ---------------------------------------------------------------------------
def test_a_reference_fixture_outlives_a_smoke_render():
    """The whole point of having classes at all. Stated as an inequality rather
    than as two literals so it survives both numbers being retuned."""
    assert pr.RETENTION_DAYS["reference-fixture"] > pr.RETENTION_DAYS["smoke"]
    assert pr.RETENTION_DAYS["release-evidence"] > pr.RETENTION_DAYS["smoke"]


def test_every_class_says_why_it_exists():
    assert set(pr.WHY) == set(pr.RETENTION_DAYS)
    for cls, why in pr.WHY.items():
        assert len(why.strip()) > 40, (
            f"{cls}'s reason is too short to be a reason; a retention class with "
            f"no stated purpose is a number somebody chose")


def test_the_workflow_uploads_each_class_on_the_days_this_table_declares():
    """`retention-days:` in the YAML is two files away from the thing it
    describes, which is how the two clocks drift apart. This is the only thing
    stopping it, so it reads the actual workflow rather than trusting a
    comment."""
    wf = yaml.safe_load(WORKFLOW.read_text())
    steps = wf["jobs"]["stages-and-controls"]["steps"]
    uploads = [s for s in steps if str(s.get("uses", "")).startswith(
        "actions/upload-artifact")]
    assert uploads, "the provenance workflow uploads nothing -- the retained " \
                     "artefacts exist only inside the runner and are then discarded"
    seen = {}
    for step in uploads:
        with_ = step["with"]
        paths = str(with_["path"])
        for cls in pr.RETENTION_DAYS:
            if f"/{cls}/" in paths or paths.rstrip().endswith(f"/{cls}"):
                seen[cls] = int(with_["retention-days"])
    assert seen, f"no upload step names a retention class directory: {uploads}"
    for cls, days in seen.items():
        assert days == pr.RETENTION_DAYS[cls], (
            f"{WORKFLOW.name} uploads {cls} for {days} days and "
            f"provenance_retention.RETENTION_DAYS says {pr.RETENTION_DAYS[cls]} -- "
            f"the number has drifted from the thing it describes")


def test_the_fixture_upload_fails_the_job_when_there_is_nothing_to_upload():
    """`if-no-files-found: warn` on the fixture group would turn "the evidence
    was never produced" into a green job with a note in the log."""
    wf = yaml.safe_load(WORKFLOW.read_text())
    steps = wf["jobs"]["stages-and-controls"]["steps"]
    fixture = [s for s in steps
               if str(s.get("uses", "")).startswith("actions/upload-artifact")
               and "reference-fixture" in str(s["with"]["path"])]
    assert fixture, "no upload step covers the reference-fixture group"
    assert all(s["with"].get("if-no-files-found") == "error" for s in fixture)


# ---------------------------------------------------------------------------
# 2. marking, and the refusals
# ---------------------------------------------------------------------------
def test_a_mark_round_trips(tmp_path):
    pr.mark(tmp_path, "reference-fixture")
    assert pr.read_mark(tmp_path) == "reference-fixture"
    rec = json.loads((tmp_path / pr.MARK_NAME).read_text())
    assert rec["retention_days"] == pr.RETENTION_DAYS["reference-fixture"]
    assert rec["why"] == pr.WHY["reference-fixture"]


def test_marking_with_an_undeclared_class_is_refused(tmp_path):
    with pytest.raises(pr.Refused, match="not a declared retention class"):
        pr.mark(tmp_path, "keep-forever-probably")
    assert not (tmp_path / pr.MARK_NAME).exists(), (
        "a refused mark must not leave a file behind, or the next read succeeds")


def test_an_unmarked_record_reads_as_none_not_as_smoke(tmp_path):
    """Defaulting an unmarked record to the shortest clock is the failure this
    module is about, wearing a helpful face."""
    assert pr.read_mark(tmp_path) is None


def test_a_mark_written_by_a_different_version_of_the_table_is_refused(tmp_path):
    pr.mark(tmp_path, "smoke")
    p = tmp_path / pr.MARK_NAME
    rec = json.loads(p.read_text())
    rec["retention_days"] = 3650
    p.write_text(json.dumps(rec))
    with pytest.raises(pr.Refused, match="different version of RETENTION_DAYS"):
        pr.read_mark(tmp_path)


def test_an_unreadable_mark_is_refused_not_ignored(tmp_path):
    (tmp_path / pr.MARK_NAME).write_text("{not json")
    with pytest.raises(pr.Refused, match="not readable JSON"):
        pr.read_mark(tmp_path)


# ---------------------------------------------------------------------------
# 3. grouping
# ---------------------------------------------------------------------------
def test_grouping_places_each_record_under_its_class(tmp_path):
    root = _store(tmp_path / "store", **{
        "r-keep": ("runs", "reference-fixture"),
        "r-throw": ("runs", "smoke"),
        "j-keep": ("jobs", "reference-fixture"),
    })
    out = tmp_path / "upload"
    s = pr.group(root, out)
    assert "refused" not in s, s
    assert (out / "reference-fixture" / "runs" / "r-keep" / "manifest.json").exists()
    assert (out / "reference-fixture" / "jobs" / "j-keep" / "manifest.json").exists()
    assert (out / "smoke" / "runs" / "r-throw" / "manifest.json").exists()
    assert not (out / "smoke" / "jobs").exists()
    txt = (out / "reference-fixture" / "RETENTION.txt").read_text()
    assert str(pr.RETENTION_DAYS["reference-fixture"]) in txt
    assert s["classes"]["reference-fixture"]["records"] == 2


def test_an_empty_store_is_refused_rather_than_uploaded_as_nothing(tmp_path):
    """"A run that produced no record and an upload that dropped them all look
    the same from here." Exit 2, not exit 0 with an empty bundle."""
    s = pr.group(tmp_path / "nothing", tmp_path / "out")
    assert "refused" in s
    assert pr.main(["--root", str(tmp_path / "nothing"),
                    "--out", str(tmp_path / "out")]) == 2


# ---------------------------------------------------------------------------
# THE INJECTED CONTROL (docs/verification-rules.md 2 and 5)
# ---------------------------------------------------------------------------
# Start red: each of these is run against a store that is CORRECT first, and the
# defect is then introduced into that same store, so a refusal cannot come from
# the fixture never having worked.
def test_an_unmarked_record_makes_the_bundle_refuse_rather_than_drop_it(tmp_path):
    """The defect this is the control for: a record that belongs to no class is
    copied into no group, so `upload-artifact` never sees it. Nothing is red; the
    bundle is just quietly missing the record somebody will want."""
    root = _store(tmp_path / "store", **{"r-ok": ("runs", "smoke")})
    out = tmp_path / "out"
    assert "refused" not in pr.group(root, out), "the clean store must group cleanly"

    (root / "runs" / "r-unmarked").mkdir(parents=True)
    (root / "runs" / "r-unmarked" / "manifest.json").write_text("{}")
    s = pr.group(root, tmp_path / "out2")
    assert "refused" in s, (
        "an unmarked record was silently omitted from the bundle -- which is the "
        "exact failure this step exists to make visible")
    assert any("r-unmarked" in p for p in s["problems"])
    assert pr.main(["--root", str(root), "--out", str(tmp_path / "out3")]) == 2


def test_a_record_marked_with_an_undeclared_class_makes_the_bundle_refuse(tmp_path):
    root = _store(tmp_path / "store", **{"r-ok": ("runs", "reference-fixture")})
    assert "refused" not in pr.group(root, tmp_path / "out")

    d = root / "runs" / "r-odd"
    d.mkdir(parents=True)
    (d / pr.MARK_NAME).write_text(json.dumps(
        {"retention_class": "forever", "retention_days": 4000}))
    s = pr.group(root, tmp_path / "out2")
    assert "refused" in s and any("forever" in p for p in s["problems"])


def test_the_summary_records_what_was_grouped(tmp_path):
    root = _store(tmp_path / "store", **{"r": ("runs", "smoke")})
    out = tmp_path / "out"
    pr.group(root, out)
    s = json.loads((out / "summary.json").read_text())
    assert s["records_seen"] == 1
    assert s["classes"]["smoke"]["retention_days"] == pr.RETENTION_DAYS["smoke"]
    assert s["classes"]["smoke"]["ids"] == ["r"]
