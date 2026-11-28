"""sweep_live_midi: a precondition the wrapped verifier refuses is REFUSED at
the exit (2, one line, nothing written), never a traceback -- for both classes
verify_live_midi.py catches at its own boundary (its `main()`):

  * a tampered fpga/release/r1-kit.json refuses the sweep (#1728). The session
    is built before any oracle check runs, so what reaches main() is
    drums_fx.KitRefused -- a RuntimeError, not a refusal.Refused, which the
    `except refusal.Refused` handler #1717 added cannot see;
  * the oracle's own frozen target refusing (r1_candidate.Refused, a
    refusal.Refused) refuses the sweep the same way (#1717);
  * control: an execution error raised while a row is measured still leaves
    main() as itself. The handler names its classes; it is not
    `except Exception`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "fpga", ROOT / "fpga" / "release", ROOT / "model"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
if str(ROOT / "tools") not in sys.path:
    sys.path.append(str(ROOT / "tools"))

import drums_fx as dx                      # noqa: E402
import refusal                             # noqa: E402
import r1_candidate as r1c                 # noqa: E402
import sweep_live_midi as slm              # noqa: E402
import uart_host as uh                     # noqa: E402
import verify_live_midi as vlm             # noqa: E402

FRATE = dx.A_ENV + dx.E_CPBURST * dx.ENV_STRIDE + 3


def test_a_tampered_r1_kit_refuses_the_sweep(tmp_path, monkeypatch, capsys):
    """#1728: `uart_host.R1_KIT` pointed at a copy with one kit write altered
    (the fixture shape of
    test_r1_candidate.py::test_frozen_kit_is_by_value_and_a_tampered_copy_refuses).
    MidiSession builds the session before verify_live_midi.check builds the
    oracle, so drums_fx.KitRefused is what reaches main() -- and it is not a
    refusal.Refused, which is why the handler names it: `except
    refusal.Refused` alone is red here.

    The spy is what tells this refusal from the oracle's: r1_candidate
    re-raises a KitRefused with the same reason text, so the printed line
    alone cannot say which class was caught."""
    frozen = json.loads(uh.R1_KIT.read_text())
    rec = json.loads(uh.R1_KIT.read_text())
    rec["writes"] = [[a, v + 1 if a == FRATE else v] for a, v in rec["writes"]]
    assert sum(x != y for x, y in zip(rec["writes"], frozen["writes"])) == 1
    p = tmp_path / "r1-kit.json"
    p.write_text(json.dumps(rec))
    monkeypatch.setattr(uh, "R1_KIT", p)
    monkeypatch.setattr(slm, "OUT", tmp_path / "sweep.json")
    raised = []
    r1_kit = uh.r1_kit

    def spy():
        try:
            return r1_kit()
        except dx.KitRefused as exc:
            raised.append(exc)
            raise

    monkeypatch.setattr(uh, "r1_kit", spy)
    assert not issubclass(dx.KitRefused, refusal.Refused), (
        "KitRefused is a refusal.Refused now: drop its explicit leg in "
        "sweep_live_midi.main()")
    assert slm.main() == 2
    out = capsys.readouterr()
    assert len(raised) == 1
    assert out.out.splitlines() == [f"sweep_live_midi: REFUSED -- {raised[0]}"]
    assert out.err == ""
    assert not (tmp_path / "sweep.json").exists()


def test_a_refusing_oracle_target_refuses_the_sweep(tmp_path, monkeypatch, capsys):
    """`one()` reaches verify_live_midi.check -> target_kit_and_presets ->
    r1_candidate.frozen_kit under no handler. Injected precondition: the
    candidate's frozen kit digest is not the host's (r1_candidate.py:135)."""
    monkeypatch.setattr(r1c, "KIT_R14_SHA256", "0" * 64)
    monkeypatch.setattr(slm, "OUT", tmp_path / "sweep.json")
    assert slm.main() == 2
    assert "sweep_live_midi: REFUSED -- uart_host.R1_KIT_SHA256" in capsys.readouterr().out
    assert not (tmp_path / "sweep.json").exists()


def test_control_an_execution_error_in_the_sweep_still_propagates(tmp_path, monkeypatch):
    """The control the two refusals need: the handler names its classes. An
    image the session cannot build raises ValueError in uart_host.image_kit,
    through the same MidiSession construction that raised KitRefused above --
    an execution error, never a refusal, so it must leave main() as itself."""
    monkeypatch.setattr(vlm, "HARNESS_IMAGE", "no-such-image")
    monkeypatch.setattr(slm, "OUT", tmp_path / "sweep.json")
    with pytest.raises(ValueError, match="is not one of"):
        slm.main()
    assert not (tmp_path / "sweep.json").exists()
