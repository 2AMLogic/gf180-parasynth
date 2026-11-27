#!/usr/bin/env python3
"""The separator has to be right before anything measured with it is.

`model/drum_fit.py` decides how much of a struck hit is noise. Every number in
docs/drum-verification.md section 8 rests on it, including the one that
overturned "our snare's noise is 16 dB too quiet", so it is validated here two
ways and the method it replaced is kept as a control that must FAIL:

  1. against synthetic mixtures whose noise share is exact by construction;
  2. against our own renders, where the tonal path and the noise path can be
     rendered separately and the true share is exact arithmetic.

A control that cannot be shown wrong proves nothing, so
`test_the_whole_span_hann_split_is_the_artefact_it_is_recorded_as` asserts the
withdrawn method's error, not just the new method's accuracy.

The reference readers are held to their inventory as well (#1420). Each consumes
the 25 takes the knob dial encodes for its voice, by name, and refuses its
own law when one is absent, is a directory, or has been replaced by a file of
another name. A glob sees only what is on disk, so a missing take used to leave
a row averaged over four takes with nothing recording the loss.

    .venv/bin/python -m pytest model/test_drum_fit.py -q
"""
import glob, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "audition"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pytest
import drum_fit as df
import drums_fx as dx
from dsp import SR

SHARES = df.SELF_TEST_TARGETS


@pytest.mark.parametrize("target", SHARES)
def test_separator_recovers_a_known_noise_share(target):
    """Two damped sinusoids plus band-limited noise, scaled so the noise
    carries exactly `target` of the energy -- including where the noise decays
    faster than the tone, which is our snare's case.

    The bound is the method's MEASURED accuracy, not a round number: the
    residual is the noise plus whatever of the tone the fit could not take
    out, so the estimate is biased slightly high and the bias grows with the
    noise. Below a 30 % share -- where both our kit and the machine's SNAPPY
    5.0 sit, so where every number in the SD comparison is read -- it is
    within 0.6 percentage points; above, within 1.5."""
    x = df.synthetic_mixture(target)
    got = df.noise_share(x, 44100, df.VOICE_MODES["SD"])["share"]
    assert df.share_failure(target, got) is None, df.share_failure(target, got)


def test_the_cli_self_test_passes_on_the_unmodified_cases():
    assert df.main(["--self-test"]) == 0


def test_a_nonfinite_estimate_fails_the_cli_self_test(capsys):
    """Injected control: NaN at every target used to print eight NaNs, report
    'worst error 0.00' and exit 0."""
    from unittest.mock import patch
    with patch.object(df, "noise_share", return_value={"share": float("nan")}):
        rc = df.main(["--self-test"])
    out = capsys.readouterr().out
    assert rc != 0
    assert "worst error 0.00" not in out
    assert out.count("nonfinite") == len(df.SELF_TEST_TARGETS)


@pytest.mark.parametrize("target,got", [(0.5, 0.5 - 0.004), (0.1, float("nan")),
                                         (0.1, 0.1 + 0.007), (0.8, 0.8 + 0.016)])
def test_share_failure_rejects_what_it_must(target, got):
    assert df.share_failure(target, got) is not None


def test_separator_recovers_the_modes_it_subtracts():
    """The fitted frequencies and decays must be the ones the mixture was
    built from -- otherwise the residual is not the noise, it is the noise
    plus whatever the fit missed. Every decay reported is a tau."""
    x = df.synthetic_mixture(0.2766)
    m = df.noise_share(x, 44100, df.VOICE_MODES["SD"])["modes"]
    assert m[0]["hz"] == pytest.approx(173.0, rel=0.01)
    assert m[1]["hz"] == pytest.approx(336.0, rel=0.01)
    assert m[0]["tau_ms"] == pytest.approx(30.0, rel=0.10)


def _render_sd(override=None, seconds=0.5, accent=1.0):
    kit = dx.kit_808()
    if override:
        kit = [(a, override.get(a, v)) for a, v in kit]
    d = dx.DrumsFx()
    n = int(seconds * SR)
    dm, bd = d.play(dx.hit_writes([(10, dx.SD, accent)], kit), n)
    return dx.output_fx(np.zeros(n), 0, dm, dx.accent_reg(0.45), bd,
                        dx.accent_reg(0.45)).astype(np.float64) / 32768.0


