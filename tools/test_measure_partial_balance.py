"""Controls for `measure_partial_balance`'s preparation (#636).

The tool carried a private copy of `run_case.prepare` with the pre-#132 lead
clamp -- the #101 defect -- long after the original had stopped clamping. These
tests assert the thing the tool ships: that it prepares through
`run_case.prepare` and that a pair prepared in different states is REFUSED
before any metric runs. Synthetic fixtures only; no reference corpus needed.

Run: `python3 -m pytest tools/test_measure_partial_balance.py -q`
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import measure_partial_balance as tool
import preparation_contract as pc
import run_case as rc
from test_preparation_contract import _ours, _prepare_pre_132, _reference, _fields


def _sides():
    return ((_reference(), 44100, "the reference recording"),
            (_ours(), 48000, "our RS render"))


def test_the_tool_prepares_and_windows_through_run_case():
    assert tool.window is rc.window
    assert not hasattr(tool, "prepare"), "a private prepare() is the #101 copy-drift"


def test_a_valid_unequal_pair_is_accepted():
    ref, ours = _sides()
    ry, oy = tool.prepare_pair(ref, ours)
    lead = rc.required_lead_samples
    assert tool.origin(ry, 44100).shape[0] == ry.shape[0] - lead(44100)
    assert tool.origin(oy, 48000).shape[0] == oy.shape[0] - lead(48000)


def test_the_pre_132_clamp_is_refused(monkeypatch):
    """Control: the clamp gave the reference 0.16 ms of lead and our render
    1.00 ms (#101). Injected into the tool's own preparation it must REFUSE."""
    monkeypatch.setattr(tool, "prepared",
                        lambda x, sr, side: _prepare_pre_132(x, sr, side=side))
    ref, ours = _sides()
    with pytest.raises(pc.Refused) as why:
        tool.prepare_pair(ref, ours)
    assert "lead" in _fields(why.value)


def test_a_record_cut_into_the_strike_is_refused_not_clamped():
    x = _reference()
    cut = x[rc._onset_index(x):]
    with pytest.raises(pc.Refused):
        tool.prepared(cut, 44100, "the reference recording")


def test_a_short_lead_is_padded_not_clamped():
    x = _reference()                       # 0.16 ms of lead, far under 540 samples
    y = tool.prepared(x, 44100, "the reference recording")
    assert rc._onset_index(y) >= rc.required_lead_samples(44100)
