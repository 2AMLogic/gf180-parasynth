"""Controls for fpga/scripts/pnr_report.py (issue #565).

The defect: report.sh printed "RESULT: routed." from the ABSENCE of the text
ERROR/Error: in the nextpnr log, and the Makefile discarded nextpnr's exit
status. An empty log, an OOM-killed run, a missing tool all read "routed.".

START RED. `fpga/testdata/legacy_report.sh` is report.sh verbatim from
origin/main (426b1c7). The first test runs it on an EMPTY log and asserts the
intended control -- "an empty log must NOT yield 'RESULT: routed.'" -- and that
assertion FAILS against the legacy logic (it does print routed). The test is
written as `legacy_prints_routed == True` so the red result is recorded as an
observed fact rather than as a red suite; the same control then fails the new
code if it ever answers routed.

FIXTURES. Synthetic logs below are SYNTHETIC (built by `synth_log`, labelled
so). The independent parser reference is the tracked, complete, real log
fpga/reports/selected/linux-85f/place_route.log.gz. The raw logs behind the
committed fpga/reports/{ecp5_25f,ecp5_25f_headroom,ice40_up5k}.txt are NOT
tracked, so nothing here claims to replay them: those reports are only checked
for their headline outcome.
"""
import gzip
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

FPGA = Path(__file__).resolve().parent
sys.path.insert(0, str(FPGA / "scripts"))
import pnr_report as pr  # noqa: E402

REAL_LOG = FPGA / "reports/selected/linux-85f/place_route.log.gz"
LEGACY = FPGA / "testdata/legacy_report.sh"
OK = {"nextpnr": {"returncode": 0}, "after": []}


def synth_log(drop=(), reorder=False, sequence=None):
    """SYNTHETIC nextpnr-shaped log. `drop` removes named parts; `sequence` overrides."""
    parts = {
        "util": ["Info: Device utilisation:",
                 "Info: \t          TRELLIS_IO:      18/    197     9%",
                 "Info: \t          MULT18X18D:      14/     28    50%",
                 "Info: Placed 19 cells based on constraints."],
        "pfmax": ["Info: Max frequency for clock '$glbnet$clk_core': 30.30 MHz (PASS at 12.29 MHz)"],
        "route": ["Info: Routing complete."],
        "rfmax": ["Info: Max frequency for clock '$glbnet$clk_core': 30.49 MHz (PASS at 12.29 MHz)"],
        "delay": ["Info: Max delay posedge $glbnet$clk_core -> <async>: 5.72 ns"],
        "done": ["Info: Program finished normally."],
    }
    order = ["util", "pfmax", "route", "rfmax", "delay", "done"]
    if reorder:  # the only Max frequency lines come BEFORE the route marker
        order = ["util", "pfmax", "route", "delay", "done"]
    if sequence is not None:
        order = list(sequence)
    return "\n".join(l for k in order if k not in drop for l in parts[k]) + "\n"


FIT_LOG = ("Info: Device utilisation:\nInfo: \t ICESTORM_DSP: 14/ 8 175%\n"
           "Info: Placed 9 cells based on constraints.\n"
           "ERROR: Unable to place cell 'x', no BELs remaining to implement cell type 'ICESTORM_DSP'\n")


# ---------------------------------------------------------------- start red
def _legacy_run(tmp, log_text, bit=True):
    scripts, build = tmp / "scripts", tmp / "build"
    scripts.mkdir(), build.mkdir()
    shutil.copy(LEGACY, scripts / "report.sh")
    if log_text is not None:
        (build / "ecp5_pnr.log").write_text(log_text)
    (build / "ecp5_stat.txt").write_text("")
    return subprocess.run(["sh", str(scripts / "report.sh"), "ecp5", "ulx3s_top"],
                          capture_output=True, text=True)


def test_start_red_legacy_logic_calls_an_empty_log_routed(tmp_path):
    """RECORDED CONTROL: assertion 'empty log => not routed' FAILS on legacy.

    This is the executed reproduction from the issue's verified corrections
    (empty build/ecp5_pnr.log and ecp5_stat.txt -> 'RESULT: routed.', exit 0).
    """
    r = _legacy_run(tmp_path, "")
    empty_log_is_not_routed = "RESULT: routed." not in r.stdout   # the control
    assert not empty_log_is_not_routed, "legacy no longer reproduces: update this record"
    assert r.returncode == 0   # ...and it exited 0, so make wrote it as a report


