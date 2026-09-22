import pytest

import ext_io_timing as x


# All receiver numbers come from TI PCM5102 SLOS811 (SLAS764B, September
# 2012), Table 7 "Audio Interface Slave Timing"; the FPGA-side structure from
# rtl-sketch/i2s_tx.v and rtl-sketch/spi_ctl.v. These tests are the
# known-answer check for every number the XDC quotes.

def test_bclk_meets_the_pcm5102_clock_requirements():
    c = x.bclk_checks()
    assert c["bclk_period_ns"] == pytest.approx(325.5208, abs=1e-3)
    assert c["bclk_mhz"] == pytest.approx(3.072)
    assert c["half_period_ns"] == pytest.approx(162.7604, abs=1e-3)
    # SLOS811 Table 7: tBCY >= 40 ns, tBCH/tBCL >= 16 ns, fBCK <= 24.576 MHz.
    assert c["tbcy_margin_ns"] == pytest.approx(325.5208 - 40.0, abs=1e-3)
    assert c["tbch_margin_ns"] == pytest.approx(162.7604 - 16.0, abs=1e-3)
    assert c["tbcl_margin_ns"] == c["tbch_margin_ns"]
    assert c["fbck_margin_mhz"] == pytest.approx(24.576 - 3.072, abs=1e-3)
    assert min(c["tbcy_margin_ns"], c["tbch_margin_ns"],
               c["fbck_margin_mhz"]) > 0


def test_din_and_lrclk_delays_are_datasheet_minima_plus_flight():
    # tDS = tDH = tLB = tBL = 8 ns, plus 0.2 ns assumed flight imbalance
    # between the data jumper and the BCLK jumper (recorded assumption).
    assert x.i2s_output_delays() == {"max": 8.2, "min": -8.2}


def test_status_readback_is_impossible_at_the_supported_write_rate():
    # spi_host.py SCK_MAX_HZ = 2.0 MHz (DR 0007). The RTL re-drives MISO 3-4
    # core clocks after the SCK edge, and 4 core clocks (325.52 ns) already
    # exceed the mode-0 half period (250.0 ns) before any clock-to-out,
    # flight or controller setup. This must REFUSE, not return a budget --
    # encoding it as a test so nobody later makes the 2 MHz case "pass".
    with pytest.raises(x.Infeasible):
        x.miso_co_budget_ns(2.0)
    with pytest.raises(x.Infeasible):
        x.miso_co_budget_ns(1.536)          # the bench link rate
    with pytest.raises(x.Infeasible):
        x.miso_co_budget_ns(x.MAX_GUARANTEED_READBACK_MHZ + 0.001)


def test_readback_budget_closes_at_the_qualified_rate():
    co = x.miso_co_budget_ns(1.4)
    assert co == pytest.approx(26.422, abs=1e-3)
    assert x.miso_output_delay_max_ns() == pytest.approx(54.958, abs=1e-3)
    assert x.miso_output_delay_max_ns() < x.T_CORE_NS


def test_readback_rate_from_measured_clock_to_out():
    # rate = 1000 / (2 * (4*T_core + CO + flight + t_su)), ns and MHz.
    assert x.miso_max_readback_mhz(0.0) == pytest.approx(
        1000 / (2 * (4 * x.T_CORE_NS + x.FLIGHT_NS + x.T_SU_CTRL_NS)))
    assert x.miso_max_readback_mhz(0.0) == pytest.approx(1.5118, abs=1e-4)
    assert x.miso_max_readback_mhz(26.422) == pytest.approx(1.4, abs=1e-3)
