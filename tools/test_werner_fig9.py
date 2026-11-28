"""Known answers and controls for the Werner Figure 9 (tone stage) digitiser.

Figure 9 has **no external known answer** -- W14b prints no component values
for the tone network and no other source states its response -- so these tests
carry more of the weight than Figure 4's do. They do four things:

  * plant structures with known parameters and require the readers to recover
    them (the chaining, the k = 1.0 marker rule, the extrapolation bound);
  * assert the two facts this step exists to establish, so neither can drift
    silently: what Ht3 is, and that Ht1 and Ht2 are NOT plotted in the
    cymbal's own band;
  * break the evidence six ways and require the gate to go red for each. A
    known-answer test nobody has seen fail is not a control;
  * drive the public entry point with a precondition missing and require
    REFUSED and exit 3, never FAIL (#1788). Measured on main @ 03148399,
    red-first: an absent Figure 4 artifact printed its reason on a bare line
    and then `FAIL shared` under `GATE: FAIL`, exit 1; an Ht3 curve too short
    to fit printed `FAIL extrap ... only 31 points lie within 3.0 dB of the
    peak` under `GATE: FAIL`, exit 1. Both are the verdict of a measured
    error over tolerance, and neither measured anything.
"""

from __future__ import annotations

import json
import math
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import exit_contract  # noqa: E402
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


@pytest.mark.parametrize("p1,p2", [(400.0, 1500.0), (128.0, 590.0),
                                   (610.0, 1550.0)])
def test_bp2_poles_recovers_a_planted_real_pole_pair(p1, p2):
    """Every curve in Figure 9 reads Q < 0.5, i.e. two REAL poles -- one RC
    high-pass and one RC low-pass, which is what a passive network builds."""
    f0 = math.sqrt(p1 * p2)
    q = f0 / (p1 + p2)
    got = w9.bp2_poles(f0, q)
    assert abs(got[0] - p1) / p1 < 1e-9
    assert abs(got[1] - p2) / p2 < 1e-9


def test_the_three_window_fits_do_not_share_a_pole_pair(evidence):
    """Three transfer functions of one network to one output node share a
    denominator, so they share poles. The three window fits do not, which is
    the proof that they are local shapes and not pole locations -- and the
    reason Ht1's and Ht2's extrapolations carry a bound."""
    rows = w9.shared_denominator_note(evidence[0])
    pairs = [r[3] for r in rows]
    for i in range(3):
        for j in range(i + 1, 3):
            worst = max(abs(math.log(a / b))
                        for a, b in zip(pairs[i], pairs[j]))
            assert worst > 0.05, (
                f"{rows[i][0]} and {rows[j][0]} agree on a pole pair; if that "
                "ever becomes true the bound below can be tightened")


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
    verdict, lines = w9.check(data, meta)
    assert verdict == exit_contract.PASS, "\n".join(lines)


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


def _gate(data, meta):
    """`check()` on broken evidence, required to have MEASURED the break:
    FAIL, never REFUSED. A control that made the gate refuse would show the
    gate choking on the defect, not detecting it."""
    verdict, lines = w9.check(data, meta)
    assert verdict == exit_contract.FAIL, "\n".join(lines)


def test_control_a_fifteen_db_level_error_fails_the_passivity_gate(evidence):
    data, meta, _ = evidence
    _gate(_bend(data, "Ht2", lambda h, d: (h, d + 15.0)), meta)


def test_control_one_subplots_x_axis_off_by_five_percent_fails_the_gate(evidence):
    """The failure the multi-axes extension introduces: one sub-plot reading
    another's decade labels. The three x axes are derived independently and
    have to agree."""
    data, meta, _ = evidence
    bent = {k: dict(v) for k, v in data.items()}
    bent["Ht1"]["axes_hz"] = [v * 1.05 for v in bent["Ht1"]["axes_hz"]]
    _gate(bent, meta)