def test_legacy_missing_log_exits_zero(tmp_path):
    r = _legacy_run(tmp_path, None)
    assert r.returncode == 0 and "tool missing" in r.stdout


def test_new_logic_same_control_is_green():
    v = pr.classify("", OK)
    assert v.state == pr.REFUSED and "empty" in " ".join(v.reasons)


# ------------------------------------------------------------- clean fixtures
def test_synthetic_complete_log_is_routed():
    assert pr.classify(synth_log(), OK).state == pr.ROUTED


def test_real_complete_log_is_routed_independent_reference():
    log = gzip.decompress(REAL_LOG.read_bytes()).decode()
    assert pr.classify(log, OK).state == pr.ROUTED


def test_resource_exhaustion_is_does_not_fit():
    v = pr.classify(FIT_LOG, {"nextpnr": {"returncode": 255}, "after": []})
    assert v.state == pr.NOFIT and "no BELs remaining" in v.errors[0]


# ------------------------------------------------------------ injected bugs
@pytest.mark.parametrize("name,log,marker", [
    ("empty", "", "empty"),
    ("placement-only", synth_log(drop=("route", "rfmax", "delay", "done")), "Routing complete"),
    ("no-route-marker", synth_log(drop=("route",)), "Routing complete"),
    ("no-post-route-timing", synth_log(drop=("rfmax",)), "AFTER"),
    ("no-utilisation", synth_log(drop=("util",)), "Device utilisation"),
    ("empty-utilisation",
     synth_log().replace("Info: \t          TRELLIS_IO:      18/    197     9%\n", "")
                .replace("Info: \t          MULT18X18D:      14/     28    50%\n", ""), "empty"),
    ("timing-only-before-route", synth_log(reorder=True), "AFTER"),
])
def test_incomplete_log_with_exit_zero_is_refused(name, log, marker):
    v = pr.classify(log, OK)
    assert v.state == pr.REFUSED, name
    assert marker in " ".join(v.reasons), (name, v.reasons)


def test_utilisation_only_after_route_marker_is_refused_for_order():
    """The ONLY defect is order: a populated utilisation block, the route marker
    and a Max frequency line after it are all present, but the utilisation block
    comes after 'Routing complete.' (e.g. a log stitched from two runs)."""
    log = synth_log(sequence=("pfmax", "route", "rfmax", "util", "delay", "done"))
    v = pr.classify(log, OK)
    assert v.state == pr.REFUSED
    assert "(order)" in " ".join(v.reasons), v.reasons


def test_evaluate_empty_bitstream_is_refused(tmp_path):
    """Complete log + OK status + a 0-byte bitstream must not be ROUTED."""
    (tmp_path / "ecp5_pnr.log").write_text(synth_log())
    (tmp_path / "ecp5_pnr.status.json").write_text(json.dumps(OK))
    (tmp_path / "ecp5.bit").write_bytes(b"")
    v, text = pr.evaluate("ecp5", "ulx3s_top", tmp_path)
    assert v.state == pr.REFUSED and "ecp5.bit missing or empty" in v.reasons[0]
    assert "RESULT: routed." not in text
    (tmp_path / "ecp5.bit").write_bytes(b"x")   # same inputs, non-empty: the control's twin
    assert pr.evaluate("ecp5", "ulx3s_top", tmp_path)[0].state == pr.ROUTED


def test_refused_report_does_not_label_an_fmax_as_post_route_result():
    log = synth_log(drop=("route", "rfmax", "delay", "done"))   # placement estimate only
    v = pr.classify(log, OK)
    text = pr.render("ecp5", "ulx3s_top", log, "", None, v)
    assert v.state == pr.REFUSED
    assert "post-route RESULT" not in text and "ESTIMATE:" not in text
    assert "suppressed" in text


def test_real_log_truncated_or_stripped_is_refused():
    lines = gzip.decompress(REAL_LOG.read_bytes()).decode().splitlines(True)
    cut = next(i for i, l in enumerate(lines) if "Routing complete" in l)
    assert pr.classify("".join(lines[:cut]), OK).state == pr.REFUSED
    assert pr.classify("".join(lines[:cut + 1]), OK).state == pr.REFUSED
    nofmax = [l for l in lines[cut:] if "Max frequency" not in l]
    assert pr.classify("".join(lines[:cut] + nofmax), OK).state == pr.REFUSED


def test_missing_log_and_missing_status_are_refused():
    assert pr.classify(None, OK).state == pr.REFUSED
    v = pr.classify(synth_log(), None)
    assert v.state == pr.REFUSED and "exit status" in v.reasons[0]


