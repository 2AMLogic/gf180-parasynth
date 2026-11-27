#!/usr/bin/env python3
"""Validation cases for `tools/measure_repeatability.py`.

The tool's whole output is a REFUSAL plus a set of spreads, so the two ways it
can lie are: calling a knob sweep "repeats" (a false green on the refusal), and
reporting a spread that came from the estimator rather than the machine. Both
are tested against signals whose answer is constructed, not observed.

    .venv/bin/python -m pytest tools/test_measure_repeatability.py -q
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

import numpy as np
import pytest
from scipy.io import wavfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import measure_repeatability as mr                                 # noqa: E402
import refaudio_local as rl                                        # noqa: E402

SR = 44100


# ---------------------------------------------------------------------------
# longest_true_run -- the precondition that decides clipping
# ---------------------------------------------------------------------------
def test_longest_true_run_counts_only_true():
    assert mr.longest_true_run(np.array([0, 1, 1, 0, 1], dtype=bool)) == 2
    assert mr.longest_true_run(np.zeros(9, dtype=bool)) == 0
    assert mr.longest_true_run(np.ones(4, dtype=bool)) == 4


def test_a_single_full_scale_sample_is_normalisation_not_clipping():
    """57 of the legacy pack's 144 bass drums have exactly one sample at
    exactly 1.0 and are not clipped. A run of one must not refuse."""
    x = 0.5 * np.sin(2 * np.pi * 50 * np.arange(SR) / SR)
    x[100] = 1.0
    assert mr.longest_true_run(np.abs(x) >= 1.0 - 1e-9) == 1


# ---------------------------------------------------------------------------
# spearman -- the statistic the refusal rests on
# ---------------------------------------------------------------------------
def test_spearman_is_one_on_a_monotone_sequence():
    assert mr.spearman([1.0, 2.0, 3.0, 4.0, 5.0, 6.0]) == pytest.approx(1.0)
    assert mr.spearman([6.0, 5.0, 4.0, 3.0, 2.0, 1.0]) == pytest.approx(-1.0)


def test_spearman_is_not_one_on_a_shuffled_sequence():
    assert abs(mr.spearman([3.0, 1.0, 6.0, 2.0, 5.0, 4.0])) < 1.0


# ---------------------------------------------------------------------------
# rank1_fraction -- the physical half of the refusal.
#
# START RED, in the sense verification-rules.md means: the discriminator is
# shown BOTH cases and must separate them. A test that only ever sees the knob
# would pass on a function that returns 1.0 unconditionally.
# ---------------------------------------------------------------------------
def _voice(seed: int) -> np.ndarray:
    t = np.arange(int(0.3 * SR)) / SR
    return np.exp(-t / 0.05) * np.sin(2 * np.pi * 50 * t + 0.0 * seed)


def _click(scale: float) -> np.ndarray:
    n = int(0.3 * SR)
    k = int(0.002 * SR)
    y = np.zeros(n)
    y[:k] = scale * np.exp(-np.arange(k) / (0.0004 * SR))
    return y


def test_rank1_finds_a_knob_sweep():
    """Six recordings that are one voice plus c_k times one fixed additive
    term ARE rank 1, with a monotone loading. That is a knob."""
    sigs = [_voice(0) + _click(0.05 * (k + 1)) for k in range(6)]
    frac, loading = mr.rank1_fraction(sigs)
    assert frac > 0.95
    assert abs(mr.spearman(loading)) == pytest.approx(1.0)


def test_rank1_does_not_find_a_knob_in_independent_repeats():
    """Six repeats differing by independent noise and independent jitter are
    not rank 1 and their loading is not monotone. If this passes with the
    previous test, the discriminator separates the two cases rather than
    answering the same way to everything."""
    rng = np.random.default_rng(7)
    sigs = []
    for _ in range(6):
        y = _voice(0) + _click(0.15)
        y = y + rng.normal(0.0, 0.01, len(y))
        sigs.append(np.roll(y, int(rng.integers(-3, 4))))
    frac, loading = mr.rank1_fraction(sigs)
    assert frac < 0.60, f"independent repeats read as rank {frac:.2f}"


# ---------------------------------------------------------------------------
# truncation_sensitivity -- the #118 length criterion
# ---------------------------------------------------------------------------
def test_truncation_probe_is_quiet_on_a_record_with_enough_tail():
    x = mr.synthetic_bd(SR, tau=0.05, seconds=3.0)
    assert abs(mr.truncation_sensitivity(x, SR)) < 1.0


def test_truncation_probe_fires_on_a_record_cut_while_it_is_still_sounding():
    """The case #118 is about. **This test asserted the defect until #118 was
    fixed**: `schroeder_t20` guarded truncation on `tail_db`, the last value of
    the backward-integrated curve, and that curve falls towards -inf at the last
    sample of ANY finite record -- so it passed this cut and returned a badly
    wrong answer. The guard is now a LENGTH criterion and refuses.

    Both halves are asserted. The level criterion is recomputed from the
    estimator's own `tail_db`, so if anyone reinstates it as the truncation test
    this record goes green again and this test goes red -- and the local probe
    must still fire either way, because a probe that never fires is not a
    probe."""
    import audio_measure as am
    import run_case as rc
    x = mr.synthetic_bd(SR, tau=0.40, seconds=4.0)
    cut = x[: int(0.5 * SR)]
    seg = rc.window(cut, SR, 0.005, None)
    guard = am.schroeder_t20(seg, SR)
    assert not guard.ok, \
        f"the length guard must refuse a cut record; it returned {guard.value*1e3:.0f} ms"
    assert "ends before" in guard.reason
    assert guard.detail["tail_db"] < -35.0, \
        ("the LEVEL criterion passes this record comfortably (tail_db "
         f"{guard.detail['tail_db']:.1f} dB) -- which is why the guard is a length")
    # what it would have reported, had it reported
    unguarded = am.schroeder_t20(seg, SR, min_tail_t20=0.0)
    assert abs(unguarded.value * 1e3 - am.t20_from_tau(0.40) * 1e3) > 100.0
    assert abs(mr.truncation_sensitivity(cut, SR)) > 5.0


# ---------------------------------------------------------------------------
# the estimators, on a signal whose answer is closed form
# ---------------------------------------------------------------------------
def test_synthetic_bd_reads_back_its_own_f0_and_t20():
    import audio_measure as am
    import run_case as rc
    plan = mr.metrics()
    y = rc.prepare(mr.synthetic_bd(SR, f0=50.0, tau=0.120), SR)
    assert plan["Pitch trajectory"][1](y, SR).require() == pytest.approx(50.0, abs=0.1)
    assert (plan["decay"][1](y, SR).require()
            == pytest.approx(am.t20_from_tau(0.120) * 1e3, rel=0.01))


def test_measure_all_keeps_a_refusal_as_a_refusal():
    """An estimator that cannot answer must come back None, never 0.0 -- zero
    reads as a perfect match and is how a scorecard starts lying."""
    plan = mr.metrics()
    out = mr.measure_all(np.zeros(SR), SR, plan)
    assert all(v is None for v in out.values()), out


# ---------------------------------------------------------------------------
# the windowing defect, #101, on a synthetic signal
# ---------------------------------------------------------------------------
def _prepare_with_the_clamp(x, sr):
    """`run_case.prepare` EXACTLY as it stood before #101 was fixed, so the
    defect this test was written for stays reproducible after the repair. The
    `max(0, ...)` is the whole of it: an onset closer to the start than 1 ms
    could not be given a 1 ms lead, so it silently got whatever was there --
    0.16 ms on every file in this corpus, inside `sosfiltfilt`'s 27-sample
    pad."""
    import audio_measure as am
    x = np.asarray(x, dtype=np.float64)
    pk = float(np.abs(x).max())
    i = int(np.argmax(np.abs(x) > 0.02 * pk))
    lead = max(0, i - int(1e-3 * sr))
    if lead >= int(5e-3 * sr):
        x = x - float(x[:lead].mean())
    y = x[lead:]
    p = float(np.abs(y).max())
    return y / p if p > 0 else y


def test_the_shipped_band_split_does_not_move_when_the_head_trim_moves():
    """Four samples of head trim cannot change what the machine did.

    **This test asserted the opposite until #101 was fixed** -- that the shipped
    split moved and only the padded control held still, which is what made the
    0.57 dB measured on the corpus the apparatus rather than the machine.
    `prepare` now gives both sides 20 padlens of true silence, so the shipped
    path holds still too, and the two must agree.

    The pre-repair path is reimplemented above and asserted to STILL move, so
    this is a control and not merely an absence."""
    import run_case as rc
    plan = mr.metrics()
    # Eight more samples of leading silence on top of the fixture's own, so
    # every trim below comes out of silence and the EVENT is bit-identical in
    # all four: any movement at all is the apparatus.
    x = np.concatenate([np.zeros(8),
                        mr.synthetic_bd(SR, tau=0.12, seconds=1.0, click=0.4)])
    shipped = [plan["body spectrum"][1](rc.prepare(x[h:], SR), SR).require()
               for h in (0, 2, 4, 6)]
    padded = [plan["body spectrum (padded)"][1](rc.prepare(x[h:], SR), SR).require()
              for h in (0, 2, 4, 6)]
    before = [rc.band_ratio_db(_prepare_with_the_clamp(x[h:], SR)[:int(0.150 * SR)],
                               SR, rc.SPLIT_HZ["BD"], *rc.BAND["BD"]).require()
              for h in (0, 2, 4, 6)]
    assert max(padded) - min(padded) < 0.01, padded
    assert max(shipped) - min(shipped) < 0.01, shipped
    assert abs(shipped[0] - padded[0]) < 0.01, (shipped[0], padded[0])
    assert max(before) - min(before) > 0.1, \
        f"the pre-repair path must still move, or this is not a control: {before}"


# ---------------------------------------------------------------------------
# #111 take-to-take from the 4x4 loops. No audio from the licensed library is
# used or needed here: every loop is built, so its answer is known.
# ---------------------------------------------------------------------------
BPM = 120.0
BEAT = int(60 / BPM * SR)


def _tiled(strikes, bars=16):
    x = np.zeros(BEAT * bars)
    for i in range(bars):
        s = strikes[i % len(strikes)][:BEAT - 20]
        x[i * BEAT + 20:i * BEAT + 20 + len(s)] += s
    return x / np.abs(x).max()


def _one(f0=50.0, seed=0, tau=0.06):
    return mr.synthetic_bd(SR, f0=f0, tau=tau, seconds=0.5, seed=seed)


def test_loop_controls_all_pass():
    ok, ev = mr.loop_controls(report=lambda *a: None)
    assert ok, ev


def test_a_pasted_loop_is_flagged_as_duplicates():
    """START RED for the audit: if this returned < 1.0 a copy-pasted loop would
    be measured and report a spread of exactly zero as the machine's."""
    sl, _ = mr.slice_strikes(_tiled([_one(seed=1)]), SR, BPM)
    frac, _ = mr.duplicate_fraction(sl)
    assert frac == 1.0


