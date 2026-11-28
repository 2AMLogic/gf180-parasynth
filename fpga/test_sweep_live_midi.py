"""sweep_live_midi: the oracle's refusal is REFUSED at the exit, never a
traceback (#1717)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "fpga", ROOT / "fpga" / "release"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import r1_candidate as r1c                     # noqa: E402
import sweep_live_midi as slm                  # noqa: E402


def test_a_refusing_oracle_target_refuses_the_sweep(tmp_path, monkeypatch, capsys):
    """`one()` reaches verify_live_midi.check -> target_kit_and_presets ->
    r1_candidate.frozen_kit under no handler. Injected precondition: the
    candidate's frozen kit digest is not the host's (r1_candidate.py:135)."""
    monkeypatch.setattr(r1c, "KIT_R14_SHA256", "0" * 64)
    monkeypatch.setattr(slm, "OUT", tmp_path / "sweep.json")
    assert slm.main() == 2
    assert "sweep_live_midi: REFUSED -- uart_host.R1_KIT_SHA256" in capsys.readouterr().out
    assert not (tmp_path / "sweep.json").exists()