def test_control_swapping_ht1_and_ht3_fails_the_prose_gate(evidence):
    data, meta, _ = evidence
    bent = {k: dict(v) for k, v in data.items()}
    bent["Ht1"], bent["Ht3"] = bent["Ht3"], bent["Ht1"]
    _gate(bent, meta)


def test_control_a_disagreeing_marker_offset_fails_the_gate(evidence):
    data, meta, _ = evidence
    _gate(data, {**meta, "marker_agreement_pt": w9.MARKER_AGREE_PT + 1})


def test_control_an_ht3_that_is_not_a_bp2_fails_the_extrapolation_gate(evidence):
    """If the one curve that CAN be checked stopped being a plain 2-pole
    section, the extrapolations for Ht1 and Ht2 would lose their only
    validation, and the gate must go red rather than keep reporting them."""
    data, meta, _ = evidence
    def spoil(h, d):
        return (h, w9._extra_section_db(h, -22.0, 783.0, 0.41, 2500.0, "pole"))
    _gate(_bend(data, "Ht3", spoil), meta)


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


# ---------------------------------------------------------------------------
# A refusal is a third outcome: REFUSED and exit 3, never FAIL
# ---------------------------------------------------------------------------


PASS, FAIL, REFUSED = (exit_contract.PASS, exit_contract.FAIL,
                       exit_contract.REFUSED_ALTERNATE)


def _run(capsys, argv):
    rc = w9.main(argv)
    caught = capsys.readouterr()
    return rc, caught.out, caught.err


def _row(text, name):
    """The gate's one line for check `name`: `<outcome> <name> ...`."""
    hits = [ln for ln in text.splitlines() if ln.split()[1:2] == [name]]
    assert len(hits) == 1, f"expected one {name!r} row in:\n{text}"
    return hits[0]


def _labelled(text, label):
    """Every gate row whose outcome is `label`."""
    return [ln for ln in text.splitlines() if ln.strip().startswith(label + " ")]


def _evidence(tmp_path, *bends):
    """A copy of the committed Figure 9 evidence with each `bend(blob)`
    applied, at a path the CLI can be pointed at."""
    blob = json.loads(w9.ARTIFACT.read_text())
    for bend in bends:
        bend(blob)
    path = tmp_path / "fig9.json"
    path.write_text(json.dumps(blob))
    return path


def _loud_ht2(blob):
    """The +15 dB Ht2 level error of the passivity control, as a file: a
    MEASURED mismatch, which must read FAIL whatever else the gate does."""
    for c in blob["bands"]["Ht2"]["curves"]:
        c["db"] = [d + 15.0 for d in c["db"]]


def _short_ht3(blob):
    """Ht3's k = 1.0 curve cut to its first 49 points: too few in its top 3 dB
    to fit, so the extrapolation control cannot run."""
    band = blob["bands"]["Ht3"]
    c = band["curves"][band["k1_index"]]
    c["hz"], c["db"] = c["hz"][:49], c["db"][:49]


def _absent_fig4(monkeypatch, tmp_path):
    """ONLY `wf.from_artifact()` is redirected, to its own loader with an
    absent path: Figure 4's evidence is missing, so the shared-machinery check
    cannot run, and everything else is the committed evidence."""
    absent = tmp_path / "no-fig4.json"
    load = w9.wf.from_artifact
    monkeypatch.setattr(w9.wf, "from_artifact", lambda: load(absent))
    return absent


@pytest.mark.parametrize("argv", ([], ["--check"]))
def test_the_cli_passes_the_committed_evidence(capsys, argv):
    rc, out, err = _run(capsys, argv)
    assert (rc, err) == (PASS, ""), (rc, out, err)
    assert "GATE: PASS" in out, out
    assert not _labelled(out, "FAIL") and not _labelled(out, "REFUSED"), out


