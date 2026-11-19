#!/usr/bin/env python3
"""`tools/reference_suites.py`, and the scoring boundary that refuses a suite
mix (#1050).

    python3 -m pytest tools/test_reference_suites.py -q

Three things are asserted here:

* the registry derives a record's suite from the identity fields the frozen
  V3 manifests ALREADY carry, and refuses an identity it has never declared
  (a guessed suite is how a V4 render would end up graded against V3);
* the registry's declared V3 version matches all three frozen manifests, so
  the registry cannot drift from the records it names;
* the scoring entry points (`tools/mono_m1a_score.py`,
  `tools/mono_m5a_score.py`) refuse a manifest whose identity has been
  swapped to Mini V4 -- the mutant that mixes the suites, run through the
  shipping scorer, not through a helper.

The V4 suite definition is also cross-checked here against the board's own
case table (`docs/scorecard/cases.csv`): the suite declares all 32 Mono
cases and only those, every field is explicit, and holdouts carry their
sealing statement.
"""
from __future__ import annotations

import copy
import csv
import json
import pathlib
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "model"))

import reference_suites as rs                                     # noqa: E402
import mono_m1a_score as m1a                                      # noqa: E402
import mono_m5a_score as m5a                                      # noqa: E402

FROZEN_MANIFESTS = [
    ROOT / "docs/scorecard/mono-m1a-miniv3/manifest.json",
    ROOT / "docs/scorecard/mono-m5a-miniv3/manifest.json",
    ROOT / "docs/scorecard/mono-m5b-miniv3/manifest.json",
]


def v3_identity() -> dict:
    return {"name": "Mini V3", "version": rs.V3_FROZEN_VERSION,
            "bundle_id": rs.V3_BUNDLE_ID}


def v4_identity(version=None) -> dict:
    return {"name": "Mini V4",
            "version": version or rs.V4_REQUIRED_VERSION}


# ===========================================================================
# suite derivation
# ===========================================================================
def test_a_frozen_v3_identity_derives_to_miniv3_frozen():
    assert rs.suite_of_identity(v3_identity()) == rs.SUITE_V3_FROZEN


def test_the_required_v4_identity_derives_to_miniv4():
    assert rs.suite_of_identity(v4_identity()) == rs.SUITE_V4


def test_an_unknown_version_is_refused_not_guessed():
    # The vendor ships 4.0.5 tomorrow: a version the registry never declared
    # has no suite, and an unsuiteed record must never be scored.
    with pytest.raises(rs.Refused, match="4\\.0\\.5"):
        rs.suite_of_identity(v4_identity("4.0.5.9999"))


def test_an_unknown_plugin_is_refused_not_guessed():
    with pytest.raises(rs.Refused, match="no reference suite"):
        rs.suite_of_identity({"name": "Mini V5", "version": "1.0.0"})


def test_a_record_without_an_identity_block_is_refused():
    with pytest.raises(rs.Refused, match="no identity block"):
        rs.suite_of_identity(None)


def test_the_registry_version_matches_every_frozen_manifest():
    # External grounding: the constant is not self-consistent, it matches the
    # records it names. Drift here would freeze the wrong identity.
    for path in FROZEN_MANIFESTS:
        manifest = json.loads(path.read_text())
        assert manifest["identity"]["version"] == rs.V3_FROZEN_VERSION, path
        assert rs.suite_of_identity(manifest["identity"]) == rs.SUITE_V3_FROZEN


# ===========================================================================
# the mix refused at the scoring boundary
# ===========================================================================
def test_require_suite_passes_a_matching_record_and_refuses_a_mix():
    good = {"identity": v3_identity()}
    assert rs.require_suite(good, rs.SUITE_V3_FROZEN) == rs.SUITE_V3_FROZEN
    mixed = {"identity": v4_identity()}
    with pytest.raises(rs.Refused, match="miniv4.*miniv3-frozen"):
        rs.require_suite(mixed, rs.SUITE_V3_FROZEN)


def _v4_swapped_manifest(src: pathlib.Path, tmp_path: pathlib.Path) -> pathlib.Path:
    """A frozen manifest with ONLY its identity swapped to Mini V4 -- the
    exact mutant 'a V4 render substituted into a V3 expectation'."""
    original = json.loads(src.read_text())
    manifest = json.loads(src.read_text())
    manifest["identity"] = v4_identity()
    manifest["audio"]["file"] = str(src.parent / original["audio"]["file"])
    p = tmp_path / f"manifest-{original['case_id']}.json"
    p.write_text(json.dumps(manifest))
    return p


def test_m5a_scoring_refuses_a_v4_manifest_substituted_into_the_v3_slot(
        tmp_path, monkeypatch):
    swapped = _v4_swapped_manifest(FROZEN_MANIFESTS[1], tmp_path)
    monkeypatch.setattr(m5a, "MANIFEST", swapped)
    with pytest.raises(m5a.Refused, match="reference-suite mix"):
        m5a.measure()


