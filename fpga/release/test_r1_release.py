"""The R1 release manifest (#280): bound to the published R1 image, refused
when the selected artifacts disagree, STALE for each real wrong state, and
R0 left exactly as it was."""
import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for _p in (HERE, HERE.parent, ROOT / "tools"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import r1_release as rr                   # noqa: E402
import release_manifest as rm            # noqa: E402
import trial                             # noqa: E402
import uart_host as uh                   # noqa: E402

R1_BIT = "544499e2c97061957004f999caf21d5caaf520f6b2682f84c6e6773e7d291d21"
R1_DCP = "0f81026ecc8a0455da62ec2b7140ea806e00a97a71006d99743340299476ad5f"
R0_BIT = "a66c9349ef9b5572f3c3453777f38e1b143136755620fe419e730d6f5c84cb95"


def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def fresh():
    return json.loads(json.dumps(rr.build()))


def test_committed_r1_manifest_is_bound():
    verdict, detail = rr.check()
    assert verdict == "BOUND", detail


def test_the_image_is_the_r1_build_and_passes_every_gate(fresh):
    img = fresh["image"]
    assert img["bitstream_sha256"] == R1_BIT and img["routed_dcp_sha256"] == R1_DCP
    assert img["configuration"] == {"OSC2X": 1, "FILTER2X": 1, "PULSE2X": 0}
    assert img["tool"][0].startswith("vivado v2025.1") and "6140274" in img["tool"][1]
    assert img["internal_timing_pass"] and img["external_io_timing_qualified"]
    t = img["timing"]
    assert t["wns_ns"] > 0 and t["whs_ns"] > 0 and t["setup_failing"] == t["hold_failing"] == 0
    assert img["output_delay_exceptions"] == ["i2s_bclk"]
    assert img["dsp_feedback_review"]["complete"] and img["dsp_feedback_review"]["targets"] == 13
    assert img["dsp_feedback_review"]["routed_dcp_sha256"] == R1_DCP
    assert img["source_commit"].startswith("6864435") and img["source_commit_verified"]
    assert fresh["external_io"]["state"] == "PASS"
    assert fresh["external_io"]["control"]["caught"]
    assert fresh["external_io"]["spi_miso"]["max_guaranteed_readback_mhz"] >= 1.4


def test_the_host_is_bound_as_r1_and_r0_is_the_rollback(fresh):
    h = fresh["host"]
    assert fresh["contract_revision"] == 14 and h["contract_revision"] == 14
    assert h["selector"] == "--image r1"                  # the named R1 selection (#323)
    assert h["kit"]["clap_final_strike"]["value"] == 68
    # #280 keeps R0 the default; the switch is the operator's
    assert h["default_image"] == "release" and h["default_image_is_r1"] is False
    rb = fresh["rollback"]
    assert rb["name"] == "R0" and rb["bitstream_sha256"] == R0_BIT
    assert rb["contract_revision"] == 11


def test_r0_is_untouched_and_still_bound():
    """R1's publication is a new directory; R0's manifest still binds R0."""
    assert rm.check()[0] == "BOUND"
    pub = json.loads((rm.PUB_DIR / "publication.json").read_text())
    assert pub["bitstream_sha256"] == R0_BIT
    assert rr.PUB_DIR != rm.PUB_DIR and rr.MANIFEST != rm.MANIFEST


def _copy_pub(tmp_path):
    dst = tmp_path / "pub"
    shutil.copytree(rr.PUB_DIR, dst)
    return dst


def test_a_tampered_bitstream_is_refused(tmp_path, monkeypatch):
    dst = _copy_pub(tmp_path)
    b = bytearray((dst / "arty.bit").read_bytes())
    b[-1] ^= 1
    (dst / "arty.bit").write_bytes(bytes(b))
    monkeypatch.setattr(rr, "PUB_DIR", dst)
    verdict, detail = rr.check()
    assert verdict == "REFUSED" and "arty.bit" in detail, detail


def test_a_failing_timing_report_is_refused(tmp_path, monkeypatch):
    """The shipped timing.rpt is re-parsed: a failing endpoint refuses even
    before the digest check could (the digest is also checked)."""
    dst = _copy_pub(tmp_path)
    pub = json.loads((dst / "publication.json").read_text())
    rpt = (dst / "timing.rpt").read_text()
    wns = f"{pub['timing']['wns_ns']:.3f}"
    assert rpt.count(f"     {wns}  ") >= 1
    (dst / "timing.rpt").write_text(rpt.replace(f"     {wns}  ", f"    -{wns}  ", 1))
    pub["published_sha256"]["timing.rpt"] = _sha(dst / "timing.rpt")
    (dst / "publication.json").write_text(json.dumps(pub, indent=2) + "\n")
    monkeypatch.setattr(rr, "PUB_DIR", dst)
    verdict, detail = rr.check()
    assert verdict == "REFUSED" and "publisher's checks" in detail, detail


def test_an_ext_io_record_of_another_checkpoint_is_refused(tmp_path, monkeypatch):
    dst = _copy_pub(tmp_path)
    p = dst / rr.EXT_IO
    rec = json.loads(p.read_text())
    rec["dcp_sha256"] = "6c3c22c591671eb1f6790ac0500980f9660b8433ef8a1e235d3da2ee4a21fbf8"
    p.write_text(json.dumps(rec, indent=1) + "\n")
    monkeypatch.setattr(rr, "PUB_DIR", dst)
    verdict, detail = rr.check()
    assert verdict == "REFUSED" and "ext-io" in detail, detail


def test_dsp_evidence_from_another_checkpoint_is_refused(tmp_path, monkeypatch):
    dst = _copy_pub(tmp_path)
    ev = dst / "dsp-dpreg-evidence" / "routed_dcp.sha256"
    ev.write_text(ev.read_text().replace(R1_DCP, "0" * 64))
    monkeypatch.setattr(rr, "PUB_DIR", dst)
    verdict, detail = rr.check()
    assert verdict == "REFUSED", detail


def test_flipping_the_default_to_r1_is_visible_to_both_manifests(monkeypatch):
    """The one-line switch (DEFAULT_IMAGE = "tree") is not silent: this
    manifest records host.default_image (STALE), and R0's manifest pins the
    bytes of its commands WITHOUT --image (REFUSED) until both are re-bound."""
    monkeypatch.setattr(uh, "DEFAULT_IMAGE", "tree")
    verdict, detail = rr.check()
    assert verdict == "STALE" and "host.default_image" in detail, detail
    # uart_host.main reads its argparse default from the module constant at call
    # time, so R0's no-flag commands now emit R1 bytes: R0's manifest refuses,
    # because its replayed evidence no longer matches what the CLI sends
    verdict, detail = rm.check()
    assert verdict == "REFUSED" and "not what the CLI emits now" in detail, detail


def test_missing_manifest_is_refused(tmp_path):
    assert rr.main(["--manifest", str(tmp_path / "absent.json")]) == 2


@pytest.mark.parametrize("case", ["image", "host"])
def test_each_stale_control_is_caught(tmp_path, case, capsys):
    assert rr.stale_control(case, tmp_path) == 1
    out = capsys.readouterr().out
    assert "stale_control: STALE" in out and "unmodified copy: r1_release: BOUND" in out


def test_the_trial_passes_with_both_controls_caught(tmp_path):
    run_dir, rec = trial.run_trial("T-RELEASE-BOUND-R1", out_base=tmp_path)
    assert rec["verdict"] == trial.PASS, rec["verdict_reasons"]
    assert {c["id"]: c["caught"] for c in rec["controls"]} == {"stale-r1-image": True,
                                                              "stale-r1-host": True}
    ok, problems, _ = trial.check_receipt(run_dir / "receipt.json")
    assert ok, problems


@pytest.mark.parametrize("control", ["stale-r1-image", "stale-r1-host"])
def test_each_stale_control_as_the_candidate_is_fail(tmp_path, control):
    run_dir, rec = trial.run_trial("T-RELEASE-BOUND-R1", as_candidate=control, out_base=tmp_path)
    assert rec["verdict"] == trial.FAIL, rec["verdict_reasons"]


# ---- #319: the ext-I/O record is RE-DERIVED from its raw paths, not trusted ----------
def _tamper_ext_io(tmp_path, monkeypatch, edit):
    dst = _copy_pub(tmp_path)
    p = dst / rr.EXT_IO
    rec = json.loads(p.read_text())
    edit(rec)
    p.write_text(json.dumps(rec, indent=1) + "\n")
    monkeypatch.setattr(rr, "PUB_DIR", dst)
    return dst


def _set(path, value):
    def edit(rec):
        d = rec
        for k in path[:-1]:
            d = d[k]
        assert path[-1] in d, path
        d[path[-1]] = value
    return edit


DERIVED_TAMPERS = {
    # the Judge's #319 repro: a fabricated slack became BOUND under --write
    "spi_miso setup slack": (("ports", "spi_miso", "setup_slack_ns"), 25.0),
    "uart_txd hold slack": (("ports", "uart_txd", "hold_slack_ns"), 9.0),
    "worst setup slack": (("worst_setup_slack_ns",), 16.0),
    "readback MHz": (("spi_miso", "max_guaranteed_readback_mhz"), 2.0),
    "CO bound": (("spi_miso", "sta_co_bound_ns"), 9.5),
    "control slack": (("control", "spi_miso_setup_slack_ns"), -1.0),
    "control verdicts": (("control", "verdicts", "led[1]"), "FAIL"),
    "uart synchroniser match": (("synchronisers", "patterns", "xdc:uart_async_reg", "matched"), 2),
    "an input slack": (("inputs", "spi_sck", "slack_ns"), 1.0),
}
EXTRACTION_TAMPERS = {
    "tcl digest": (("tcl_sha256",), "0" * 64),
    "log digest": (("log_sha256",), "0" * 64),
    "tool": (("tool",), "Vivado v2024.2 SW Build 1"),
    "vivado rc": (("vivado_rc",), 1),
    "state": (("state",), "FAIL"),
    "summary": (("summary",), "7 timed outputs, all fine"),
    "dcp path": (("dcp",), "/elsewhere/routed.dcp"),
}


@pytest.mark.parametrize("name", sorted(DERIVED_TAMPERS) + sorted(EXTRACTION_TAMPERS))
def test_a_tampered_ext_io_field_is_refused_even_under_write(tmp_path, monkeypatch, capsys, name):
    path, value = {**DERIVED_TAMPERS, **EXTRACTION_TAMPERS}[name]
    _tamper_ext_io(tmp_path, monkeypatch, _set(path, value))
    out = tmp_path / "manifest.json"
    assert rr.main(["--write", "--manifest", str(out)]) == 2
    assert not out.exists()                          # nothing was bound
    assert "REFUSED" in capsys.readouterr().out
    verdict, detail = rr.check()
    assert verdict == "REFUSED" and rr.EXT_IO in detail, detail


def test_an_unknown_or_missing_ext_io_field_is_refused(tmp_path, monkeypatch):
    _tamper_ext_io(tmp_path, monkeypatch, lambda rec: rec.pop("failing_ports"))
    verdict, detail = rr.check()
    assert verdict == "REFUSED" and "failing_ports" in detail, detail


def test_the_clean_ext_io_record_re_derives_field_for_field():
    rec = json.loads((rr.PUB_DIR / rr.EXT_IO).read_text())
    derived = rr.rederive_ext_io(rr.PUB_DIR / rr.EXT_IO)
    assert set(derived) == set(rec) - set(rr.EXT_IO_EXTRACTION_ONLY)
    assert all(derived[k] == rec[k] for k in derived)


def test_write_on_the_clean_evidence_reproduces_the_committed_manifest(tmp_path):
    out = tmp_path / "manifest.json"
    assert rr.main(["--write", "--manifest", str(out)]) == 0
    assert out.read_bytes() == rr.MANIFEST.read_bytes()


# ---- the ext-I/O instrument is pinned by version, not read from the tree -------------
@pytest.fixture
def no_pinned_cache():
    rr._PINNED.clear()
    yield
    rr._PINNED.clear()


def test_the_tree_extractor_is_not_what_r1_is_checked_against(monkeypatch, no_pinned_cache):
    """R2 changes fpga/ext_io_extract.py (the #315 UART patterns). R1's record is
    checked with the instrument pinned at EXT_IO_INSTRUMENT_COMMIT: a broken
    tree module changes nothing."""
    import types
    broken = types.ModuleType("ext_io_extract")
    broken.evaluate = broken.parse = broken.tcl = lambda *a, **k: 1 / 0
    monkeypatch.setitem(sys.modules, "ext_io_extract", broken)
    verdict, detail = rr.check()
    assert verdict == "BOUND", detail


def test_control_a_record_from_another_instrument_is_refused(tmp_path, monkeypatch,
                                                             no_pinned_cache):
    _tamper_ext_io(tmp_path, monkeypatch, _set(("instrument_sha256",), "0" * 64))
    verdict, detail = rr.check()
    assert verdict == "REFUSED" and "other than the one pinned" in detail, detail


def test_control_a_wrong_pin_is_refused(monkeypatch, no_pinned_cache):
    """Pinned at the commit before the instrument existed (it was added in
    EXT_IO_INSTRUMENT_COMMIT): nothing can be verified, so REFUSED."""
    import subprocess
    parent = subprocess.run(["git", "-C", str(ROOT), "rev-parse",
                             rr.EXT_IO_INSTRUMENT_COMMIT + "^"],
                            capture_output=True, text=True, check=True).stdout.strip()
    monkeypatch.setattr(rr, "EXT_IO_INSTRUMENT_COMMIT", parent)
    verdict, detail = rr.check()
    assert verdict == "REFUSED" and "cannot read fpga/ext_io_extract.py" in detail, detail
