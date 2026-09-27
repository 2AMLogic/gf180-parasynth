"""Known answers and controls for the Werner Figure 9 (tone stage) digitiser.

Figure 9 has **no external known answer** -- W14b prints no component values
for the tone network and no other source states its response -- so these tests
carry more of the weight than Figure 4's do. They do three things:

  * plant structures with known parameters and require the readers to recover
    them (the chaining, the k = 1.0 marker rule, the extrapolation bound);
  * assert the two facts this step exists to establish, so neither can drift
    silently: what Ht3 is, and that Ht1 and Ht2 are NOT plotted in the
    cymbal's own band;
  * break the evidence six ways and require the gate to go red for each. A
    known-answer test nobody has seen fail is not a control.
"""

from __future__ import annotations

import math
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import werner_fig4 as wf  # noqa: E402
import werner_fig9 as w9  # noqa: E402


def _grid(f_lo, f_hi, n=1200):
    return np.logspace(math.log10(f_lo), math.log10(f_hi), n)


# ---------------------------------------------------------------------------
# Planted answers
# ---------------------------------------------------------------------------


def test_chain_reassembles_a_polyline_split_into_shuffled_chunks():
    """MATLAB emits one curve as 300-point chunks, interleaved with the other
    fourteen curves' chunks and sometimes reversed. `werner_fig4._join` only
    looks at the chunk immediately before; this must not care about order."""
    pts = [(float(i), float(i * i % 37)) for i in range(900)]
    chunks = [pts[0:300], pts[299:600], pts[599:900]]
    scrambled = [list(reversed(chunks[2])), chunks[0], chunks[1]]
    runs = [r for r in w9.chain(scrambled) if len(r) > 50]
    assert len(runs) == 1
    assert len(runs[0]) == 900
    assert sorted(runs[0]) == sorted(pts)


def _planted_family(f0s, gains, q=0.4):
    hz = _grid(20.0, 20000.0, 600)
    out = []
    for f0, g in zip(f0s, gains):
        db = w9.bp2_db(hz, g, f0, q)
        out.append((hz, db, math.log10(f0) * 100.0, g * 10.0))
    out.sort(key=lambda t: -t[3])
    return out


def test_identify_marked_recovers_a_planted_offset_and_its_members():
    """The k = 1.0 rule: one glyph offset has to fit all three asterisks. The
    planted answer deliberately marks the TOP member of one family and the
    BOTTOM member of the other two, which is the real figure's pattern and the
    one a 'the marked curve is the top one' shortcut gets wrong twice."""
    fams = [_planted_family([700, 800, 900], [-22, -28, -35]),
            _planted_family([950, 960, 970], [-14.4, -14.5, -15.1]),
            _planted_family([265, 266, 274], [-25.5, -25.6, -26.4])]
    want = (0, 2, 2)
    offset = (7.3, 18.9)
    origins = [(fams[i][p][2] - offset[0], fams[i][p][3] - offset[1])
               for i, p in enumerate(want)]
    pick, vecs, spread = w9.identify_marked(fams, origins)
    assert pick == want
    assert spread < 1e-9
    assert abs(vecs[0][0] - offset[0]) < 1e-9
    assert abs(vecs[0][1] - offset[1]) < 1e-9


def test_control_the_topmost_curve_everywhere_does_not_fit_one_glyph_offset():
    """The converse of the test above, and the control that matters: the
    shortcut assignment must be rejected, not merely scored lower."""
    fams = [_planted_family([700, 800, 900], [-22, -28, -35]),
            _planted_family([950, 960, 970], [-14.4, -14.5, -15.1]),
            _planted_family([265, 266, 274], [-25.5, -25.6, -26.4])]
    offset = (7.3, 18.9)
    origins = [(fams[i][p][2] - offset[0], fams[i][p][3] - offset[1])
               for i, p in enumerate((0, 2, 2))]
    vecs = [(fams[i][0][2] - origins[i][0], fams[i][0][3] - origins[i][1])
            for i in range(3)]
    spread = max(math.hypot(a[0] - b[0], a[1] - b[1])
                 for a in vecs for b in vecs)
    assert spread > w9.MARKER_AGREE_PT


def test_control_two_identical_families_make_the_marker_ambiguous():
    fams = [_planted_family([700, 700], [-22, -22]) for _ in range(3)]
    origins = [(fams[i][0][2] - 7.3, fams[i][0][3] - 18.9) for i in range(3)]
    with pytest.raises(w9.Refused):
        w9.identify_marked(fams, origins)


def test_truncation_control_passes_on_a_true_two_pole_band_pass():
    hz = _grid(20.0, 20000.0, 2000)
    db = w9.bp2_db(hz, -22.0, 783.0, 0.41)
    got = w9.truncation_control(hz, db)
    assert got["worst_error_db"] < 1e-3


