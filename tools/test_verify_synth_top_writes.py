"""Register-write capture verdicts with four silent I2S periods (#1376)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "rtl-sketch"))
import verify_synth_top as top


@pytest.fixture
def capture(tmp_path, monkeypatch):
    cmds = [(1, 0, 0, top.A.A_VOL, 0)] * 2
    calibration = {"calibration": "fixture", "image": "fixture", "points": []}
    monkeypatch.setattr(top, "f1cal_script", lambda *_: (cmds, cmds, 1, calibration))
    out = {name: tmp_path / name for name in ("wrs", "i2s", "samp")}
    out["i2s"].write_text("".join(f"{i} 0 0 32 32\n" for i in range(4)))
    out["samp"].write_text("".join(f"{i} 0\n" for i in range(4)))
    out.update(
        sources=top.resolve_sources(None),
        report=[
            "datapath busy at a tick: 0 overrun 0; overflow 0",
            "sample strobed in 4 frames, MISSING in 0, worst strobe cycle 100",
        ],
    )
    monkeypatch.setattr(top, "simulate", lambda *args, **kwargs: out)
    monkeypatch.setattr(top, "LAST", {})

    def write_capture(payloads):
        out["wrs"].write_text(
            "".join(
                f"{frame} 0 0 {top.A.A_VOL} {data} {frame}\n"
                for frame, data in enumerate(payloads)
            )
        )
        return ["--f1cal-smoke", "fixture", "--frames", "4", "--outdir", str(tmp_path)]

    return write_capture


@pytest.mark.parametrize(
    ("payloads", "status"),
    [
        ([0, 0], 0),  # exact capture
        ([0, 0, 0], 2),  # inaudible duplicate
        ([0], 2),  # matching prefix only
        ([], 2),  # nothing arrived
        ([1, 0], 1),  # corrupted payload
        ([1, 0, 0], 2),  # count takes precedence over corruption
    ],
)
def test_write_capture_verdict(capture, capsys, payloads, status):
    assert top.main(capture(payloads)) == status
    log = capsys.readouterr().out
    assert (top.LAST["writes_seen"], top.LAST["writes_sent"]) == (len(payloads), 2)
    assert ("PASS --" in log) == (status == 0)
    if status == 2:
        assert (
            f"REFUSED -- {len(payloads)} writes reached the register port, 2 were sent"
            in log
        )
        assert "writes_bad" not in top.LAST
    if status == 1:
        assert "1 of 2 writes arrived corrupted" in log


def test_expect_fail_requires_a_complete_capture(capture, monkeypatch, capsys):
    def expect_fail(payloads):
        args = ["verify_synth_top.py", *capture(payloads), "--expect-fail"]
        monkeypatch.setattr(sys, "argv", args)
        return top.cli()

    assert expect_fail([1, 0]) == 0
    assert top.LAST["writes_bad"] == 1
    assert "CAUGHT (comparison failed as required)" in capsys.readouterr().out
    assert expect_fail([1, 0, 0]) == 2
    assert "NEGATIVE CONTROL NOT CAUGHT (status 2)" in capsys.readouterr().out
    assert "writes_bad" not in top.LAST
