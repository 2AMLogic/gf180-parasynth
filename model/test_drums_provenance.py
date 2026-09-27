#!/usr/bin/env python3
"""The provenance registry of model/drums_fx.py (#114): a status is data, and an
absent status is a refusal.

These tests do two things the prose comments could not. They hold the SCHEMA to
its promises -- a measured constant cannot be entered without its sample, a fit
cannot be entered without saying what was held out -- and they hold the REGISTRY
to the comments, by reading the four prose tags back out of the source and
requiring every tagged constant to have a record. A tagged constant added later
and not registered turns the second one red, which is the only part of this that
cannot be satisfied by writing a paragraph.

The migration's own control is not here, because it cannot be: bit-exactness is
a comparison between two trees. `model/drums_provenance.py --registers` prints
every register write the reference host sends (3699 lines: `kit_808`, the frozen
revision-11 kit, all sixteen presets and a full PATTERN_808 pass with the BD
attack window and the toms' pitch-drop sequences at four accents), and the
stream was byte-identical before and after this migration -- sha256
3b12fd0eec864455c064728c596dd9867e7280daf6b220f96ad37081f352691a on both. What
IS here is the reason that comparison can be trusted going forward:
`test_the_register_stream_cannot_depend_on_the_registry`.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "audition"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dataclasses
import pytest
import drums_fx as dx
import drums_provenance as dp


# ---- one test per status kind -------------------------------------------------
def test_a_derived_from_circuit_constant_cites_the_hardwares_own_documentation():
    """BD_HZ is 49.4 Hz because R161/R165/R166/R170/C41/C42 say so, and the
    entry may not smuggle a fit in beside that."""
    p = dx.provenance_of("BD_HZ")
    assert p.status == dx.PROV_DERIVED
    assert p.prose_tag == "VERIFIED IN A SOURCE"
    assert "R161" in p.source and "section 1.2" in p.source
    assert p.fitted_on is None and p.holdout is None
    assert not p.has_holdout
    for name in dx.constants_by_status()[dx.PROV_DERIVED]:
        e = dx.provenance_of(name)
        assert e.fitted_on is None and e.holdout is None, name


def test_a_measured_constant_carries_the_sample_it_was_read_from():
    """n, spread and date are fields. TOM_HW_TAU is six files of one machine;
    CP_TAIL_TAU is one record and says so rather than leaving n blank."""
    measured = dx.constants_by_status()[dx.PROV_MEASURED]
    assert set(measured) == {"TOM_HW_TAU", "CY_DECAY_T20", "CP_TAIL_TAU"}, measured
    for name in measured:
        p = dx.provenance_of(name)
        assert isinstance(p.n, int) and p.n > 0, name
        assert p.spread and p.date, name
        assert len(p.date) == 10 and p.date[4] == p.date[7] == "-", (name, p.date)
        assert p.fitted_on is None, name
    tau = dx.provenance_of("TOM_HW_TAU")
    assert tau.n == len(dx.TOM_HW_TAU) == 6
    assert "0.999" in tau.spread
    assert dx.provenance_of("CP_TAIL_TAU").n == 1


def test_an_inferred_constant_claims_no_sample_and_no_fit():
    """TOM_PRESET's (f0, Q) are a reading of reference 4's component table.
    Nobody measured them, so the entry may not carry an n."""
    p = dx.provenance_of("TOM_PRESET")
    assert p.status == dx.PROV_INFERRED
    assert p.prose_tag == "INFERRED"
    assert "R1/R2/C1/C2" in p.source
    for name in dx.constants_by_status()[dx.PROV_INFERRED]:
        e = dx.provenance_of(name)
        assert e.n is None and e.fitted_on is None and e.holdout is None, name


def test_a_fitted_constant_says_what_it_was_fitted_on_and_what_was_held_out():
    """The clap's final strike was selected on development offsets and confirmed
    on eight nobody had looked at; the cymbal's three envelopes were searched
    against one recording and nothing was held out. Both are `fitted`, and the
    registry distinguishes them without anyone reading a paragraph."""
    clap = dx.provenance_of("CP_FINAL_TAU")
    assert clap.status == dx.PROV_FITTED and clap.has_holdout
    assert "3301" in clap.holdout and "development offsets" in clap.fitted_on

    cy = dx.provenance_of("CY_TAU_DECAY")
    assert cy.status == dx.PROV_FITTED and not cy.has_holdout
    assert cy.holdout == dx.HOLDOUT_NONE
    assert "Schroeder T20" in cy.fitted_on

    for name in dx.constants_by_status()[dx.PROV_FITTED]:
        e = dx.provenance_of(name)
        assert e.fitted_on and e.holdout, name

    assert "CY_TAU_DECAY" in dx.fits_without_holdout()
    assert "CP_FINAL_TAU" not in dx.fits_without_holdout()


def test_the_tom_drop_ratio_is_recorded_as_the_fit_it_is():
    """The comment above it says HARDWARE-MEASURED and the value is the law
    evaluated at accent 1.0 -- 1.060 where that cell's own measured median is
    x1.054. The registry records the status the code has, the tag the comment
    claimed, and the difference; that is the whole point of migrating the medium
    rather than trusting the prose."""
    p = dx.provenance_of("TOM_DROP_RATIO")
    assert p.status == dx.PROV_FITTED
    assert p.prose_tag == "HARDWARE-MEASURED"
    assert p.has_holdout and "MT unseen" in p.holdout
    assert "0.0543" in p.notes


# ---- the schema refuses rather than defaults ---------------------------------
def test_a_status_outside_the_closed_vocabulary_is_rejected():
    with pytest.raises(ValueError, match="not one of"):
        dx.Provenance(status="probably-fine", source="s", justification="j")
    assert dx.PROV_STATUSES == ("derived-from-circuit", "measured", "inferred", "fitted")


def test_a_measured_entry_without_its_sample_is_rejected():
    for missing in ({"spread": "x", "date": "2026-01-01"},
                    {"n": 3, "date": "2026-01-01"},
                    {"n": 3, "spread": "x"}):
        with pytest.raises(ValueError, match="must carry its sample"):
            dx.Provenance(status=dx.PROV_MEASURED, source="s", justification="j", **missing)
    with pytest.raises(ValueError, match="not a sample"):
        dx.Provenance(status=dx.PROV_MEASURED, source="s", justification="j",
                      n=0, spread="x", date="2026-01-01")


def test_a_fit_without_a_stated_holdout_is_rejected():
    with pytest.raises(ValueError, match="what was held out"):
        dx.Provenance(status=dx.PROV_FITTED, source="s", justification="j",
                      fitted_on="one file")
    with pytest.raises(ValueError, match="what was held out"):
        dx.Provenance(status=dx.PROV_FITTED, source="s", justification="j",
                      holdout=dx.HOLDOUT_NONE)
    ok = dx.Provenance(status=dx.PROV_FITTED, source="s", justification="j",
                       fitted_on="one file", holdout=dx.HOLDOUT_NONE)
    assert not ok.has_holdout


def test_a_holdout_that_says_none_and_then_qualifies_it_is_rejected():
    """This registry's first draft wrote holdout='none: one record, the fit and
    its check are the same file'. It reads as a confession and it compares as a
    holdout, so `fits_without_holdout()` silently missed the cymbal fit -- the
    exact failure the registry exists to prevent, committed while building it."""
    with pytest.raises(ValueError, match="qualifies it"):
        dx.Provenance(status=dx.PROV_FITTED, source="s", justification="j",
                      fitted_on="one file", holdout="none: one record")


def test_only_a_fit_may_carry_fitted_on_or_a_holdout():
    with pytest.raises(ValueError, match="only a fitted"):
        dx.Provenance(status=dx.PROV_MEASURED, source="s", justification="j",
                      n=1, spread="x", date="2026-01-01", fitted_on="a file")


def test_an_entry_without_a_source_or_a_justification_is_rejected():
    with pytest.raises(ValueError, match="needs a source"):
        dx.Provenance(status=dx.PROV_INFERRED, source="  ", justification="j")
    with pytest.raises(ValueError, match="needs a justification"):
        dx.Provenance(status=dx.PROV_INFERRED, source="s", justification="")


def test_a_prose_tag_outside_the_four_is_rejected():
    with pytest.raises(ValueError, match="prose_tag"):
        dx.Provenance(status=dx.PROV_INFERRED, source="s", justification="j",
                      prose_tag="PROBABLY MEASURED")


# ---- an untagged constant is refused, not defaulted --------------------------
def test_a_constant_with_no_record_is_refused_rather_than_defaulted():
    """AMP_TOM is real, is used, and has no evidentiary record: it is balance
    output. Asking for its provenance must raise, because answering anything at
    all would put a status where the absence of one is the finding."""
    with pytest.raises(dx.UntaggedConstant):
        dx.provenance_of("AMP_TOM")
    with pytest.raises(dx.UntaggedConstant):
        dx.provenance_of("NO_SUCH_CONSTANT")
    assert "AMP_TOM" in dx.unregistered_constants()
    assert "TOM_DROP_RATIO" not in dx.unregistered_constants()
    # the message says what to do, not just that it failed
    with pytest.raises(dx.UntaggedConstant, match="PROVENANCE"):
        dx.provenance_of("AMP_TOM")


def test_the_registry_only_names_constants_the_module_actually_defines():
    constants = dx.module_constants()
    for name in dx.PROVENANCE:
        assert name in constants, f"{name} is registered and does not exist"
        assert hasattr(dx, name), name


def test_every_entry_lands_in_exactly_one_status_group():
    groups = dx.constants_by_status()
    assert set(groups) == set(dx.PROV_STATUSES)
    flat = [n for names in groups.values() for n in names]
    assert sorted(flat) == sorted(dx.PROVENANCE)
    assert len(flat) == len(set(flat))


# ---- the comments and the registry must agree -------------------------------
def test_every_prose_tagged_constant_in_the_source_is_registered():
    """No silent drops: the four tags are read back out of the source, so a
    constant given a tagged comment and no record fails here.

    The preconditions are asserted first. A scanner that found nothing would
    pass this test vacuously, which is the unsatisfiable gate's twin."""
    tagged = dx.prose_tagged_constants()
    assert len(tagged) >= 20, f"the tag scanner found only {len(tagged)}"
    for expected in ("BD_HZ", "BD_DECAY_Q", "TOM_DROP_RATIO", "TOM_PRESET",
                     "TOM_HW_TAU", "RS_GATE_TAU", "MA_HP_HZ", "CY_TAU_SHORT",
                     "CY_LO_HZ", "PEAK_RSX"):
        assert expected in tagged, f"{expected} lost its prose tag"
    found = {t for tags in tagged.values() for t in tags}
    assert found == set(dx.PROSE_TAGS), found
    missing = sorted(n for n in tagged if n not in dx.PROVENANCE)
    assert not missing, f"prose-tagged and unregistered: {missing}"