def test_control_truncation_control_fails_on_a_section_that_is_not_a_bp2():
    """If Ht3 were NOT a plain 2-pole band-pass, fitting its top 3 dB would
    mispredict its own tail -- and the gate must say so rather than reporting
    the extrapolated number for Ht1 and Ht2 as if it were safe."""
    hz = _grid(20.0, 20000.0, 2000)
    db = w9._extra_section_db(hz, -22.0, 783.0, 0.41, 2500.0, "pole")
    got = w9.truncation_control(hz, db)
    assert got["worst_error_db"] > w9.TRUNCATION_TOL_DB


def test_extrapolation_bound_brackets_a_planted_extra_pole():
    """A 3 dB window cannot tell a bp2 from a bp2 with a pole above the
    window. The bound has to contain the truth that the plain fit misses."""
    hz = _grid(560.0, 1650.0, 900)
    truth_at = w9._extra_section_db(np.array([7100.0]), -15.0, 970.0, 0.45,
                                    4000.0, "pole")[0]
    db = w9._extra_section_db(hz, -15.0, 970.0, 0.45, 4000.0, "pole")
    b = w9.extrapolation_bound(hz, db, 7100.0)
    assert b["lo_db"] <= truth_at <= b["hi_db"]
    assert b["hi_db"] - b["lo_db"] > 2.0, "a 3 dB window is not this decisive"


def test_extrapolation_bound_is_narrow_where_the_curve_is_fully_plotted():
    hz = _grid(20.0, 20000.0, 2000)
    db = w9.bp2_db(hz, -22.0, 783.0, 0.41)
    b = w9.extrapolation_bound(hz, db, 7100.0)
    assert b["hi_db"] - b["lo_db"] < 0.5


def test_name_families_follows_the_prose_over_a_wrong_legend():
    """The real figure's legend reads t3, t2, t3 -- `t3` twice and no `t1`.
    Naming must come out Ht3 / Ht2 / Ht1 and must SAY that it overrode a
    label, not quietly renumber."""
    names, notes = w9.name_families([28.7, 0.7, 0.9], ["t3", "t2", "t3"])
    assert names == ["Ht3", "Ht2", "Ht1"]
    assert notes and "elimination" in notes[0]


def test_control_a_legend_that_contradicts_the_prose_is_reported():
    """If the sub-plot the tone control moves most were labelled Ht1, the
    prose would still name it Ht3 -- and the override has to be printed, not
    swallowed."""
    names, notes = w9.name_families([0.9, 0.7, 28.7], ["t3", "t2", "t1"])
    assert names[2] == "Ht3"
    assert any("prose is taken over the legend" in n for n in notes)


def test_control_a_family_with_no_k_dependence_is_refused():
    with pytest.raises(w9.Refused):
        w9.name_families([0.9, 0.7, 0.8], ["t1", "t2", "t3"])


def test_control_two_families_varying_a_lot_are_refused():
    with pytest.raises(w9.Refused):
        w9.name_families([28.0, 0.7, 25.0], ["t1", "t2", "t3"])


# ---------------------------------------------------------------------------
# The real evidence
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def evidence():
    try:
        return w9.from_artifact()
    except w9.Refused as exc:
        pytest.skip(str(exc))


def test_the_committed_evidence_passes_the_gate(evidence):
    data, meta, _ = evidence
    ok, lines = w9.check(data, meta)
    assert ok, "\n".join(lines)


def test_the_evidence_names_the_paper_and_its_hash(evidence):
    src = evidence[2]["source"]
    assert src["sha256"] == wf.W14B_SHA256
    assert "zenodo" in src["url"]


def test_ht3_at_k_one_is_a_two_pole_band_pass_near_780_hz(evidence):
    """The value #390 came for, and the only one of the three that is MEASURED
    rather than extrapolated: Ht3's sub-plot is 36 dB tall, so its curve is
    plotted across the whole 20 Hz - 20 kHz axis."""
    data = evidence[0]
    hz, db = data["Ht3"]["curves"][data["Ht3"]["k1_index"]]
    assert hz.min() < 25.0 and hz.max() > 19000.0
    fit = w9.fit_bp2(hz, db)
    assert fit["rms_db"] < 0.1, "a fifth-order function, but a bp2 to 0.05 dB"
    assert 740.0 < fit["f0"] < 830.0
    assert 0.38 < fit["q"] < 0.44
    assert -23.0 < fit["gain_db"] < -21.0


def test_the_tone_stage_cancels_the_level_stages_rising_slope(evidence):
    """The headline. W14b Fig. 10 puts the LEVEL buffer at +16.6 dB across
    2-20 kHz; Ht3 measures -17.7 dB across the same span, so the two nearly
    cancel. The model has the first and not the second."""
    data = evidence[0]
    hz, db = data["Ht3"]["curves"][data["Ht3"]["k1_index"]]
    at = lambda f: float(np.interp(math.log10(f), np.log10(hz), db))  # noqa: E731
    tone_tilt = at(19000.0) - at(2000.0)
    assert -19.0 < tone_tilt < -16.0
    level_tilt = 16.6  # tools/werner_fig4.py --level, reference §10
    assert abs(tone_tilt + level_tilt) < 3.0


