#!/usr/bin/env python3
"""Controls for `tools/corpus_lineage.py`.

The committed manifest and document must pass every rule -- an unsatisfiable
gate is worse than no gate -- and **every rule must have an input that defeats
the manifest and turns it red**. `docs/verification-rules.md` rule 8: a guard
ships with the input that defeats it. A checker that only ever sees a good
manifest has demonstrated nothing, and this file exists because four guards in
this repository were defeated by the exact pathology they were written to catch.

Each `test_r*_*` below mutates a COPY of the committed manifest (or document) in
a tmp dir, in the one way that rule is supposed to notice, and asserts the rule
fires. The mutation is always the *plausible* one -- the shortcut somebody would
actually take -- not an obvious corruption.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import corpus_lineage as cl                                          # noqa: E402


def _rules(findings, status="FALSE"):
    return {f["rule"] for f in findings if f["status"] == status}


@pytest.fixture
def tree(tmp_path):
    """A copy of the committed manifest and document, mutable per test.

    `evidence` paths are checked against the real repository root, so `check`
    is handed `root=ROOT` while the manifest and document come from tmp.
    """
    d = tmp_path / "docs"
    d.mkdir()
    shutil.copy(cl.MANIFEST, d / "corpus-lineage.json")
    shutil.copy(cl.DOC, d / "corpus-lineage.md")

    class T:
        manifest = d / "corpus-lineage.json"
        doc = d / "corpus-lineage.md"

        def load(self):
            return json.loads(self.manifest.read_text())

        def save(self, m):
            self.manifest.write_text(json.dumps(m, indent=2))

        def pack(self, m, pid):
            return next(p for p in m["packs"] if p["id"] == pid)

        def check(self):
            return cl.check(self.manifest, self.doc, ROOT)

    return T()


# ---------------------------------------------------------------------------
# the gate, against the current state, before anything is injected
# ---------------------------------------------------------------------------
def test_the_committed_manifest_and_document_pass_every_rule():
    findings = cl.check()
    assert _rules(findings) == set(), [f for f in findings if f["status"] == "FALSE"]


def test_check_exits_zero_on_the_committed_state():
    assert cl.main(["check"]) == 0


def test_the_committed_state_still_has_open_fields_and_strict_says_so():
    """Six fields await an operator with the corpus mounted. `--strict` is how
    that operator finds them; plain `check` must NOT fail on them, or the gate
    is red on a repository that is in its correct state."""
    assert _rules(cl.check(), "OPEN"), "no OPEN fields -- did the markers get edited away?"
    assert cl.main(["check"]) == 0
    assert cl.main(["check", "--strict"]) == 2


def test_open_subcommand_lists_the_gaps_and_does_not_fail():
    assert cl.main(["open"]) == 0


def test_list_subcommand_names_every_pack():
    assert cl.main(["list"]) == 0
    assert len(cl.load()["packs"]) == len(cl.doc_pack_ids())


# ---------------------------------------------------------------------------
# R1 -- fields present
# ---------------------------------------------------------------------------
def test_r1_fires_on_a_blank_licence(tree):
    """The plausible shortcut: a pack added in a hurry with the licence left
    blank. A blank is NOT the same as a marked unknown and must fail."""
    m = tree.load()
    tree.pack(m, "808-from-mars")["licence"] = "   "
    tree.save(m)
    assert "R1" in _rules(tree.check())


def test_r1_reports_a_marked_unknown_as_open_not_false(tree):
    m = tree.load()
    tree.pack(m, "808-from-mars")["known_settings"] = m["unknown_marker"]
    tree.save(m)
    f = tree.check()
    assert "R1" not in _rules(f)
    assert "R1" in _rules(f, "OPEN")


# ---------------------------------------------------------------------------
# R2 -- lineage vocabulary
# ---------------------------------------------------------------------------
def test_r2_fires_on_an_invented_lineage_status(tree):
    m = tree.load()
    tree.pack(m, "808-from-mars")["lineage_status"] = "probably-fine"
    tree.save(m)
    assert "R2" in _rules(tree.check())


# ---------------------------------------------------------------------------
# R3 -- the input that defeats R2 and R5 together
# ---------------------------------------------------------------------------
def test_r3_fires_when_a_pack_with_no_established_unit_is_declared_documented(tree):
    """THE defeating input. `boutique-808` has an unknown unit; relabelling it
    `documented` would let it back a verdict, satisfying R5 while violating
    everything R5 is for."""
    m = tree.load()
    p = tree.pack(m, "boutique-808")
    p["lineage_status"] = "documented"
    p["roles"] = ["held-out-validation"]
    tree.save(m)
    f = tree.check()
    assert "R3" in _rules(f), "a documented pack with no established unit passed"
    assert "R5" not in _rules(f), "R5 is satisfied by the relabel -- which is why R3 exists"


def test_r3_does_not_fire_on_a_documented_unit_with_an_open_chain(tree):
    """The other half of R3, and the reason it is scoped to `unit` alone.
    `legowelt-minimoog-5529` has the best-identified unit in the corpus -- a
    published serial number -- and an undocumented recording chain. That is a
    documented unit with an open field, not an undocumented lineage, and a rule
    that failed it would be red on the current tree for no reason."""
    p = next(x for x in cl.load()["packs"] if x["id"] == "legowelt-minimoog-5529")
    assert p["lineage_status"] == "documented"
    assert p["dry_or_processed"] == cl.load()["unknown_marker"]
    assert "R3" not in _rules(cl.check())


# ---------------------------------------------------------------------------
# R4 / R5 -- groups and weak evidence
# ---------------------------------------------------------------------------
def test_r4_fires_on_a_group_name_that_is_not_one_of_the_three(tree):
    m = tree.load()
    tree.pack(m, "808-from-mars")["roles"] = ["validation"]
    tree.save(m)
    assert "R4" in _rules(tree.check())


def test_r5_fires_when_a_claimed_lineage_backs_a_verdict(tree):
    """The whole point of #158: a vendor's word promoted to a verdict."""
    m = tree.load()
    p = tree.pack(m, "808-from-mars")
    p["roles"] = ["analyzer-development", "held-out-validation"]
    tree.save(m)
    assert "R5" in _rules(tree.check())