def _peak_addr(e):
    return dx.A_ENV + e * dx.ENV_STRIDE + 1


def _true_share(override=None):
    """Exact: the snare's tonal path and its noise path are separate paths into
    separate modes with LIN nonlinearities, so rendering each alone and summing
    reproduces the full render bit for bit, and the true noise share is
    arithmetic rather than an estimate."""
    override = dict(override or {})
    full = _render_sd(override)
    tone = _render_sd({**override, _peak_addr(dx.E_SDN): 0})
    noise = _render_sd({**override, _peak_addr(dx.E_SDX): 0})
    assert np.allclose(full, tone + noise, atol=2e-3), "the two paths are not additive"
    i = max(0, int(np.argmax(np.abs(full) > 0.02 * np.abs(full).max())) - int(5e-4 * SR))
    n = int(0.25 * SR)
    f, nn = full[i:i + n], noise[i:i + n]
    return float((nn @ nn) / (f @ f)), full


def test_separator_matches_the_exact_share_of_our_own_render():
    """The one case where the truth is not a construction but our own
    arithmetic: within 1 percentage point of it."""
    truth, full = _true_share()
    got = df.noise_share(full, SR, df.VOICE_MODES["SD"])["share"]
    assert abs(got - truth) < 0.01, f"truth {truth:.4f}, separator {got:.4f}"


# The snare's snappy envelope exactly as revision 6 shipped it: tau 15 ms,
# peak 0.5. A literal, so the control below keeps measuring the case section
# 8.0 was written from however the kit moves afterwards.
REV6_SNAPPY = {dx.A_ENV + dx.E_SDN * dx.ENV_STRIDE + 1: dx.peak_reg(0.5),
               dx.A_ENV + dx.E_SDN * dx.ENV_STRIDE + 2: dx.rate_reg(15e-3)}


def _hann_split(x):
    """The withdrawn method verbatim: power above and below 700 Hz of a
    Hann-windowed 500 ms span, `drum_verify.measure`'s body/air split."""
    x = df.trim_onset(x, SR)[:int(0.5 * SR)]
    S = np.abs(np.fft.rfft(x * np.hanning(len(x)), 1 << 18)) ** 2
    f = np.fft.rfftfreq(1 << 18, 1.0 / SR)
    return float(S[(f >= 700) & (f < 16000)].sum() / S[(f > 20) & (f < 16000)].sum())


def test_the_whole_span_hann_split_is_the_artefact_it_is_recorded_as():
    """The control, in the exact case section 8.0 was written from: revision
    6's snappy envelope, where the noise decays faster than the tone and the
    split therefore under-reports by more than a factor of five. The snappy
    registers are pinned here as literals so this keeps testing THAT case, not
    whatever the kit happens to hold. If it ever stops failing, the diagnosis
    in docs/drum-verification.md section 8 is wrong and its numbers have to be
    re-derived."""
    truth, full = _true_share(REV6_SNAPPY)
    split = _hann_split(full)
    assert split < truth / 5.0, (
        f"the whole-span Hann split reported {split * 100:.2f} % against a true "
        f"{truth * 100:.2f} %; it was supposed to be wrong by more than 5x")


def test_the_withdrawn_split_reverses_when_the_noise_outlasts_the_tone():
    """The same method on the CURRENT kit, and the sharper statement of what
    is wrong with it. Revision 7's snappy envelope (tau 30 ms) outlasts the
    upper body mode, so the whole-span Hann window now lands on noise rather
    than on tone and the split OVER-reports -- on the same voice, by the same
    code, in the opposite direction.

    That is the mechanism section 8.0 names ("it reports whichever component
    decays slowest") shown rather than argued, and it is why the replacement
    is a separator and not a re-tuned split: no choice of split frequency
    fixes a window that weights t = 10 ms by 0.0039 against t = 250 ms by 1.0.
    """
    truth, full = _true_share()
    split = _hann_split(full)
    assert split > truth * 1.4, (
        f"on the current kit the split reported {split * 100:.2f} % against a true "
        f"{truth * 100:.2f} %; it was supposed to over-report by at least 1.4x")
    rev6_truth, rev6_full = _true_share(REV6_SNAPPY)
    assert _hann_split(rev6_full) < rev6_truth, \
        "the same method must still under-report revision 6's snare: the direction is the claim"


