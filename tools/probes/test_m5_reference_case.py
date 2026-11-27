"""Cross-load the two real Mini V3 phrases at the M5A reference boundary."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import measure_m5a_pulse_duty as pulse
import measure_m5a_signal_path as saw
import mono_m5a_score as scorer
import refusal


class ReachedRenderer(Exception):
    """Stop at dispatch so the check never needs to synthesize a phrase."""


def stop_render(sequence, *args, **kwargs):
    notes = tuple(event[1] for event in sequence)
    raise ReachedRenderer(notes)


def measure(reader):
    if reader == "scorer":
        return scorer.measure(case_id="M5A", keep_audio=False)
    if reader == "pulse":
        return pulse.measure(shapes=("square",))
    return saw.measure([14073], [0.75])


@pytest.mark.parametrize("reader", ["scorer", "pulse", "saw"])
def test_expected_m5a_reaches_the_renderer(monkeypatch, reader):
    monkeypatch.setattr(scorer.vf, "render_mono_fx", stop_render)
    with pytest.raises(ReachedRenderer) as caught:
        measure(reader)
    assert caught.value.args == ((84, 96),)


@pytest.mark.parametrize("reader", ["scorer", "pulse", "saw"])
def test_m5a_reader_refuses_m5b_before_render(monkeypatch, reader):
    monkeypatch.setattr(scorer, "MANIFEST", scorer.MANIFESTS["M5B"])
    monkeypatch.setattr(scorer.vf, "render_mono_fx", stop_render)
    with pytest.raises(refusal.Refused, match=r"M5B.*M5A"):
        measure(reader)
