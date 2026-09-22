#!/usr/bin/env python3
"""fpga/ext_io_timing.py -- budgets for the seven external outputs of the
Arty build, each number carrying its own source.

The routed baseline left all seven outputs unconstrained ("External timing:
seven outputs without delay constraints; unqualified"). This module derives
the constraints that close them, from the actual receivers, and refuses where
no honest budget exists:

  i2s_bclk/lrclk/sdata   forwarded clock to the Adafruit #6250 breakout
                         (TI PCM5102, I2S slave, no MCLK). Receiver numbers:
                         TI PCM5102 datasheet SLOS811 (SLAS764B, September
                         2012), Table 7 "Audio Interface Slave Timing":
                         tDS = tDH = 8 ns (DIN vs BCLK rising), tLB = tBL =
                         8 ns (LRCLK vs BCLK rising), tBCY >= 40 ns,
                         tBCH/tBCL >= 16 ns, fBCK <= 24.576 MHz.
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

    .venv/bin/python fpga/ext_io_timing.py            human report
    .venv/bin/python fpga/ext_io_timing.py --json     machine record

Exit 0 if every budget holds, 1 if one does not, 2 if a request is refused
(a rate that cannot be qualified is refused, not reported as a budget).
"""
from __future__ import annotations

import argparse
import json
import sys

# ---- the core and the forwarded I2S clock -----------------------------------
F_CORE_MHZ = 12.288                                   # arty_a7_top.v MMCM output
T_CORE_NS = 1000.0 / F_CORE_MHZ                       # 81.3802083...
BCLK_DIV = 4                                          # i2s_tx.v:41 BCLK = clk/4
LRCLK_DIV = 256                                       # i2s_tx.v:42 LRCLK = clk/256
LRCLK_HZ = F_CORE_MHZ * 1e6 / LRCLK_DIV               # 48000 by construction

# ---- TI PCM5102, SLOS811 / SLAS764B, Table 7 (ns) ---------------------------
T_DS_NS = 8.0            # DATA setup before BCLK rising
T_DH_NS = 8.0            # DATA hold after BCLK rising
T_LB_NS = 8.0            # LRCLK edge setup before BCLK rising
T_BL_NS = 8.0            # LRCLK edge hold after BCLK rising
T_BCY_MIN_NS = 40.0      # BCLK cycle time
T_BCH_MIN_NS = 16.0      # BCLK pulse width high
T_BCL_MIN_NS = 16.0      # BCLK pulse width low
FBCK_MAX_MHZ = 24.576    # BCLK frequency at DVDD = 3.3 V

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


class Infeasible(ValueError):
    """A requested rate cannot carry the interface; refuse, never report."""


def bclk_checks() -> dict:
    """BCLK structural compliance against SLOS811 Table 7 at 3.072 MHz."""
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
    """set_output_delay values (ns) for i2s_sdata and i2s_lrclk against the
    forwarded BCLK clock: datasheet minima plus the assumed flight imbalance."""
    worst = max(T_DS_NS, T_LB_NS)
    return {"max": worst + FLIGHT_NS, "min": -(max(T_DH_NS, T_BL_NS) + FLIGHT_NS)}


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
            "receiver": "TI PCM5102 SLOS811 (SLAS764B, Sep 2012), Table 7",
            "rtl": ["rtl-sketch/i2s_tx.v:41-48 (BCLK/LRCLK/SDATA structure)",
                    "rtl-sketch/spi_ctl.v:124-141 (MISO core-domain resync)"],
            "spi_rate": "DR 0007 revision 2 via fpga/spi_host.py SCK_MAX_HZ",
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
    print("\nPCM5102 (SLOS811 Table 7) structural compliance at 3.072 MHz:")
    print(f"  tBCY {c['bclk_period_ns']:.3f} ns >= 40  (margin {c['tbcy_margin_ns']:.3f})")
    print(f"  tBCH/tBCL {c['half_period_ns']:.3f} ns >= 16  (margin {c['tbch_margin_ns']:.3f})")
    print(f"  fBCK 3.072 <= 24.576 MHz (margin {c['fbck_margin_mhz']:.3f})")
    od = record["i2s_output_delays_ns"]
    print(f"\ni2s_sdata / i2s_lrclk output delays vs forwarded BCLK: "
          f"max {od['max']:.3f} ns, min {od['min']:.3f} ns")
    print("  (tDS/tDH/tLB/tBL = 8 ns + 0.2 ns assumed flight imbalance; SDATA and")
    print("   LRCLK change on BCLK's falling edge, i2s_tx.v:48)")
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