def test_a_recorded_prose_tag_is_one_the_comment_actually_carries():
    """The other direction: an entry may not cite a tag its comment does not
    have. `prose_tag=''` is allowed and means the comment block's tag is not a
    claim about this constant -- PEAK_CLX shares PEAK_RSX's block."""
    tagged = dx.prose_tagged_constants()
    for name, tags in tagged.items():
        recorded = dx.PROVENANCE[name].prose_tag
        if recorded:
            assert recorded in tags, (name, recorded, tags)
    assert dx.PROVENANCE["PEAK_CLX"].prose_tag == ""


def test_the_completeness_check_turns_red_when_a_record_is_removed(monkeypatch):
    """The control for the check above: drop BD_HZ's record and the no-silent-
    drops test must fail, naming it. A gate nobody has seen fail is a gate
    nobody knows is connected."""
    monkeypatch.setattr(dx, "PROVENANCE",
                        {k: v for k, v in dx.PROVENANCE.items() if k != "BD_HZ"})
    with pytest.raises(AssertionError, match="prose-tagged and unregistered"):
        test_every_prose_tagged_constant_in_the_source_is_registered()


def test_the_entries_citing_a_tag_from_further_up_the_paragraph_are_the_known_ones():
    """Thirteen entries cite a tag that documents their GROUP rather than one in
    the comment block directly above them -- the four extra pitch-drop terms,
    the RS high / CL pair, the cymbal levels, CY_DECAY_T20 ('from the same five
    files') and CY_FIT ('what the fit above achieved'). Each was read and is a
    real citation. Pinning the list means the fourteenth has to be read too,
    instead of arriving as a tag nobody checked."""
    tagged = dx.prose_tagged_constants()
    cited_from_above = sorted(n for n, p in dx.PROVENANCE.items()
                              if p.prose_tag and n not in tagged)
    assert cited_from_above == sorted((
        "TOM_DROP_ACCENT_0", "TOM_DROP_ACCENT_0_CONGA",
        "TOM_DROP_TUNING_G", "TOM_DROP_TUNING_G_CONGA",
        "RS_HI_HZ", "RS_HI_Q", "CL_HZ", "CL_Q",
        "PEAK_CYS", "PEAK_CYD", "PEAK_CYL", "CY_DECAY_T20", "CY_FIT")), \
        cited_from_above


