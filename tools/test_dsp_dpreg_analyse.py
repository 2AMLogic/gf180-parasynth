#!/usr/bin/env python3
"""Controls for the DPREG-4 analyser (tools/dsp_dpreg_analyse.py).

The analyser dismisses a DPREG-4 warning when no reachable OPMODE value
selects P as an operand. A dismissal checker that could never fire would be
an unsatisfiable gate, so these tests prove both directions:

  * a cell whose OPMODE routing COULD present 010 in the Z field (two
    independent dynamic nets on OPMODE[5] and OPMODE[4]) must be reported
    P-FEEDBACK REACHABLE -- the checker's own red control;
  * a cell whose OPMODE[5] and [4] are the SAME net (the routed reality of
    all 13 flagged instances) must be dismissed.

The fixture is a synthetic dump snippet in the dsp_cells_dump.txt format;
no checkpoint or network is needed.
"""

import importlib.util
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "dsp_dpreg_analyse", HERE / "dsp_dpreg_analyse.py")
mod = importlib.util.module_from_spec(_spec)
sys.modules["dsp_dpreg_analyse"] = mod
_spec.loader.exec_module(mod)

CELL = """==================================================
==== CELL u_synth/u_voice/test/prod0__0 ====
---- properties ----
PREG = {preg}
SITE = DSP48_X1Y99
---- pins ----
{pins}
---- P/PCOUT fanout ----
FANOUT P[0] net test/n_0 loads=1
  load test/ff/D (FDRE)
---- control cones (OPMODE/ALUMODE/CARRYINSEL/CARRYIN/CE/RST) ----
CONE test/prod0__0 END nets=0
==== END CELL u_synth/u_voice/test/prod0__0 ====
"""


def dump(preg, pins):
    return f"TOTAL_DSP48E1 1\n" + CELL.format(preg=preg, pins=pins)


OPMODE_PINS = """PIN OPMODE[6] dir=IN net=test/<const0> driver=u_synth/test/GND/G (GND)
PIN OPMODE[5] dir=IN net=test/{net5} driver={drv5}
PIN OPMODE[4] dir=IN net=test/{net4} driver={drv4}
PIN OPMODE[3] dir=IN net=test/<const0> driver=u_synth/test/GND/G (GND)
PIN OPMODE[2] dir=IN net=test/<const1> driver=u_synth/test/VCC/P (VCC)
PIN OPMODE[1] dir=IN net=test/<const0> driver=u_synth/test/GND/G (GND)
PIN OPMODE[0] dir=IN net=test/<const1> driver=u_synth/test/VCC/P (VCC)"""


def test_same_net_pairing_is_dismissed():
    pins = OPMODE_PINS.format(net5="q", drv5="u_synth/test/lut/O (LUT5)",
                              net4="q", drv4="u_synth/test/lut/O (LUT5)")
    cells = mod.parse_cells(dump("0", pins))
    rec = mod.analyse("u_synth/u_voice/test/prod0__0",
                      cells["u_synth/u_voice/test/prod0__0"])
    assert not rec["p_feedback_reachable"]
    zs = {v["z"] for v in rec["reachable_opmode_values"]}
    assert zs == {"0", "C"}  # 000 and 011 only -- never P


def test_separate_nets_make_p_reachable():
    pins = OPMODE_PINS.format(net5="qa", drv5="u_synth/test/lut/O (LUT5)",
                              net4="qb", drv4="u_synth/test/lut2/O (LUT5)")
    cells = mod.parse_cells(dump("0", pins))
    rec = mod.analyse("u_synth/u_voice/test/prod0__0",
                      cells["u_synth/u_voice/test/prod0__0"])
    assert rec["p_feedback_reachable"]  # the checker CAN fire
    assert any(v["z"] == "P" for v in rec["reachable_opmode_values"])


def test_preg_one_still_reports_mux_truth():
    # PREG is not part of the reachability verdict; record that the property
    # is carried through so the document table stays honest.
    pins = OPMODE_PINS.format(net5="q", drv5="u_synth/test/lut/O (LUT5)",
                              net4="q", drv4="u_synth/test/lut/O (LUT5)")
    cells = mod.parse_cells(dump("1", pins))
    rec = mod.analyse("u_synth/u_voice/test/prod0__0",
                      cells["u_synth/u_voice/test/prod0__0"])
    assert rec["PREG"] == "1"
    assert not rec["p_feedback_reachable"]