@pytest.mark.parametrize("nx", [{"returncode": 1}, {"returncode": 137}, {"signal": 9, "returncode": -9},
                                {"error": "nextpnr-ecp5: not found"}])
def test_bad_exit_never_routed_even_beside_complete_log(nx):
    v = pr.classify(synth_log(), {"nextpnr": nx, "after": []})
    assert v.state == pr.REFUSED


def test_generic_tool_error_is_refused_not_no_fit():
    v = pr.classify("ERROR: Cannot open JSON file\n", {"nextpnr": {"returncode": 255}, "after": []})
    assert v.state == pr.REFUSED and "unclassified" in " ".join(v.reasons)


def test_exit_zero_with_error_line_is_refused():
    assert pr.classify(synth_log() + "ERROR: late failure\n", OK).state == pr.REFUSED


def test_failed_pack_stage_is_refused():
    st = {"nextpnr": {"returncode": 0}, "after": [{"name": "icetime", "returncode": 1}]}
    v = pr.classify(synth_log(), st)
    assert v.state == pr.REFUSED and "icetime" in v.reasons[0]


def test_exit_codes():
    ok, nf, rf = pr.Verdict(pr.ROUTED), pr.Verdict(pr.NOFIT), pr.Verdict(pr.REFUSED)
    assert [pr._exit_code(v, False) for v in (ok, nf, rf)] == [0, 1, 2]
    assert [pr._exit_code(v, True) for v in (ok, nf, rf)] == [0, 0, 2]   # headroom never accepts REFUSED


# --------------------------------------------- port equivalence vs legacy
def test_report_text_matches_legacy_on_a_complete_real_log(tmp_path):
    log = gzip.decompress(REAL_LOG.read_bytes()).decode()
    stat_txt = "=== ulx3s_top ===\n\n   22534 cells\n   1163   CCU2C\n   8412   TRELLIS_FF\n"
    old = tmp_path / "old"
    old.mkdir()
    _legacy_run(old, log)
    (old / "build/ecp5_stat.txt").write_text(stat_txt)
    (old / "build/ecp5.bit").write_bytes(b"x" * 10)
    want = subprocess.run(["sh", str(old / "scripts/report.sh"), "ecp5", "ulx3s_top"],
                          capture_output=True, text=True).stdout
    got = pr.render("ecp5", "ulx3s_top", log, stat_txt, old / "build/ecp5.bit",
                    pr.classify(log, OK))
    assert got == want


def test_report_text_matches_legacy_on_a_no_fit_log(tmp_path):
    _legacy_run(tmp_path, FIT_LOG)
    want = subprocess.run(["sh", str(tmp_path / "scripts/report.sh"), "ecp5", "ulx3s_top"],
                          capture_output=True, text=True).stdout
    v = pr.classify(FIT_LOG, {"nextpnr": {"returncode": 255}, "after": []})
    got = pr.render("ecp5", "ulx3s_top", FIT_LOG, "", tmp_path / "build/ecp5.bit", v)
    assert got == want


def test_historical_reports_keep_their_headline_outcomes():
    """Headline expectations only: the raw logs are untracked, no replay is claimed."""
    r = FPGA / "reports"
    assert "RESULT: routed." in (r / "ecp5_25f.txt").read_text()
    assert "RESULT: routed." in (r / "ecp5_25f_headroom.txt").read_text()
    assert "RESULT: DOES NOT FIT" in (r / "ice40_up5k.txt").read_text()


def test_report_sh_is_a_single_delegation():
    body = [l for l in (FPGA / "scripts/report.sh").read_text().splitlines()
            if l.strip() and not l.startswith("#")]
    assert body == ['exec python3 "$(dirname "$0")/pnr_report.py" report "$@"']


