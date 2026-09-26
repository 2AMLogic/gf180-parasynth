#!/usr/bin/env python3
"""Tests for check_route.py and run_librelane.py -- the parsing and the refusals.

A checker's only failure mode that matters is a FALSE GREEN, so most of these are
cases that once produced one, or would:

  * `stat -width` gives counts and widths; reading the count alone under-reports
    every multi-bit register.
  * `stat`'s trailing `=== design hierarchy ===` section holds the FLATTENED
    totals.  Attributing them to the last module seen reported synth_top's own
    29 flops as 12,572.
  * a module with no flops of its own must report 0, not be absent, or the caller
    REFUSES on a correct design.
  * `$mem` is not a flop.  (The cause of the other wrong-then-right number here:
    without the `memory` pass drum_regs censuses at 11 instead of 3,520.)
  * the DEF bucketer must key on the MASTER name for sequential-ness -- an
    instance name proves nothing about what was placed.

Run: python3 -m pytest pnr/shuttle/test_check_route.py -q
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import check_route as cr           # noqa: E402
import run_librelane as rl         # noqa: E402


# --------------------------------------------------------------------------- #
# yosys stat parsing
# --------------------------------------------------------------------------- #

STAT = """
6. Printing statistics.

=== $paramod$deadbeef\\drum_regs ===

   Number of wires:               1000
   Number of cells:                177
          1   $dff_11
         63   $dff_16
         16   $dff_2
         24   $dff_24
         23   $dff_25
         32   $dff_26
         18   $dff_27
        400   $mux

=== $paramod$c0ffee\\drum_kit ===

   Number of cells:                  2
          1   $paramod$1\\drum_dp
          1   $paramod$2\\modal_dp

=== synth_top ===

   Number of cells:                 40
          1   $dff_8
          1   $dff_16
          3   $dff_1
          1   $mem

=== design hierarchy ===

   synth_top                         1
       9999   $dff_16
       9999   $dff_1
