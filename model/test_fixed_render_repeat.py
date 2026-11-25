"""Two isolated copies of a note must start from the same ladder state."""
import numpy as np
import pytest

import fixed_render as audition


@pytest.mark.parametrize("reset_each_note", [False, True], ids=["as-shipped", "reset-control"])
def test_repeat_starts_from_reset_in_both_filter_auditions(monkeypatch, reset_each_note):
    if reset_each_note:
        process = audition.fixed.LadderFx.process

        def from_reset(ladder, *args, **kwargs):
            ladder.reset()
            return process(ladder, *args, **kwargs)

        monkeypatch.setattr(audition.fixed.LadderFx, "process", from_reset)

    sr = audition.SR
    frames = 480
    second = frames * 2
    duration = frames / sr
    _, _, _, bass = audition.patches.MONO[0][1][0]
    seq = [(start / sr, 28, duration, bass) for start in (0, second)]
    monkeypatch.setattr(audition.patches, "MONO", [("repeat", seq, (second + frames) / sr)])
    recordings = {}

    def capture(path, audio):
        recordings[path.name] = audio

    monkeypatch.setattr(audition, "write", capture)
    audition.main()

    for name in ("repeat-float.wav", "repeat-fixed.wav"):
        audio = recordings[name]
        first, repeated = audio[:frames], audio[second:]
        assert len(repeated) == frames
        assert np.isfinite(audio).all()
        assert np.any(first) and np.any(repeated)
        assert not np.any(audio[frames:second])
        changed = np.count_nonzero(first != repeated)
        assert changed == 0, f"{name}: {changed}/{frames} repeated samples differ"
