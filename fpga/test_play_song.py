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


LONG_REST = (120, "C3:1 R:4 E3:1 R:6 G3:1")      # write gaps of 99600 and 147600 frames > 2^16
FIXTURES = {"twinkle": ps.SONGS["twinkle"], "long-rest": LONG_REST}


def naive_unwrap(frames16, want_frames, p0):
    """The original sequential unwrap: unambiguous only if consecutive writes
    are under 2^16 frames apart (a modulo-2^16 step cannot show a longer one).
    The log cannot tell, so the expected schedule is checked and an unsafe
    fixture REFUSES rather than being silently mis-unwrapped."""
    gaps = [b - a for a, b in zip(want_frames, want_frames[1:])]
    assert max(gaps) < 0x10000, f"unsafe sequential unwrap: gap {max(gaps)} frames"
    got, prev = [], p0
    for f in frames16:
        prev += (f - prev) & 0xFFFF
        got.append(prev - p0)
    return got


def schedule_unwrap(frames16, want_frames, p0):
    """Unwrap each 16-bit log entry to the absolute frame nearest its expected
    one. Unlike the sequential unwrap it does not depend on the gap between
    writes, but it is anchored to the answer, so it has a blind spot:

    - a write delivered an exact multiple of 2^16 frames late (or early) is
      unwrapped straight back onto its due frame -- UNDETECTABLE here;
    - any other error beyond +-0x8000 frames is aliased to the wrong size and
      possibly the wrong sign (40000 late reads as 25536 early).

    Only errors within +-0x8000 are reported truthfully. That is inherent in
    the device's 16-bit frame log, and acceptable only because the harness has
    no other timeline; test_schedule_unwrap_cannot_see_a_whole_wrap_error pins
    the limitation so it is not mistaken for coverage."""
    out = []
    for f, due in zip(frames16, want_frames):
        d = ((f - (p0 + due) + 0x8000) & 0xFFFF) - 0x8000
        out.append(due + d)
    return out


def gate_offs_from_text(text, bpm, gate):
    """Note-off frames straight from the song text, independent of key_events:
    round(start + gate * beats * 60 / bpm * 48000) for each sounding note."""
    t, offs = 0.0, []
    for tok in text.split():
        name, beats = tok.split(":")
        beats = float(beats)
        if name != "R":
            offs.append(round(t + gate * beats * 60 / bpm * 48000))
        t += beats * 60 / bpm * 48000
    return offs


def test_naive_unwrap_refuses_the_long_rest_fixture():
    # control: the original sequential unwrap on the long-rest schedule
    events, _ = ps.key_events(ps.parse_song(LONG_REST[1])[1], LONG_REST[0], gate=0.85)
    want = ps.song_writes(events, None)
    with pytest.raises(AssertionError, match="unsafe sequential unwrap"):
        naive_unwrap([(1000 + w[0]) & 0xFFFF for w in want],
                     [w[0] for w in want], 1000)
    # and if it did not refuse it would be wrong: silently mis-unwrapped
    prev, bad = 1000, []
    for w in want:
        prev += ((1000 + w[0]) - prev) & 0xFFFF
        bad.append(prev - 1000)
    assert bad != [w[0] for w in want]