def test_the_scanner_attributes_a_tag_to_the_constant_under_it():
    """A grep names a line; this names a constant. Checked on a block whose tag
    is many lines above the assignment (the tom pitch drop's is ~45)."""
    src = ('# INFERRED [a reading]: what it is\nFOO = 1\nBAR = 2\n\n'
           '# plain comment\nBAZ = 3\n')
    tagged = dx.prose_tagged_constants(src)
    assert tagged == {"FOO": ("INFERRED",)}
    assert "TOM_DROP_RATIO" in dx.prose_tagged_constants()


# ---- the query, and the control ---------------------------------------------
def test_the_listing_reaches_every_registered_constant():
    text = "\n".join(dp.listing())
    for name in dx.PROVENANCE:
        assert name in text, name
    for status in dx.PROV_STATUSES:
        assert status in text
    assert "NO HOLDOUT" in text and "HELD OUT" in text
    assert dp.main([]) == 0
    assert dp.main(["--status", "fitted"]) == 0
    assert dp.main(["--no-holdout"]) == 0
    assert dp.main(["--json"]) == 0


def test_the_listing_as_json_carries_the_structured_fields_not_prose():
    doc = dp.as_json()
    entry = doc["constants"]["TOM_HW_TAU"]
    assert entry["status"] == dx.PROV_MEASURED and entry["n"] == 6
    assert entry["date"] == "2026-09-18"
    assert doc["constants"]["CY_TAU_LOW"]["has_holdout"] is False
    assert doc["constants"]["CP_PERIOD"]["has_holdout"] is True
    assert "AMP_TOM" in doc["unregistered"]
    assert set(doc["by_status"]) == set(dx.PROV_STATUSES)


def test_the_register_stream_cannot_depend_on_the_registry(monkeypatch):
    """The migration is a change of medium, and this is why that claim holds
    rather than merely having been checked once: every register write is
    computed from the constants, and emptying the registry moves none of them.

    The before/after comparison itself is in this file's docstring -- 3699
    identical lines across the two trees -- because a two-tree diff is not
    something a unit test can perform."""
    before = dp.register_stream()
    assert len(before) > 3000
    monkeypatch.setattr(dx, "PROVENANCE", {})
    monkeypatch.setattr(dx, "_PROVENANCE_TABLE", ())
    assert dp.register_stream() == before


def test_the_frozen_revision_11_kit_still_reproduces():
    """An independent check on the same claim, and one that predates it: the
    revision-11 kit is frozen by a hash taken before this migration, and
    `kit_808_rev11()` REFUSES if any shared write moved."""
    assert dx._kit_sha256(dx.kit_808_rev11()) == dx.KIT808_REV11_SHA256


def test_the_registry_is_immutable_per_entry():
    p = dx.provenance_of("BD_HZ")
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.status = dx.PROV_MEASURED