def test_the_cli_refuses_rather_than_fails_on_an_absent_figure4(capsys, monkeypatch, tmp_path):
    """The issue's first reproduction. On main @ 03148399 the reason printed
    on a bare line of its own, then `FAIL shared` under `GATE: FAIL`, exit 1:
    nothing was measured, and the reader was told something was."""
    absent = _absent_fig4(monkeypatch, tmp_path)
    rc, out, _ = _run(capsys, ["--check"])
    row = _row(out, "shared")
    assert row.startswith("REFUSED shared"), row
    assert f"{absent} is absent" in row, row
    assert not _labelled(out, "FAIL"), out
    for name in ("passive", "one-x", "marker", "prose", "extrap"):
        assert _row(out, name).startswith("PASS "), out
    assert "GATE: REFUSED" in out, out
    assert rc == REFUSED, rc


@pytest.mark.parametrize("argv", ([], ["--check"]))
def test_the_default_invocation_gives_the_check_verdict(capsys, monkeypatch, tmp_path, argv):
    """CI runs `--check`; a person runs the tool bare. The two must not
    disagree about a refusal."""
    _absent_fig4(monkeypatch, tmp_path)
    rc, out, _ = _run(capsys, argv)
    assert _row(out, "shared").startswith("REFUSED "), out
    assert "GATE: REFUSED" in out, out
    assert rc == REFUSED, rc


def test_the_cli_refuses_rather_than_fails_on_an_ht3_too_short_to_fit(capsys, tmp_path):
    """The issue's second reproduction. On main @ 03148399: `FAIL extrap ...
    only 31 points lie within 3.0 dB of the peak` under `GATE: FAIL`, exit 1.
    The row must carry the control's own reason, verbatim."""
    path = _evidence(tmp_path, _short_ht3)
    data, _meta, _ = w9.from_artifact(path)
    hz, db = data["Ht3"]["curves"][data["Ht3"]["k1_index"]]
    assert len(hz) == 49
    with pytest.raises(w9.Refused) as raised:
        w9.truncation_control(hz, db)
    rc, out, _ = _run(capsys, ["--check", "--artifact", str(path)])
    row = _row(out, "extrap")
    assert row.startswith("REFUSED extrap"), row
    assert str(raised.value) in row, row
    assert not _labelled(out, "FAIL"), out
    for name in ("shared", "passive", "one-x", "marker", "prose"):
        assert _row(out, name).startswith("PASS "), out
    assert "GATE: REFUSED" in out, out
    assert rc == REFUSED, rc


def test_the_cli_still_fails_a_measured_mismatch(capsys, tmp_path):
    rc, out, _ = _run(capsys, ["--check", "--artifact",
                               str(_evidence(tmp_path, _loud_ht2))])
    assert _row(out, "passive").startswith("FAIL passive"), out
    assert not _labelled(out, "REFUSED"), out
    assert "GATE: FAIL" in out, out
    assert rc == FAIL, rc


def test_a_measured_failure_outranks_a_shared_refusal_beside_it(capsys, monkeypatch, tmp_path):
    """The refusal sits in the FIRST row. It must not hide the later measured
    failure, and the summary must not call a result a refusal."""
    absent = _absent_fig4(monkeypatch, tmp_path)
    rc, out, _ = _run(capsys, ["--check", "--artifact",
                               str(_evidence(tmp_path, _loud_ht2))])
    row = _row(out, "shared")
    assert row.startswith("REFUSED ") and f"{absent} is absent" in row, row
    assert _row(out, "passive").startswith("FAIL passive"), out
    assert "GATE: FAIL" in out and "GATE: REFUSED" not in out, out
    assert rc == FAIL, rc


def test_a_measured_failure_outranks_an_extrap_refusal_beside_it(capsys, tmp_path):
    rc, out, _ = _run(capsys, ["--check", "--artifact",
                               str(_evidence(tmp_path, _short_ht3, _loud_ht2))])
    assert _row(out, "passive").startswith("FAIL passive"), out
    assert _row(out, "extrap").startswith("REFUSED extrap"), out
    assert "GATE: FAIL" in out, out
    assert rc == FAIL, rc