def test_schedule_unwrap_cannot_see_a_whole_wrap_error():
    # a recorded LIMITATION, not coverage: the anchored unwrap maps a write
    # delivered exactly 2^16 frames late back onto its due frame
    events, _ = ps.key_events(ps.parse_song(LONG_REST[1])[1], LONG_REST[0], gate=0.85)
    due = [w[0] for w in ps.song_writes(events, None)]
    p0 = 1000
    for k in (len(due) - 1, len(due) // 2):
        for shift in (0x10000, -0x10000, 2 * 0x10000):
            late = [p0 + d for d in due]
            late[k] += shift
            assert schedule_unwrap([f & 0xFFFF for f in late], due, p0) == due
    # control: an error inside +-0x8000 is seen at its true size and sign
    late = [p0 + d for d in due]
    late[-1] += 1000
    assert schedule_unwrap([f & 0xFFFF for f in late], due, p0)[-1] == due[-1] + 1000
    # and one beyond it aliases: 40000 frames late reads as 25536 early
    late[-1] += 39000
    assert schedule_unwrap([f & 0xFFFF for f in late], due, p0)[-1] == due[-1] - 25536


@pytest.mark.parametrize("fixture", sorted(FIXTURES))
@pytest.mark.parametrize("epoch", [0, 65000])
def test_the_device_fires_every_song_write_in_its_frame(epoch, fixture):
    # the contract device on simulated time, across a frame-counter wrap
    song_bpm, text = FIXTURES[fixture]
    h = vrp.Harness(epoch)
    rc, out, err = run(["--notes", text, "--bpm", str(song_bpm), "--port", "sim"],
                       factory=h.factory)
    bpm, notes = ps.parse_song(text)
    bpm = bpm or song_bpm
    events, length = ps.key_events(notes, bpm, gate=0.85)
    h.ser.run_until(h.clock.t + length / 48000 + 2.0)
    assert rc == 0, err + out
    assert not h.sim.errors and not h.sim.drops
    want = ps.song_writes(events, None)
    live = [w for w in h.sim.writes if w[5] == "live"]
    ev = [w for w in h.sim.writes if w[5] == "event"]
    patch = ps.build_commands([], preset=None, image="r1")
    assert [(w[1], w[2], w[3], w[4]) for w in live] == [c[1:] for c in patch]
    p0 = h.bridge.performance_origin
    assert len(ev) == len(want)
    # the device logs 16-bit frames and a song can outlast, and rest longer
    # than, 2^16: unwrap each entry against its expected frame, not its neighbour.
    # Blind spot: a write off by exactly k*2^16 frames unwraps onto its due
    # frame and passes; other errors beyond +-0x8000 alias (see schedule_unwrap)
    frames = schedule_unwrap([w[0] for w in ev], [w[0] for w in want], p0)
    got = [(f, w[1], w[2], w[3], w[4]) for f, w in zip(frames, ev)]
    assert got == want
    if fixture == "twinkle":
        # this fixture is the one the sequential unwrap is safe for: it agrees
        assert naive_unwrap([w[0] for w in ev], [w[0] for w in want], p0) == frames
    # and the song's own grid, independently: every gate-on lands on a beat
    ons = [g[0] for g in got if g[3] == 0x20]
    beat = 48000 * 60 / bpm
    starts, t = [], 0.0
    for n, b in notes:
        if n is not None:
            starts.append(t * beat)
        t += b
    assert len(ons) == len(starts) == sum(1 for n, _ in notes if n is not None)
    assert max(abs(o - s) for o, s in zip(ons, starts)) <= 6   # key writes precede the gate
    # note-offs, from the song text alone (not key_events / song_writes)
    offs = [g[0] for g in got if g[3] == 0x21]
    assert len(offs) == len(starts)
    assert max(abs(o - e) for o, e in zip(offs, gate_offs_from_text(text, bpm, 0.85))) <= 6


def test_the_note_off_check_catches_a_wrong_gate_fraction():
    # control: schedules built at the wrong gate must disagree with the text
    for text_bpm, text in (FIXTURES["twinkle"], LONG_REST):
        notes = ps.parse_song(text)[1]
        for wrong in (0.5, 0.75, 1.0):
            events, _ = ps.key_events(notes, text_bpm, gate=wrong)
            offs = [f for f, op, _m in events if op == "off"]
            worst = max(abs(o - e) for o, e in
                        zip(offs, gate_offs_from_text(text, text_bpm, 0.85)))
            assert worst > 6, (text_bpm, wrong)
        events, _ = ps.key_events(notes, text_bpm, gate=0.85)
        offs = [f for f, op, _m in events if op == "off"]
        assert offs == gate_offs_from_text(text, text_bpm, 0.85)


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