# ------------------------------------------- fake-executable make integration
FAKE_YOSYS = '''#!{py}
import re, sys
a = sys.argv; s = " ".join(a)
log = a[a.index("-l") + 1]; open(log, "w").write("yosys fake\\n")
m = re.search(r"-json (\\S+)", s)
if m: open(m.group(1), "w").write("{{}}")
m = re.search(r"tee -o (\\S+) stat -top (\\S+)", s)
if m: open(m.group(1), "w").write("=== %s ===\\n\\n   100 cells\\n   5   TRELLIS_FF\\n" % m.group(2))
m = re.search(r"write_verilog -noattr (\\S+)", s)
if m: open(m.group(1), "w").write("")
'''
FAKE_PNR = '''#!{py}
import os, signal, sys
sys.path.insert(0, {tests!r})
import test_pnr_report as t
a = sys.argv; mode = os.environ.get("FAKE_MODE", "ok")
log = a[a.index("--log") + 1]
open(log, "w").write({{"ok": t.synth_log(), "kill": t.synth_log(drop=("route", "rfmax", "delay", "done")),
    "empty": "", "trunc": t.synth_log(drop=("route", "done")), "fit": t.FIT_LOG,
    "exit1": t.synth_log()}}.get(mode, t.synth_log()))
for flag in ("--textcfg", "--asc"):
    if flag in a and mode in ("ok", "exit1", "kill", "trunc", "empty"):
        open(a[a.index(flag) + 1], "w").write("cfg")
if mode == "kill": os.kill(os.getpid(), signal.SIGKILL)
if mode == "exit1": sys.exit(1)
if mode == "fit": sys.exit(255)
'''
FAKE_PACK = '''#!{py}
import os, sys
if os.environ.get("FAKE_PACK") == "fail" and os.path.basename(sys.argv[0]) in os.environ.get("FAKE_PACK_WHICH", "").split(","):
    sys.exit(3)
if os.environ.get("FAKE_PACK") == "silent" and os.path.basename(sys.argv[0]) in os.environ.get("FAKE_PACK_WHICH", "").split(","):
    sys.exit(0)   # claims success, writes nothing
for p in sys.argv[1:]:
    if p.endswith((".bit", ".bin")) or p.endswith("icetime.txt"): open(p, "w").write("BITS" * 4)
'''


@pytest.fixture
def rig(tmp_path):
    if shutil.which("make") is None:
        pytest.skip("make not installed")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in (("yosys", FAKE_YOSYS), ("nextpnr-ecp5", FAKE_PNR), ("nextpnr-ice40", FAKE_PNR),
                       ("ecppack", FAKE_PACK), ("icepack", FAKE_PACK), ("icetime", FAKE_PACK)):
        p = bindir / name
        p.write_text(body.format(py=sys.executable, tests=str(FPGA)))
        p.chmod(p.stat().st_mode | stat.S_IXUSR)
    build, reports = tmp_path / "build", tmp_path / "reports"

    def run(target, **env):
        e = dict(os.environ, **{k: str(v) for k, v in env.items()})
        return subprocess.run(
            ["make", "-C", str(FPGA), target, f"BUILD={build}", f"REPORTS={reports}",
             f"PYTHON={sys.executable}", f"YOSYS={bindir/'yosys'}",
             f"NEXTPNR_ECP5={bindir/'nextpnr-ecp5'}", f"NEXTPNR_ICE40={bindir/'nextpnr-ice40'}",
             f"ECPPACK={bindir/'ecppack'}", f"ICEPACK={bindir/'icepack'}", f"ICETIME={bindir/'icetime'}"],
            capture_output=True, text=True, env=e)

    def seed_stale(name, text):
        """Pre-seed a SUCCESSFUL-looking earlier run + an old committed report."""
        build.mkdir(exist_ok=True), reports.mkdir(exist_ok=True)
        (build / "ecp5_pnr.log").write_text(synth_log())
        (build / "ecp5.config").write_text("old")
        (build / "ecp5.bit").write_bytes(b"old-bitstream")
        (build / "ecp5_pnr.status.json").write_text(json.dumps(OK))
        rep = reports / name
        rep.write_text(text)
        os.utime(rep, (1, 1))   # older than every source, so make wants to rebuild
        return rep

    return run, seed_stale, build, reports


def test_make_ecp5_success_writes_report(rig):
    run, _, build, reports = rig
    r = run("ecp5")
    assert r.returncode == 0, r.stderr
    assert "RESULT: routed." in (reports / "ecp5_25f.txt").read_text()
    assert "bitstream: ecp5.bit 16 bytes" in (reports / "ecp5_25f.txt").read_text()


@pytest.mark.parametrize("mode", ["kill", "exit1", "empty", "trunc", "fit"])
def test_make_ecp5_fails_visibly_despite_stale_success(rig, mode):
    run, seed, build, reports = rig
    rep = seed("ecp5_25f.txt", "OLD COMMITTED REPORT\n")
    r = run("ecp5", FAKE_MODE=mode)
    assert r.returncode != 0, (mode, r.stdout, r.stderr)
    assert rep.read_text() == "OLD COMMITTED REPORT\n"      # preserved, not clobbered
    assert "pnr_report:" in r.stderr
    assert "RESULT: routed." not in r.stdout