@pytest.mark.parametrize("target", (0.05, 0.2766, 0.50))
def test_the_withdrawn_split_is_wrong_on_synthetic_mixtures_too(target):
    """Not just on our render: on mixtures with a known share, the whole-span
    Hann split under-reports by 5-9x whenever the noise decays faster than the
    tone. This is why the 47.3 % / 1.2 % pair could not be compared."""
    x = df.synthetic_mixture(target)
    y = df.trim_onset(x, 44100)[:int(0.5 * 44100)]
    S = np.abs(np.fft.rfft(y * np.hanning(len(y)), 1 << 18)) ** 2
    f = np.fft.rfftfreq(1 << 18, 1.0 / 44100)
    tot = S[(f > 20) & (f < 16000)].sum()
    split = float(S[(f >= 700) & (f < 16000)].sum() / tot)
    assert split < target / 4.0, f"target {target}, split {split}"


def test_knob_position_reads_a_ratio_off_a_curve_and_refuses_below_it():
    """A measured ratio maps to a knob position by interpolation in dB, and a
    ratio below the curve's own floor returns NaN rather than 0 -- 'quieter
    than the machine is at knob 0' is not 'at knob 0'."""
    law = {0.0: (0.047, 0.212, 0.07), 2.5: (0.050, 0.219, 0.07),
           5.0: (0.277, 0.619, 0.13), 7.5: (0.560, 1.153, 0.21),
           10.0: (0.719, 1.652, 0.30)}
    assert df.knob_position(0.619, law) == pytest.approx(5.0, abs=0.05)
    assert 2.5 < df.knob_position(0.40, law) < 5.0
    assert np.isnan(df.knob_position(0.10, law))


def test_a_short_window_fft_invents_a_peak_where_band_energy_does_not():
    """The withdrawn claim, reproduced and then refuted on a signal whose
    content is known exactly.

    A 50 Hz damped sinusoid -- one pure tone, nothing else -- gated to its
    first 4 ms and transformed has 250 Hz bins, so its apparent spectral peak
    is bin 1 at 250 Hz, and shortening or lengthening the gate walks that
    "peak" along the bin spacing. `band_energy_interval` filters first and
    integrates over the same interval, and puts the energy where it is."""
    t = np.arange(int(0.5 * SR)) / SR
    x = np.exp(-t / 0.18) * np.sin(2 * np.pi * 50.0 * t)
    peaks = []
    for ms in (4.0, 8.0, 16.0):
        g = x[:int(ms * 1e-3 * SR)]
        S = np.abs(np.fft.rfft(g))
        f = np.fft.rfftfreq(len(g), 1.0 / SR)
        peaks.append(float(f[1:][np.argmax(S[1:])]))
    assert peaks[0] > 200.0, f"the 4 ms gate should read a spurious peak above 200 Hz, got {peaks[0]:.0f}"
    assert peaks[0] > peaks[1] > peaks[2], f"the apparent peak must track the bin spacing: {peaks}"
    assert peaks[0] == pytest.approx(SR / int(4e-3 * SR), rel=0.01), "bin 1, exactly"

    edges = [20, 80, 150, 300, 600, 2000]
    for t1 in (0.004, 0.008, 0.016, 0.100):
        sh = df.band_energy_interval(x, SR, edges, 0.0, t1)
        assert np.argmax(sh) == 0, (t1, sh)         # the 20-80 Hz band, every time
        assert sh[0] > 75.0, (t1, sh)


KNOB_POSITIONS = ["0.0", "2.5", "5.0", "7.5", "10.0"]
KNOB_VALUES = [0.0, 2.5, 5.0, 7.5, 10.0]


def _take(voice: str, tone: float, decay: float, sr: int = 44100) -> np.ndarray:
    """A synthetic take for knob position (tone, decay). The two filename
    fields are made distinguishable in what the readers measure -- the bass
    drum's body frequency follows TUNING and its decay follows DECAY, the
    snare's noise share follows SNAPPY -- so a row can be shown to rest on
    five DIFFERENT takes. It stands in for audio and claims nothing about the
    machine."""
    if voice == "BD":
        t = np.arange(int(0.10 * sr)) / sr
        return 0.5 * np.exp(-t / (0.02 + 0.02 * decay)) * np.sin(2 * np.pi * (44.0 + 2.0 * tone) * t)
    x = df.synthetic_mixture(0.02 * decay / 2.5, sr=sr, dur=0.10,
                             tone_hz=(160.0 + tone, 320.0 + 2.0 * tone),
                             seed=int(tone * 10 + decay * 100))
    return 0.5 * x / np.abs(x).max()


