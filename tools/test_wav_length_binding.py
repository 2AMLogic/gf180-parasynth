#!/usr/bin/env python3
"""A WAV bound to its record by hash is not thereby as long as its record says
(issue #2080).

A sha256 says "these are the bytes". It says nothing about how many. Three
readers hashed a stored WAV, then read it, and nothing between the two asked
whether what came back was what the record described:

    reader                                what its record carries
    ---------------------------------------------------------------------------
    manifest.load_render                 n_samples, written by render()
    measure_osc_drift_reference._clip    M5A: audio.samples. M1A: no frame
                                         count; the last window's end is a
                                         minimum
    envelope_history_337.measure         no length; the last note's gate end
                                         is a minimum

So a WAV cut short and re-hashed parsed, and every index the record carries
was applied to the shorter take as though it were the whole one. The numbers
came back looking exactly like data.

Every `..._refuses...` test below was written first and watched fail against
the unguarded readers; what they returned instead is under RED FIRST. Eleven
controls were green there as well, on purpose: the hash still refusing a
same-length edit, the committed audio still measuring as before, a file that
ends exactly where its last window ends, and the file cut AFTER that. The
last is a guard's blind spot, pinned so that closing it cannot happen
unnoticed (`docs/verification-rules.md`, rule 8): an M1A
manifest states no frame count for a clip and an attack-context record none
for a render, so a file truncated anywhere past its last window is, by
everything those tools can read, the file.

What no guard here can see, stated rather than caught: a record edited to
agree with the cut (`sha256` and the length field both rewritten), and a file
cut and padded back to length. Each checks the WAV against its record, not the
record against the render; git history is the only binding on the record.

RED FIRST -- 23 failed, 11 passed, on origin/main @ 8eb04e5b, every cut with
its record's hash rewritten to match the file. What the unguarded readers
returned:

  manifest.load_render, raw.wav of 2400 samples
      cut to 2376 / 1200, padded to 2880, truncated on disk to 1400
          LOADED 2376 samples; the record says n_samples=2400      (and so on)
      n_samples deleted / null / "2400" / 2400.0 / true
          LOADED 2400 samples; the record says n_samples='<absent>' (and so on)

  measure_osc_drift_reference, control-osc1_open.wav (359,956 frames)
      at 75 % and one frame short of its last window: the three committed lines
         m1a/osc1_open/n0-midi36   REFUSED   TOO_FEW_PERIODS: 12 clean periods < 24
         m1a/osc1_open/n1-midi43   STABLE    rms  0.0108 c  p2p  0.0473 c ...
         m1a/osc1_open/n2-midi36   REFUSED   TOO_FEW_PERIODS: 12 clean periods < 24
      at 3.125 s, inside the second note's window: the same first two, and
         m1a/osc1_open/n2-midi36   REFUSED   TOO_FEW_PERIODS: 0.0 periods < 24
      a window the clip no longer holds reads as a note with no pitch
  measure_osc_drift_reference, m5a-miniv3-raw.wav at 80 %, and intact with
  its record's `samples` deleted / null / a string / a float
         m5a/seg0-saw/n0-midi84    STABLE    rms  0.0047 c  p2p  0.0142 c ...
      four such lines: the committed numbers, on 80 % of the audio

  envelope_history_337, saw-isolated84-0.wav at 5 % and
  saw-repeat84_gap5-0.wav cut 960 frames short
      saw   isolated84       resid   -120.0 dB  attack5ms   7.33  attack1ms   7.40
      saw   repeat84_gap5    resid   -120.0 dB  attack5ms   3.56  attack1ms   1.94
      both the committed rows; the same render at 45 % returned
      REFUSED no measurable held level -- a refusal that blames the instrument
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import wave
from pathlib import Path

import numpy as np
import pytest
from scipy.io import wavfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "model"))

import envelope_history_337 as eh                                  # noqa: E402
import manifest as mf                                              # noqa: E402
import measure_osc_drift_reference as mod                          # noqa: E402
import refusal                                                     # noqa: E402

SR = 48000
SCORECARD = ROOT / "docs" / "scorecard"
M1A = SCORECARD / "mono-m1a-miniv3"
M5A = SCORECARD / "mono-m5a-miniv3"
ATTACK = SCORECARD / "mono-attack-context"
M1A_OSC1 = "control-osc1_open.wav"
M5A_RAW = "m5a-miniv3-raw.wav"
GAP5 = "saw-repeat84_gap5-0.wav"
ISOLATED = "saw-isolated84-0.wav"

needs_frozen = pytest.mark.skipif(
    not all(p.exists() for p in (M1A / M1A_OSC1, M5A / M5A_RAW, ATTACK / GAP5)),
    reason="frozen Mini V3 audio absent from this checkout")


# ---- the inputs: cut, pad, re-hash ------------------------------------------
def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rewrite_json(path: Path, change) -> None:
    record = json.loads(path.read_text())
    change(record)
    path.write_text(json.dumps(record))


def _frames(path: Path) -> int:
    return len(wavfile.read(path)[1])


def _cut_wav(path: Path, n_frames: int, *, pad: int = 0) -> int:
    """`path` rewritten with its first `n_frames` frames, then `pad` silent
    frames, in its own sample format: a well-formed file whose header and
    data agree, so nothing but its length shows the edit. Returns the frame
    count it had."""
    sr, x = wavfile.read(path)
    assert n_frames <= len(x), (n_frames, len(x))
    y = np.concatenate([x[:n_frames], np.zeros(pad, dtype=x.dtype)])
    wavfile.write(path, sr, y)
    return len(x)


def _same(a, b) -> bool:
    """JSON equality: the same NaN is the same, where `==` would say not."""
    return json.dumps(a, sort_keys=True, default=float) == \
        json.dumps(b, sort_keys=True, default=float)


# ---- manifest.load_render ---------------------------------------------------
def _tone(seconds=0.05):
    n = int(SR * seconds)
    t = np.arange(n) / SR
    return (0.5 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float64), SR


def _retained(tmp_path: Path) -> dict:
    """A render as `render()` retains it: `runs/<render_id>/` with its WAV."""
    return mf.render("T1", {"hz": 440.0}, _tone, runs_dir=tmp_path / "runs")


def _wav_of(tmp_path: Path, rec: dict) -> Path:
    return tmp_path / "runs" / rec["render_id"] / rec["wav_path"]


def _cut_render(tmp_path: Path, n_frames: int, *, pad: int = 0):
    """The retained WAV cut to `n_frames` frames (plus `pad` silent ones) and
    its manifest's `wav_sha256` rewritten to match: the #2080 input."""
    rec = _retained(tmp_path)
    wav = _wav_of(tmp_path, rec)
    _cut_wav(wav, n_frames, pad=pad)
    _rewrite_json(wav.parent / "manifest.json", lambda m: m.update(wav_sha256=_render_sha(wav)))
    return rec, wav


