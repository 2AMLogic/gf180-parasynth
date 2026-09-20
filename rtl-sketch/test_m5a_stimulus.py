from __future__ import annotations

import pathlib

import pytest

import verify_synth_top as top

MANIFEST = pathlib.Path(__file__).resolve().parents[1] / "docs/scorecard/mono-m5a-miniv3/manifest.json"


def test_m5a_smoke_writes_selected_pulse29_through_the_register_image():
    writes, _tail, report = top.m5a_script(str(MANIFEST), smoke=True,
                                           pulse_shape="pulse29")
    pulse_writes = [w for w in writes if w[3] == top.A.A_WAVE]
    assert report["pulse_shape"] == "pulse29"
    assert any(w[4] == top.WAVE_CODE["pulse29"] for w in pulse_writes)
    assert report["events"] and {event["wave"] for event in report["events"]} == {"saw", "pulse"}


def test_m5a_stimulus_refuses_waveforms_without_a_supported_pulse_duty():
    with pytest.raises(ValueError, match="supported rectangular shape"):
        top.m5a_script(str(MANIFEST), smoke=True, pulse_shape="tri")
