"""held_note_audible's init check: a refusal is REFUSED at the exit, never a
traceback (#1717)."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for _p in (HERE, ROOT / "fpga", ROOT / "model", ROOT / "audition", ROOT / "tools"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import held_note_audible as hna               # noqa: E402
import r1_candidate as r1c                     # noqa: E402


def test_a_refusing_init_check_is_refused_not_a_traceback(tmp_path, monkeypatch, capsys):
    """`r1c.check_init(setup, kit_expected=False)` is called under no handler.
    With kit_expected False the real check_init returns before frozen_kit(),
    so the refusal is injected at the call."""
    def refuse(setup, *, kit_expected):
        raise r1c.Refused("injected: the target check refused")
    monkeypatch.setattr(r1c, "check_init", refuse)
    assert hna.main(["--preset", "default", "--image", "tree", "--outdir", str(tmp_path)]) == 2
    out = capsys.readouterr().out
    assert "held_note_audible: REFUSED -- injected: the target check refused" in out
    assert not (tmp_path / "held_note_audible.json").exists()