def _render_sha(wav: Path) -> str:
    """The hash as `render()` writes it (`provenance.file_sha`: `sha256:` and
    sixteen hex digits), so the rewritten record agrees with its WAV."""
    return mf.provenance.file_sha(wav)


def _load(tmp_path: Path, rec: dict) -> str:
    """What `load_render` did, as one line: its refusal, or what it returned."""
    try:
        manifest, x, _ = mf.load_render(rec["render_id"], runs_dir=tmp_path / "runs")
    except refusal.Refused as exc:
        return f"REFUSED {exc}"
    return (f"LOADED {len(x)} samples; the record says "
            f"n_samples={manifest.get('n_samples', '<absent>')!r}")


N_TONE = len(_tone()[0])


@pytest.mark.parametrize("frac", [0.99, 0.50], ids=["99pct", "50pct"])
def test_a_cut_render_with_a_rewritten_hash_refuses(tmp_path, frac):
    """99 / 50 % of the retained WAV, re-hashed so `wav_sha256` agrees. The
    record still says how long the render was, so the reader refuses, naming
    the WAV and both lengths."""
    kept = int(N_TONE * frac)
    rec, wav = _cut_render(tmp_path, kept)
    outcome = _load(tmp_path, rec)
    assert outcome.startswith("REFUSED "), outcome
    assert f"{wav} holds {kept} samples" in outcome, outcome
    assert f"records n_samples={N_TONE}" in outcome, outcome


def test_a_render_padded_past_its_end_refuses_too(tmp_path):
    """The other direction: 480 silent samples appended, re-hashed."""
    rec, wav = _cut_render(tmp_path, N_TONE, pad=480)
    assert _frames(wav) == N_TONE + 480
    outcome = _load(tmp_path, rec)
    assert outcome.startswith("REFUSED "), outcome
    assert f"{wav} holds {N_TONE + 480} samples" in outcome, outcome
    assert f"records n_samples={N_TONE}" in outcome, outcome