"""


def test_widths_are_multiplied_not_counted():
    # 1*11 + 63*16 + 16*2 + 24*24 + 23*25 + 32*26 + 18*27 = 3520
    got = cr.parse_yosys_stat(STAT)
    assert got["drum_regs"] == 3520


def test_design_hierarchy_section_is_not_attributed_to_a_module():
    got = cr.parse_yosys_stat(STAT)
    # 1*8 + 1*16 + 3*1 = 27, NOT 27 + 9999*16 + 9999
    assert got["synth_top"] == 27


def test_module_with_no_flops_reports_zero_not_absent():
    got = cr.parse_yosys_stat(STAT)
    assert got["drum_kit"] == 0


def test_mem_is_not_a_flop():
    # synth_top's section carries a $mem; it must not contribute.
    assert cr.parse_yosys_stat(STAT)["synth_top"] == 27


def test_only_requested_modules_are_reported():
    assert set(cr.parse_yosys_stat(STAT)) <= set(cr.CENSUS_MODULES)


@pytest.mark.parametrize("raw,want", [
    ("$paramod$deadbeef\\drum_regs", "drum_regs"),
    ("$paramod\\ladder_dp_n\\NCH=2", "NCH=2"),   # documents the shape we do NOT see
    ("\\i2s_tx", "i2s_tx"),
    ("spi_ctl", "spi_ctl"),
])
def test_module_base_name(raw, want):
    assert cr.module_base_name(raw) == want


# --------------------------------------------------------------------------- #
# DEF parsing / bucketing
# --------------------------------------------------------------------------- #

DEF = """VERSION 5.8 ;
DESIGN chip_top ;
COMPONENTS 6 ;
- core.u_synth.u_dregs.a1_reg[0][0] gf180mcu_fd_sc_mcu7t5v0__dffq_1 + PLACED ( 100 100 ) N ;
- core.u_synth.u_drums.bank.y_reg[3] gf180mcu_fd_sc_mcu7t5v0__sdffq_2 + PLACED ( 200 100 ) N ;
- core.u_synth.u_voice.u_ladder.s[1] gf180mcu_fd_sc_mcu7t5v0__dffrnq_1 + PLACED ( 300 100 ) N ;
- core.u_synth.u_dregs._0123_ gf180mcu_fd_sc_mcu7t5v0__nand2_1 + PLACED ( 400 100 ) N ;
- clk_pad gf180mcu_fd_io__in_s + FIXED ( 0 0 ) N ;
- qrcode_id gf180mcu_ws_ip__qrcode_id + FIXED ( 26 26 ) N ;
END COMPONENTS
END DESIGN
"""


@pytest.fixture()
def def_file(tmp_path):
    p = tmp_path / "final.def"
    p.write_text(DEF)
    return str(p)


def test_def_components_parsed(def_file):
    comps = cr.parse_def_components(def_file)
    assert len(comps) == 6
    assert ("clk_pad", "gf180mcu_fd_io__in_s") in comps


def test_sequential_is_keyed_on_the_master_not_the_instance():
    # a combinational cell under u_dregs must NOT count as a flop
    assert cr.SEQ_MASTER.search("gf180mcu_fd_sc_mcu7t5v0__nand2_1") is None
    assert cr.SEQ_MASTER.search("gf180mcu_fd_sc_mcu7t5v0__dffq_1")
    assert cr.SEQ_MASTER.search("gf180mcu_fd_sc_mcu7t5v0__sdffq_2")
    assert cr.SEQ_MASTER.search("gf180mcu_fd_sc_mcu7t5v0__dffrnq_1")


def test_buckets_reach_through_the_padframe_wrapper():
    # The instance path is chip_top -> core -> u_synth -> ...; the buckets must
    # match on the synth_top-relative part, not anchor at the start of the name.
    assert "drum_regs" in cr.bucket_of("core.u_synth.u_dregs.a1_reg[0][0]")
    assert "modal_dp" in cr.bucket_of("core.u_synth.u_drums.bank.y_reg[3]")
    assert "ladder_dp_n" in cr.bucket_of("core.u_synth.u_voice.u_ladder.s[1]")
    assert "outside synth_top" in cr.bucket_of("clk_pad")


def test_pad_and_ws_ip_masters_are_distinguished():
    assert cr.PAD_MASTER.search("gf180mcu_fd_io__in_s")
    assert cr.PAD_MASTER.search("gf180mcu_ef_io__bi_t")
    assert not cr.PAD_MASTER.search("gf180mcu_ws_ip__qrcode_id")
    assert cr.WS_IP_MASTER.search("gf180mcu_ws_ip__qrcode_id")


def test_no_def_refuses_rather_than_passing(tmp_path):
    with pytest.raises(cr.Refusal):
        cr.find_final_def(str(tmp_path))


def test_find_final_def_takes_the_latest_step(tmp_path):
    for step in ("05-floorplan", "40-openroad-detailedrouting"):
        d = tmp_path / step
        d.mkdir()
        (d / "chip_top.def").write_text(DEF)
    got = cr.find_final_def(str(tmp_path))
    assert "40-openroad-detailedrouting" in got


# --------------------------------------------------------------------------- #
# run_librelane preconditions
# --------------------------------------------------------------------------- #

def test_image_tag_follows_the_host_arch(monkeypatch):
    monkeypatch.setattr(rl.platform, "machine", lambda: "x86_64")
    assert rl.default_image().endswith("-x86_64")
    monkeypatch.setattr(rl.platform, "machine", lambda: "aarch64")
    assert rl.default_image().endswith("-aarch64")


def test_unknown_arch_refuses_rather_than_emulating(monkeypatch):
    monkeypatch.setattr(rl.platform, "machine", lambda: "riscv64")
    with pytest.raises(rl.Refusal):
        rl.default_image()


def test_missing_pad_library_refuses(tmp_path):
    root = tmp_path / "gf180mcuD"
    (root / "libs.tech" / "librelane").mkdir(parents=True)
    (root / "libs.tech" / "librelane" / "config.tcl").write_text("")
    (root / "libs.ref" / "gf180mcu_fd_sc_mcu7t5v0").mkdir(parents=True)
    with pytest.raises(rl.Refusal, match="gf180mcu_fd_io"):
        rl.check_pdk(str(tmp_path), "gf180mcuD", "gf180mcu_fd_sc_mcu7t5v0", "gf180mcu_fd_io")


def test_pad_library_without_blackbox_pp_refuses(tmp_path):
    root = tmp_path / "gf180mcuD"
    (root / "libs.tech" / "librelane").mkdir(parents=True)
    (root / "libs.tech" / "librelane" / "config.tcl").write_text("")
    (root / "libs.ref" / "gf180mcu_fd_sc_mcu7t5v0").mkdir(parents=True)
    vdir = root / "libs.ref" / "gf180mcu_fd_io" / "verilog"
    vdir.mkdir(parents=True)
    (vdir / "gf180mcu_fd_io.v").write_text("")      # the volare-era build
    with pytest.raises(rl.Refusal, match="blackbox_pp"):
        rl.check_pdk(str(tmp_path), "gf180mcuD", "gf180mcu_fd_sc_mcu7t5v0", "gf180mcu_fd_io")


def test_openlane1_era_pdk_refuses(tmp_path):
    root = tmp_path / "gf180mcuD"
    (root / "libs.tech" / "openlane").mkdir(parents=True)
    with pytest.raises(rl.Refusal, match="libs.tech/librelane"):
        rl.check_pdk(str(tmp_path), "gf180mcuD", "gf180mcu_fd_sc_mcu7t5v0", "gf180mcu_fd_io")


def test_verilog_files_are_read_from_the_config_not_duplicated():
    files = rl.verilog_files(rl.HERE)
    names = [os.path.basename(f) for f in files]
    assert "chip_top.sv" in names and "chip_core.sv" in names
    assert "synth_top.v" in names and "drum_kit.v" in names
    # in place, under rtl-sketch/ -- not copied, or the $readmemh paths break
    assert any(os.path.join("rtl-sketch", "voice_dp.v") in f for f in files)


def test_floorplan_mode_does_not_ask_for_final_views():
    args = rl.stage_args("floorplan", "/runs")
    assert "--save-views-to" not in args
    assert args == ["--to", "OpenROAD.Floorplan"]


def test_full_mode_skips_signoff_but_still_saves_views():
    args = rl.stage_args("full", "/runs")
    assert "--save-views-to" in args
    for step in ("Magic.DRC", "Netgen.LVS", "KLayout.XOR", "Checker.KLayoutAntenna"):
        assert step in args
    # the detailed router is NOT skipped: `full` still routes
    assert "OpenROAD.DetailedRouting" not in args


def test_signoff_mode_skips_nothing():
    assert "--skip" not in rl.stage_args("signoff", "/runs")


def test_density_is_layered_last_and_only_when_present(tmp_path):
    files = rl.config_files(rl.HERE, str(tmp_path / "absent.yaml"))
    assert len(files) == 3
    d = tmp_path / "density.yaml"
    d.write_text("PL_TARGET_DENSITY_PCT: 55\n")
    files = rl.config_files(rl.HERE, str(d))
    assert files[-1] == str(d)


def test_docker_argv_maps_host_paths_onto_themselves(monkeypatch):
    monkeypatch.setattr(rl.platform, "machine", lambda: "x86_64")
    argv = rl.docker_argv("img", ["/a", "/b"], "/b", ["librelane", "x"])
    assert "-v" in argv and "/a:/a" in argv and "/b:/b" in argv
    assert argv[argv.index("--platform") + 1] == "linux/amd64"
    assert argv[-2:] == ["librelane", "x"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))


# --------------------------------------------------------------------------- #
# metric provenance: the carried-forward pre-route number
# --------------------------------------------------------------------------- #
#
# LibreLane's metrics are cumulative, so a key no later step updates survives into
# the final payload looking like a fresh measurement.  In the halfslot run,
# `timing__setup__ws__corner:nom_ss_125C_4v50` = -178.5 ns was written ONCE by step
# 12 (OpenROAD.STAPrePNR, unplaced and unrouted, ideal clock) and is byte-identical
# in every step after it.  Read naively it says the routed chip misses the slow
# corner by more than two clock periods.  There is no routed slow corner in that run
# at all.  These tests pin the discriminator.

def _fake_run(tmp_path, steps):
    """steps = [(dirname, metrics dict)] -- writes a minimal LibreLane run tree."""
    for name, metrics in steps:
        d = tmp_path / name
        d.mkdir()
        (d / "state_out.json").write_text(__import__("json").dumps({"metrics": metrics}))
    return str(tmp_path)


def test_metric_source_step_names_the_step_that_last_changed_a_key(tmp_path):
    run = _fake_run(tmp_path, [
        ("12-openroad-staprepnr", {"timing__setup__ws__corner:ss": -178.5, "a": 1}),
        ("30-openroad-stamidpnr", {"timing__setup__ws__corner:ss": -178.5, "a": 2}),
        ("43-openroad-detailedrouting", {"timing__setup__ws__corner:ss": -178.5, "a": 2,
                                         "route__drc_errors": 0}),
    ])
    src = cr.metric_source_step(run)
    # carried forward unchanged for two steps -- still attributed to step 12
    assert src["timing__setup__ws__corner:ss"] == "12-openroad-staprepnr"
    assert src["a"] == "30-openroad-stamidpnr"
    assert src["route__drc_errors"] == "43-openroad-detailedrouting"


def test_post_route_keys_excludes_the_carried_forward_pre_route_corner(tmp_path):
    run = _fake_run(tmp_path, [
        ("12-openroad-staprepnr", {"timing__setup__ws__corner:ss": -178.5}),
        ("43-openroad-detailedrouting", {"timing__setup__ws__corner:ss": -178.5,
                                         "route__drc_errors": 7}),
    ])
    post = cr.post_route_keys(run)
    assert "route__drc_errors" in post
    assert "timing__setup__ws__corner:ss" not in post


def test_post_route_keys_is_empty_before_detailed_routing(tmp_path):
    """A run that never reached the router has NOTHING post-route in it.

    Returning anything here would let a placement-only run be reported as a route.
    """
    run = _fake_run(tmp_path, [
        ("12-openroad-staprepnr", {"timing__setup__ws__corner:ss": -178.5}),
        ("27-openroad-globalplacement", {"design__instance__utilization": 0.73}),
    ])
    assert cr.post_route_keys(run) == set()


def test_worst_setup_refuses_to_fall_back_to_the_nominal_corner(tmp_path):
    """The whole point: NOT MEASURED must not become "fine".

    `timing__setup__ws` is the nominal corner.  On the halfslot run it is +34 ns
    while the only per-corner numbers present are pre-route.  A fallback would
    report +34 as the design's worst slack.
    """
    import report_halfslot as rh
    m = {"timing__setup__ws": 34.06, "timing__setup__ws__corner:ss": -178.5}
    slack, why = rh.worst_setup(m, post=set())
    assert slack is None
    assert "after detailed routing" in why
    # ...and with a post-route corner present it does report it
    slack, corner = rh.worst_setup(m, post={"timing__setup__ws__corner:ss"})
    assert (slack, corner) == (-178.5, "ss")


def test_verdict_marks_an_unfinished_route_not_measured_rather_than_failed(tmp_path):
    """NOT MEASURED and FAILED are different outcomes and must read differently."""
    import report_halfslot as rh
    m = {"design__instance__utilization": 0.73,
         "design__instance__count__padcells": 754,
         "timing__setup__ws": 34.06,
         "timing__setup__ws__corner:nom_ss_125C_4v50": -178.5}
    body = rh.verdict_section(m, post=set(), src={})
    assert "*not measured*" in body
    assert "NOT MEASURED is not FAILED" in body
    # the pre-route -178.5 must not be presented as a failed post-route corner
    assert "-178.500" not in body
