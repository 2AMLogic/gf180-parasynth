"""Isolated notes in the filter-only audition must start from the same state."""

from functools import cache

import fixed_render
import numpy as np
import pytest


@pytest.mark.parametrize(
    "cache_ladder", [False, True], ids=["per-note", "cached-control"]
)
@pytest.mark.parametrize(
    "note, patch",
    [(28, fixed_render.patches._BASS), (67, fixed_render.patches._LEAD)],
    ids=["bass", "lead"],
)
def test_repeat_starts_from_reset_in_both_filter_auditions(
    monkeypatch, cache_ladder, note, patch
):
    if cache_ladder:
        # Restore the original lifetime: one mutable ladder for this whole patch.
        monkeypatch.setattr(
            fixed_render.fixed, "LadderFx", cache(fixed_render.fixed.LadderFx)
        )

    frames = 480
    sr = fixed_render.SR
    seq = [(start / sr, note, frames / sr, patch) for start in (0, 2 * frames)]
    monkeypatch.setattr(
        fixed_render.patches, "MONO", [("repeat", seq, 3 * frames / sr)]
    )
    recordings = {}

    def capture(path, audio):
        recordings[path.name] = audio

    monkeypatch.setattr(fixed_render, "write", capture)
    fixed_render.main()

    for name in ("repeat-float.wav", "repeat-fixed.wav"):
        audio = recordings[name]
        assert audio.shape == (3 * frames,)
        assert np.isfinite(audio).all()
        first, gap, repeat = np.split(audio, 3)
        assert np.any(first) and np.any(repeat)
        assert not np.any(gap)
        changed = np.count_nonzero(first != repeat)
        print(f"{name}: {changed}/{frames} repeated samples differ")
        if cache_ladder and name == "repeat-fixed.wav":
            assert changed > 0, "the repeat fixture missed the cached ladder"
        else:
            assert changed == 0, f"{name}: {changed}/{frames} repeated samples differ"