def test_make_ecp5_killed_names_the_signal(rig):
    run, seed, *_ = rig
    seed("ecp5_25f.txt", "x")
    assert "signal 9" in run("ecp5", FAKE_MODE="kill").stderr


def test_make_ecp5_failed_packer_fails_and_keeps_report(rig):
    run, seed, build, reports = rig
    rep = seed("ecp5_25f.txt", "OLD\n")
    r = run("ecp5", FAKE_PACK="fail", FAKE_PACK_WHICH="ecppack")
    assert r.returncode != 0 and rep.read_text() == "OLD\n"
    assert "ecppack" in r.stderr


def test_make_ecp5_missing_tool_fails_and_keeps_report(rig, tmp_path):
    run, seed, *_ = rig
    rep = seed("ecp5_25f.txt", "OLD\n")
    r = subprocess.run(["make", "-C", str(FPGA), "ecp5", f"BUILD={tmp_path/'build'}",
                        f"REPORTS={tmp_path/'reports'}", f"PYTHON={sys.executable}",
                        f"YOSYS={tmp_path/'bin/yosys'}", "NEXTPNR_ECP5=/nonexistent/nextpnr"],
                       capture_output=True, text=True)
    assert r.returncode != 0 and rep.read_text() == "OLD\n"
    assert "REFUSED" in r.stderr


@pytest.mark.parametrize("target,packer,bit,report,stale", [
    ("ecp5", "ecppack", "ecp5.bit", "ecp5_25f.txt", ("ecp5.config",)),
    ("ice40", "icepack", "ice40.bin", "ice40_up5k.txt", ("ice40.asc", "ice40_icetime.txt")),
])
def test_make_silent_packer_beside_stale_bitstream_fails(rig, target, packer, bit, report, stale):
    """THE STALE-ARTEFACT CONTROL. nextpnr succeeds, the packer exits 0 and
    writes NOTHING, and an old bitstream sits in the build dir. Without the
    pre-run delete in cmd_run, the old file's size is reported as this run's
    and make exits 0 (Judge mutation on PR #594: reproduced red, see PR body)."""
    run, _, build, reports = rig
    build.mkdir(exist_ok=True), reports.mkdir(exist_ok=True)
    (build / bit).write_bytes(b"old-bitstream-19byt")
    for name in stale:
        (build / name).write_text("old")
    (build / f"{target}_pnr.log").write_text(synth_log())
    (build / f"{target}_pnr.status.json").write_text(json.dumps(OK))
    rep = reports / report
    rep.write_bytes(b"OLD COMMITTED REPORT\n")
    os.utime(rep, (1, 1))
    r = run(target, FAKE_PACK="silent", FAKE_PACK_WHICH=packer)
    assert r.returncode != 0, (r.stdout, r.stderr)
    assert rep.read_bytes() == b"OLD COMMITTED REPORT\n"
    assert f"{bit} missing or empty" in r.stderr, r.stderr
    assert "RESULT: routed." not in r.stdout
    assert not (build / bit).exists()


def test_make_ice40_fake_icetime_failure_fails_visibly(rig):
    run, _, build, reports = rig
    r = run("ice40", FAKE_PACK="fail", FAKE_PACK_WHICH="icetime")
    assert r.returncode != 0 and not (reports / "ice40_up5k.txt").exists()
    assert "icetime" in r.stderr


def test_make_ice40_success_and_no_fit(rig):
    run, _, build, reports = rig
    assert run("ice40").returncode == 0
    assert "bitstream: ice40.bin" in (reports / "ice40_up5k.txt").read_text()
    (reports / "ice40_up5k.txt").unlink()
    r = run("ice40", FAKE_MODE="fit")
    assert r.returncode != 0 and "Error 1" in r.stderr and not (reports / "ice40_up5k.txt").exists()
    assert "DOES NOT FIT" in (build / "ice40_nofit.txt").read_text()


def test_make_headroom_accepts_only_declared_no_fit(rig):
    run, _, build, reports = rig
    rep = reports / "ecp5_25f_headroom.txt"
    r = run("headroom", FAKE_MODE="fit")
    assert r.returncode == 0 and "DOES NOT FIT" in rep.read_text()
    rep.unlink()
    for mode in ("kill", "empty", "trunc", "exit1"):
        r = run("headroom", FAKE_MODE=mode)
        assert r.returncode != 0 and not rep.exists(), mode
