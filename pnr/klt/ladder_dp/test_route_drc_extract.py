"""Tests for the route-DRC extract check (issue #1006).

The extract exists so two artefact-only claims in ``docs/tool-findings.md``
are checkable without the bucket that expires. What these tests pin:

* the committed extract passes against the committed classification record
  and the committed DEF-site analysis;
* every way the check is supposed to go red actually goes red -- an injected
  wrong hash (the control #1006's test plan names), a pair claim whose two
  files are no longer byte-identical, final-count claims that disagree, and
  a site analysis whose report binding no longer matches the extract's
  (the "re-run reproduced the original route" claim going false).

What these tests cannot do, by construction: check the extract against the
artefacts the sha256s name, which are not in the tree. That half is
``classify_route_drc.py`` run against the operator's copy or the bucket, not
this file.
"""
from __future__ import annotations

import copy
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import check_route_drc_extract  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent


def _load(name: str) -> dict:
    return json.loads((HERE / name).read_text())


def _mutated(name: str, mutate) -> dict:
    doc = _load(name)
    mutate(doc)
    return doc


def test_the_committed_extract_passes() -> None:
    """Run a gate against the current state before committing it."""
    problems = check_route_drc_extract.verify(
        _load("route_drc_extract.json"),
        _load("route_drc_classification.json"),
        _load("def_site_analysis.json"),
    )
    assert problems == [], problems


def test_an_injected_wrong_hash_goes_red() -> None:
    """The control #1006's test plan names: a wrong sha256 must fail the check."""
    extract = _mutated(
        "route_drc_extract.json",
        lambda d: d["inputs"]["metrics"].__setitem__(
            "sha256", "sha256:" + "0" * 64
        ),
    )
    problems = check_route_drc_extract.verify(
        extract,
        _load("route_drc_classification.json"),
        _load("def_site_analysis.json"),
    )
    assert any("metrics" in p and "disagrees" in p for p in problems), problems


def test_a_pair_that_is_no_longer_byte_identical_goes_red() -> None:
    """The pair claim's whole content is 'same bytes'; a differing sha must fail."""
    extract = _mutated(
        "route_drc_extract.json",
        lambda d: d["inputs"]["drc_report_pass_0"].__setitem__(
            "sha256", "sha256:" + "1" * 64
        ),
    )
    problems = check_route_drc_extract.verify(
        extract,
        _load("route_drc_classification.json"),
        _load("def_site_analysis.json"),
    )
    assert any("drc_report_pass_0" in p for p in problems), problems


def test_a_disagreeing_final_count_goes_red() -> None:
    """Series final, headline counts and the classification total must be one number."""
    extract = _mutated(
        "route_drc_extract.json",
        lambda d: d["claims"]["per_iteration_violation_series"].__setitem__(
            "final_value", 23
        ),
    )
    problems = check_route_drc_extract.verify(
        extract,
        _load("route_drc_classification.json"),
        _load("def_site_analysis.json"),
    )
    assert any("disagree" in p for p in problems), problems


def test_a_series_without_its_final_iteration_goes_red() -> None:
    """A series that claims final 24 at iteration 60 must actually have that point."""
    extract = _mutated(
        "route_drc_extract.json",
        lambda d: d["claims"]["per_iteration_violation_series"]["series"].pop("60"),
    )
    problems = check_route_drc_extract.verify(
        extract,
        _load("route_drc_classification.json"),
        _load("def_site_analysis.json"),
    )
    assert any("final_iteration" in p for p in problems), problems


def test_a_broken_reproduction_claim_goes_red() -> None:
    """The re-run's report must stay byte-identical to the committed binding.

    The docs' central provenance sentence ("the re-run reproduced the
    original route") is the equality of these two sha256s; mutating either
    side must fail the check, exactly like any other injected-hash control.
    """
    site_analysis = _mutated(
        "def_site_analysis.json",
        lambda d: d["inputs"]["drc_report"].__setitem__(
            "sha256", "sha256:" + "2" * 64
        ),
    )
    problems = check_route_drc_extract.verify(
        _load("route_drc_extract.json"),
        _load("route_drc_classification.json"),
        site_analysis,
    )
    assert any("reproduction claim fails" in p for p in problems), problems


def test_the_classification_record_total_is_24() -> None:
    """The number the docs restate, pinned where it is read from."""
    record = _load("route_drc_classification.json")
    assert record["drc_report"]["total_records"] == 24
    assert record["drc_report"]["distinct_sites_type_srcs_bbox_layer"] == 24