def test_m5b_scoring_refuses_a_v4_manifest_substituted_into_the_v3_slot(
        tmp_path, monkeypatch):
    swapped = _v4_swapped_manifest(FROZEN_MANIFESTS[2], tmp_path)
    monkeypatch.setattr(m5a, "MANIFESTS",
                        {"M5A": FROZEN_MANIFESTS[1], "M5B": swapped})
    with pytest.raises(m5a.Refused, match="reference-suite mix"):
        m5a.measure(case_id="M5B")


def test_m1a_scoring_refuses_a_v4_manifest_substituted_into_the_v3_slot(
        tmp_path, monkeypatch):
    src = FROZEN_MANIFESTS[0]
    manifest = json.loads(src.read_text())
    manifest["identity"] = v4_identity()
    renders = copy.deepcopy(manifest["renders"])
    for item in renders:
        item["file"] = str(src.parent / item["file"])
    manifest["renders"] = renders
    for item in manifest["controls"].values():
        item["file"] = str(src.parent / item["file"])
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(manifest))
    # load_reference binds its path default at definition time, so the mutant
    # is passed explicitly rather than through the module attribute.
    with pytest.raises(m1a.Refused, match="reference-suite mix"):
        m1a.load_reference(p)


def test_m1a_names_the_suite_mix_before_any_manifest_symptom(tmp_path):
    """The suite gate fires before the manifest checks: a record with an
    unqualified body but a Mini V4 identity refuses as the mix, not as
    'M1A reference manifest is not qualified' -- a symptom would leave the
    operator diagnosing the manifest instead of the substitution."""
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps({"identity": v4_identity()}))
    with pytest.raises(m1a.Refused, match="reference-suite mix"):
        m1a.load_reference(p)


# ===========================================================================
# the V4 suite definition, cross-checked against the board's own case table
# ===========================================================================
def test_the_v4_suite_declares_exactly_the_board_mono_cases():
    suite = rs.load_v4_suite()
    with open(ROOT / "docs/scorecard/cases.csv") as f:
        board = {r["case_id"] for r in csv.DictReader(f) if r["family"] == "Mono"}
    declared = {c["case_id"] for c in suite["cases"]}
    assert declared == board, (
        "the suite and the board disagree: "
        f"missing {sorted(board - declared)}, extra {sorted(declared - board)}")


def test_the_v4_suite_names_its_waiting_cases_and_its_v3_frozen_records():
    suite = rs.load_v4_suite()
    waiting = [c["case_id"] for c in suite["cases"]
               if c["render_state"] == "awaiting-render"]
    assert len(waiting) == 32, "no V4 render exists yet: all cases are waiting"
    anchored = {c["case_id"]: c["reference_before_v4"]
                for c in suite["cases"] if c["reference_before_v4"]}
    assert set(anchored) == {"M1A", "M5A", "M5B"}
    for cid, rel in anchored.items():
        assert (ROOT / rel).is_dir(), f"{cid}'s pre-V4 reference path is wrong"


def test_every_suite_case_carries_every_setting_explicitly():
    suite = rs.load_v4_suite()   # load_v4_suite refuses a missing key itself
    for c in suite["cases"]:
        assert c["velocity"] == 100
        assert c["phrase_s"] > 0
        assert c["patch"].strip(), f"{c['case_id']} has an empty patch"


def test_a_sealed_holdout_without_a_sealing_statement_is_refused(tmp_path):
    suite = rs.load_v4_suite()
    for c in suite["cases"]:
        if c["base_midi_note"] is None:
            assert c.get("sealing", "").strip(), (
                f"{c['case_id']} has a null note set but no sealing statement")
    broken = copy.deepcopy(suite)
    for c in broken["cases"]:
        if c["case_id"] == "M1D":
            del c["sealing"]
    p = tmp_path / "suite.json"
    p.write_text(json.dumps(broken))
    with pytest.raises(rs.Refused, match="M1D.*sealing"):
        rs.load_v4_suite(p)


def test_a_suite_manifest_baselining_the_wrong_version_is_refused(tmp_path):
    suite = rs.load_v4_suite()
    suite["plugin"]["required_version"] = "4.0.5.0"
    p = tmp_path / "suite.json"
    p.write_text(json.dumps(suite))
    with pytest.raises(rs.Refused, match="4\\.0\\.5\\.0"):
        rs.load_v4_suite(p)


def test_a_case_with_a_missing_field_is_refused_not_defaulted(tmp_path):
    suite = rs.load_v4_suite()
    del suite["cases"][0]["phrase_s"]
    p = tmp_path / "suite.json"
    p.write_text(json.dumps(suite))
    with pytest.raises(rs.Refused, match="phrase_s"):
        rs.load_v4_suite(p)
