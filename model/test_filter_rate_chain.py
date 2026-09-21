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


def test_reconstruction_can_preserve_interpolator_headroom_without_changing_legacy_mode():
    # Two aligned positive samples make the default polyphase interpolator's
    # impulse response exceed full scale.  The experiment must retain this
    # legal internal Q1.15 headroom instead of saturating before the ladder.
    x = np.array([32767, 32767, 0, 0, 0, 0, 0, 0], dtype=np.int16)
    legacy = frc.reconstruct_q15(x, 2)
    headroom = frc.reconstruct_q15(x, 2, preserve_headroom=True)
    assert legacy.dtype == np.int16
    assert headroom.dtype == np.int32
    assert np.max(headroom) > 32767
    assert np.array_equal(legacy.astype(np.int32), np.clip(headroom, -32768, 32767))


def test_rate_converted_ladder_headroom_mode_reports_clamp_prevented_samples():
    x = np.array([32767, 32767, 0, 0, 0, 0, 0, 0], dtype=np.int16)
    chain = frc.RateConvertedLadder(2, preserve_headroom=True)
    n = len(x)
    chain.process(x, np.full(n, 8000), 0.0, 0.1,
                  g_q16=np.full(n, 1), k_q14=np.zeros(n, dtype=np.int64),
                  gain=1, ogain=1)
    assert chain.last_reconstruction["would_clip_fraction"] > 0
    assert chain.last_reconstruction["max_abs_q15"] > 32767


def _causal_convert(chunks, factor=2):
    converter = frc.CausalRateConverter(factor)
    result = []
    for chunk in chunks:
        result.append(converter.decimate(converter.reconstruct(chunk)))
    return np.concatenate(result), converter


def test_causal_converter_is_chunk_invariant_and_has_explicit_latency():
    rng = np.random.default_rng(7331)
    x = rng.integers(-32768, 32768, 2048, dtype=np.int16)
    whole, converter = _causal_convert([x])
    chunks, _ = _causal_convert([x[:1], x[1:257], x[257:1024], x[1024:]])
    assert np.array_equal(chunks, whole)
    assert converter.latency_frames == 20
    assert np.max(np.abs(whole.astype(np.int64))) <= 32768


def test_causal_converter_reset_repeats_impulse_and_known_tone_response():
    x = np.zeros(4096, dtype=np.int16)
    x[0] = 12000
    first, converter = _causal_convert([x])
    peak = int(np.argmax(np.abs(first)))
    assert peak == converter.latency_frames
    converter.reset()
    repeated = np.concatenate([converter.decimate(converter.reconstruct(x))])
    assert np.array_equal(first, repeated)

    sr = 48_000
    t = np.arange(16_384) / sr
    tone = np.rint(0.2 * 32768 * np.sin(2 * np.pi * 1000 * t)).astype(np.int16)
    converted, _ = _causal_convert([tone])
    rms = np.sqrt(np.mean((converted[256:-256] / 32768.0) ** 2))
    assert rms == pytest.approx(0.2 / np.sqrt(2), abs=3e-4)


def test_causal_decimator_preserves_the_ladders_19_bit_output_headroom():
    high_rate = np.full(4096, 40000, dtype=np.int32)
    converter = frc.CausalRateConverter(2)
    clipped16 = converter.decimate(high_rate, output_bits=16)
    converter.reset()
    preserved19 = converter.decimate(high_rate, output_bits=19)
    assert np.max(clipped16[256:]) == 32767
    assert np.mean(preserved19[256:]) == pytest.approx(40000, abs=1)


def test_causal_ladder_adapter_is_chunk_invariant_and_resettable():
    rng = np.random.default_rng(1977)
    x = rng.integers(-9000, 9001, 384, dtype=np.int16)
    g = np.full(len(x), 1200, dtype=np.int64)
    k = np.full(len(x), 4096, dtype=np.int64)
    cfg = {"oversample": 2}

    def make():
        return frc.RateConvertedLadder(2, cfg, preserve_headroom=True, causal=True)

    whole_chain = make()
    whole = whole_chain.process(x, None, 0.0, 0.7, g_q16=g, k_q14=k,
                                k=0, gain=1 << 16, ogain=1 << 16)
    chunk_chain = make()
    pieces = []
    for start, stop in ((0, 1), (1, 97), (97, 257), (257, len(x))):
        pieces.append(chunk_chain.process(
            x[start:stop], None, 0.0, 0.7, g_q16=g[start:stop],
            k_q14=k[start:stop], k=0, gain=1 << 16, ogain=1 << 16))
    assert np.array_equal(np.concatenate(pieces), whole)
    assert whole_chain.latency_frames == 20
    chunk_chain.reset()
    assert np.array_equal(chunk_chain.process(
        x, None, 0.0, 0.7, g_q16=g, k_q14=k,
        k=0, gain=1 << 16, ogain=1 << 16), whole)


def test_causal_ladder_output_responds_to_a_control_step_at_a_bounded_time():
    n, step = 512, 100
    x = np.full(n, 12000, dtype=np.int16)
    k = np.zeros(n, dtype=np.int64)
    g_before = np.full(n, 2000, dtype=np.int64)
    g_after = g_before.copy()
    g_after[step:] = 12000

    def render(g):
        chain = frc.RateConvertedLadder(2, {"oversample": 2},
                                        preserve_headroom=True, causal=True)
        return chain.process(x, None, 0.0, 0.5, g_q16=g, k_q14=k,
                             k=0, gain=1 << 16, ogain=1 << 16)

    changed = np.flatnonzero(render(g_after) != render(g_before))
    assert len(changed) > 0
    # FIR precursors can create a small response before its 50% energy point;
    # the bounded assertion catches accidental full-block or unbounded delay.
    assert changed[0] >= step
    assert changed[0] <= step + 10


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


def test_voice_exposes_the_causal_headroom_candidate_and_latency_trace():
    import voice_fx as vf

    voice = vf.VoiceFx(oversample_2x=True,
                       ladder_cfg={**vf.LADDER_CFG, "oversample": 2},
                       rate_converted_ladder=True,
                       preserve_filter_headroom=True, causal_filter=True)
    output = voice.note(84, 0.02, gate=0.015, waves=("saw",) * 3,
                        mix=(1.0, 0.0, 0.0), cutoff=(8_000, 8_000),
                        q=0.0, drive=0.75)
    assert output.shape == (int(0.02 * vf.SR),)
    assert voice.ladder.latency_frames == 20
    assert voice.trace["filter_reconstruction"]["preserved"] is True


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
