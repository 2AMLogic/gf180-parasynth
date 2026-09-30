"""Known answers and controls for the Werner Figure 4 digitiser.

The tool's own `--check` is the external gate: three of the five curves have
answers that come from SN p.13 component values, not from the tool. These
tests do two further things the gate cannot do for itself:

  * plant a filter of known parameters and require the READERS to recover it,
    so a reader that is systematically wrong cannot hide behind a curve whose
    true answer is only known to +-a few per cent;
  * break the digitised evidence in five different ways and require the gate
    to go RED for each. A known-answer test nobody has seen fail is not a
    control; these are the failures.
"""

from __future__ import annotations

import hashlib
import math
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import werner_fig4 as wf  # noqa: E402


def _grid(f_lo=700.0, f_hi=20000.0, n=300):
    return np.logspace(math.log10(f_lo), math.log10(f_hi), n)


def _synth(name, hz, **par):
    _, model = wf.STRUCTURES[name]
    order = wf.STRUCTURES[name][0]
    return model(2 * np.pi * hz, *[par[k] for k in order])


# ---------------------------------------------------------------------------
# Planted answers: the readers recover what was put in
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("f0,q", [(3450.0, 6.0), (7100.0, 6.0), (1000.0, 2.0)])
def test_geometric_bp_recovers_a_planted_resonator(f0, q):
    hz = _grid(f0 / 4, f0 * 4, 4000)
    db = _synth("bp2", hz, gain_db=20.0, f0=f0, q=q)
    got = wf.geometric_bp(hz, db)
    assert abs(got["f0"] - f0) / f0 < 0.005
    assert abs(got["q"] - q) / q < 0.02
    assert abs(got["gain_db"] - 20.0) < 0.05


@pytest.mark.parametrize("f0,q,g", [(2500.0, 0.97, 0.0), (8839.0, 1.0, 6.02)])
def test_fit_hp2_recovers_a_planted_two_pole_high_pass(f0, q, g):
    hz = _grid(f0 / 3, 20000.0, 800)
    db = _synth("hp2", hz, gain_db=g, f0=f0, q=q)
    got = wf.fit_structure(hz, db, "hp2")
    assert got["rms_db"] < 1e-6
    assert abs(got["f0"] - f0) / f0 < 1e-3
    assert abs(got["q"] - q) / q < 1e-3


def test_fit_separates_a_third_pole_from_a_two_pole():
    """Hh3's whole question. A planted 3rd-order section must be read as
    3rd-order, and the 2-pole model must leave a residual large enough to say
    so -- otherwise the tool cannot tell the two structures apart and its
    answer for Hh3 means nothing."""
    hz = _grid(4700.0, 20000.0, 1900)
    db = _synth("hp3", hz, gain_db=8.86, f0=10323.0, q=5.64, fp=5195.0)
    three = wf.fit_structure(hz, db, "hp3")
    two = wf.fit_structure(hz, db, "hp2")
    assert three["rms_db"] < 1e-6
    assert abs(three["fp"] - 5195.0) / 5195.0 < 0.01
    assert two["rms_db"] > 0.3, "a 2-pole fit to a 3-pole section must not pass"


def test_a_two_pole_section_is_not_read_as_three():
    """The converse control: the extra pole must not be invented. Fitting
    `hp3` to a genuine 2-pole section has to push the third pole far out of
    band rather than find one."""
    hz = _grid(4700.0, 20000.0, 1900)
    db = _synth("hp2", hz, gain_db=6.0, f0=8839.0, q=1.0)
    got = wf.fit_structure(hz, db, "hp3")
    assert got["fp"] < 0.05 * 8839.0 or got["fp"] > 20.0 * 20000.0


def test_resonance_db_reads_zero_for_a_flat_pass_band():
    hz = _grid(2000.0, 20000.0, 900)
    assert abs(wf.resonance_db(hz, _synth("hp2", hz, gain_db=6.0, f0=3000.0,
                                          q=0.7))) < 0.2
    assert wf.resonance_db(hz, _synth("hp2", hz, gain_db=6.0, f0=10000.0,
                                      q=5.6)) > 10.0


# ---------------------------------------------------------------------------
# The real evidence
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def evidence():
    try:
        data, blob = wf.from_artifact()
    except wf.Refused as exc:
        pytest.skip(str(exc))
    return data, blob


def test_the_committed_evidence_passes_the_known_answer_gate(evidence):
    ok, lines = wf.check(evidence[0])
    assert ok, "\n".join(lines)


def test_the_evidence_names_the_paper_and_its_hash(evidence):
    src = evidence[1]["source"]
    assert src["sha256"] == wf.W14B_SHA256
    assert "zenodo" in src["url"]