def test_a_wav_truncated_on_disk_refuses_though_its_header_still_promises_the_whole(tmp_path):
    """The accidental shape of the same cut: a half-copied file whose header
    still declares every sample, its record's hash rewritten to match what is
    there. The guard counts the samples it can read, not the ones promised."""
    rec = _retained(tmp_path)
    wav = _wav_of(tmp_path, rec)
    data = bytearray(wav.read_bytes())
    del data[-2 * 1000:]
    wav.write_bytes(bytes(data))
    _rewrite_json(wav.parent / "manifest.json", lambda m: m.update(wav_sha256=_render_sha(wav)))
    assert _render_sha(wav) != "missing"
    with wave.open(str(wav), "rb") as w:
        assert w.getnframes() == N_TONE, "the header must still promise the whole render"
    outcome = _load(tmp_path, rec)
    assert outcome.startswith("REFUSED "), outcome
    assert f"{wav} holds {N_TONE - 1000} samples" in outcome, outcome
    assert f"records n_samples={N_TONE}" in outcome, outcome


def test_the_hash_still_refuses_a_wav_that_is_the_right_length(tmp_path):
    """Control: the length check must not displace the hash check. One byte of
    the last sample changed in place -- the length untouched, the hash not
    rewritten -- still refuses on the hash."""
    rec = _retained(tmp_path)
    wav = _wav_of(tmp_path, rec)
    data = bytearray(wav.read_bytes())
    data[-1] ^= 1
    wav.write_bytes(bytes(data))
    outcome = _load(tmp_path, rec)
    assert "does not match the hash" in outcome, outcome


def test_an_untouched_render_loads_as_long_as_its_record_says(tmp_path):
    """Control: a render exactly as long as its record says is the ordinary
    path, and nothing about it changes."""
    rec = _retained(tmp_path)
    assert _load(tmp_path, rec) == f"LOADED {N_TONE} samples; the record says n_samples={N_TONE}"


_NO_COUNT = {
    "field-deleted": lambda m: m.pop("n_samples"),
    "null": lambda m: m.update(n_samples=None),
    "string": lambda m: m.update(n_samples=str(N_TONE)),
    "float-of-the-right-value": lambda m: m.update(n_samples=float(N_TONE)),
    "true": lambda m: m.update(n_samples=True),
}


@pytest.mark.parametrize("damage", sorted(_NO_COUNT), ids=sorted(_NO_COUNT))
def test_a_manifest_that_declares_no_sample_count_refuses_rather_than_skips(tmp_path, damage):
    """The guard's own defeating input (rule 8): delete or mistype the field it
    compares against. The WAV is the one `render()` wrote, byte for byte. A
    `manifest.get` that fell through would hand the guard nothing to compare,
    and it would pass."""
    rec = _retained(tmp_path)
    _rewrite_json(_wav_of(tmp_path, rec).parent / "manifest.json", _NO_COUNT[damage])
    outcome = _load(tmp_path, rec)
    assert outcome.startswith("REFUSED "), outcome
    assert f"{N_TONE} samples on disk" in outcome and "not a sample count" in outcome, outcome


# ---- measure_osc_drift_reference --------------------------------------------
@pytest.fixture
def scorecard(tmp_path, monkeypatch):
    for source in (M1A, M5A):
        case = tmp_path / source.name
        case.mkdir()
        for path in (source / "manifest.json", *source.glob("*.wav")):
            shutil.copyfile(path, case / path.name)
    monkeypatch.setattr(mod, "SCOREBOARD", str(tmp_path))
    return tmp_path


@pytest.fixture(scope="module")
def committed_drift():
    """`measure_miniv3()` over the committed scoreboard, read only."""
    saved = mod.SCOREBOARD
    mod.SCOREBOARD = str(SCORECARD)
    try:
        return mod.measure_miniv3()
    finally:
        mod.SCOREBOARD = saved


def _clip_record(man: dict, case: str, name: str) -> dict:
    """The manifest record that binds clip `name` to its sha256."""
    if case == "mono-m5a-miniv3":
        return man["audio"]
    return next(r for r in [*man["renders"], *man["controls"].values()] if r["file"] == name)