def test_independent_strikes_are_not_flagged():
    sl, _ = mr.slice_strikes(_tiled([_one(seed=10 + i) for i in range(16)]), SR, BPM)
    frac, ev = mr.duplicate_fraction(sl)
    assert frac == 0.0 and ev["residual_max_abs_median"] > mr.DUP_ABS


def test_alignment_does_not_prefer_the_loud_end_of_a_decaying_strike():
    """The regression the copy-paste control caught: a raw dot product slid the
    window toward the loud part of a decaying strike, so IDENTICAL strikes were
    aligned tens of samples off and a pasted loop read as a performance."""
    a = _one(seed=1)[:BEAT]
    corr, mx, rel = mr._aligned_residual(a, a.copy())
    assert corr == pytest.approx(1.0) and mx == 0.0 and rel == 0.0


def test_a_duplicate_one_lsb_off_is_still_a_duplicate_but_noise_is_not():
    s = _one(seed=1)
    lsb = 2.0 ** -23
    jitter = s + np.random.default_rng(0).choice([-lsb, 0.0, lsb], len(s))
    assert mr._aligned_residual(s[:BEAT], jitter[:BEAT])[1] <= mr.DUP_ABS
    noisy = s + np.random.default_rng(0).normal(0, 1e-4, len(s))
    assert mr._aligned_residual(s[:BEAT], noisy[:BEAT])[1] > mr.DUP_ABS