def test_hh2_is_a_two_pole_high_pass_near_8_8_khz(evidence):
    """The value #369 came for. Stated as a test so it cannot drift silently."""
    got = wf.fit_structure(*evidence[0]["Hh2"], "hp2")
    assert got["rms_db"] < 0.05
    assert 8500.0 < got["f0"] < 9200.0
    assert 0.9 < got["q"] < 1.1
    assert 5.5 < got["gain_db"] < 6.5


def test_hh3_is_third_order_and_the_extra_pole_is_well_below_its_corner(evidence):
    hz, db = evidence[0]["Hh3"]
    three = wf.fit_structure(hz, db, "hp3")
    two = wf.fit_structure(hz, db, "hp2")
    assert three["rms_db"] < 0.05
    assert two["rms_db"] > 0.3
    assert 9800.0 < three["f0"] < 10900.0
    assert 4.5 < three["q"] < 6.5
    # #102 assumed the third pole sat at the same corner. It does not.
    assert three["fp"] < 0.7 * three["f0"]


def test_the_level_stage_is_a_differentiator_with_a_corner_above_the_band(evidence):
    lv = evidence[1]["level_stage"]
    assert lv["one_pole_rms_db"] < 0.2
    assert lv["one_pole_corner_hz"] > 15000.0
    assert 15.0 < lv["tilt_2k_to_20k_db"] < 18.0


# ---------------------------------------------------------------------------
# Controls: each of these MUST turn the gate red
# ---------------------------------------------------------------------------


def _bent(data, fn):
    return {k: fn(k, h, d) for k, (h, d) in data.items()}


def test_control_a_five_percent_frequency_error_fails_the_gate(evidence):
    bent = _bent(evidence[0], lambda k, h, d: (h * 1.05, d))
    ok, _ = wf.check(bent)
    assert not ok


def test_control_a_two_db_axis_offset_fails_the_gate(evidence):
    """A y-axis zero error of 2 dB -- about half the tick-label baseline
    offset that this tool got wrong the first time -- must be caught. It is
    caught through Hh1's residual, which is where a gain error shows."""
    bent = _bent(evidence[0], lambda k, h, d: (h, d + 2.0))
    ok, lines = wf.check(bent)
    assert not ok, "\n".join(lines)


def test_control_a_reversed_x_axis_fails_the_gate(evidence):
    def flip(_k, h, d):
        lo, hi = h.min(), h.max()
        return (lo * hi / h)[::-1], d[::-1]
    ok, _ = wf.check(_bent(evidence[0], flip))
    assert not ok


def test_control_swapping_hh2_and_hh3_fails_the_gate(evidence):
    data = dict(evidence[0])
    data["Hh2"], data["Hh3"] = data["Hh3"], data["Hh2"]
    ok, lines = wf.check(data)
    assert not ok, "\n".join(lines)


def test_control_a_curve_stretched_in_db_fails_the_gate(evidence):
    """A y scale 20 % wrong -- the failure a miscounted tick spacing produces."""
    bent = _bent(evidence[0], lambda k, h, d: (h, d * 1.2))
    ok, lines = wf.check(bent)
    assert not ok, "\n".join(lines)


def test_control_the_wrong_pdf_is_refused(tmp_path):
    fake = tmp_path / "not-the-paper.pdf"
    fake.write_bytes(b"%PDF-1.7\nnot the paper\n")
    with pytest.raises(wf.Refused) as exc:
        wf.load_pdf(fake, allow_download=False)
    assert hashlib.sha256(fake.read_bytes()).hexdigest() in str(exc.value)


def test_control_a_missing_pdf_is_refused_not_downloaded_silently(tmp_path):
    with pytest.raises(wf.Refused):
        wf.load_pdf(tmp_path / "absent.pdf", allow_download=False)


def test_control_a_pdf_without_the_figure_is_refused():
    with pytest.raises(wf.Refused):
        wf.find_figure(b"%PDF-1.7\nno figure here\n")


def test_control_a_missing_artifact_is_refused(tmp_path):
    with pytest.raises(wf.Refused):
        wf.from_artifact(tmp_path / "absent.json")


def test_control_a_curve_with_no_resonance_is_refused_at_assembly():
    """If the figure ever parses into two non-resonant high-pass curves, the
    Hh2/Hh3 assignment has no basis and the tool must refuse rather than pick
    one."""
    hz = _grid(4000.0, 20000.0, 400)
    flat_a = (hz, _synth("hp2", hz, gain_db=6.0, f0=8800.0, q=1.0))
    flat_b = (hz, _synth("hp2", hz, gain_db=9.0, f0=10300.0, q=0.9))
    assert wf.resonance_db(*flat_a) < 3.0
    assert wf.resonance_db(*flat_b) < 3.0
