"""Ground-truth tests for the offline ladder-rate conversion experiment."""
import numpy as np
import pytest

import filter_rate_chain as frc


@pytest.mark.parametrize("factor", [2, 4])
def test_rate_conversion_preserves_a_known_in_band_tone(factor):
    sr = 48_000
    t = np.arange(16_384) / sr
    x = np.rint(0.25 * 32768 * np.sin(2 * np.pi * 1_000 * t)).astype(np.int16)
    high = frc.reconstruct_q15(x, factor)
    back = frc.decimate_q15(high, factor)
    assert len(high) == factor * len(x)
    assert len(back) == len(x)
    # Discard FIR boundary transients; RMS is known independently from the
    # filter under test and includes Q15 quantization tolerance.
    expected = 0.25 / np.sqrt(2)
    measured = np.sqrt(np.mean((back[256:-256] / 32768.0) ** 2))
    assert measured == pytest.approx(expected, abs=2e-4)


@pytest.mark.parametrize("factor", [2, 4])
def test_rate_conversion_rejects_a_known_out_of_band_tone(factor):
    high_sr = 48_000 * factor
    t = np.arange(16_384 * factor) / high_sr
    # 30 kHz cannot exist in the 48 kHz output. Without a proper decimator it
    # folds to 18 kHz, making this a direct negative control for downsampling.
    x = np.rint(0.25 * 32768 * np.sin(2 * np.pi * 30_000 * t)).astype(np.int16)
    back = frc.decimate_q15(x, factor)
    rms = np.sqrt(np.mean((back[256:-256] / 32768.0) ** 2))
    assert rms < 1e-3


def test_rate_converted_ladder_executes_and_returns_base_rate_words():
    n = 512
    cutoff = np.full(n, 8_000, dtype=np.int64)
    g, k, gain, ogain = 1, np.zeros(n, dtype=np.int64), 1, 1
    chain = frc.RateConvertedLadder(factor=4)
    out = chain.process(np.zeros(n, dtype=np.int16), cutoff, 0.0, 0.0,
                        g_q16=np.full(n, g, dtype=np.int64),
                        k_q14=k, gain=gain, ogain=ogain)
    assert out.shape == (n,)
    assert out.dtype == np.int32
    assert np.count_nonzero(out) == 0


@pytest.mark.parametrize("factor", [2, 4])
def test_voice_uses_rate_matched_roms_with_the_reconstructed_ladder(factor):
    import voice_fx as vf

    cfg = {**vf.LADDER_CFG, "oversample": factor}
    voice = vf.VoiceFx(oversample_2x=True, ladder_cfg=cfg,
                       rate_converted_ladder=True)
    output = voice.note(84, 0.08, gate=0.06, waves=("saw", "saw", "saw"),
                       mix=(1.0, 0.0, 0.0), cutoff=(8_000, 8_000), q=0.0,
                       drive=0.75)
    assert output.shape == (int(0.08 * vf.SR),)
    assert voice.ladder.factor == factor
    assert voice.g_rom.tolist() == vf.make_g_rom(vf.GROM_BITS, factor).tolist()


def test_pulse479_is_exact_model_only_challenger():
    import voice_fx as vf

    assert vf.DUTY["pulse479"] == round((1 << vf.PHASE_BITS) * 0.479)
    assert "pulse479" in vf.BLEP_SHAPES and "pulse479" in vf.TWO_EDGE
    assert "pulse479" not in vf.WAVE_CODE


def test_naive_hold_control_is_rejected_by_the_rate_conversion_test():
    factor = 2
    high_sr = 48_000 * factor
    t = np.arange(16_384 * factor) / high_sr
    out_of_band = np.rint(0.25 * 32768 * np.sin(2 * np.pi * 30_000 * t)).astype(np.int16)
    broken_drop_decimator = out_of_band[::factor]
    wrong_rms = np.sqrt(np.mean((broken_drop_decimator[256:-256] / 32768.0) ** 2))
    filtered_rms = np.sqrt(np.mean((frc.decimate_q15(out_of_band, factor)[256:-256] / 32768.0) ** 2))
    assert wrong_rms > 0.1
    assert filtered_rms < 1e-3
