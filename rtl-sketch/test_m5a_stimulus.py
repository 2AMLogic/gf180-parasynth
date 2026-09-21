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


def test_m5a_saw_candidate_settings_are_written_over_spi_and_pulse_is_restored():
    writes, _tail, report = top.m5a_script(
        str(MANIFEST), smoke=True, pulse_shape="pulse29",
        saw_cutoff_hz=20_000, saw_volume_correction_db=-0.45428)
    controls = [w[3:] for w in writes
                if w[3] in (top.A.A_CUT_LO, top.A.A_CUT_HI, top.A.A_VOL)]
    base = top.vf.VoiceFx.patch_regs(cutoff=(14_073, 14_073), vol=0.45)
    scale = 10 ** (-0.45428 / 20.0)
    saw = top.vf.VoiceFx.patch_regs(cutoff=(20_000, 20_000), vol=0.45 * scale)
    assert controls[-6:] == [
        (top.A.A_CUT_LO, saw["cut_lo"]), (top.A.A_CUT_HI, saw["cut_hi"]),
        (top.A.A_VOL, saw["vol"]),
        (top.A.A_CUT_LO, base["cut_lo"]), (top.A.A_CUT_HI, base["cut_hi"]),
        (top.A.A_VOL, base["vol"]),
    ]
    assert report["saw_cutoff_hz"] == 20_000
    assert report["saw_volume_correction_db"] == pytest.approx(-0.45428)


@pytest.mark.parametrize("kwargs", [
    {"saw_cutoff_hz": 30_000},
    {"saw_volume_correction_db": 13.0},
    {"saw_volume_correction_db": float("nan")},
])
def test_m5a_stimulus_refuses_unsupported_saw_overrides(kwargs):
    with pytest.raises(ValueError, match="saw (cutoff|volume correction)"):
        top.m5a_script(str(MANIFEST), smoke=True, **kwargs)