def test_r5_fires_when_an_unknown_lineage_calibrates_a_threshold(tree):
    m = tree.load()
    p = tree.pack(m, "boutique-808")
    p["roles"] = ["threshold-calibration"]
    p["role_overlap_why"] = ""
    tree.save(m)
    assert "R5" in _rules(tree.check())


def test_r5_permits_a_claimed_lineage_in_analyzer_development(tree):
    """The rule confines weak evidence; it does not forbid it. If this failed,
    the gate would be unsatisfiable for every commercial pack."""
    m = tree.load()
    tree.pack(m, "mini-from-mars")["roles"] = ["analyzer-development"]
    tree.save(m)
    assert "R5" not in _rules(tree.check())


def test_r5_fires_when_a_claimed_lineage_calibrates_with_no_justification(tree):
    """The second tier's teeth. 808 From Mars really does floor a scorecard
    tolerance; that is allowed only because `weak_evidence_why` says the floor
    does not depend on which machine it is. Delete that sentence and the
    manifest is asserting a threshold on a vendor's word with nothing said."""
    m = tree.load()
    tree.pack(m, "808-from-mars")["weak_evidence_why"] = ""
    tree.save(m)
    assert "R5" in _rules(tree.check())


def test_r5_permits_a_claimed_lineage_to_calibrate_when_it_justifies_itself(tree):
    """...and the first tier's flat ban would have been an UNSATISFIABLE gate.
    This is the live state of the repository: two claimed editions of one
    machine floor the f0 discrimination band, legitimately. A rule written as
    'not documented => analyzer-development only' was tried first and was red
    on the committed manifest, which is how the two tiers came to exist."""
    m = cl.load()
    for pid in ("808-from-mars", "808-from-mars-legacy"):
        p = next(x for x in m["packs"] if x["id"] == pid)
        assert p["lineage_status"] == "claimed"
        assert "threshold-calibration" in p["roles"]
        assert p["weak_evidence_why"].strip()
        assert "held-out-validation" not in p["roles"]
    assert "R5" not in _rules(cl.check())


# ---------------------------------------------------------------------------
# R6 / R7 -- overlap and absence both have to be explained
# ---------------------------------------------------------------------------
def test_r6_fires_when_a_lineage_spans_two_groups_with_no_stated_separation(tree):
    """The Fischer lineage really is in two groups. Deleting the paragraph that
    says what holds instead must turn the manifest red, because that paragraph
    is the only thing standing between this corpus and a circular claim."""
    m = tree.load()
    tree.pack(m, "fischer-tr808-103852")["role_overlap_why"] = ""
    tree.save(m)
    assert "R6" in _rules(tree.check())


def test_r7_fires_when_a_pack_is_in_no_group_and_says_nothing(tree):
    m = tree.load()
    p = tree.pack(m, "808-loops-from-mars")
    p["group_why"] = ""
    tree.save(m)
    assert "R7" in _rules(tree.check())


# ---------------------------------------------------------------------------
# R8 -- document agreement, both directions and order
# ---------------------------------------------------------------------------
def test_r8_fires_when_a_pack_is_added_to_the_manifest_only(tree):
    """The drift that actually happens: a pack lands in the machine-readable
    file and the prose is never touched."""
    m = tree.load()
    new = dict(tree.pack(m, "808-from-mars"))
    new["id"] = "909-from-mars"
    m["packs"].append(new)
    tree.save(m)
    f = tree.check()
    assert "R8" in _rules(f)
    assert any("909-from-mars" in x["what"] for x in f if x["rule"] == "R8")


def test_r8_fires_when_a_pack_is_added_to_the_document_only(tree):
    text = tree.doc.read_text().replace(
        "<!-- /corpus-lineage:packs -->",
        "| `727-from-mars` | Roland TR-727 | ? | unknown | ? | ? |\n"
        "<!-- /corpus-lineage:packs -->")
    tree.doc.write_text(text)
    assert "R8" in _rules(tree.check())


