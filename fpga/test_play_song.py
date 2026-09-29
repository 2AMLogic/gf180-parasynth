"""fpga/play_song.py: the song reaches the device exactly, and sounds as written.

Expectations come from the song text and equal temperament, never from the
code under test: a note's pitch is 440 * 2^((n - 69) / 12), and its frame is
beats * 60 / bpm * 48000."""
import contextlib
import io

import numpy as np
import pytest

import play_song as ps
import uart_host as uh
import verify_rolling_playback as vrp


def run(argv, factory=None):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = ps.main(argv, bridge_factory=factory)
    return rc, out.getvalue(), err.getvalue()


def test_note_names_are_midi():
    assert ps.note_number("C4") == 60 and ps.note_number("A4") == 69
    assert ps.note_number("A2") == 45 and ps.note_number("F#3") == 54
    assert ps.note_number("Bb2") == 46 and ps.note_number("C-1") == 0


def test_parse_song_rests_tempo_and_errors():
    bpm, notes = ps.parse_song("bpm: 90\nC3:1 R:0.5 # comment\n  F#3:1.5\n# all\nBb2:1")
    assert bpm == 90 and notes == [(48, 1.0), (None, 0.5), (54, 1.5), (46, 1.0)]
    for bad in ("C3", "H3:1", "C3:0", "R:1", ""):
        with pytest.raises(ValueError):
            ps.parse_song(bad)


def test_key_events_land_on_the_beat_grid():
    events, length = ps.key_events([(48, 1), (None, 1), (52, 2)], 120, gate=0.5)
    # 120 bpm = 24000 frames a beat
    assert events == [(0, "on", 48), (12000, "off", 48),
                      (48000, "on", 52), (72000, "off", 52)]
    assert length == 96000


def test_a_note_outside_the_qualified_range_is_refused_not_repitched():
    import sys
    sys.path.insert(0, str(ps.ROOT) + "/fpga/release")
    import qualified_domain as qd
    lo, hi = qd.playable_notes(uh.preset_regs(None)["detune"])     # (12, 126) today
    assert (lo, hi) == (12, 126)
    for argv in (["--notes", "G9:1"], ["--notes", "B-1:1"],          # 127, 11
                 ["--notes", "F#9:1", "--transpose", "1"]):
        rc, _out, err = run(argv + ["--dry-run"])
        assert rc == 2 and "REFUSED" in err, argv
    for argv in (["--notes", "F#9:1"], ["--notes", "C0:1"]):         # the edges play
        rc, _out, err = run(argv + ["--dry-run"])
        assert rc == 0, (argv, err)


def test_every_song_is_deliverable():
    for name in ps.SONGS:
        rc, out, err = run(["--song", name, "--dry-run"])
        assert rc == 0, err
        assert "preflight FEASIBLE" in out


@pytest.mark.parametrize("epoch", [0, 65000])
def test_the_device_fires_every_song_write_in_its_frame(epoch):
    # the contract device on simulated time, across a frame-counter wrap
    h = vrp.Harness(epoch)
    rc, out, err = run(["--song", "twinkle", "--port", "sim"], factory=h.factory)
    h.ser.run_until(h.clock.t + 2.0)
    assert rc == 0, err + out
    assert not h.sim.errors and not h.sim.drops
    bpm, notes = ps.parse_song(ps.SONGS["twinkle"][1])
    events, _ = ps.key_events(notes, ps.SONGS["twinkle"][0], gate=0.85)
    want = ps.song_writes(events, None)
    live = [w for w in h.sim.writes if w[5] == "live"]
    ev = [w for w in h.sim.writes if w[5] == "event"]
    patch = ps.build_commands([], preset=None, image="r1")
    assert [(w[1], w[2], w[3], w[4]) for w in live] == [c[1:] for c in patch]
    p0 = h.bridge.performance_origin
    # the device logs 16-bit frames and the song outlasts 2^16; writes are in
    # time order and closer than 2^16 frames apart, so unwrap sequentially
    got, prev = [], p0
    for w in ev:
        prev += (w[0] - prev) & 0xFFFF
        got.append((prev - p0, w[1], w[2], w[3], w[4]))
    assert got == want
    # and the song's own grid, independently: every gate-on lands on a beat
    ons = [g[0] for g in got if g[3] == 0x20]
    beat = 48000 * 60 / ps.SONGS["twinkle"][0]
    starts = np.cumsum([0] + [b for _n, b in notes[:-1]]) * beat
    assert len(ons) == len(notes)
    assert max(abs(o - s) for o, s in zip(ons, starts)) <= 6   # key writes precede the gate


def peak_cents(seg, sr, want):
    """Cents from `want` of the strongest spectral peak within a semitone of it
    (Hann window, zero-padded to 2^20 points: ~0.05 Hz bins)."""
    seg = (seg - seg.mean()) * np.hanning(len(seg))
    mag = np.abs(np.fft.rfft(seg, 1 << 20))
    f = np.fft.rfftfreq(1 << 20, 1 / sr)
    band = (f > want * 2 ** (-1 / 12)) & (f < want * 2 ** (1 / 12))
    return 1200 * np.log2(f[np.argmax(np.where(band, mag, 0))] / want)


def test_the_pitch_estimator_reads_a_known_sine():
    # a known answer, independent of the synth; the first estimator (an
    # autocorrelation) read the synth's correct 55 Hz sub-octave as 51.5 Hz
    t = np.arange(12000) / 48000
    assert abs(peak_cents(np.sin(2 * np.pi * 123.4 * t), 48000, 123.4)) < 1
    assert abs(peak_cents(np.sin(2 * np.pi * 123.4 * t), 48000, 116.5) - 99.6) < 2


def _written_pitches_ok(wav, notes):
    from scipy.io import wavfile
    sr, x = wavfile.read(wav)
    x = x.astype(np.float64)
    beat = 24000
    worst = 0.0
    for k, midi in enumerate(notes):
        seg = x[64 + k * beat + 6000: 64 + k * beat + 18000]   # the held middle
        want = 440.0 * 2 ** ((midi - 69) / 12)
        worst = max(worst, abs(peak_cents(seg, sr, want)),
                    abs(peak_cents(seg, sr, want / 2)))        # the -12 oscillator
    return worst


def test_the_model_plays_the_written_pitches(tmp_path):
    assert min(uh.preset_regs(None)["detune"]) == -12.0      # the sub-octave checked
    song, notes = "A2:1 C3:1 E3:1 A3:1", [45, 48, 52, 57]
    wav = tmp_path / "s.wav"
    rc, _out, err = run(["--notes", song, "--bpm", "120", "--render", str(wav)])
    assert rc == 0, err
    assert _written_pitches_ok(wav, notes) < 10               # measured 2.5 cents
    # control: the same song a semitone up must fail against the written pitches
    rc, _out, err = run(["--notes", song, "--bpm", "120", "--transpose", "1",
                         "--render", str(wav)])
    assert rc == 0, err
    assert _written_pitches_ok(wav, notes) > 80