def _frames_needed(case: str, name: str) -> int:
    """Where the last window measured from clip `name` ends, by the index rule
    the tool slices its segments by: `int(t1 * sr)`."""
    man = json.loads((SCORECARD / case / "manifest.json").read_text())
    picker = {"mono-m1a-miniv3": mod._windows_m1a, "mono-m5a-miniv3": mod._windows_m5a}[case]
    return max(int(t1 * SR) for fn, _s, _l, _n, _t0, t1, _c in picker(man) if fn == name)


def _cut_clip(scorecard: Path, case: str, name: str, n_frames: int, *, agree=False,
              rehash=True) -> None:
    """The frozen clip `name` cut to `n_frames` frames. With `rehash`, its
    manifest's sha256 is rewritten to match; with `agree`, an M5A record's own
    frame count is rewritten to the cut length as well."""
    path = scorecard / case / name

    def damage(man):
        rec = _clip_record(man, case, name)
        rec["sha256"] = _sha256(path)
        if agree:
            rec["samples"] = n_frames
    _cut_wav(path, n_frames)
    if rehash:
        _rewrite_json(scorecard / case / "manifest.json", damage)


def _by(reports, prefix):
    return [r for r in reports if r["label"].startswith(prefix)]


def _window_lines(reports, prefix) -> str:
    """The window lines `main()` prints for these reports, in its own format."""
    lines = []
    for r in _by(reports, prefix):
        head = f"   {r['label']:<40} {r['verdict']:<9}"
        if r["verdict"] == "REFUSED":
            lines.append(f"{head} {r['refusal']}: {r.get('refusal_detail', '')[:70]}")
        else:
            lines.append(head + (f" rms {r['drift_rms_cents']:7.4f} c  "
                                 f"p2p {r['drift_p2p_cents']:7.4f} c  "
                                 f"floor {r['floor_cents']:7.4f} c  "
                                 f"f0 {r['f0_offset_cents']:+7.4f} c  "
                                 f"ripple {r['env_ripple_db']:5.2f} dB"))
    return "\n".join(lines)


@needs_frozen
@pytest.mark.parametrize("kept", [
    lambda had, need: had * 3 // 4,
    lambda had, need: int(3.125 * SR),
], ids=["three-quarters", "inside-note-2s-window"])
def test_a_clip_cut_inside_a_window_refuses_each_window_measured_from_it(scorecard, kept):
    """The oscillator-1 control cut at 75 % -- inside the third note's window
    (4.1 s to 5.9 s), after its held part -- and at 3.125 s, inside the
    second's. The manifest's sha256 is rewritten to match. An M1A manifest
    states no frame count, so what the reader can assert is that the clip
    reaches the end of every window measured from it."""
    case = "mono-m1a-miniv3"
    need = _frames_needed(case, M1A_OSC1)
    kept = kept(_frames(M1A / M1A_OSC1), need)
    assert kept < need
    _cut_clip(scorecard, case, M1A_OSC1, kept)
    report = mod.measure_miniv3()
    rows = _by(report["reports"], "m1a/osc1_open/")
    lines = _window_lines(report["reports"], "m1a/osc1_open/")
    assert [r.get("refusal") for r in rows] == ["CLIP_SHORT"] * 3, lines
    assert all(f"{M1A_OSC1}: {kept} frames, needs {need}" in r["refusal_detail"] for r in rows), rows
    pair = report["across_note"]["m1a-osc1-midi36-4.0s-apart"]
    assert pair["verdict"] == "REFUSED" and "span_cents" not in pair, pair
    assert any("drift_rms_cents" in r for r in _by(report["reports"], "m1a/phrase0/")), lines


@needs_frozen
def test_an_m5a_clip_cut_after_its_last_window_refuses_on_the_length_its_record_says(scorecard):
    """80 % of the M5A audio: it still contains every window measured from it,
    re-hashed. This is the cut a minimum length cannot see, and the one the
    M5A record can -- its `audio.samples` says 1,305,512 and the file holds
    fewer."""
    case = "mono-m5a-miniv3"
    need = _frames_needed(case, M5A_RAW)
    had = _frames(M5A / M5A_RAW)
    kept = had * 4 // 5
    assert kept >= need, "the cut must leave every window whole"
    _cut_clip(scorecard, case, M5A_RAW, kept)
    report = mod.measure_miniv3()
    rows = _by(report["reports"], "m5a/")
    assert [r.get("refusal") for r in rows] == ["CLIP_LENGTH"] * 4, _window_lines(rows, "")
    for row in rows:
        assert f"{M5A_RAW}: {kept} frames, its record says {had} samples" in row["refusal_detail"], row
    pair = report["across_note"]["m5a-midi84-saw-vs-pulse"]
    assert pair["verdict"] == "REFUSED", pair
    assert not {"span_cents", "between_captures"} & set(pair), pair


