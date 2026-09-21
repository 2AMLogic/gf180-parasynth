"""Controls for the reference-integrity apparatus configuration."""
from __future__ import annotations

import numpy as np

from model import reference_integrity as ri


def test_miniv3_integrity_render_uses_the_requested_host_block(monkeypatch):
    observed = {}

    class MiniV3Stub:
        I = {"lvl_ext": 1, "ext_sw": 2, "lvl_o1": 3, "o1": 4,
             "cutoff": 5, "emphasis": 6}

        def __init__(self, *, block):
            observed["block"] = block

        def set(self, _index, _value):
            pass

        def render(self, _audio, seconds):
            return np.ones(int(seconds * ri.SR), dtype=np.float32) * 0.1

    monkeypatch.setattr(ri.rr, "MiniV3Rig", MiniV3Stub)
    audio = ri.steady("miniv3", seconds=1.0, source="smooth", block=16)
    assert observed["block"] == 16
    assert len(audio) == ri.SR