def test_k_equals_one_is_not_the_topmost_curve_in_two_of_three_bands(evidence):
    """#390 asked for this explicitly. Assuming 'topmost' would put Ht1 and
    Ht2 about 1 dB too loud and, worse, would say the tone control makes the
    first two bands louder as it opens when it makes them quieter."""
    data = evidence[0]
    assert data["Ht3"]["k1_index"] == 0
    assert data["Ht2"]["k1_index"] == len(data["Ht2"]["curves"]) - 1
    assert data["Ht1"]["k1_index"] == len(data["Ht1"]["curves"]) - 1


def test_ht1_and_ht2_are_not_plotted_anywhere_near_the_cymbals_band(evidence):
    """The limitation, as a test so it cannot be quietly forgotten. Figure 9
    gives Ht1 and Ht2 on 4 dB and 3 dB tall axes, so they leave the plot far
    below the 3.45 kHz and 7.1 kHz band-passes. Any value quoted for them up
    there is an extrapolation, and `extrapolation_bound` says how wide."""
    data = evidence[0]
    for name, ceiling in (("Ht1", 900.0), ("Ht2", 2200.0)):
        hz, _db = data[name]["curves"][data[name]["k1_index"]]
        assert hz.max() < ceiling
    hz3, _ = data["Ht3"]["curves"][data["Ht3"]["k1_index"]]
    assert hz3.max() > 19000.0


def test_the_inter_band_levels_are_reported_as_uncertain_not_as_measured(evidence):
    """Ht1's and Ht2's values at 7.1 kHz are what an inter-band balance claim
    would rest on, and the figure does not determine them to better than
    several dB. If this ever narrows below 4 dB, something has been assumed."""
    data = evidence[0]
    for name in ("Ht1", "Ht2"):
        hz, db = data[name]["curves"][data[name]["k1_index"]]
        b = w9.extrapolation_bound(hz, db, 7100.0)
        assert b["hi_db"] - b["lo_db"] > 4.0
        assert b["n_alternatives"] > 10


# ---------------------------------------------------------------------------
# Controls: each of these MUST turn the gate red
# ---------------------------------------------------------------------------


def _bend(data, name, fn):
    out = {k: dict(v) for k, v in data.items()}
    out[name]["curves"] = [fn(h, d) for h, d in out[name]["curves"]]
    return out


def test_control_a_fifteen_db_level_error_fails_the_passivity_gate(evidence):
    data, meta, _ = evidence
    ok, lines = w9.check(_bend(data, "Ht2", lambda h, d: (h, d + 15.0)), meta)
    assert not ok, "\n".join(lines)


def test_control_one_subplots_x_axis_off_by_five_percent_fails_the_gate(evidence):
    """The failure the multi-axes extension introduces: one sub-plot reading
    another's decade labels. The three x axes are derived independently and
    have to agree."""
    data, meta, _ = evidence
    bent = {k: dict(v) for k, v in data.items()}
    bent["Ht1"]["axes_hz"] = [v * 1.05 for v in bent["Ht1"]["axes_hz"]]
    ok, lines = w9.check(bent, meta)
    assert not ok, "\n".join(lines)


def test_control_swapping_ht1_and_ht3_fails_the_prose_gate(evidence):
    data, meta, _ = evidence
    bent = {k: dict(v) for k, v in data.items()}
    bent["Ht1"], bent["Ht3"] = bent["Ht3"], bent["Ht1"]
    ok, lines = w9.check(bent, meta)
    assert not ok, "\n".join(lines)


def test_control_a_disagreeing_marker_offset_fails_the_gate(evidence):
    data, meta, _ = evidence
    ok, lines = w9.check(data, {**meta,
                                "marker_agreement_pt": w9.MARKER_AGREE_PT + 1})
    assert not ok, "\n".join(lines)


def test_control_an_ht3_that_is_not_a_bp2_fails_the_extrapolation_gate(evidence):
    """If the one curve that CAN be checked stopped being a plain 2-pole
    section, the extrapolations for Ht1 and Ht2 would lose their only
    validation, and the gate must go red rather than keep reporting them."""
    data, meta, _ = evidence
    def spoil(h, d):
        return (h, w9._extra_section_db(h, -22.0, 783.0, 0.41, 2500.0, "pole"))
    ok, lines = w9.check(_bend(data, "Ht3", spoil), meta)
    assert not ok, "\n".join(lines)


def test_control_a_figure_with_the_wrong_asterisk_count_is_refused():
    """Figure 10's family carries one asterisk, Figure 9's three. Reading one
    for the other is a whole different measurement."""
    with pytest.raises(w9.Refused):
        w9.find_figure(b"%PDF-1.7\nno figure here\n")


def test_control_a_missing_artifact_is_refused(tmp_path):
    with pytest.raises(w9.Refused):
        w9.from_artifact(tmp_path / "absent.json")


def test_control_two_axes_boxes_where_three_are_needed_is_refused():
    rects = [((0.0, 0.0), (100.0, 100.0)),
             ((10.0, 10.0), (90.0, 40.0)),
             ((10.0, 50.0), (90.0, 80.0))]
    with pytest.raises(w9.Refused):
        w9._plot_boxes(rects)