def _make_corpus(root, voices=("BD", "SD")) -> None:
    """Every take the knob dial encodes for each voice, in the reference set's
    layout `<voice>8/<VOICE><tone><decay>.WAV`, and the cowbell the report
    reads by its exact name once the two readers are done."""
    from scipy.io import wavfile
    files = {f"{v.lower()}8/{v}{a}{b}.WAV": _take(v, k1, k2)
             for v in voices for a, k1 in df.KNOB.items() for b, k2 in df.KNOB.items()}
    files["cb8/CB.WAV"] = _take("SD", 5.0, 5.0)
    for rel, x in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        wavfile.write(str(p), 44100, np.round(x * 32767).astype(np.int16))


def _section(out: str, title: str) -> list[str]:
    """The data rows of the report section whose title holds `title`, its
    column header skipped -- or [] when that section never printed."""
    lines = out.splitlines()
    for i, line in enumerate(lines):
        if title in line:
            rows = []
            for nxt in lines[i + 2:]:
                if not nxt.strip():
                    break
                rows.append(nxt)
            return rows
    return []


def _positions(rows: list[str]) -> list[str]:
    """The knob position that begins each report row."""
    return [r.split()[0] for r in rows]


def test_a_complete_set_reaches_the_knob_curve_with_five_takes_in_every_law_row(tmp_path):
    _make_corpus(tmp_path, ("SD",))
    c = df.knob_curve(str(tmp_path), "SD")
    assert len(c["share"]) == 25
    assert sorted(c["law"]) == KNOB_VALUES
    assert {k2: sum(1 for k in c["share"] if k[1] == k2) for k2 in c["law"]} == dict.fromkeys(c["law"], 5)
    means = [c["law"][k2][0] for k2 in sorted(c["law"])]
    assert all(b > a for a, b in zip(means, means[1:])), means


def test_a_complete_set_reaches_the_decay_law_with_five_takes_in_every_row(tmp_path):
    _make_corpus(tmp_path, ("BD",))
    law = df.bd_decay_law(str(tmp_path))
    assert sorted(law["hz"]) == KNOB_VALUES
    assert all(len(v) == 5 for v in law["hz"].values())
    assert all(len(v) == 5 for v in law["taus"].values())
    for k2 in law["hz"]:
        assert sorted(law["hz"][k2]) == pytest.approx([44.0, 49.0, 54.0, 59.0, 64.0], abs=0.5), k2
    taus = [float(np.mean(law["taus"][k2])) for k2 in sorted(law["taus"])]
    assert all(b > a for a, b in zip(taus, taus[1:])), taus


def test_the_inventory_is_what_the_readers_sorted_globs_returned_on_a_complete_corpus(tmp_path):
    """Same paths, same order, as the globs the readers used before #1420 --
    so a complete corpus is read exactly as it was, and the report's row order
    and its means cannot move."""
    _make_corpus(tmp_path)
    root = str(tmp_path)
    for voice, pats in (("BD", ("BD????.WAV", "BD*.WAV")), ("SD", ("SD????.WAV",))):
        for pat in pats:
            old = sorted(glob.glob(os.path.join(root, voice.lower() + "8", pat)))
            assert df.reference_takes(root, voice) == old, (voice, pat)
    names = [os.path.basename(p) for p in df.reference_takes(root, "BD")]
    assert names == sorted(f"BD{a}{b}.WAV" for a in df.KNOB for b in df.KNOB)
    assert names[:5] == ["BD0000.WAV", "BD0010.WAV", "BD0025.WAV", "BD0050.WAV", "BD0075.WAV"]


