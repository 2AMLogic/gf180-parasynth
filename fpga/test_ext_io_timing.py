import pathlib

import pytest

import ext_io_timing as x

XDC_TEXT = x.XDC.read_text()

# All receiver numbers come from TI PCM5102 SLAS764B (Table 7 p.13, "Audio
# Interface Slave Timing"; an earlier revision of these files mis-cited the
# wrong identifier SLOS811 -- withdrawn); the FPGA-side structure from
# rtl-sketch/i2s_tx.v and rtl-sketch/spi_ctl.v. These tests are the
# known-answer check for every number the XDC quotes, and the drift gate
# between the recorded budget and the shipped XDC.


def test_bclk_meets_the_pcm5102_clock_requirements():
    c = x.bclk_checks()
    assert c["bclk_period_ns"] == pytest.approx(325.5208, abs=1e-3)
    assert c["bclk_mhz"] == pytest.approx(3.072)
    assert c["half_period_ns"] == pytest.approx(162.7604, abs=1e-3)
    # SLAS764B Table 7: tBCY >= 40 ns, tBCH/tBCL >= 16 ns, fBCK <= 24.576 MHz.
    assert c["tbcy_margin_ns"] == pytest.approx(325.5208 - 40.0, abs=1e-3)
    assert c["tbch_margin_ns"] == pytest.approx(162.7604 - 16.0, abs=1e-3)
    assert c["tbcl_margin_ns"] == c["tbch_margin_ns"]
    assert c["fbck_margin_mhz"] == pytest.approx(24.576 - 3.072, abs=1e-3)
    assert min(c["tbcy_margin_ns"], c["tbch_margin_ns"],
               c["fbck_margin_mhz"]) > 0


def test_i2s_budget_states_the_formulation_actually_shipped():
    # The XDC ships -min 154.560 (half BCLK period minus the DAC hold
    # budget: a clock-vs-data skew bound, because the RTL launches
    # SDATA/LRCLK on BCLK-falling cycles only). The budget record must
    # describe THAT formulation -- an earlier revision recorded the naive
    # half-cycle -8.2 figure, which is not the constraint Vivado runs and
    # which failed the routed checkpoint with phantom hold violations.
    d = x.i2s_output_delays()
    assert d["max_ns"] == pytest.approx(8.2, abs=1e-3)
    assert d["min_ns"] == pytest.approx(154.560, abs=1e-3)
    assert d["min_ns"] == pytest.approx(
        x.bclk_checks()["half_period_ns"] - (x.T_DH_NS + x.FLIGHT_NS),
        abs=1e-3)
    assert d["refuted_alternative"]["min_ns"] == pytest.approx(-8.2, abs=1e-9)
    assert "phantom" in d["refuted_alternative"]["why_refused"]


def test_shipped_xdc_matches_the_recorded_budget():
    # drift gate: parse the shipped XDC, compare against the derived
    # budget. This fails if the XDC and the record ever disagree again.
    assert x.xdc_contract_drift(XDC_TEXT) == []


def test_xdc_value_drift_is_refused():
    text = XDC_TEXT.replace("-max 8.200 [get_ports i2s_sdata]",
                            "-max 12.000 [get_ports i2s_sdata]")
    drift = x.xdc_contract_drift(text)
    assert any("value drift" in r and "i2s_sdata" in r for r in drift)


def test_xdc_missing_constraint_is_refused():
    text = "\n".join(l for l in XDC_TEXT.splitlines()
                     if "154.560 [get_ports i2s_lrclk]" not in l)
    drift = x.xdc_contract_drift(text)
    assert any("missing constraint" in r and "i2s_lrclk" in r for r in drift)


def test_xdc_extra_constraint_is_refused():
    # "a constraint exists" is not "the intended constraint is present":
    # an unapproved extra output delay is drift too
    text = XDC_TEXT + ("set_output_delay -clock hardware_clock.clock_raw "
                       "-max 5.000 [get_ports i2s_bclk]\n")
    drift = x.xdc_contract_drift(text)
    assert any("unexpected constraint" in r for r in drift)


def test_exception_list_must_be_exactly_the_permitted_one():
    drift = x.xdc_contract_drift(XDC_TEXT, exceptions=["i2s_bclk"])
    assert drift == []
    drift = x.xdc_contract_drift(XDC_TEXT, exceptions=["i2s_bclk", "led[1]"])
    assert any("permitted" in r for r in drift)


def test_uart_gate_is_inert_without_uart_ports():
    assert x.uart_gate_drift(XDC_TEXT) == []


def test_uart_ports_require_constraints_and_disposition(monkeypatch):
    text = XDC_TEXT + (
        "set_property PACKAGE_PIN D10 [get_ports uart_tx]\n"
        "set_property IOSTANDARD LVCMOS33 [get_ports uart_tx]\n"
        "set_property PACKAGE_PIN A9 [get_ports uart_rx]\n"
        "set_property IOSTANDARD LVCMOS33 [get_ports uart_rx]\n")
    drift = x.uart_gate_drift(text)
    assert any("uart_tx" in r and "output-delay" in r for r in drift)
    assert any("uart_tx" in r and "disposition" in r for r in drift)
    assert any("uart_rx" in r and "ASYNC_REG" in r for r in drift)
    assert any("uart_rx" in r and "false path" in r for r in drift)
    # with the full evidence present the gate closes: pin, delay and a
    # recorded disposition for TX; pin, ASYNC_REG sync and false path for RX
    monkeypatch.setattr(x, "UART_TX_DISPOSITION",
                        {"receiver": "example 16550", "baud_assumed": 1e6,
                         "assumption": "receiver setup 10 ns -- ASSUMED"})
    text += ("set_output_delay -clock hardware_clock.clock_raw -max 10.000 "
             "[get_ports uart_tx]\n"
             "set_output_delay -clock hardware_clock.clock_raw -min 0.000 "
             "[get_ports uart_tx]\n"
             "set_property ASYNC_REG TRUE [get_cells -hier -regexp "
             "{.*u_uart/rx_q_reg\\[[01]\\]}]\n"
             "set_false_path -from [get_ports uart_rx] -to [get_pins -hier "
             "-regexp {.*u_uart/rx_q_reg\\[0\\]/D}]\n")
    assert x.uart_gate_drift(text) == []


def test_citations_name_the_distributed_datasheet():
    # every live citation must name SLAS764B; the withdrawn identifier may
    # appear only inside recorded-correction notes (lines that say so).
    # The needle is composed so this scanner does not itself contain it.
    needle = "SLOS" + "811"
    files = ["ext_io_timing.py", "boards/arty-a7-100.xdc", "ARTY.md",
             "test_ext_io_timing.py"]
    for name in files:
        for i, line in enumerate(
                (pathlib.Path(x.__file__).parent / name)
                .read_text().splitlines(), 1):
            if needle in line:
                assert any(w in line for w in
                           ("withdrawn", "mis-cited", "wrong")), \
                    f"{name}:{i} cites the withdrawn identifier as live"


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