# ---------------------------------------------------------------------------
# The --from-pdf --json writer: both gate guards must PASS before a write
# ---------------------------------------------------------------------------


def _dense(data):
    """A stand-in for the full-resolution curves: each committed curve
    interpolated onto a grid DECIMATE times denser that holds the curve's own
    points at every DECIMATEth index, so `to_artifact`'s decimation returns
    the committed evidence."""
    def up(h, d):
        t = np.linspace(0, len(h) - 1, w9.DECIMATE * (len(h) - 1) + 1)
        return (np.exp(np.interp(t, np.arange(len(h)), np.log(h))),
                np.interp(t, np.arange(len(h)), d))
    return {n: {**v, "curves": [up(h, d) for h, d in v["curves"]]}
            for n, v in data.items()}


def _writer(monkeypatch, data, meta, out):
    """`--from-pdf --json` without the paper, which is not redistributable
    and which CI cannot fetch: the digitiser is replaced by evidence shaped
    exactly like its own output, so both guards run on real `check()` calls
    and only the PDF reader is absent."""
    monkeypatch.setattr(w9.wf, "load_pdf", lambda path, download: b"")
    monkeypatch.setattr(w9, "read_figure", lambda pdf: (data, meta))
    return ["--from-pdf", "--json", str(out)]


def test_the_writer_writes_evidence_that_passes_both_guards(capsys, monkeypatch, tmp_path):
    data, meta, _ = w9.from_artifact()
    out = tmp_path / "out.json"
    rc, stdout, err = _run(capsys, _writer(monkeypatch, _dense(data), meta, out))
    assert (rc, err) == (PASS, ""), (rc, stdout, err)
    assert out.exists() and f"wrote {out}" in stdout, stdout


def test_the_writer_withholds_when_the_full_resolution_gate_refuses(capsys, monkeypatch, tmp_path):
    data, meta, _ = w9.from_artifact()
    absent = _absent_fig4(monkeypatch, tmp_path)
    out = tmp_path / "out.json"
    rc, _, err = _run(capsys, _writer(monkeypatch, _dense(data), meta, out))
    assert not out.exists()
    assert rc == REFUSED, err
    assert err.startswith("REFUSED:"), err
    assert "REFUSED shared" in err and f"{absent} is absent" in err, err
    assert not _labelled(err, "FAIL"), err


def test_the_writer_withholds_when_the_decimated_gate_refuses(capsys, monkeypatch, tmp_path):
    """The committed evidence passes on its own curves and refuses once
    decimated again: Ht3's k = 1.0 curve keeps 11 of its 62 top-3 dB points,
    under the 50 the control needs. The first guard passes; the second must
    withhold, and say which stage refused."""
    data, meta, _ = w9.from_artifact()
    assert w9.check(data, meta)[0] == PASS, "precondition: the committed evidence passes"
    out = tmp_path / "out.json"
    rc, _, err = _run(capsys, _writer(monkeypatch, data, meta, out))
    assert not out.exists()
    assert rc == REFUSED, err
    assert err.startswith("REFUSED:"), err
    assert "REFUSED extrap" in err and "too few to run the truncation control" in err, err
    assert "decimated (every 6th point)" in err and "full resolution" not in err, err


def test_the_writer_withholds_on_a_measured_failure_and_calls_it_fail(
        capsys, monkeypatch, tmp_path):
    """Withheld is not the same as refused. A measured failure is a result
    and keeps FAIL's word and code, so it is never hidden in the family that
    means `nothing was measured`."""
    data, meta, _ = w9.from_artifact()
    loud = _bend(data, "Ht2", lambda h, d: (h, d + 15.0))
    out = tmp_path / "out.json"
    rc, _, err = _run(capsys, _writer(monkeypatch, _dense(loud), meta, out))
    assert not out.exists()
    assert rc == FAIL, err
    assert err.startswith("FAIL:"), err
    assert "FAIL passive" in err, err
    assert not _labelled(err, "REFUSED") and not err.startswith("REFUSED:"), err