def test_the_cli_reports_both_laws_from_a_complete_corpus(tmp_path, capsys):
    """Positive control for every refusal below: with all fifty takes present
    the report runs to its end and exits 0 with five rows in each law."""
    _make_corpus(tmp_path)
    assert df.main(["--refs", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert _positions(_section(out, "the SNAPPY law")) == KNOB_POSITIONS
    assert _positions(_section(out, "the DECAY law")) == KNOB_POSITIONS
    assert "REFUSED" not in out


def test_the_cli_refuses_the_snappy_law_before_printing_it_when_a_take_is_absent(tmp_path, capsys):
    _make_corpus(tmp_path)
    (tmp_path / "sd8" / "SD0025.WAV").unlink()
    assert df.main(["--refs", str(tmp_path)]) == 2
    out = capsys.readouterr().out
    assert "REFUSED" in out and "sd8/SD0025.WAV" in out
    assert "the SNAPPY law" not in out


def test_the_cli_refuses_the_decay_law_before_printing_it_and_keeps_the_complete_snappy_law(tmp_path, capsys):
    """The check is limited to the set each reader consumes: the snare's set is
    complete, so its law is published; the bass drum's is not, so its law is
    refused -- by name, before a row of it prints, where the glob used to
    average the DECAY 2.5 row over four takes."""
    _make_corpus(tmp_path)
    (tmp_path / "bd8" / "BD0025.WAV").unlink()
    assert (tmp_path / "bd8" / "BD5050.WAV").is_file()
    assert df.main(["--refs", str(tmp_path)]) == 2
    out = capsys.readouterr().out
    assert "REFUSED" in out and "bd8/BD0025.WAV" in out
    assert _positions(_section(out, "the SNAPPY law")) == KNOB_POSITIONS
    assert "the DECAY law" not in out


_READERS = [("SD", lambda root: df.knob_curve(root, "SD")), ("BD", df.bd_decay_law)]
_READER_IDS = ["snappy_law", "decay_law"]


@pytest.mark.parametrize("voice,read", _READERS, ids=_READER_IDS)
def test_a_reader_names_an_absent_take(tmp_path, voice, read):
    _make_corpus(tmp_path, (voice,))
    (tmp_path / f"{voice.lower()}8" / f"{voice}0025.WAV").unlink()
    with pytest.raises(df.Refused) as e:
        read(str(tmp_path))
    assert e.value.missing == [f"{voice.lower()}8/{voice}0025.WAV"]


@pytest.mark.parametrize("voice,read", _READERS, ids=_READER_IDS)
def test_an_equal_count_substitute_is_refused_by_name_not_by_a_parsing_error(tmp_path, voice, read):
    """The substitute keeps the file count and still matches the reader's old
    glob. The old reader then read it as a take and died in `KNOB['99']` -- a
    KeyError, which is not a diagnostic: it names no take and says no law."""
    _make_corpus(tmp_path, (voice,))
    d = tmp_path / f"{voice.lower()}8"
    (d / f"{voice}0025.WAV").rename(d / f"{voice}9999.WAV")
    with pytest.raises(df.Refused) as e:
        read(str(tmp_path))
    assert e.value.missing == [f"{voice.lower()}8/{voice}0025.WAV"]
    assert not isinstance(e.value, KeyError)


@pytest.mark.parametrize("voice,read", _READERS, ids=_READER_IDS)
def test_a_directory_at_a_wav_path_is_not_a_take(tmp_path, voice, read):
    _make_corpus(tmp_path, (voice,))
    p = tmp_path / f"{voice.lower()}8" / f"{voice}0025.WAV"
    p.unlink()
    p.mkdir()
    with pytest.raises(df.Refused) as e:
        read(str(tmp_path))
    assert e.value.missing == [f"{voice.lower()}8/{voice}0025.WAV"]


@pytest.mark.parametrize("voice,read,n", [("SD", lambda root: df.knob_curve(root, "SD")["share"], 25),
                                              ("BD", lambda root: df.bd_decay_law(root)["hz"], 5)],
                             ids=_READER_IDS)
def test_a_reader_requires_only_the_takes_it_consumes(tmp_path, voice, read, n):
    _make_corpus(tmp_path, (voice,))
    assert len(read(str(tmp_path))) == n


@pytest.mark.parametrize("present,read,absent", [("BD", _READERS[0][1], "SD"), ("SD", df.bd_decay_law, "BD")],
                         ids=["bd_present_reads_sd", "sd_present_reads_bd"])
def test_a_reader_names_every_take_of_its_own_set_and_no_other_when_its_voice_is_absent(
        tmp_path, present, read, absent):
    _make_corpus(tmp_path, (present,))
    with pytest.raises(df.Refused) as e:
        read(str(tmp_path))
    assert e.value.missing == sorted(f"{absent.lower()}8/{absent}{a}{b}.WAV" for a in df.KNOB for b in df.KNOB)
