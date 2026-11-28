"""`filter_corner_recalibration.reference_side`'s handler: a refusal in, a refusal out (#1777).

Section 2 of `tools/probes/filter_corner_recalibration.py` asks
`refprofile.load_clip` whether each F1 reference clip is cached on this host
and prints `REFUSED: <reason>` when it is not. Its handler was
`except Exception`, so whatever the reader raised was printed as a refusal: a
`KeyError` in the clip table or a `TypeError` inside `refprofile` read as "the
precondition is missing" -- an execution error filed as a REFUSED outcome,
the #1771 shape (PR #1781) at the first of the two probe sites it named.

`load_clip` refuses in `refprofile.Refused`, a `refusal.Refused` since #815,
and raises nothing else by design: not a clip in the profile, not in the
cache, drifted from the profile, not usable audio. The handler now catches the
base class, so the callee's own refusal is still a `REFUSED:` line and
anything else propagates with its traceback.

THE PAIR. An injected `KeyError` must propagate out of `reference_side`, and
an injected refusal must still print `REFUSED:` for each of F1A/F1B/F1C. RED
FIRST against the unfixed handler (2026-11-28, origin/main @ 1c56aaf6): 3
failed, 2 passed -- every execution-error control with `DID NOT RAISE`. Seen
directly too, on `git show HEAD:tools/probes/filter_corner_recalibration.py`
run outside pytest: the injected `KeyError` printed, for all three clips, as
`REFUSED: 'injected bug in the clip table'`. With the fix: 5 passed.

The positive controls inject TWO classes on purpose. `rp.Refused` is the
callee's own; `rc.Refused` (which is `preparation_contract.Refused`) is a
CROSS-MODULE sibling of it, so a handler narrowed to the callee's class
(#1646's shape) lets that one escape and its control fails. Checked by
mutation: with the handler as `except rp.Refused`, 1 failed, 4 passed -- the
cross-module control; with `except Exception` put back, the three
execution-error controls fail again.

WHAT IS NOT CLAIMED. No real input is known that makes `load_clip` raise
anything but a `Refused`; the negative controls exercise the handler's class
boundary, not a production path. And `reference_side` only prints -- section 2
is "refused on this host" by design and files nothing -- so the propagated
error's route is the probe's own traceback and exit 1, which this file does
not drive.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FCR = ROOT / "tools" / "probes" / "filter_corner_recalibration.py"

F1_CLIPS = ("F1A", "F1B", "F1C")


def _load():
    spec = importlib.util.spec_from_file_location("filter_corner_recalibration_under_test", FCR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _raises(exc):
    def f(*_a, **_k):
        raise exc
    return f


@pytest.fixture
def probe(monkeypatch):
    mod = _load()
    # The frozen profile is not what is under test; only what `load_clip` raises.
    monkeypatch.setattr(mod.rp, "load_profile", lambda: {})
    return mod


@pytest.mark.parametrize("bug", [
    KeyError("injected bug"), TypeError("injected bug"), RuntimeError("injected bug")],
    ids=["KeyError", "TypeError", "RuntimeError"])
def test_reference_side_lets_a_reader_bug_escape(probe, monkeypatch, capsys, bug):
    """#1777's injected-bug control: an exception `load_clip` raises that is not
    a refusal is a bug in the reader or in the probe, and must not be printed
    as `REFUSED:`."""
    monkeypatch.setattr(probe.rp, "load_clip", _raises(bug))
    with pytest.raises(type(bug), match="injected bug"):
        probe.reference_side()
    assert "REFUSED: injected bug" not in capsys.readouterr().out


@pytest.mark.parametrize("family", ["callee", "cross-module"])
def test_reference_side_still_prints_a_refusal_as_refused(probe, monkeypatch, capsys, family):
    """Its paired positive control: a refusal the reader raises is still one
    `REFUSED:` line per F1 clip, and no clip is reported CACHED."""
    cls = probe.rp.Refused if family == "callee" else probe.rc.Refused
    monkeypatch.setattr(probe.rp, "load_clip",
                        _raises(cls("injected: the clip is not in the cache")))
    probe.reference_side()
    out = capsys.readouterr().out
    assert out.count("REFUSED: injected: the clip is not in the cache") == len(F1_CLIPS), out
    assert "CACHED" not in out