def test_r8_fires_on_reordering_alone(tree):
    """Order matters because a reader compares the two files row by row."""
    m = tree.load()
    m["packs"] = [m["packs"][1], m["packs"][0], *m["packs"][2:]]
    tree.save(m)
    assert "R8" in _rules(tree.check())


def test_a_document_with_no_delimited_region_is_refused_not_passed(tree):
    tree.doc.write_text("# no region here\n")
    with pytest.raises(cl.Refused):
        tree.check()


# ---------------------------------------------------------------------------
# R9 / R10 -- the vocabularies cannot be renamed in one file only
# ---------------------------------------------------------------------------
def test_r9_fires_when_the_document_stops_naming_a_group(tree):
    tree.doc.write_text(tree.doc.read_text().replace("held-out-validation", "held-out"))
    assert "R9" in _rules(tree.check())


def test_r10_fires_when_a_fourth_group_is_invented(tree):
    m = tree.load()
    m["groups"]["sanity-check"] = "a fourth group"
    tree.save(m)
    assert "R10" in _rules(tree.check())


def test_r10_fires_when_a_target_loses_its_current_work_statement(tree):
    """'Which current work belongs to each target' is an acceptance criterion of
    #520, so an empty one is a violation, not a blank to fill in later."""
    m = tree.load()
    m["targets"]["hardware-variation-coverage"]["current_work"] = ""
    tree.save(m)
    assert "R10" in _rules(tree.check())


def test_the_hardware_variation_target_is_allowed_to_say_none(tree):
    """It says NONE today and that is the honest answer. The gate must accept a
    stated absence while rejecting a blank -- otherwise it pressures the next
    author into inventing coverage."""
    assert "NONE" in cl.load()["targets"]["hardware-variation-coverage"]["current_work"]
    assert "R10" not in _rules(cl.check())


# ---------------------------------------------------------------------------
# R11 -- evidence
# ---------------------------------------------------------------------------
def test_r11_fires_on_an_evidence_path_that_does_not_exist(tree):
    m = tree.load()
    tree.pack(m, "808-from-mars")["evidence"] = ["docs/a-file-that-was-renamed.md"]
    tree.save(m)
    assert "R11" in _rules(tree.check())


def test_r11_fires_on_an_empty_evidence_list(tree):
    m = tree.load()
    tree.pack(m, "mini-from-mars")["evidence"] = []
    tree.save(m)
    assert "R11" in _rules(tree.check())


# ---------------------------------------------------------------------------
# apparatus preconditions: REFUSED is a first-class outcome, not a pass
# ---------------------------------------------------------------------------
def test_a_missing_manifest_is_refused(tmp_path):
    with pytest.raises(cl.Refused):
        cl.load(tmp_path / "nope.json")


def test_invalid_json_is_refused_rather_than_skipped(tmp_path):
    p = tmp_path / "corpus-lineage.json"
    p.write_text("{not json")
    with pytest.raises(cl.Refused):
        cl.load(p)


def test_a_manifest_missing_a_top_level_key_is_refused(tree):
    m = tree.load()
    del m["assignment_rule"]
    tree.save(m)
    with pytest.raises(cl.Refused):
        tree.check()


# ---------------------------------------------------------------------------
# the lineage facts this document exists to hold, asserted so a silent edit
# cannot remove them
# ---------------------------------------------------------------------------
def test_the_fischer_unit_is_named_and_is_the_only_documented_808():
    m = cl.load()
    docd = [p for p in m["packs"] if p["lineage_status"] == "documented"
            and p["instrument"] == "Roland TR-808"]
    assert {p["id"] for p in docd} == {"fischer-tr808-103852", "dirt-samples-808"}
    for p in docd:
        assert "103852" in p["unit"], p["id"]


def test_the_dirt_samples_pack_is_recorded_as_a_re_pressing_and_used_by_nothing():
    """'An extra library does not establish an extra machine' -- the one case
    where it already happened. If this pack ever acquires a role, the claim
    count in this repository silently doubles on the same bytes."""
    p = next(x for x in cl.load()["packs"] if x["id"] == "dirt-samples-808")
    assert p["roles"] == []
    assert "RE-PRESSING" in p["relationships"] or "re-pressing" in p["relationships"]


def test_no_pack_claims_to_be_dry_except_the_fischer_set():
    """The only dry material in the corpus is the Fischer individual voice
    outputs. 'Clean' in a commercial pack name is a subset label, not a chain."""
    dry = {p["id"] for p in cl.load()["packs"] if p["dry_or_processed"] == "dry"}
    assert dry == {"fischer-tr808-103852", "dirt-samples-808"}


def test_the_manifest_records_that_no_pack_has_been_descent_tested_yet():
    """Six packs say 'no -- never run'. This test is a tripwire, not an
    assertion that the state is good: when somebody RUNS the test, this test
    fails and is updated with what was found. That is the intended lifecycle."""
    never = [p["id"] for p in cl.load()["packs"]
             if str(p["descent_tested"]).startswith("no --")]
    assert never, "descent_tested wording changed -- update this tripwire"
    assert "808-from-mars" in never
    assert "boutique-808" in never
