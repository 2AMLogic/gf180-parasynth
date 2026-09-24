#!/usr/bin/env python3
"""fpga/ext_io_timing.py -- budgets for the seven external outputs of the
Arty build, each number carrying its own source.

The routed baseline left all seven outputs unconstrained ("External timing:
seven outputs without delay constraints; unqualified"). This module derives
the constraints that close them, from the actual receivers, and refuses where
no honest budget exists:

  i2s_bclk/lrclk/sdata   forwarded clock to the Adafruit #6250 breakout
                         (TI PCM5102, I2S slave, no MCLK). Receiver numbers:
                         TI PCM5102 datasheet SLAS764B (the document
                         Adafruit distributes; an earlier revision of this
                         file mis-cited the wrong identifier SLOS811 --
                         withdrawn), Table 7 "Audio Interface Slave
                         Timing", page 13: tDS = tDH = 8 ns
                         (DIN vs BCLK rising), tLB = tBL = 8 ns (LRCLK vs
                         BCLK rising), tBCY >= 40 ns, tBCH/tBCL >= 16 ns,
                         fBCK <= 24.576 MHz.
  spi_miso               FPGA is the SPI slave (DR 0007); the controller is
                         master and samples MISO on SCK rising edges (mode 0).
                         The RTL re-drives MISO in the core-clock domain
                         (rtl-sketch/spi_ctl.v:124-141), so the STA-expressible
                         budget is a clock-to-out bound, and the qualified
                         readback rate is NOT the 2.0 MHz write ceiling.
  led[1..3]              indicators with no synchronous receiver: no
                         receiver-derived budget exists, so the XDC applies a
                         real trivial constraint (one core period) instead of
                         a false path, and this module records why.

FORMULATION (reconciled 2026-09-22 -- the machine-readable budget must
describe the constraint actually shipped in fpga/boards/arty-a7-100.xdc):

  -max = tDS + flight = 8.200 ns. STA's worst analyzed launch/capture pair
         is a full core period; the real window is a half BCLK period, so
         this is pessimistic in the safe direction.
  -min = 162.760 - (tDH + flight) = 154.560 ns. SDATA/LRCLK launch on
         BCLK-falling cycles only (rtl-sketch/i2s_tx.v:48), so the
         checkable requirement is a bound on clock-vs-data skew, not a
         coincident-edge hold. A plain "-min -8.2" (an earlier form of
         this record) demands 8.2 ns of hold skew on a launch/capture
         pair the RTL never creates and FAILED on the routed checkpoint
         with phantom hold violations (WHS -9.874); see commit d277e53
         and fpga/ext_io_checkpoint_experiments.py. Measured skew on the
         routed design: 1.4-4.9 ns, so the DAC's tDH margin is ~158 ns.

Board-level flight: short jumper wires between the Arty headers and the
breakouts, ~0.2 ns one way (ASSUMPTION, not a measurement). Equal-length
jumpers make the data-minus-clock flight difference cancel for the I2S
outputs; 0.2 ns is kept as margin on both sides.

The controller-side SPI setup requirement is an ASSUMPTION (5 ns): there is no
guaranteed spec for "a Mac driving a Pmod jumper", and no datasheet to cite.
It is recorded here and in fpga/ARTY.md, and every figure that depends on it
is labelled with it.

Nominal rates (48 kHz LRCLK, 3.072 MHz BCLK, 12.288 MHz core) are asserted by
the existing simulation evidence and Vivado's clock report; this module does
not claim oscillator error or signal quality from arithmetic.

This module is also the binding authority for publication: the publisher
refuses to publish unless the XDC the build actually used carries exactly
the constraints derived here (xdc_contract_drift), the routed report's
output-delay exception list is exactly the permitted one, and UART ports
appear only with their constraints and disposition (uart_gate_drift).

    .venv/bin/python fpga/ext_io_timing.py            human report
    .venv/bin/python fpga/ext_io_timing.py --json     machine record

Exit 0 if every budget holds, 1 if one does not, 2 if a request is refused
(a rate that cannot be qualified is refused, not reported as a budget).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

XDC = pathlib.Path(__file__).resolve().parent / "boards/arty-a7-100.xdc"

# ---- the core and the forwarded I2S clock -----------------------------------
F_CORE_MHZ = 12.288                                   # arty_a7_top.v MMCM output
T_CORE_NS = 1000.0 / F_CORE_MHZ                       # 81.3802083...
BCLK_DIV = 4                                          # i2s_tx.v:41 BCLK = clk/4
LRCLK_DIV = 256                                       # i2s_tx.v:42 LRCLK = clk/256
LRCLK_HZ = F_CORE_MHZ * 1e6 / LRCLK_DIV               # 48000 by construction

# ---- TI PCM5102, SLAS764B Table 7 page 13 (ns) ------------------------------
T_DS_NS = 8.0            # DATA setup before BCLK rising
T_DH_NS = 8.0            # DATA hold after BCLK rising
T_LB_NS = 8.0            # LRCLK edge setup before BCLK rising
T_BL_NS = 8.0            # LRCLK edge hold after BCLK rising
T_BCY_MIN_NS = 40.0      # BCLK cycle time
T_BCH_MIN_NS = 16.0      # BCLK pulse width high
T_BCL_MIN_NS = 16.0      # BCLK pulse width low
FBCK_MAX_MHZ = 24.576    # BCLK frequency at DVDD = 3.3 V
DATASHEET = "SLAS764B, Table 7 p.13 (Audio Interface Slave Timing)"

# ---- board and controller assumptions (recorded, not measured) --------------
FLIGHT_NS = 0.2          # jumper flight, one way; residual data-clock imbalance
T_SU_CTRL_NS = 5.0       # ASSUMED external SPI controller setup; no guaranteed spec

# ---- the SPI control link (DR 0007 revision 2, fpga/spi_host.py) ------------
SCK_WRITE_MAX_MHZ = 2.0          # SCK_MAX_HZ: the WRITE path ceiling
MISO_SYNC_FLOPS = 4              # spi_ctl.v: 2-flop sync + edge flop + miso flop
MISO_LATENCY_NS = MISO_SYNC_FLOPS * T_CORE_NS    # worst case after the SCK edge
READBACK_QUALIFIED_MHZ = 1.4     # rate this deliverable qualifies for readback
# Absolute ceiling with zero clock-to-out -- no real route reaches it; quoted
# only so the refusal boundary is a number somebody can recompute.
MAX_GUARANTEED_READBACK_MHZ = 1000.0 / (2.0 * (MISO_LATENCY_NS + FLIGHT_NS + T_SU_CTRL_NS))

# ---- the publication-time exception and UART contracts ----------------------
# A forwarded clock carries no output delay (its generated clock IS the
# constraint; an output delay fails a self-referential hold check, measured
# WHS -1.021). This is the ONLY output-delay exception publication permits.
PERMITTED_OUTPUT_DELAY_EXCEPTIONS = ["i2s_bclk"]

# Integrated tree: the UART bridge ports exist on arty_a7_top (uart_txd D10,
# uart_rxd A9), so these checks are ACTIVE, not gated off. The port names are
# the wrapper's -- an earlier revision anticipated "uart_tx"/"uart_rx", which
# would have left this gate silently inert against the real ports.
UART_TX_PORT, UART_TX_PIN = "uart_txd", "D10"
UART_RX_PORT, UART_RX_PIN = "uart_rxd", "A9"
# Receiver/baud disposition for uart_txd, recorded now that the port ships.
# A build whose netlist drives the port while this is None is REFUSED at
# publication -- an unconstrained new output must not slip in.
UART_TX_DISPOSITION = {
    "receiver": "FTDI FT2232H USB-UART bridge (the Arty's programming cable)",
    "baud": 115200, "framing": "8N1",
    "budget": "one core period (81.380 ns) as a real output delay, not a "
              "false path: any clock-to-out below it is 0.94% of the "
              "8.681 us bit period, invisible to a receiver that tolerates "
              "several percent of baud error",
    "assumption": "FTDI resynchronises each bit against its own oversampling "
                  "clock; no per-receiver setup spec exists -- ASSUMED, not "
                  "measured",
}


class Infeasible(ValueError):
    """A requested rate cannot carry the interface; refuse, never report."""


def bclk_checks() -> dict:
    """BCLK structural compliance against SLAS764B Table 7 at 3.072 MHz."""
    period = BCLK_DIV * T_CORE_NS
    half = period / 2
    return {
        "bclk_period_ns": period,
        "half_period_ns": half,
        "bclk_mhz": 1000.0 / period,
        "lrclk_hz": LRCLK_HZ,
        "tbcy_margin_ns": period - T_BCY_MIN_NS,
        "tbch_margin_ns": half - T_BCH_MIN_NS,
        "tbcl_margin_ns": half - T_BCL_MIN_NS,
        "fbck_margin_mhz": FBCK_MAX_MHZ - 1000.0 / period,
    }


def i2s_output_delays() -> dict:
    """The output-delay formulation SHIPPED in fpga/boards/arty-a7-100.xdc.

    The -min is a clock-vs-data skew bound (half BCLK period minus the DAC
    hold requirement), NOT the naive half-cycle figure this record once
    carried; see the module docstring and commit d277e53.
    """
    half = bclk_checks()["half_period_ns"]
    hold_budget = max(T_DH_NS, T_BL_NS) + FLIGHT_NS
    return {
        "max_ns": round(T_DS_NS + FLIGHT_NS, 3),
        "max_derivation": f"tDS {T_DS_NS} ({DATASHEET}) + {FLIGHT_NS} ns "
                          f"assumed flight imbalance",
        "min_ns": round(half - hold_budget, 3),
        "min_derivation": f"half BCLK period {half:.3f} - (tDH {T_DH_NS} + "
                          f"flight {FLIGHT_NS}): skew bound, launches on "
                          f"BCLK-falling cycles only (i2s_tx.v:48)",
        "refuted_alternative": {
            "min_ns": round(-(T_DH_NS + FLIGHT_NS), 3),
            "why_refused": "coincident-edge hold on a launch/capture pair the "
                           "RTL never creates; phantom WHS -9.874 on the "
                           "routed checkpoint (commit d277e53)",
        },
        "hold_skew_measured_ns": "1.4-4.9 (routed checkpoint, "
                                 "fpga/ext_io_checkpoint_experiments.py)",
    }


def miso_co_budget_ns(sck_mhz: float) -> float:
    """The clock-to-out budget spi_miso can afford at a given SCK rate, from

        T_sck/2 >= MISO_LATENCY_NS + CO + FLIGHT_NS + T_SU_CTRL_NS

    Raises Infeasible for any rate where the left side is already smaller than
    the fixed RTL latency -- at the 2.0 MHz write ceiling (half period 250 ns
    vs 4 core clocks 325.5 ns) there is no budget at ANY clock-to-out >= 0.
    """
    half = 1000.0 / (2.0 * sck_mhz)
    budget = half - MISO_LATENCY_NS - FLIGHT_NS - T_SU_CTRL_NS
    if budget <= 0.0:
        raise Infeasible(
            f"spi_miso readback at {sck_mhz} MHz is infeasible: mode-0 half "
            f"period {half:.3f} ns <= fixed RTL resync latency "
            f"{MISO_LATENCY_NS:.3f} ns (+ flight {FLIGHT_NS} + assumed "
            f"controller setup {T_SU_CTRL_NS}); no clock-to-out budget exists")
    return budget


def miso_output_delay_max_ns() -> float:
    """The -max output delay (ns) that enforces the readback CO budget against
    the core clock: the STA check becomes CO <= T_core - output_delay."""
    return T_CORE_NS - miso_co_budget_ns(READBACK_QUALIFIED_MHZ)


def miso_max_readback_mhz(co_ns: float) -> float:
    """The greatest SCK rate at which readback is guaranteed for a MEASURED
    clock-to-out (the routed report supplies the real CO)."""
    return 1000.0 / (2.0 * (MISO_LATENCY_NS + co_ns + FLIGHT_NS + T_SU_CTRL_NS))


# ----------------------------------------------------------- XDC contract
def _r3(value: float) -> float:
    """The XDC carries three decimals; the contract compares at that
    granularity (bound: half a milli-unit, the printed precision)."""
    return round(value, 3)


def expected_output_delay_constraints() -> list:
    """The exact set_output_delay set the shipped XDC must carry, derived
    from the constants above -- never parsed back from the XDC itself."""
    skew = i2s_output_delays()
    i2s_clock = "i2s_bclk_ext"
    core_clock = "hardware_clock.clock_raw"
    return [
        {"clock": i2s_clock, "dir": "max", "ns": skew["max_ns"],
         "ports": ["i2s_sdata"]},
        {"clock": i2s_clock, "dir": "min", "ns": skew["min_ns"],
         "ports": ["i2s_sdata"]},
        {"clock": i2s_clock, "dir": "max", "ns": skew["max_ns"],
         "ports": ["i2s_lrclk"]},
        {"clock": i2s_clock, "dir": "min", "ns": skew["min_ns"],
         "ports": ["i2s_lrclk"]},
        {"clock": core_clock, "dir": "max",
         "ns": _r3(miso_output_delay_max_ns()), "ports": ["spi_miso"]},
        {"clock": core_clock, "dir": "min", "ns": 0.0, "ports": ["spi_miso"]},
        {"clock": core_clock, "dir": "max", "ns": 0.0,
         "ports": ["led[1]", "led[2]", "led[3]"]},
        {"clock": core_clock, "dir": "min", "ns": 0.0,
         "ports": ["led[1]", "led[2]", "led[3]"]},
        {"clock": core_clock, "dir": "max", "ns": 0.0, "ports": ["uart_txd"]},
        {"clock": core_clock, "dir": "min", "ns": 0.0, "ports": ["uart_txd"]},
    ]


_OUTPUT_DELAY_RE = re.compile(
    r"^set_output_delay\s+-clock\s+(\S+)\s+-(max|min)\s+(-?[\d.]+)\s+"
    r"\[get_ports\s+(.+?)\]\s*$", re.MULTILINE)


def parse_output_delay_constraints(xdc_text: str) -> list:
    """Every set_output_delay in the XDC as {clock, dir, ns, ports}."""
    out = []
    for clock, direction, value, ports in _OUTPUT_DELAY_RE.findall(xdc_text):
        names = [p for p in ports.replace("{", " ").replace("}", " ").split()]
        out.append({"clock": clock, "dir": direction,
                    "ns": float(value), "ports": names})
    return out


def xdc_ports(xdc_text: str) -> set:
    """Every port name the XDC mentions via get_ports."""
    return set(re.findall(
        r"get_ports\s+\{?([A-Za-z_][A-Za-z_0-9]*(?:\[\d+\])?)\}?",
        xdc_text))


def xdc_contract_drift(xdc_text: str, exceptions=None) -> list:
    """Reasons the XDC's output-delay set is not exactly the recorded
    budget. Empty list = the shipped constraint IS the intended one.

    "A constraint exists" is not "the intended constraint is present":
    values are compared against the derived budget (not merely for
    existence), unexpected constraints are drift, and the routed report's
    output-delay exception list must equal the permitted one.
    """
    reasons = []
    actual = parse_output_delay_constraints(xdc_text)
    expected = expected_output_delay_constraints()

    def key(c):
        return (c["clock"], c["dir"], tuple(c["ports"]))

    exp_map = {key(c): c for c in expected}
    act_map = {}
    for c in actual:
        k = key(c)
        if k in act_map:
            reasons.append(f"duplicate set_output_delay for {k}")
        act_map[k] = c
    for k, c in exp_map.items():
        if k not in act_map:
            reasons.append(f"missing constraint: {c['dir']} {c['ns']} ns "
                           f"on {list(k[2])} vs {k[0]}")
            continue
        want, got = c["ns"], act_map[k]["ns"]
        if abs(want - got) > 5e-4:
            reasons.append(f"value drift: {k[1]} on {list(k[2])} is "
                           f"{got} ns, budget says {want} ns")
    for k, c in act_map.items():
        if k not in exp_map:
            reasons.append(f"unexpected constraint: {c['dir']} {c['ns']} ns "
                           f"on {list(k[2])} vs {k[0]}")
    permitted = PERMITTED_OUTPUT_DELAY_EXCEPTIONS if exceptions is None \
        else sorted(exceptions)
    if permitted != sorted(PERMITTED_OUTPUT_DELAY_EXCEPTIONS):
        reasons.append(
            f"output-delay exceptions {permitted} != permitted "
            f"{PERMITTED_OUTPUT_DELAY_EXCEPTIONS}")
    return reasons


# ------------------------------------------------------------- UART gate
def uart_gate_drift(xdc_text: str) -> list:
    """UART-port requirements, active ONLY when the ports exist.

    The integrated tree will add uart_tx (D10) and uart_rx (A9). Until it
    does, this gate is inert; when they appear, publication requires:

      uart_tx: pin + IOSTANDARD, an output-delay constraint, and a
               recorded receiver/baud disposition (UART_TX_DISPOSITION).
      uart_rx: pin + IOSTANDARD and synchronizer evidence -- ASYNC_REG on
               the sync stages plus a false path (or input delay) into
               the first stage, mirroring the spi input pattern.
    """
    reasons = []
    ports = xdc_ports(xdc_text)
    tx, rx = UART_TX_PORT in ports, UART_RX_PORT in ports
    if not (tx or rx):
        return reasons
    if tx:
        if f"PACKAGE_PIN {UART_TX_PIN} [get_ports {UART_TX_PORT}]" \
                not in xdc_text.replace("{", "").replace("}", "") \
                .replace("  ", " "):
            reasons.append(f"{UART_TX_PORT} present but not pinned to "
                           f"{UART_TX_PIN}")
        if not re.search(r"set_output_delay\s+-clock\s+\S+\s+-(max|min)\s+"
                         r"-?[\d.]+\s+\[get_ports\s+" + UART_TX_PORT,
                         xdc_text):
            reasons.append(f"{UART_TX_PORT} present without an output-delay "
                           f"constraint")
        if UART_TX_DISPOSITION is None:
            reasons.append(f"{UART_TX_PORT} present without a receiver/baud "
                           f"disposition (UART_TX_DISPOSITION in "
                           f"fpga/ext_io_timing.py)")
    if rx:
        if f"PACKAGE_PIN {UART_RX_PIN} [get_ports {UART_RX_PORT}]" \
                not in xdc_text.replace("{", "").replace("}", "") \
                .replace("  ", " "):
            reasons.append(f"{UART_RX_PORT} present but not pinned to "
                           f"{UART_RX_PIN}")
        if not re.search(r"ASYNC_REG\s+TRUE[^\n]*uart", xdc_text) and \
                not re.search(r"uart[^\n]*ASYNC_REG", xdc_text, re.I):
            reasons.append(f"{UART_RX_PORT} present without ASYNC_REG on its "
                           f"synchronizer stages")
        if not re.search(r"(false_path|set_input_delay)[^\n]*"
                         r"get_ports\s+" + UART_RX_PORT, xdc_text):
            reasons.append(f"{UART_RX_PORT} present without a false path or "
                           f"input delay into its synchronizer")
    return reasons


def report() -> dict:
    checks = bclk_checks()
    infeasible_at_write_rate = False
    try:
        miso_co_budget_ns(SCK_WRITE_MAX_MHZ)
    except Infeasible:
        infeasible_at_write_rate = True
    return {
        "core_mhz": F_CORE_MHZ, "core_period_ns": T_CORE_NS,
        "bclk": checks,
        "i2s_output_delays_ns": i2s_output_delays(),
        "citations": {
            "receiver": f"TI PCM5102 {DATASHEET}",
            "rtl": ["rtl-sketch/i2s_tx.v:41-48 (BCLK/LRCLK/SDATA structure)",
                    "rtl-sketch/spi_ctl.v:124-141 (MISO core-domain resync)"],
            "spi_rate": "DR 0007 revision 2 via fpga/spi_host.py SCK_MAX_HZ",
            "formulation_note": "the -min 154.560 skew formulation shipped in "
                                "fpga/boards/arty-a7-100.xdc is the "
                                "constraint of record; see i2s_output_delays",
        },
        "xdc_contract": {
            "expected_output_delay_constraints": expected_output_delay_constraints(),
            "permitted_output_delay_exceptions": PERMITTED_OUTPUT_DELAY_EXCEPTIONS,
            "uart_gate": {"tx_port": UART_TX_PORT, "tx_pin": UART_TX_PIN,
                          "rx_port": UART_RX_PORT, "rx_pin": UART_RX_PIN,
                          "tx_disposition": UART_TX_DISPOSITION},
        },
        "miso": {
            "write_max_mhz": SCK_WRITE_MAX_MHZ,
            "readback_infeasible_at_write_rate": infeasible_at_write_rate,
            "resync_latency_ns": MISO_LATENCY_NS,
            "flight_ns_assumed": FLIGHT_NS,
            "controller_setup_ns_assumed": T_SU_CTRL_NS,
            "qualified_readback_mhz": READBACK_QUALIFIED_MHZ,
            "co_budget_ns": miso_co_budget_ns(READBACK_QUALIFIED_MHZ),
            "output_delay_max_ns": miso_output_delay_max_ns(),
            "co_budget_ns_at_bench_1p536": None,
        },
        "led": {
            "exception": "indicator, no synchronous receiver; budget = one core "
                         "period, applied as a real output delay, not a false path",
        },
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="machine record only")
    parser.add_argument("--co-ns", type=float, default=None,
                        help="measured spi_miso clock-to-out (ns) from the routed "
                             "report; reports the maximum guaranteed readback rate")
    args = parser.parse_args(argv)
    try:
        record = report()
    except Infeasible as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    if args.json:
        if args.co_ns is not None:
            record["miso"]["max_readback_mhz_at_measured_co"] = \
                miso_max_readback_mhz(args.co_ns)
        print(json.dumps(record, indent=2))
        return 0

    c = record["bclk"]
    print("EXTERNAL I/O TIMING BUDGETS -- Arty A7-100T, Vivado 2025.1 build")
    print("=" * 74)
    print(f"Core {F_CORE_MHZ} MHz (period {T_CORE_NS:.4f} ns); BCLK "
          f"{c['bclk_mhz']:.3f} MHz, LRCLK {c['lrclk_hz']:.0f} Hz by construction.")
    print(f"\nPCM5102 ({DATASHEET}) structural compliance at 3.072 MHz:")
    print(f"  tBCY {c['bclk_period_ns']:.3f} ns >= 40  (margin {c['tbcy_margin_ns']:.3f})")
    print(f"  tBCH/tBCL {c['half_period_ns']:.3f} ns >= 16  (margin {c['tbch_margin_ns']:.3f})")
    print(f"  fBCK 3.072 <= 24.576 MHz (margin {c['fbck_margin_mhz']:.3f})")
    od = record["i2s_output_delays_ns"]
    print(f"\ni2s_sdata / i2s_lrclk output delays vs forwarded BCLK (the "
          f"shipped formulation):")
    print(f"  -max {od['max_ns']:.3f} ns  ({od['max_derivation']})")
    print(f"  -min {od['min_ns']:.3f} ns  (skew bound: {od['min_derivation']})")
    print(f"  refuted alternative -min {od['refuted_alternative']['min_ns']:.3f} ns:"
          f" {od['refuted_alternative']['why_refused']}")
    print(f"  measured clock-vs-data skew: {od['hold_skew_measured_ns']}")
    m = record["miso"]
    print(f"\nspi_miso: MISO re-driven {m['resync_latency_ns']:.3f} ns (4 core clocks) "
          f"after the SCK edge")
    print(f"  at the {m['write_max_mhz']} MHz write ceiling: "
          f"{'INFEASIBLE (readback not qualified there)' if m['readback_infeasible_at_write_rate'] else 'feasible'}")
    print(f"  qualified readback rate {m['qualified_readback_mhz']} MHz -> "
          f"CO budget {m['co_budget_ns']:.3f} ns -> output delay -max "
          f"{m['output_delay_max_ns']:.3f} ns")
    if args.co_ns is not None:
        print(f"  at measured CO {args.co_ns} ns: max guaranteed readback "
              f"{miso_max_readback_mhz(args.co_ns):.4f} MHz")
    print(f"\nled[1..3]: {record['led']['exception']}")
    print(f"Assumptions: flight {FLIGHT_NS} ns/jumper; controller setup "
          f"{T_SU_CTRL_NS} ns (no guaranteed spec -- recorded, not measured).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