def test_slicing_finds_every_strike_and_refuses_double_time():
    x = _tiled([_one(seed=3)])
    assert len(mr.loop_onsets(x, BEAT)) == 16
    dbl = x.copy()
    dbl[BEAT // 2 + 20:BEAT // 2 + 4020] += 0.9 * x[20:4020]
    with pytest.raises(mr.Refused, match="not one strike per beat"):
        mr.slice_strikes(dbl / np.abs(dbl).max(), SR, BPM)


def test_slicing_refuses_a_missing_strike():
    x = _tiled([_one(seed=3)])
    x[5 * BEAT:6 * BEAT] = 0.0
    with pytest.raises(mr.Refused):
        mr.slice_strikes(x, SR, BPM)


def test_loop_measure_sees_an_injected_f0_difference_and_not_a_clean_loop():
    """SENSITIVITY: 3 Hz injected into every other strike must be measured at
    about 3 Hz; independent-noise strikes of one f0 must be measured as ~0."""
    mixed = _tiled([_one(f0=50.0, seed=2), _one(f0=53.0, seed=3)])
    clean = _tiled([_one(f0=50.0, seed=20 + i) for i in range(16)])
    m = mr.loop_measure({"m": (mr.slice_strikes(mixed, SR, BPM)[0], SR)})
    c = mr.loop_measure({"c": (mr.slice_strikes(clean, SR, BPM)[0], SR)})
    assert m["per_loop"]["m"]["Pitch trajectory"]["span"] == pytest.approx(3.0, abs=0.3)
    assert c["per_loop"]["c"]["Pitch trajectory"]["span"] < 0.01


def test_loop_measure_drops_the_first_strike():
    x = _tiled([_one(seed=30 + i) for i in range(16)])
    sl, _ = mr.slice_strikes(x, SR, BPM)
    out = mr.loop_measure({"x": (sl, SR)})
    assert out["per_loop"]["x"]["Pitch trajectory"]["n"] == len(sl) - 1


def test_loop_measure_records_a_refusal_instead_of_a_number():
    """Strikes cut while still sounding: decay must be COUNTED as refused, never
    replaced by a figure (#118)."""
    x = _tiled([_one(seed=40 + i, tau=0.15) for i in range(16)])
    out = mr.loop_measure({"x": (mr.slice_strikes(x, SR, BPM)[0], SR)})
    d = out["per_loop"]["x"]["decay"]
    assert "refused" in d and d["n_refused"] > 0
    assert "refused" in out["metrics"]["decay"]


def test_loop_audit_refuses_when_the_files_are_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(mr, "LOOPS_DIR", tmp_path / "nope")
    with pytest.raises(mr.Refused, match="refaudio_s3.py"):
        mr.loop_files()


# ---------------------------------------------------------------------------
# #1427: the loop reader is held to its inventory. catalog.json carries no
# per-file index for 808_loops_from_mars.zip, so the WAVs its 4x4 folder holds
# are committed by name, read once from the hash-verified archive, and a folder
# that does not hold every one of them is REFUSED before any loop is read.
# ---------------------------------------------------------------------------
def _short(name: str) -> str:
    """The label `loop_audit` keys a loop by: its own shortening, repeated."""
    return name.replace("BD 4x4 ", "").replace(" 808 Loops", "")[:-4]


def _placeholders(d: pathlib.Path, names) -> pathlib.Path:
    """A `LOOPS_DIR` holding each of `names` as an empty file: enough for the
    membership check, which reads names and nothing else."""
    d.mkdir(exist_ok=True)
    for n in names:
        (d / n).touch()
    return d


def _loop(bpm: float, seed0: int, pasted: bool = False, pattern: bool = False) -> np.ndarray:
    """Six bars of one bass drum per beat at `bpm`. Each strike is an
    independent take, a few samples off the grid as a sequencer free-running
    against the DAW puts them. `pasted` is one strike tiled; `pattern` adds a
    second strike on every off-beat. Every strike has the same shape, so a
    metric that reads it against its own onset (attack) reads a spread of
    exactly zero, which the report table has to survive."""
    beat = 60.0 / bpm * SR
    n = int(round(beat * 6))
    x = np.zeros(n)
    rng = np.random.default_rng(seed0)
    for bar in range(6):
        s = _one(seed=seed0 if pasted else seed0 + bar)[:int(beat) - 60]
        j = int(round(bar * beat)) + 20 + int(rng.integers(-6, 7))
        x[j:j + len(s)] += s
        if pattern:
            k = int(round(bar * beat + beat / 2)) + 20
            off = s[:max(0, n - k)]
            x[k:k + len(off)] += 0.9 * off
    return (x / np.abs(x).max()).astype(np.float32)


def _loops_cli(monkeypatch, tmp_path, pattern=(), pasted=()):
    """`--loops` with the real audit and measurement over the whole committed
    inventory. The apparatus controls are the one thing stubbed: they are
    synthetic, and `test_loop_controls_all_pass` runs them."""
    pytest.importorskip("soundfile")
    names = mr.expected_loops()
    d = tmp_path / "4x4"
    d.mkdir()
    for i, name in enumerate(names):
        bpm = float(mr.LOOP_BPM_RE.search(name)["bpm"])
        wavfile.write(str(d / name), SR, _loop(bpm, 100 * i, pasted=name in pasted,
                                              pattern=name in pattern))
    monkeypatch.setattr(mr, "LOOPS_DIR", d)
    monkeypatch.setattr(mr, "loop_controls",
                        lambda report=print: (True, {"stubbed": "not what these tests measure"}))
    return d, names


def test_the_committed_inventory_is_the_published_loop_set():
    """Twenty-seven names, sorted, each carrying a bpm. Shortened, they are the
    27 audit labels `docs/bd-repeatability-results.json` published from the
    real archive: an independent record that the right folder was read."""
    names = mr.expected_loops()
    assert len(names) == 27 and names == sorted(names)
    assert all(mr.LOOP_BPM_RE.search(n) for n in names)
    audit = json.loads((mr.REPO / "docs" / "bd-repeatability-results.json").read_text(
        encoding="utf-8"))["take_to_take"]["audit"]["loops"]
    assert {_short(n) for n in names} == set(audit)


def test_the_inventory_is_bound_to_the_catalogued_archive():
    """The list is evidence only if it is tied to the archive it was read from.
    Its header carries that archive's SHA-256, which is the one catalog.json
    records, and the commands to regenerate the list and to fetch the loops."""
    head = [ln for ln in mr.LOOPS_INVENTORY.read_text(encoding="utf-8").splitlines()
            if ln.startswith("#")]
    want = rl.pack_record(mr.LOOPS_ARCHIVE)["archive_sha256"]
    assert f"# archive_sha256 {want}" in head
    assert any(f"tools/refaudio_member_list.py {mr.LOOPS_ARCHIVE} " in ln and "4x4" in ln
               for ln in head), head
    assert any(f"tools/refaudio_s3.py --prefix {mr.LOOPS_ARCHIVE} " in ln for ln in head), head


@pytest.mark.parametrize("body", [None, "", "# a header and nothing else\n\n"])
def test_an_unavailable_or_empty_inventory_is_refused(tmp_path, monkeypatch, body):
    p = tmp_path / "inventory.txt"
    if body is not None:
        p.write_text(body, encoding="utf-8")
    _placeholders(tmp_path / "4x4", ["BD 4x4 whatever 808 Loops 120.wav"])
    monkeypatch.setattr(mr, "LOOPS_DIR", tmp_path / "4x4")
    monkeypatch.setattr(mr, "LOOPS_INVENTORY", p)
    with pytest.raises(mr.Refused, match="inventory"):
        mr.loop_files()


def test_an_inventory_that_lists_a_loop_twice_is_refused(tmp_path, monkeypatch):
    """`loop_audit` reads each listed file and appends each to its admitted
    list, so a loop the inventory names twice would be read twice and counted
    twice."""
    p = tmp_path / "inventory.txt"
    p.write_text("# a header\nBD 4x4 twice 808 Loops 120.wav\nBD 4x4 twice 808 Loops 120.wav\n",
                 encoding="utf-8")
    _placeholders(tmp_path / "4x4", ["BD 4x4 twice 808 Loops 120.wav"])
    monkeypatch.setattr(mr, "LOOPS_DIR", tmp_path / "4x4")
    monkeypatch.setattr(mr, "LOOPS_INVENTORY", p)
    with pytest.raises(mr.Refused, match="more than once"):
        mr.loop_files()


def test_a_complete_folder_reads_every_inventory_loop_in_order(tmp_path, monkeypatch):
    names = mr.expected_loops()
    d = _placeholders(tmp_path / "4x4", names)
    monkeypatch.setattr(mr, "LOOPS_DIR", d)
    got = mr.loop_files()
    assert [p.name for p in got] == names and got == sorted(got)


def test_one_absent_loop_is_named(tmp_path, monkeypatch):
    """The old reader returned the other 26 and every spread was read over them."""
    names = mr.expected_loops()
    d = _placeholders(tmp_path / "4x4", names)
    (d / names[0]).unlink()
    monkeypatch.setattr(mr, "LOOPS_DIR", d)
    with pytest.raises(mr.Refused) as e:
        mr.loop_files()
    assert names[0] in str(e.value) and mr.LOOPS_INVENTORY.name in str(e.value)


def test_an_equal_count_substitute_does_not_stand_in_for_a_loop(tmp_path, monkeypatch):
    """The folder holds 27 WAVs again, so the old glob audited a stranger as the loop."""
    names = mr.expected_loops()
    d = _placeholders(tmp_path / "4x4", names)
    stranger = names[0].replace("120.wav", "999.wav")
    (d / names[0]).rename(d / stranger)
    assert len(list(d.glob("*.wav"))) == 27
    monkeypatch.setattr(mr, "LOOPS_DIR", d)
    with pytest.raises(mr.Refused) as e:
        mr.loop_files()
    assert names[0] in str(e.value) and stranger not in str(e.value)


def test_an_unexpected_wav_never_contributes(tmp_path, monkeypatch):
    names = mr.expected_loops()
    d = _placeholders(tmp_path / "4x4", names)
    (d / "not one of them.wav").touch()
    monkeypatch.setattr(mr, "LOOPS_DIR", d)
    assert [p.name for p in mr.loop_files()] == names


def test_an_empty_folder_keeps_the_fetch_hint(tmp_path, monkeypatch):
    """Nothing fetched is a different diagnosis from a partly fetched folder:
    it names the command, as before, and not 27 names."""
    (tmp_path / "4x4").mkdir()
    monkeypatch.setattr(mr, "LOOPS_DIR", tmp_path / "4x4")
    with pytest.raises(mr.Refused, match="refaudio_s3.py"):
        mr.loop_files()


def test_the_loops_cli_measures_the_complete_inventory(tmp_path, monkeypatch, capsys):
    """All 27 are audited and admitted, every one yields strikes the estimators
    read, the report is MEASURED, and the audit's keys are the inventory's names."""
    _loops_cli(monkeypatch, tmp_path)
    out = tmp_path / "result.json"
    assert mr.main(["--loops", "--json", str(out)]) == 0
    text = capsys.readouterr().out
    assert "are incomplete" not in text and "are not in refaudio/cache" not in text
    res = json.loads(out.read_text(encoding="utf-8"))["take_to_take"]
    assert res["outcome"] == "MEASURED"
    assert sorted(res["audit"]["loops"]) == sorted(_short(n) for n in mr.expected_loops())
    assert sorted(res["audit"]["admitted"]) == sorted(res["audit"]["loops"])
    assert res["audit"]["excluded_not_one_per_beat"] == res["audit"]["refused_copy_paste"] == []
    read = [row["Pitch trajectory"]["n"] for row in res["measure"]["per_loop"].values()]
    assert len(read) == 27 and all(n >= 3 for n in read), read


def test_per_loop_acoustic_exclusions_are_kept_on_a_complete_inventory(tmp_path, monkeypatch):
    """Complete inventory, two loops the ACOUSTICS exclude -- a pattern and a
    pasted strike -- and they are excluded with their reasons, as before, while
    the report is still measured over the rest."""
    names = mr.expected_loops()
    _loops_cli(monkeypatch, tmp_path, pattern=names[1:2], pasted=names[2:3])
    out = tmp_path / "result.json"
    assert mr.main(["--loops", "--json", str(out)]) == 0
    res = json.loads(out.read_text(encoding="utf-8"))["take_to_take"]
    assert res["outcome"] == "MEASURED"
    assert res["audit"]["excluded_not_one_per_beat"] == [_short(names[1])]
    assert res["audit"]["refused_copy_paste"] == [_short(names[2])]
    assert len(res["audit"]["admitted"]) == 25


def test_the_loops_cli_refuses_a_missing_loop_and_leaves_the_json_intact(
        tmp_path, monkeypatch, capsys):
    names = mr.expected_loops()
    d = _placeholders(tmp_path / "4x4", names)
    (d / names[5]).unlink()
    monkeypatch.setattr(mr, "LOOPS_DIR", d)
    monkeypatch.setattr(mr, "loop_controls", lambda report=print: (True, {}))
    out = tmp_path / "result.json"
    previous = '{"session_to_session": "the previous run"}'
    out.write_text(previous, encoding="utf-8")
    assert mr.main(["--loops", "--json", str(out)]) == 2
    text = capsys.readouterr().out
    assert "REFUSED" in text and names[5] in text and mr.LOOPS_INVENTORY.name in text
    assert "MEASURE" not in text
    assert out.read_text(encoding="utf-8") == previous


def test_the_loops_cli_refuses_an_equal_count_substitute(tmp_path, monkeypatch, capsys):
    names = mr.expected_loops()
    d = _placeholders(tmp_path / "4x4", names)
    stranger = names[7].replace(".wav", " edit.wav")
    (d / names[7]).rename(d / stranger)
    assert len(list(d.glob("*.wav"))) == 27
    monkeypatch.setattr(mr, "LOOPS_DIR", d)
    monkeypatch.setattr(mr, "loop_controls", lambda report=print: (True, {}))
    assert mr.main(["--loops"]) == 2
    text = capsys.readouterr().out
    assert "REFUSED" in text and names[7] in text and stranger not in text


def test_the_loops_cli_keeps_the_fetch_hint_for_an_empty_folder(tmp_path, monkeypatch, capsys):
    (tmp_path / "4x4").mkdir()
    monkeypatch.setattr(mr, "LOOPS_DIR", tmp_path / "4x4")
    monkeypatch.setattr(mr, "loop_controls", lambda report=print: (True, {}))
    assert mr.main(["--loops"]) == 2
    text = capsys.readouterr().out
    assert "REFUSED" in text and "refaudio_s3.py" in text


def test_loop_compare_reads_the_committed_floors():
    take = {"metrics": {"Pitch trajectory": {"units": "Hz", "loops": 1, "strikes_refused": 0,
                                              "pairwise_abs_diff_median_of_loops": 0.0014},
                        "decay": {"refused": "no loop yielded 3 readable strikes"}}}
    out = mr.loop_compare(take)
    assert out["Pitch trajectory"]["session_floor"] == pytest.approx(1.3852)
    assert out["Pitch trajectory"]["take_over_session"] == pytest.approx(0.001, abs=1e-3)
    assert out["decay"] == {"refused": "no loop yielded 3 readable strikes"}


def _conditioned(monkeypatch, exc):
    import test_discrimination as td

    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(td, "condition", boom)
    return mr._promoted("lowband_level_db")(np.ones(4410), 44100)


def test_an_unusable_clip_is_a_conditioning_refusal(monkeypatch):
    e = _conditioned(monkeypatch, ValueError("empty"))
    assert not e.ok and "conditioning refused" in e.reason


def test_a_defect_in_the_conditioner_is_not_reported_as_a_refusal(monkeypatch):
    """#615: any exception used to read "conditioning refused"."""
    e = _conditioned(monkeypatch, TypeError("bug"))
    assert not e.ok
    assert "TypeError" in e.reason and "refused" not in e.reason


# ---------------------------------------------------------------------------
# The report table itself. `loop_compare` answers None for tolerance / take
# when the take is exactly 0, and the table used to format that None as a float.
# ---------------------------------------------------------------------------
def test_the_report_prints_a_metric_whose_take_to_take_is_exactly_zero(tmp_path, monkeypatch, capsys):
    """A traceback, exit 1, after the whole corpus was measured and before
    --json was written. Found by #1427's complete-inventory test on built
    loops, whose attack reads the same in every strike. The table shows the
    ratio as unbounded; the JSON keeps the None, which is the honest value."""
    take = {"metrics": {"attack": {"units": "ms", "loops": 1, "strikes_refused": 0,
                                   "pairwise_abs_diff_median_of_loops": 0.0}}}
    monkeypatch.setattr(mr, "loop_controls", lambda report=print: (True, {}))
    monkeypatch.setattr(mr, "loop_audit", lambda report=print, files=None: ({"x": ([], 44100)}, {}))
    monkeypatch.setattr(mr, "loop_measure", lambda admitted, report=print: take)
    out = tmp_path / "result.json"
    assert mr.main(["--loops", "--json", str(out)]) == 0
    row = next(ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("  attack"))
    assert "inf" in row, row
    got = json.loads(out.read_text(encoding="utf-8"))["take_to_take"]
    assert got["outcome"] == "MEASURED" and got["compare"]["attack"]["tolerance_over_take"] is None


def test_the_loops_cli_names_every_loop_when_the_whole_folder_is_substituted(
        tmp_path, monkeypatch, capsys):
    """27 other WAVs in an existing folder: the count is right and not one name
    is. The refusal names what is absent AND keeps the fetch hint (#1427)."""
    names = mr.expected_loops()
    d = _placeholders(tmp_path / "4x4", [f"other {i}.wav" for i in range(len(names))])
    monkeypatch.setattr(mr, "LOOPS_DIR", d)
    monkeypatch.setattr(mr, "loop_controls", lambda report=print: (True, {}))
    assert mr.main(["--loops"]) == 2
    text = capsys.readouterr().out
    assert "REFUSED" in text and "refaudio_s3.py" in text
    assert all(n in text for n in names) and "other 0.wav" not in text


@pytest.mark.parametrize("blocked", ["index", "cache", "4x4"])
def test_the_loops_cli_refuses_an_inaccessible_directory(tmp_path, monkeypatch, capsys, blocked):
    inventory = tmp_path / "index" / "loops.txt"
    inventory.parent.mkdir()
    inventory.write_text("expected.wav\n", encoding="utf-8")
    cache = tmp_path / "cache"
    folder = cache / "4x4"
    folder.mkdir(parents=True)
    (folder / "expected.wav").touch()
    monkeypatch.setattr(mr, "LOOPS_INVENTORY", inventory)
    monkeypatch.setattr(mr, "LOOPS_DIR", folder)
    monkeypatch.setattr(mr, "loop_controls", lambda report=print: (True, {}))
    out = tmp_path / "result.json"
    previous = b'{"take_to_take": "previous"}\n'
    out.write_bytes(previous)

    directory = {"index": inventory.parent, "cache": cache, "4x4": folder}[blocked]
    directory.chmod(0)
    try:
        if os.access(directory, os.X_OK):
            pytest.skip("requires a user subject to directory search permissions")
        assert mr.main(["--loops", "--json", str(out)]) == 2
    finally:
        directory.chmod(0o700)
    text = capsys.readouterr().out
    assert "REFUSED" in text and "Permission denied" in text and str(directory) in text
    assert out.read_bytes() == previous


def _unreadable(monkeypatch, target):
    real = pathlib.Path.read_text

    def read_text(self, *a, **k):
        if self == target:
            raise PermissionError(13, "Permission denied", str(self))
        return real(self, *a, **k)
    monkeypatch.setattr(pathlib.Path, "read_text", read_text)


def test_the_loops_cli_refuses_an_unreadable_inventory_and_keeps_the_json(
        tmp_path, monkeypatch, capsys):
    d = _placeholders(tmp_path / "4x4", mr.expected_loops())
    monkeypatch.setattr(mr, "LOOPS_DIR", d)
    monkeypatch.setattr(mr, "loop_controls", lambda report=print: (True, {}))
    out = tmp_path / "result.json"
    previous = '{"take_to_take": "the previous run"}'
    out.write_text(previous, encoding="utf-8")
    _unreadable(monkeypatch, mr.LOOPS_INVENTORY)
    assert mr.main(["--loops", "--json", str(out)]) == 2
    text = capsys.readouterr().out
    assert "REFUSED" in text and mr.LOOPS_INVENTORY.name in text and "Permission denied" in text
    assert out.read_text(encoding="utf-8") == previous
