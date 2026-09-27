"""fpga/test_uart_replay_reuse.py -- a reused RTL run must BE the run asked for.

`verify_uart_bridge.simulate_replay(reuse=True)` re-analyses a replay on
disk instead of re-simulating (~50 minutes a musical fixture). Review found
the first version checked only stimulus and length: a changed source, a
requested injection, or edited outputs would all have been re-analysed as
if fresh -- and the injection was silently dropped from the returned run.
The simulator is stubbed here (iverilog/vvp are not the subject); every
reuse refusal below must fire for its own reason.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import uart_host as uh
import verify_uart_bridge as vub


def _capture(tmp_path: Path) -> str:
    prefix = tmp_path / "cap"
    pkt = uh.pkt_write(0, 0, 0x20, 1)
    row = {"index": 0, "kind": "write", "packet": pkt.hex(), "send_frame": 1,
           "accept_frame": 2, "due": -1, "apply_frame": 3,
           "expect": {"flag": 0, "sec": 0, "addr": 0x20, "data": 1}}
    Path(f"{prefix}.plan.json").write_text(json.dumps({"rows": [row]}))
    Path(f"{prefix}.cmds").write_text(f"S 1 {pkt.hex(' ')}\n")
    return str(prefix)


# the simulator's reported version; a test may change it to fake a different
# simulator (#313: a run from another iverilog must never be reused)
SIM_VERSION = {"iverilog": "Icarus Verilog version 14.0 (devel) (s20260301-476-g1a23d1ffd)",
               "vvp": "Icarus Verilog runtime version 14.0 (devel) (s20260301-476-g1a23d1ffd)"}


@pytest.fixture
def fake_sim(monkeypatch, tmp_path):
    calls = []
    rom = tmp_path / "voice.hex"
    rom.write_text("0001\n0002\n")
    monkeypatch.setattr(vub, "roms", lambda: [rom])
    monkeypatch.setitem(SIM_VERSION, "iverilog", SIM_VERSION["iverilog"])

    def run(cmd, **kw):
        if len(cmd) == 2 and cmd[1] == "-V":                  # a version query
            name = Path(str(cmd[0])).name
            return subprocess.CompletedProcess(cmd, 0, SIM_VERSION[name] + "\n", "")
        calls.append(cmd)
        if any(str(c).startswith("+i2s=") for c in cmd):      # the vvp run
            for c in cmd:
                for k in ("i2s", "wrs", "txd", "samp"):
                    if str(c).startswith(f"+{k}="):
                        Path(str(c).split("=", 1)[1]).write_text(f"{k} 1\n")
            out = "tb_uart_bx: ran 900 frames in 1 segments; 900 I2S periods decoded\n"
            return subprocess.CompletedProcess(cmd, 0, out, "")
        return subprocess.CompletedProcess(cmd, 0, "", "")      # the compile
    monkeypatch.setattr(vub.subprocess, "run", run)
    monkeypatch.setattr(vub.top, "tool", lambda name: f"/stub/{name}")
    return calls


def test_fresh_run_then_reuse_returns_the_same_run(tmp_path, fake_sim):
    cap, out = _capture(tmp_path), tmp_path / "rtl"
    fresh = vub.simulate_replay(cap, out)
    assert fresh and (out / "run_identity.json").exists()
    n = len(fake_sim)
    again = vub.simulate_replay(cap, out, reuse=True)
    assert again and again["reused"] and len(fake_sim) == n   # nothing re-run


def test_reuse_refuses_a_run_with_no_identity(tmp_path, fake_sim, capsys):
    cap, out = _capture(tmp_path), tmp_path / "rtl"
    vub.simulate_replay(cap, out)
    (out / "run_identity.json").unlink()
    assert vub.simulate_replay(cap, out, reuse=True) is None
    assert "no run identity" in capsys.readouterr().out


def test_reuse_refuses_a_requested_injection(tmp_path, fake_sim, capsys):
    cap, out = _capture(tmp_path), tmp_path / "rtl"
    vub.simulate_replay(cap, out)
    assert vub.simulate_replay(cap, out, inject="UART_SKIP_BYTE", reuse=True) is None
    assert "defines" in capsys.readouterr().out


def test_reuse_refuses_changed_sources(tmp_path, fake_sim, capsys):
    cap, out = _capture(tmp_path), tmp_path / "rtl"
    vub.simulate_replay(cap, out)
    rec = json.loads((out / "run_identity.json").read_text())
    first = next(iter(rec["identity"]["sources"]))
    rec["identity"]["sources"][first] = "0" * 64    # as if that file changed
    (out / "run_identity.json").write_text(json.dumps(rec))
    assert vub.simulate_replay(cap, out, reuse=True) is None
    assert "sources" in capsys.readouterr().out


def test_reuse_refuses_edited_outputs(tmp_path, fake_sim, capsys):
    cap, out = _capture(tmp_path), tmp_path / "rtl"
    vub.simulate_replay(cap, out)
    (out / "uart_wrs.txt").write_text("edited\n")
    assert vub.simulate_replay(cap, out, reuse=True) is None
    assert "outputs changed" in capsys.readouterr().out


def test_reuse_refuses_a_different_length(tmp_path, fake_sim, capsys):
    cap, out = _capture(tmp_path), tmp_path / "rtl"
    vub.simulate_replay(cap, out)
    assert vub.simulate_replay(cap, out, tail_frames=48_000, reuse=True) is None
    assert "tail_frames" in capsys.readouterr().out


def test_an_injected_fresh_run_reports_its_injection(tmp_path, fake_sim):
    cap, out = _capture(tmp_path), tmp_path / "rtl"
    run = vub.simulate_replay(cap, out, inject="UART_SKIP_BYTE")
    assert run["inject"] == "UART_SKIP_BYTE"
    assert "INJECT_BUG_UART_SKIP_BYTE" in run["defines"]


def test_reuse_refuses_a_changed_rom(tmp_path, fake_sim, capsys):
    # a ROM the RTL $readmemh's changes the sound with no Verilog change
    cap, out = _capture(tmp_path), tmp_path / "rtl"
    vub.simulate_replay(cap, out)
    (tmp_path / "voice.hex").write_text("0001\n0003\n")
    assert vub.simulate_replay(cap, out, reuse=True) is None
    assert "roms" in capsys.readouterr().out


def test_the_identity_names_every_rom_the_bench_reads(tmp_path, fake_sim):
    cap, out = _capture(tmp_path), tmp_path / "rtl"
    vub.simulate_replay(cap, out)
    rec = json.loads((out / "run_identity.json").read_text())
    assert list(rec["identity"]["roms"].values()) == \
        [vub._sha(tmp_path / "voice.hex")]


def test_the_real_rom_set_is_not_empty():
    # the resolver the identity uses must find the repository's ROMs
    assert any(str(p).endswith(".hex") for p in vub.roms())


# ---- #313: the simulator is part of the identity ------------------------------------
def test_the_identity_names_the_simulator_and_the_oss_cad_pin(tmp_path, fake_sim):
    cap, out = _capture(tmp_path), tmp_path / "rtl"
    vub.simulate_replay(cap, out)
    sim = json.loads((out / "run_identity.json").read_text())["identity"]["simulator"]
    assert sim["iverilog -V"] == SIM_VERSION["iverilog"]
    assert sim["vvp -V"] == SIM_VERSION["vvp"]
    assert sim["oss_cad_pin"]["release"] and len(sim["oss_cad_pin"]["sha256"]) == 64


def test_control_a_different_simulator_version_forces_re_simulation(tmp_path, fake_sim,
                                                                     capsys):
    """THE CONTROL: the same sources, ROMs, stimulus and length, but the
    simulator reports another version. `reuse="auto"` (the trials'
    --reuse-rtl-if-identical) must SIMULATE; reuse=True must REFUSE, naming
    the simulator."""
    cap, out = _capture(tmp_path), tmp_path / "rtl"
    vub.simulate_replay(cap, out)
    n = len(fake_sim)
    SIM_VERSION["iverilog"] = "Icarus Verilog version 12.0 (stable) ()"
    assert vub.simulate_replay(cap, out, reuse=True) is None
    assert "simulator" in capsys.readouterr().out
    again = vub.simulate_replay(cap, out, reuse="auto")
    assert again and not again.get("reused") and len(fake_sim) > n     # it re-ran
    assert "simulator" in again["reuse"]["why"]


def test_an_unknown_simulator_version_is_never_reused(tmp_path, fake_sim, capsys):
    cap, out = _capture(tmp_path), tmp_path / "rtl"
    vub.simulate_replay(cap, out)
    SIM_VERSION["iverilog"] = ""                         # -V answered nothing
    assert vub.simulate_replay(cap, out, reuse=True) is None
    assert "simulator" in capsys.readouterr().out


def test_the_run_reports_whether_it_was_reused_and_why(tmp_path, fake_sim):
    cap, out = _capture(tmp_path), tmp_path / "rtl"
    fresh = vub.simulate_replay(cap, out, reuse="auto")
    assert fresh["reuse"] == {"asked": "auto", "reused": False,
                              "why": fresh["reuse"]["why"]} and fresh["reuse"]["why"]
    again = vub.simulate_replay(cap, out, reuse="auto")
    assert again["reuse"]["reused"] is True and again["reuse"]["why"] == "identical run on disk"