@needs_frozen
def test_the_console_prints_a_refusal_where_a_cut_clip_printed_numbers(scorecard, capsys):
    """Through `main()`, the entry that is run: the same 80 % cut. On the
    unguarded reader these four lines each carried a drift figure in cents,
    measured on 80 % of the audio the record describes."""
    had = _frames(M5A / M5A_RAW)
    kept = had * 4 // 5
    _cut_clip(scorecard, "mono-m5a-miniv3", M5A_RAW, kept)
    mod.main([])
    printed = [line for line in capsys.readouterr().out.splitlines() if line.startswith("   m5a/")]
    assert len(printed) == 4, "\n".join(printed)
    assert all(re.search(r"REFUSED\s+CLIP_LENGTH: ", line) for line in printed), "\n".join(printed)
    assert all(f"{kept} frames" in line and f"{had} samples" in line for line in printed), \
        "\n".join(printed)


@needs_frozen
@pytest.mark.parametrize("short_by", [0, 1], ids=["exactly-reaches", "one-frame-short"])
def test_a_clip_is_refused_by_its_last_frame_not_by_a_margin(scorecard, committed_drift, short_by):
    """The boundary. A clip that ends exactly where its last window ends holds
    every sample any window reads, so it measures, and what it measures is
    what the uncut clip measures. One frame fewer is a cut inside the window."""
    case = "mono-m1a-miniv3"
    need = _frames_needed(case, M1A_OSC1)
    _cut_clip(scorecard, case, M1A_OSC1, need - short_by)
    cut = _by(mod.measure_miniv3()["reports"], "m1a/osc1_open/")
    if short_by:
        assert [r.get("refusal") for r in cut] == ["CLIP_SHORT"] * 3, _window_lines(cut, "")
        assert all(f"needs {need}" in r["refusal_detail"] for r in cut), cut
        return
    whole = _by(committed_drift["reports"], "m1a/osc1_open/")
    assert _same(cut, whole), "a clip that reaches every window must measure as the whole one"


@needs_frozen
def test_the_untouched_copies_measure_exactly_what_the_committed_tree_measures(
        scorecard, committed_drift):
    """Control: the guard passes the seven clips it was written for. Nothing
    is refused for length, and every report is the committed one."""
    mine = mod.measure_miniv3()
    assert not [r for r in mine["reports"] if r.get("refusal") in ("CLIP_SHORT", "CLIP_LENGTH")]
    assert _same(mine, committed_drift)


def _no_count(n):
    return {
        "field-deleted": lambda a: a.pop("samples"),
        "null": lambda a: a.update(samples=None),
        "string": lambda a: a.update(samples=str(n)),
        "float-of-the-right-value": lambda a: a.update(samples=float(n)),
    }


@needs_frozen
@pytest.mark.parametrize("damage", sorted(_no_count(0)), ids=sorted(_no_count(0)))
def test_an_m5a_record_that_declares_no_frame_count_refuses_rather_than_skips(scorecard, damage):
    """Rule 8 again, for the declared length: the clip is the committed one,
    byte for byte, and its record no longer says how long it is. Falling back
    to the minimum length here would skip the check that sees the cut this
    record exists to describe."""
    n = _frames(M5A / M5A_RAW)
    _rewrite_json(scorecard / "mono-m5a-miniv3" / "manifest.json",
                  lambda man: _no_count(n)[damage](man["audio"]))
    rows = _by(mod.measure_miniv3()["reports"], "m5a/")
    assert [r.get("refusal") for r in rows] == ["CLIP_LENGTH"] * 4, _window_lines(rows, "")
    assert f"not a frame count; the clip holds {n} frames" in rows[0]["refusal_detail"], rows[0]


