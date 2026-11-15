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


def test_the_tool_carries_no_private_prepare_or_window():
    """#636's control, kept as the stronger statement. The `window = rc.window`
    alias it used to assert lost its last caller in #772 (both remaining callers
    were fixed-window estimator copies), so the tool must now define neither
    name -- a private copy of either is the #101 drift this file pins."""
    assert not hasattr(tool, "prepare"), "a private prepare() is the #101 copy-drift"
    assert not hasattr(tool, "window"), "a private window() is the same shape"


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


# --- #772: the tool must not carry its own copy of a shipped estimator, nor
# advertise a command it does not dispatch.
#
# The file's own comment at the `window = rc.window` alias records why: "A
# private copy of either is how this file came to carry the #101 clamp after
# run_case had moved on (#636)". #636 replaced the private `prepare`/`window`
# and left private `tone_ratio_db`/`band_pair_db` behind, labelled "the
# scorecard's two estimators, verbatim" -- a label that stopped being true at
# #109, when D10A/D13A's "Partial balance" moved to `balance_trajectory_db`.
#
# WHAT DEFEATS THESE CHECKS (docs/verification-rules.md rule 8):
#   - `test_no_private_copy_of_a_shipped_estimator` keys on the DOMAINS names,
#     so a copy renamed `_pre109_tone_ratio_db` passes it. That is deliberate:
#     #772's option 2 kept exactly that open, and a renamed copy no longer
#     makes the false claim this check is about (that the tool's number is the
#     shipped estimator's).
#   - `test_the_tool_computes_no_ratio_of_its_own` is the leg a rename does not
#     defeat, but it is a source scan over two named primitives. A copy built
#     from `np.fft` directly, from `scipy.signal.sosfiltfilt`, or from a third
#     `audio_measure` primitive would pass it. It catches the two shapes that
#     actually occurred here, not every conceivable one.

def test_no_private_copy_of_a_shipped_estimator():
    """No function defined in this module may share a name with a declared
    estimator. An alias bound to the shipped object (as `window` is) is fine;
    a definition of its own is the copy-drift this file exists to pin."""
    import audio_measure as am
    for name in am.DOMAINS:
        v = getattr(tool, name, None)
        assert v is None or v is getattr(rc, name, object()), (
            f"{name} is defined in measure_partial_balance rather than taken "
            f"from run_case -- the #636 copy-drift shape")
    assert not hasattr(tool, "tone_ratio_db")
    assert not hasattr(tool, "band_pair_db")


def test_the_tool_computes_no_ratio_of_its_own():
    """The two removed copies were built from `am.tone_amplitude` and
    `am.band_energy`. The tool measures through `partial_trajectory`; it has no
    business calling an estimator primitive directly."""
    src = pathlib.Path(tool.__file__).read_text()
    for prim in ("am.tone_amplitude(", "am.band_energy("):
        assert prim not in src, (
            f"{prim} in measure_partial_balance -- an estimator assembled here "
            f"is a copy that can drift from run_case's")


def test_the_usage_block_advertises_exactly_the_commands_that_dispatch():
    """`apparatus` was advertised in the module docstring and reachable from
    the CLI long after nothing ran it (#772). The usage block and the dispatch
    table are now one source checked against each other."""
    advertised = {line.split("measure_partial_balance.py")[1].split()[0]
                  for line in (tool.__doc__ or "").splitlines()
                  if "measure_partial_balance.py" in line}
    assert advertised, "the docstring no longer shows how to run the tool"
    assert advertised == set(tool.COMMANDS), (
        f"advertised {sorted(advertised)} but dispatches {sorted(tool.COMMANDS)}")
    assert "apparatus" not in tool.COMMANDS
    assert not hasattr(tool, "cmd_apparatus")


def test_the_operating_point_is_the_one_the_scorecard_scores():
    """Tautological while `OP` is derived from `RS_BALANCE_OP`/`CB_BALANCE_OP`
    (#772, which found `win_ms`, `hop_ms`, `t_end` and both search ranges
    duplicated by value). It is here to fail if they are ever re-hardcoded and
    then drift -- which is how this file came to carry a stale clamp (#636) and
    stale estimators (#772)."""
    for voice, scored in (("RS", rc.RS_BALANCE_OP), ("CB", rc.CB_BALANCE_OP)):
        op = tool.OP[voice]
        assert (op["win_ms"], op["hop_ms"], op["t_end"]) == \
            (scored["win_ms"], scored["hop_ms"], scored["t_end"]), voice
        assert tuple(op["lo"]) == tuple(scored["f_lo_range"]), voice
        assert tuple(op["hi"]) == tuple(scored["f_hi_range"]), voice
