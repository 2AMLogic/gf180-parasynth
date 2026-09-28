#!/usr/bin/env python3
"""Tests for `tools/cymbal_vca_drive.py` -- the swing-VCA drive read, SN p.13.

Three kinds of test, in the order they carry weight:

  1. KNOWN ANSWERS THAT DO NOT COME FROM THIS MODULE. The band-pass peak gains
     must reproduce W14b Figure 4's digitised values (`model/cymbal_candidate
     .BP_PEAK_DB`), and the f0/Q of both band-passes and of Hh1 must reproduce
     reference §10. Nothing about Figure 4 or §10 informed the component values
     read here, and in particular nothing anywhere in this repository carried
     the band-passes' INPUT networks before this module did -- so the peak-gain
     agreement is an external check on the part of the read that is new.
  2. CONTROLS THAT MUST FAIL. Seven injected defects, each one a change to a
     component value or a wire, each required to turn at least one property
     red; four blindness assertions, each a property that must NOT move under
     a defect it has no business seeing. A known answer nobody has seen fail is
     not a control.
  3. REFUSALS. The pinned scan, and the committed balance record this module's
     headline is quoted against.
"""

from __future__ import annotations

import json
import math
import pathlib
import re
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for p in (ROOT / "tools", ROOT / "model"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import cymbal_candidate as cc          # noqa: E402
import cymbal_vca_drive as vd          # noqa: E402
import tone_stage_schematic as ts      # noqa: E402
import werner_fig4 as wf               # noqa: E402

GRID = np.geomspace(300.0, 30000.0, 601)


# ---------------------------------------------------------------------------
# 1. Known answers that do not come from this module
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("which", ["low", "high"])
def test_peak_gain_reproduces_werner_figure_4(which):
    """The external known answer, and the only test of the input networks.

    C10/R52 and C11/R55 appear in no other document here. They set the
    filters' absolute gain and nothing else in the read does. Figure 4 was
    digitised from a different artifact by a different author with no
    knowledge of them.
    """
    _, db = vd.bandpass_peak(which)
    assert abs(db - cc.BP_PEAK_DB[which]) <= vd.PEAK_TOL_DB, (
        f"{which}: schematic {db:.3f} dB vs Figure 4 {cc.BP_PEAK_DB[which]:.2f} dB")


@pytest.mark.parametrize("which,f0", [("low", 3450.0), ("high", 7100.0)])
def test_f0_reproduces_reference_section_10(which, f0):
    got, _ = vd.bandpass_f0_q(which)
    assert abs(got - f0) / f0 <= vd.F0_TOL, f"{which}: {got:.1f} Hz vs {f0} Hz"


@pytest.mark.parametrize("which", ["low", "high"])
def test_q_reproduces_reference_section_10(which):
    _, q = vd.bandpass_f0_q(which)
    assert abs(q - 6.0) / 6.0 <= vd.Q_TOL, f"{which}: Q {q:.3f} vs 6"


def test_hh1_reproduces_reference_section_10():
    """Hh1 is the network the upper-bound convention depends on, so its own
    values get a known answer rather than being trusted because §10 has them."""
    f0, q = vd.hh1_f0_q()
    assert abs(f0 - 2500.0) / 2500.0 <= vd.F0_TOL, f0
    assert abs(q - 0.97) / 0.97 <= vd.Q_TOL, q


def test_the_two_band_pass_formulations_agree():
    """A hand-eliminated expression and an explicit (G + sC) v = b solve.

    Two formulations where one would do, for the same reason
    `tone_stage_schematic` carries two: the hand elimination is the one a
    reader can check, and the MNA build is the one that is hard to get subtly
    wrong. Agreement makes the hand step a checked one.
    """
    for which in ("low", "high"):
        a = vd._db(vd.bandpass_response(which, GRID))
        b = vd._db(vd.bandpass_response_mna(which, GRID))
        assert float(np.max(np.abs(a - b))) < 1e-9, which


def test_control_the_formulations_would_notice_a_swapped_component():
    """...and that agreement is not vacuous.

    Perturb the shunt resistor inside ONE formulation only. If the two builds
    were secretly the same code path the difference would stay at 1e-14.
    """
    cfg = vd.config()
    bad = {"stages": cfg["stages"], "hh1": cfg["hh1"], "defect": None,
           "bandpass": {k: dict(v) for k, v in cfg["bandpass"].items()}}
    bad["bandpass"]["low"]["shunt_ohm"] = 5600.0
    a = vd._db(vd.bandpass_response("low", GRID, cfg))
    b = vd._db(vd.bandpass_response_mna("low", GRID, bad))
    assert float(np.max(np.abs(a - b))) > 5.0


# ---------------------------------------------------------------------------
# 2. The structural finding
# ---------------------------------------------------------------------------


def test_the_three_input_stages_are_component_identical():
    st = vd.config()["stages"]
    for field in ("couple_f", "bias_ohm", "emitter_ohm"):
        vals = {b: st[b][field] for b in vd.BANDS}
        assert len(set(vals.values())) == 1, f"{field}: {vals}"


def test_the_two_high_bands_hang_on_one_node():
    st = vd.config()["stages"]
    assert st["short"]["feed"] == st["decay"]["feed"] == "IC3 pin 7"
    assert st["low"]["feed"] == "IC3 pin 1"


def test_only_the_collector_load_differs():
    diff = vd.stage_differences()
    assert diff["differing"] == ["load_ohm"], diff


def test_the_base_bias_is_a_series_pair_not_a_divider():
    """Load-bearing, and it is the thing I first read wrong.

    A 1M/1M divider to ground would fix each base's VOLTAGE, and the three
    stages' collector currents would then depend on Vbe spread. Two 1M in
    SERIES from B1 with no ground leg fixes each base's CURRENT, so all three
    stages carry the same Ic and the same gm, and the gain ratio is exactly
    the collector-load ratio. The read is 2 MΩ, not 500 kΩ.
    """
    for b in vd.BANDS:
        st = vd.STAGES[b]
        assert st["bias_ohm"] == pytest.approx(2.0e6)
        assert len(st["bias"]) == 2 and all("1M" in x for x in st["bias"])


def test_the_vca_term_is_exactly_the_collector_load_ratio():
    term = vd.vca_term_db()
    assert term["chain_db"]["short"] == pytest.approx(20 * math.log10(39 / 22), abs=1e-3)
    assert term["chain_db"]["decay"] == pytest.approx(20 * math.log10(33 / 22), abs=1e-3)
    assert term["chain_db"]["low"] == pytest.approx(0.0, abs=1e-9)


def test_the_loaded_convention_is_an_upper_bound_on_the_unloaded_one():
    """Loading the low band and not the high bands can only RAISE the ratio.

    That is the direction that matters: the headline is that the VCA term is
    too SMALL to close the gap, so it must be argued against the largest
    admissible value, not the most likely one.
    """
    term = vd.vca_term_db()
    for b in ("short", "decay"):
        assert term["bound_db"][b] > term["chain_db"][b]


def test_the_short_band_would_need_a_collector_load_the_schematic_does_not_print():
    need = vd.required_collector_load()
    assert need["short"]["printed_ohm"] == 39e3
    assert need["short"]["required_ohm"] == pytest.approx(1.79e6, rel=0.01)
    assert need["short"]["shortfall_db"] > 30.0


def test_the_decay_bands_gap_is_the_one_the_vca_nearly_reaches():
    """Reported because it is the half that does NOT support the headline.

    The DECAY band's gap is 9.85 dB and the upper bound supplies 7.26 dB. The
    conclusion "the VCA section is not the missing factor" is carried by the
    SHORT band alone, and saying so is the difference between a finding and an
    overstatement.
    """
    term = vd.vca_term_db()
    gap = vd.balance_targets()["gap_db"]
    assert gap["decay"] - term["bound_db"]["decay"] < 3.0
    assert gap["short"] - term["bound_db"]["short"] > vd.GAP_MARGIN_DB


# ---------------------------------------------------------------------------
# 3. Controls that must fail, and blindness
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("defect", vd.DEFECTS)
def test_every_injected_defect_turns_a_property_red(defect):
    targets = vd.balance_targets()
    got = vd.properties(vd.config(defect), targets)
    red = [n for n, p in got.items() if not p["ok"]]
    assert red, f"{defect} turned nothing red"


@pytest.mark.parametrize("defect,blind", sorted(vd.BLIND.items()))
def test_asserted_blindness_is_verified_blind(defect, blind):
    targets = vd.balance_targets()
    got = vd.properties(vd.config(defect), targets)
    moved = [n for n in blind if not got[n]["ok"]]
    assert not moved, f"{defect} moved {moved}, which was asserted blind"


def test_the_input_network_is_load_bearing_and_only_the_peak_sees_it():
    """The paired positive for the blindness assertion above.

    `DROP_INPUT_NETWORK` must move the peak gain by a lot -- otherwise the
    external known answer is not testing the input read at all, and the
    blindness of f0/Q is a statement about nothing.
    """
    base = vd.bandpass_peak("high")[1]
    hurt = vd.bandpass_peak("high", vd.config("DROP_INPUT_NETWORK"))[1]
    assert abs(hurt - base) > 5.0, (base, hurt)


def test_every_defect_is_a_component_change_not_a_bound_change():
    """A defect that relaxed a bound would make the control vacuous."""
    base = vd.config()
    for d in vd.DEFECTS:
        cfg = vd.config(d)
        assert set(cfg) == set(base)
        assert cfg["defect"] == d
    assert vd.PEAK_TOL_DB == 0.05 and vd.GAP_MARGIN_DB == 20.0


def test_an_unknown_defect_refuses():
    with pytest.raises(vd.Refused):
        vd.config("NOT_A_DEFECT")


def test_the_gate_is_green_on_the_committed_read():
    ok, lines = vd.check()
    assert ok, "\n".join(lines)


# ---------------------------------------------------------------------------
# 4. Refusals -- preconditions asserted at the point of use
# ---------------------------------------------------------------------------


def test_verify_source_refuses_when_the_scan_is_absent(tmp_path):
    with pytest.raises(vd.SourceUnavailable):
        vd.verify_source(tmp_path / "nope.pdf")


def test_verify_source_refuses_on_a_hash_mismatch(tmp_path):
    p = tmp_path / "sn.pdf"
    p.write_bytes(b"%PDF-1.4 not the service notes")
    with pytest.raises(vd.SourceUnavailable) as exc:
        vd.verify_source(p)
    assert "not the pinned scan" in str(exc.value)


def test_require_source_is_reachable_from_the_command_line(tmp_path, capsys):
    """The gate step 9 documented and `main()` never read (#369 step 9, W-t-R 7).

    Same tree, same absent scan: exit 0 without the flag, exit 1 with it, and
    the refusal must name the scan rather than failing incidentally.
    """
    assert vd.main(["--check"]) == 0
    capsys.readouterr()
    rc = vd.main(["--check", "--require-source", str(tmp_path / "absent.pdf")])
    out = capsys.readouterr().out
    assert rc == 1
    assert "REFUSED (--require-source)" in out


def test_verify_source_exits_three_from_the_command_line(tmp_path, capsys):
    rc = vd.main(["--verify-source", str(tmp_path / "absent.pdf")])
    assert rc == 3
    assert "REFUSED" in capsys.readouterr().out


def test_balance_targets_refuses_when_the_record_is_absent(tmp_path):
    with pytest.raises(vd.Refused):
        vd.balance_targets(tmp_path / "balance.json")


def test_balance_targets_refuses_when_the_record_has_drifted(tmp_path):
    rec = json.loads(vd.BALANCE_ARTIFACT.read_text())
    rec["gap_db"]["short"]["gap_db"] = 12.0
    p = tmp_path / "balance.json"
    p.write_text(json.dumps(rec))
    with pytest.raises(vd.Refused) as exc:
        vd.balance_targets(p)
    assert "drifted" in str(exc.value)


def test_balance_targets_accepts_the_committed_record():
    """The refusal above is not vacuous: the real file passes."""
    t = vd.balance_targets()
    assert t["gap_db"]["short"] == pytest.approx(38.23, abs=0.005)
    assert t["gap_db"]["decay"] == pytest.approx(9.85, abs=0.005)


def test_the_pinned_scan_is_the_one_the_vr4_read_used():
    """Two modules reading the same page of the same document must not be able
    to drift onto different printings of it."""
    assert vd.SN_PDF_SHA256 == ts.SN_PDF_SHA256
    assert vd.SN_PDF_PAGE == ts.SN_PDF_PAGE == 13
    assert len(vd.SN_PDF_SHA256) == 64


# ---------------------------------------------------------------------------
# 5. The designator correction, as a computation rather than an assertion
# ---------------------------------------------------------------------------


def _designator_values():
    """designator -> value, from this module's read of the scan."""
    out = {}
    for which, p in vd.BANDPASS.items():
        for d in p["caps"]:
            out[d] = p["cap_f"]
        out[p["shunt"]] = p["shunt_ohm"]
        out[p["fb"]] = p["fb_ohm"]
        out[p["in_cap"]] = p["in_cap_f"]
        out[p["in_res"]] = p["in_res_ohm"]
    return out


@pytest.mark.parametrize("key", ["Hbp1", "Hbp2"])
def test_werner_fig4_source_strings_name_components_that_produce_their_own_f0(key):
    """The mechanism, not a note.

    Two independent things have to agree, and the first draft of this test
    checked only one of them and was therefore VACUOUS -- it passed on the
    exact pre-2026-09-28 string it was written to catch (wrong-then-right 3 in
    `../docs/scorecard/cymbal-369/vca-drive/README.md`):

      1. the DESIGNATORS named must be the ones this read puts on that band;
      2. the CAPACITANCE printed in the string must be the value this read
         gives those designators.

    `Hbp1` declared 3450 Hz with "C13=C14 3.3 nF". Its designators were right;
    its printed value was not (C13/C14 are 6.8 nF, and 3.3 nF on R 560/82 k is
    7117 Hz). Checking only (1) recomputes f0 from the READ's own 6.8 nF and
    sails straight past the wrong number in the text.
    """
    known = wf.KNOWN[key]
    src = known["source"]
    vals = _designator_values()
    caps = [d for d in vals if d.startswith("C") and d in src and vals[d] < 1e-6]
    res = [d for d in vals if d.startswith("R") and d in src]
    assert caps and res, f"{key}: '{src}' names no components this read carries"

    # (1) the designators are one consistent bridged-T in this read
    pair = sorted({vals[d] for d in caps})
    assert len(pair) == 1, f"{key}: '{src}' names caps of differing value {pair}"
    shunt, fb = min(vals[d] for d in res), max(vals[d] for d in res)
    f0 = 1.0 / (2 * math.pi * pair[0] * math.sqrt(shunt * fb))
    assert abs(f0 - known["f0"]) / known["f0"] <= vd.F0_TOL, (
        f"{key}: '{src}' names components that compute {f0:.0f} Hz, "
        f"declares {known['f0']:.0f} Hz")

    # (2) the value PRINTED beside them is the value they have
    m = re.search(r"([0-9.]+)\s*nF", src)
    assert m, f"{key}: '{src}' prints no capacitance to check"
    assert float(m.group(1)) * 1e-9 == pytest.approx(pair[0], rel=0.02), (
        f"{key}: '{src}' prints {m.group(1)} nF where {caps} are "
        f"{pair[0] * 1e9:.1f} nF")


@pytest.mark.parametrize("pin,f0,caps,res", [
    ("IC3 pin 1", "3.45 kHz", ("C13", "C14"), ("R56", "R57")),
    ("IC3 pin 7", "7.1 kHz", ("C15", "C16"), ("R58", "R59")),
])
def test_reference_section_10_names_the_designators_this_read_found(pin, f0, caps, res):
    """The reference is the document the model is built from, so the pairing is
    gated where a reader would look it up, not only in this module."""
    text = (ROOT / "docs" / "tr808-reference.md").read_text()
    # "560" pins this to the row that actually carries component values: the
    # "high, variable" row also says "IC3 pin 7" and "7.1 kHz" but names no
    # parts, and picking it by position would make this test order-dependent.
    rows = [ln for ln in text.splitlines()
            if ln.startswith("|") and pin in ln and f0 in ln and "560" in ln
            and "bridged-T type" not in ln]
    assert len(rows) == 1, f"§10 rows for {pin} / {f0}: {len(rows)}"
    row = rows[0]
    for d in caps + res:
        assert d in row, f"§10's {pin} row does not name {d}: {row}"


# ---------------------------------------------------------------------------
# 6. What this artifact deliberately does NOT do
# ---------------------------------------------------------------------------


def test_this_artifact_is_not_at_the_path_that_would_flip_396s_refusal():
    """`cymbal_band_balance.preconditions()` gates on a file's EXISTENCE and
    never reads its contents, so dropping any file at `vca-drive.json` would
    shorten #396's refusal list without a single drive number reaching the
    balance. This module therefore writes to `vca-drive/vca-drive.json`
    instead, and the mismatch is asserted rather than left to be noticed.
    Filed as a follow-up so the gate is fixed where it lives.
    """
    import cymbal_band_balance as bal
    assert vd.ARTIFACT != bal.VCA_ARTIFACT
    assert not bal.VCA_ARTIFACT.exists(), (
        "a bare vca-drive.json now exists; #396's precondition would pass "
        "without consuming a single number from it")


def test_the_record_round_trips(tmp_path):
    p = tmp_path / "vca-drive.json"
    assert vd.main(["--json", str(p)]) == 0
    rec = json.loads(p.read_text())
    assert rec["gate"]["ok"] is True
    assert rec["vca_term_db"]["chain_db"]["short"] == pytest.approx(4.973, abs=0.005)
    assert rec["source"]["sha256"] == vd.SN_PDF_SHA256