@needs_frozen
def test_the_hash_still_refuses_a_clip_that_is_the_length_its_record_says(scorecard):
    """Control: one bit of the M5A audio's last sample, in place -- the length
    and the record untouched. CLIP_HASH, as before: the bytes are checked
    before their number."""
    path = scorecard / "mono-m5a-miniv3" / M5A_RAW
    data = bytearray(path.read_bytes())
    data[-1] ^= 1
    path.write_bytes(bytes(data))
    rows = _by(mod.measure_miniv3()["reports"], "m5a/")
    assert [r.get("refusal") for r in rows] == ["CLIP_HASH"] * 4, _window_lines(rows, "")


@needs_frozen
def test_a_clip_cut_without_its_hash_rewritten_still_refuses_on_the_hash(scorecard):
    """Control: the accidental truncation -- a half-copied file, its record
    not touched -- was always caught, by the hash. The length guards are for
    the file whose record was rewritten to follow it."""
    _cut_clip(scorecard, "mono-m1a-miniv3", M1A_OSC1, _frames(M1A / M1A_OSC1) // 2, rehash=False)
    rows = _by(mod.measure_miniv3()["reports"], "m1a/osc1_open/")
    assert [r.get("refusal") for r in rows] == ["CLIP_HASH"] * 3, _window_lines(rows, "")


@needs_frozen
def test_a_record_made_to_agree_with_a_deep_cut_is_still_refused_by_its_windows(scorecard):
    """The declared length's own defeating input (rule 8): the M5A record
    rewritten to describe the cut clip, hash and frame count both. It passes
    the length check -- and is then refused, because half the audio cannot
    hold the windows the timeline asks for."""
    case = "mono-m5a-miniv3"
    need = _frames_needed(case, M5A_RAW)
    kept = _frames(M5A / M5A_RAW) // 2
    assert kept < need
    _cut_clip(scorecard, case, M5A_RAW, kept, agree=True)
    rows = _by(mod.measure_miniv3()["reports"], "m5a/")
    assert [r.get("refusal") for r in rows] == ["CLIP_SHORT"] * 4, _window_lines(rows, "")
    assert all(f"{M5A_RAW}: {kept} frames, needs {need}" in r["refusal_detail"] for r in rows), rows


@needs_frozen
def test_an_m1a_clip_cut_after_its_last_window_still_measures_pinning_the_limit(
        scorecard, committed_drift):
    """The M1A guard's blind spot, pinned. 95 % of the oscillator-1 control,
    re-hashed: every window is whole, the manifest states no frame count to
    disagree with, and all 22 reports are the committed ones. Only a stated
    length in the M1A manifest could see this cut."""
    case = "mono-m1a-miniv3"
    need = _frames_needed(case, M1A_OSC1)
    kept = _frames(M1A / M1A_OSC1) * 95 // 100
    assert kept >= need
    _cut_clip(scorecard, case, M1A_OSC1, kept)
    mine = mod.measure_miniv3()
    assert not [r for r in mine["reports"] if r.get("refusal") in ("CLIP_SHORT", "CLIP_LENGTH")]
    assert _same(mine, committed_drift)


# ---- envelope_history_337 ---------------------------------------------------
def _last_gate_frames(row: dict, sr: int = SR) -> int:
    """The frames a render needs: where its last note's gate ends
    (`int(round(t * sr))`) plus the 5 ms `rms_envelope` centred-window
    lookahead, `(k - 1) // 2` = 119 at 48 kHz, which `held_rms` and
    `attack_ms` read past the gate end. Written out here, not imported, so
    the control does not share the tool's arithmetic."""
    t = row["events"][-1]
    k = int(round(5.0e-3 * sr))
    return int(round((t["on_s"] + t["gate_s"]) * sr)) + (k - 1) // 2


def _committed_row(name: str) -> dict:
    return next(r for r in json.loads((ATTACK / "report.json").read_text())["renders"]
                if r["wav"] == name)


def _cut_attack_render(tmp_path: Path, name: str, n_frames: int) -> None:
    """A `report.json` holding only the row for `name`, and that frozen render
    cut to `n_frames` frames with its row's sha256 rewritten to match."""
    (tmp_path / "report.json").write_text(
        json.dumps({"renders": [_committed_row(name)]}))
    path = tmp_path / name
    shutil.copyfile(ATTACK / name, path)
    _cut_wav(path, n_frames)
    _rewrite_json(tmp_path / "report.json",
                  lambda rep: rep["renders"][0].update(sha256=_sha256(path)))


def _measured(src: Path) -> str:
    """What `measure(src)` did, as `main()` reports a render: its refusal, or
    every row's numbers."""
    try:
        rows = eh.measure(src)
    except refusal.Refused as exc:
        return f"REFUSED {exc}"
    return "\n".join(_row_line(r) for r in rows)


def _row_line(row: dict) -> str:
    return (f"{row['wave']:5s} {row['condition']:16s} resid {row['r_db']:8.1f} dB  "
            f"attack5ms {row['attack_5ms']:6.2f}  attack1ms {row['attack_1ms']:6.2f}")


@pytest.fixture(scope="module")
def committed_rows():
    """`measure()` over the committed attack-context corpus, read only."""
    return eh.measure()


_MEASURED = ("r_db", "floor_limited", "zero_run_before_on_s", "attack_5ms", "attack_1ms")


def _numbers(row: dict) -> dict:
    return {k: row[k] for k in _MEASURED}


def _committed_full(name: str, committed_rows) -> dict:
    """The row `measure()` returns for the uncut render `name`."""
    want = _committed_row(name)
    return next(r for r in committed_rows
                if (r["wave"], r["condition"], r["repeat"])
                == (want["wave"], want["condition"], want["repeat"]))


@needs_frozen
@pytest.mark.parametrize("name, keep", [
    (ISOLATED, lambda had, need: had * 5 // 100),
    (GAP5, lambda had, need: need - 960),
    (GAP5, lambda had, need: had * 45 // 100),
], ids=["isolated-at-5pct", "gap5-inside-its-held-window", "gap5-at-45pct"])
def test_a_render_cut_inside_its_last_gate_refuses(tmp_path, committed_rows, name, keep):
    """5 % of an isolated render stops about 1,200 frames short of its note's
    gate; a gap-5 render cut 960 frames short ends inside the 40 ms its held
    level is read from, and at 45 % well before the note. The record states
    no length, so what the reader can assert is that the render reaches the
    end of the last note's gate -- naming the file and both lengths."""
    need = _last_gate_frames(_committed_row(name))
    kept = keep(_frames(ATTACK / name), need)
    assert kept < need
    _cut_attack_render(tmp_path, name, kept)
    outcome = _measured(tmp_path)
    assert outcome.startswith("REFUSED "), (
        f"{outcome}\n   committed row: {_row_line(_committed_full(name, committed_rows))}")
    assert f"{name}: {kept} frames, needs {need}" in outcome, outcome


@needs_frozen
@pytest.mark.parametrize("short_by", [0, 1], ids=["exactly-as-long", "one-frame-short"])
def test_a_render_is_refused_by_its_last_frame_not_by_a_margin(tmp_path, committed_rows, short_by):
    """The boundary. A render that ends exactly where its last note's gate
    plus the envelope lookahead ends holds every sample a window reads, and
    measures what the whole render measures; one frame fewer does not."""
    need = _last_gate_frames(_committed_row(GAP5))
    _cut_attack_render(tmp_path, GAP5, need - short_by)
    outcome = _measured(tmp_path)
    if short_by:
        assert outcome.startswith("REFUSED "), outcome
        assert f"{GAP5}: {need - 1} frames, needs {need}" in outcome, outcome
        return
    assert not outcome.startswith("REFUSED"), outcome
    got = _numbers(eh.measure(tmp_path)[0])
    assert got == _numbers(_committed_full(GAP5, committed_rows)), (got, _numbers(_committed_full(GAP5, committed_rows)))


@needs_frozen
def test_the_committed_renders_reach_their_last_gate_and_measure_to_the_frozen_record(committed_rows):
    """Control: the guard passes the 36 renders it was written for, and their
    rows are the frozen record's, so no number moved."""
    rep = json.loads((ATTACK / "report.json").read_text())
    assert all(_frames(ATTACK / r["wav"]) >= _last_gate_frames(r) for r in rep["renders"])
    frozen = json.loads((ATTACK / "history-337" / "report.json").read_text())
    assert committed_rows == frozen["rows"]
    assert eh.verdict(eh.gate(committed_rows)) == frozen["verdict"]


@needs_frozen
def test_the_hash_still_refuses_a_render_whose_length_matches(tmp_path):
    """Control: one bit of the last sample, in place -- the length untouched,
    the hash not rewritten. Still the hash that refuses."""
    path = tmp_path / GAP5
    shutil.copyfile(ATTACK / GAP5, path)
    (tmp_path / "report.json").write_text(json.dumps({"renders": [_committed_row(GAP5)]}))
    data = bytearray(path.read_bytes())
    data[-1] ^= 1
    path.write_bytes(bytes(data))
    outcome = _measured(tmp_path)
    assert f"REFUSED hash mismatch: {GAP5}" in outcome, outcome


@needs_frozen
def test_a_render_cut_after_its_last_gate_still_measures_pinning_the_limit(tmp_path, committed_rows):
    """This guard's blind spot, pinned. 95 % of the render, re-hashed: the
    record states no length, every window lies inside the first 47 %, and the
    row is the committed one. Nothing this tool reads can tell it from the
    whole render."""
    row = _committed_row(GAP5)
    kept = int(_frames(ATTACK / GAP5) * 0.95)
    assert kept >= _last_gate_frames(row)
    _cut_attack_render(tmp_path, GAP5, kept)
    got = _numbers(eh.measure(tmp_path)[0])
    assert got == _numbers(_committed_full(GAP5, committed_rows)), (got, _numbers(_committed_full(GAP5, committed_rows)))


@pytest.fixture
def synthetic_nonfloor(tmp_path):
    """A render whose pre-onset audio is NOT exactly zero, so `residual_db`
    divides by the held level instead of returning the floor, and whose
    amplitude decays through the gate and then bursts for 300 frames right
    after it, inside the 119-frame lookahead, so the last held-window
    envelope values move from the lowest to the highest ranks and the median
    with them. The committed GAP5 render is floor-limited, so
    its boundary test cannot see `held_rms` move (review of #2123)."""
    sr, on, gate = SR, 0.1, 0.6
    n = int(round((on + gate) * sr)) + 5000
    t = np.arange(n) / sr
    x = 1e-3 * np.sin(2 * np.pi * 220 * t)
    m = (t >= on) & (t < on + gate)
    x[m] += 0.5 * (1 - 0.9 * (t[m] - on) / gate) * np.sin(2 * np.pi * 440 * t[m])
    g = int(round((on + gate) * sr))
    x[g:g + 300] += 0.5 * np.sin(2 * np.pi * 440 * t[g:g + 300])
    pcm = np.round(x * 32767).astype(np.int16)
    row = {"wav": "syn.wav", "wave": "syn", "condition": "c", "repeat": 0,
           "events": [{"on_s": on, "gate_s": gate}]}

    def write(frames):
        wavfile.write(tmp_path / "syn.wav", sr, pcm[:frames])
        (tmp_path / "report.json").write_text(json.dumps(
            {"renders": [dict(row, sha256=_sha256(tmp_path / "syn.wav"))]}))
        return tmp_path

    return write, _last_gate_frames(row), int(round((on + gate) * sr)), pcm


def test_the_lookahead_matters_on_a_render_that_is_not_floor_limited(synthetic_nonfloor):
    """Control with the input that defeats the GAP5 boundary test: cutting at
    the gate end alone is refused, cutting at gate end + lookahead measures
    identically to the whole render, and the gate-end cut WOULD have moved the
    number (so the refusal is not cosmetic)."""
    write, need, gate_end, pcm = synthetic_nonfloor
    whole = _numbers(eh.measure(write(need + 5000))[0])
    assert not whole["floor_limited"], "the control must exercise the divide, not the floor"
    outcome = _measured(write(gate_end))
    assert outcome.startswith("REFUSED "), outcome
    assert f"syn.wav: {gate_end} frames, needs {need}" in outcome, outcome
    assert _measured(write(need - 1)).startswith("REFUSED ")
    assert _numbers(eh.measure(write(need))[0]) == whole
    # Falsification: skip the guard and the gate-end cut does move a number.
    cut = pcm[:gate_end].astype(np.float64) / 32768.0
    full = pcm.astype(np.float64) / 32768.0
    assert eh.held_rms(cut, SR, 0.1, 0.7) != eh.held_rms(full, SR, 0.1, 0.7)
    assert eh.residual_db(cut, SR, 0.1, 0.7)[0] != eh.residual_db(full, SR, 0.1, 0.7)[0]
