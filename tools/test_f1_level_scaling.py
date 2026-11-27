"""Controls for the F1 input-scaling experiment (tools/probes/f1_level_scaling.py)
and the matched-level captures (tools/f1_level_capture.py).

The load-bearing control is the register trap: a voice constructed with a
different `volts_per_unit` gets the GLOBAL configuration's gain/ogain from
`patch_regs`, so it renders the baseline's audio. The experiment must record
the words that actually enter the ladder and REFUSE a candidate whose words
equal the baseline's.
"""
from __future__ import annotations

import itertools
import math
import pathlib
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "probes"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))

import f1_level_scaling as fls       # noqa: E402
import f1_level_capture as cap       # noqa: E402
import f1_selected_path as f1        # noqa: E402

CUT = 1000.0
FREQS = [200.0, 1000.0, 3000.0]      # a short stimulus: the controls need words, not curves


def _words(r):
    return (r["gain"], r["ogain"])


def _render(voice, regs, amp=0.25):
    xq, parts = fls.stimulus(FREQS, amp)
    cut = np.full(len(xq), regs["cut_lo"], dtype=np.int64)
    with fls.instrument(voice) as rec:
        y, _g, _k = f1.render_path(voice, xq, cut, regs)
    return y, rec


# --- the baseline assumption ------------------------------------------------
def test_baseline_vpu_is_asserted_not_assumed():
    assert fls.base_vpu_asserted() == 0.13


def test_candidate_one_is_exactly_the_host_conversion():
    assert fls.candidate_regs(1.0, CUT) == f1.host_regs(CUT, 0.0, 1.0)


@pytest.mark.parametrize("s", [0.5, 0.25])
def test_scaled_candidates_change_both_words_and_compensate_exactly(s):
    b, c = fls.candidate_regs(1.0, CUT), fls.candidate_regs(s, CUT)
    assert c["gain"] != b["gain"] and c["ogain"] != b["ogain"]
    assert c["gain"] == pytest.approx(b["gain"] * s, abs=1)
    # small-signal level: gain*ogain constant to register rounding
    assert c["gain"] * c["ogain"] == pytest.approx(b["gain"] * b["ogain"], rel=1e-4)
    for k in ("k", "cut_lo", "res", "drive"):
        assert c[k] == b[k]


# --- THE TRAP ----------------------------------------------------------------
@pytest.mark.parametrize("s", [0.5, 0.25])
def test_trap_constructor_only_vpu_leaves_the_host_words_unchanged(s):
    voice, regs = fls.constructor_only_regs(s, CUT)
    inner = voice.ladder._ladder
    assert inner.vpu == pytest.approx(0.13 * s)            # the constructor DID change
    assert _words(regs) == _words(fls.candidate_regs(1.0, CUT))   # the words did not


def test_trap_constructor_only_candidate_renders_the_baseline_audio_bit_for_bit():
    base_voice = f1.build_voice("selected")[1]
    y0, _ = _render(base_voice, fls.candidate_regs(1.0, CUT))
    trap_voice, regs = fls.constructor_only_regs(0.25, CUT)
    y1, _ = _render(trap_voice, regs)
    assert np.array_equal(y0, y1)


def test_trap_is_refused_by_the_register_assertion():
    voice, regs = fls.constructor_only_regs(0.5, CUT)
    _y, rec = _render(voice, regs)
    base = _words(fls.candidate_regs(1.0, CUT))
    with pytest.raises(fls.Refused, match="equal the baseline"):
        fls.assert_registers("half", 0.5, rec["words"], _words(regs), base)


def test_real_candidate_words_enter_the_ladder_and_change_the_audio():
    base_voice = f1.build_voice("selected")[1]
    y0, r0 = _render(base_voice, fls.candidate_regs(1.0, CUT))
    v = f1.build_voice("selected")[1]
    regs = fls.candidate_regs(0.25, CUT)
    y1, r1 = _render(v, regs)
    fls.assert_registers("quarter", 0.25, r1["words"], _words(regs),
                         _words(fls.candidate_regs(1.0, CUT)))     # does not raise
    assert r1["words"] == {_words(regs)}
    assert int(np.count_nonzero(y0 != y1)) > 0.5 * len(y0)


def test_words_predicted_but_not_entering_are_refused():
    v = f1.build_voice("selected")[1]
    predicted = _words(fls.candidate_regs(0.5, CUT))
    _y, rec = _render(v, fls.candidate_regs(1.0, CUT))           # baseline words go in
    with pytest.raises(fls.Refused, match="not the candidate's predicted"):
        fls.assert_registers("half", 0.5, rec["words"], predicted,
                             _words(fls.candidate_regs(1.0, CUT)))


# --- instrumentation is counts only ------------------------------------------
def test_instrumentation_does_not_change_a_word_and_restores_itself():
    import fixed
    orig_sat = fixed.sat
    v1 = f1.build_voice("selected")[1]
    v2 = f1.build_voice("selected")[1]
    regs = fls.candidate_regs(1.0, CUT)
    xq, _ = fls.stimulus(FREQS, 0.5)
    cut = np.full(len(xq), regs["cut_lo"], dtype=np.int64)
    y_plain, _, _ = f1.render_path(v1, xq, cut, regs)
    with fls.instrument(v2) as rec:
        y_inst, _, _ = f1.render_path(v2, xq, cut, regs)
    assert np.array_equal(y_plain, y_inst)
    assert rec["tanh_calls"] > 0
    assert fixed.sat is orig_sat and "process" not in vars(v2.ladder._ladder)


def test_clamp_counter_sees_an_overdriven_input():
    v = f1.build_voice("selected")[1]
    regs = dict(fls.candidate_regs(1.0, CUT))
    regs["gain"] = regs["gain"] * 4                        # 4x the input: past the tanh domain
    _y, rec = _render(v, regs, amp=0.9)
    assert rec["tanh_clamp"] > 0


# --- the noise estimator: a known answer --------------------------------------
def test_noise_estimator_reads_a_known_noise_floor_and_ignores_harmonics():
    sr, amp = 48000, 1000.0
    rng = np.random.default_rng(1)
    parts, segs, t0 = [], [], 0
    for f in (200.0, 1000.0):
        n = 9600
        t = np.arange(n) / sr
        s = amp * np.sin(2 * np.pi * f * t) + 0.1 * amp * np.sin(2 * np.pi * 3 * f * t)
        segs.append(s + rng.normal(0, amp / math.sqrt(2) * 10 ** (-70 / 20), n))
        parts.append((t0, n, f))
        t0 += n
    r = fls.noise_thd(np.concatenate(segs), parts, amp)
    assert r["noise_db_re_stimulus_median"] == pytest.approx(-70.0, abs=0.5)
    assert r["thd_db_median"] == pytest.approx(-20.0, abs=0.1)


# --- the captures ------------------------------------------------------------
def test_captures_verify_and_a_tampered_hash_refuses():
    m = cap.load_manifest()
    y, meta = cap.load_capture("cut250", 0.0625, 1, m)
    assert len(y) == meta["frames"]
    with pytest.raises(cap.Refused, match="hashes"):
        cap.load_capture("cut250", 0.0625, 1, m, inject="TAMPERED")


def test_captures_are_separate_from_the_frozen_profile():
    m = cap.load_manifest()
    assert all(not c["archive_member"].startswith("refprofile") for c in m["clips"].values())
    assert cap.OUT_DIR.relative_to(ROOT).parts[:3] == ("docs", "scorecard", "f1-level")


def test_post_render_pin_alias_is_limited_to_the_four_audio_in_indices():
    assert set(cap.AUDIO_IN_NAMES) == {259, 260, 264, 265}


# --- the rule itself, on synthetic tables ------------------------------------
def _fake_table(corner_err, spread=0.0, base_words=(1, 2), cand_words=(3, 4)):
    def lv(err, words, extra=0.0):
        cases = {c: {"score": {"corner_hz": {"valid": True, "error_pct": err + extra},
                               "rolloff_db_oct": {"valid": True, "error": 0.5},
                               "lowband_db": {"valid": True, "error": 0.1}},
                     "regs": {"entering": [list(words)], "baseline": list(base_words)},
                     "clipping": {"reconstruction_would_clip": 0, "output_rail_words": 0,
                                  "tanh_domain_clamps": 0, "sat_state_events": 0}}
                 for c in fls.CASES}
        return {"cases": cases, "open": {"noise": {"noise_db_re_stimulus_median": -80.0},
                                         "clipping": {"reconstruction_would_clip": 0,
                                                      "output_rail_words": 0,
                                                      "tanh_domain_clamps": 0,
                                                      "sat_state_events": 0}}}
    tags = [cap.amp_tag(a) for a in fls.AMPS]
    base = {"s": 1.0, "levels": {t: lv(-16.0, base_words, i * 5.0) for i, t in enumerate(tags)}}
    cand = {"s": 0.5, "levels": {t: lv(corner_err, cand_words, i * spread)
                                 for i, t in enumerate(tags)}}
    return {"baseline": base, "half": cand}


def test_rule_selects_a_candidate_that_meets_every_clause():
    ev = fls.evaluate(_fake_table(-5.0))
    assert ev["verdicts"]["half"]["eligible"] and ev["selection"] == "half"


def test_rule_refuses_selection_when_words_equal_the_baseline():
    ev = fls.evaluate(_fake_table(-5.0, cand_words=(1, 2)))
    assert not ev["verdicts"]["half"]["checks"]["1_registers"]
    assert ev["selection"] is None


def test_rule_rejects_an_insufficient_corner_gain():
    ev = fls.evaluate(_fake_table(-14.0))
    assert not ev["verdicts"]["half"]["checks"]["3_corner"] and ev["selection"] is None


# --- f1_level_capture.verify() and the empty record (#1469) ----------------

def test_verify_with_no_capture_in_the_manifest_refuses(monkeypatch, capsys):
    """An empty `clips` dict ran the loop zero times, so `bad` stayed 0 and
    verify() printed `0 verified, 0 failed` with OK."""
    monkeypatch.setattr(cap, "load_manifest", lambda: {"clips": {}})
    rc = cap.verify()
    out = capsys.readouterr().out
    assert rc == cap.REFUSED
    assert "REFUSED" in out and "0 verified" not in out


@pytest.mark.parametrize("payload", [b"", b"null", b"\xff"])
def test_unreadable_capture_manifest_refuses(tmp_path, monkeypatch, capsys, payload):
    path = tmp_path / "manifest.json"
    path.write_bytes(payload)
    monkeypatch.setattr(cap, "MANIFEST", path)
    assert cap.main([]) == cap.REFUSED
    out = capsys.readouterr().out
    assert "REFUSED" in out and str(path) in out and "verified" not in out


def test_explicit_empty_manifest_cannot_fall_back_to_the_archive():
    with pytest.raises(cap.Refused, match="not a capture"):
        cap.load_capture("open20k", 0.25, 1, {})


def test_verify_on_the_committed_captures_counts_what_it_verified(capsys):
    n = len(cap.load_manifest()["clips"])
    assert n > 0
    rc = cap.verify()
    assert rc == cap.OK
    assert f"{n} verified, 0 failed" in capsys.readouterr().out


def test_verify_with_a_partial_capture_manifest_refuses(monkeypatch, capsys):
    # Rule 8 defeating input for `if not m.get("clips"):`: keeping one of the
    # 24 declared (CONDITIONS x AMPS x TAKES) clips satisfied `bool(clips)` and
    # certified `1 verified, 0 failed` while 23 captures were absent.
    real = cap.load_manifest()
    first_key = sorted(real["clips"])[0]
    partial = {**real, "clips": {first_key: real["clips"][first_key]}}
    monkeypatch.setattr(cap, "load_manifest", lambda: partial)
    rc = cap.verify()
    out = capsys.readouterr().out
    assert rc == cap.REFUSED
    assert "REFUSED" in out and "0 failed" not in out


@pytest.mark.parametrize("field", ["clone", "condition", "cut_hz", "amp", "take"])
def test_verify_binds_capture_metadata_to_each_key(monkeypatch, capsys, field):
    manifest = cap.load_manifest()
    clips = manifest["clips"]
    entry = clips[cap.clip_key("open20k", 0.25, 1)]
    if field == "clone":
        manifest["clips"] = dict.fromkeys(clips, entry)
    elif field in ("cut_hz", "amp"):
        entry["commanded"][field] = 0
    else:
        entry[field] = None
    monkeypatch.setattr(cap, "load_manifest", lambda: manifest)
    assert cap.verify() == cap.REFUSED
    assert "identity" in capsys.readouterr().out


def test_verify_with_a_corrupted_clip_fails(monkeypatch, capsys):
    import copy
    bad_m = copy.deepcopy(cap.load_manifest())
    first_key = sorted(bad_m["clips"])[0]
    sha = bad_m["clips"][first_key]["sha256"]
    bad_m["clips"][first_key]["sha256"] = ("0" if sha[0] != "0" else "1") + sha[1:]
    monkeypatch.setattr(cap, "load_manifest", lambda: bad_m)
    rc = cap.verify()
    out = capsys.readouterr().out
    assert rc == cap.FAIL
    assert "FAIL" in out and "1 failed" in out


def _verify_with(monkeypatch, tmp_path, mutate_entry, rewrite_wav=None):
    """verify() on a manifest copy whose archive may be swapped for one with
    hash-correct, deliberately wrong audio (#1469 review: zero frames, or one
    take's bytes under every key)."""
    import copy
    import hashlib
    import io
    import zipfile
    m = copy.deepcopy(cap.load_manifest())
    if rewrite_wav is not None:
        zin = zipfile.ZipFile(cap.ARCHIVE)
        out = tmp_path / "arch.zip"
        with zipfile.ZipFile(out, "w") as z:
            for n in zin.namelist():
                z.writestr(n, rewrite_wav(n, zin.read(n)))
        monkeypatch.setattr(cap, "ARCHIVE", out)
        with zipfile.ZipFile(out) as z:
            for meta in m["clips"].values():
                meta["sha256"] = hashlib.sha256(z.read(meta["archive_member"])).hexdigest()
    mutate_entry(m)
    monkeypatch.setattr(cap, "load_manifest", lambda: m)
    return cap.verify()


def test_verify_refuses_hash_correct_zero_frame_audio(monkeypatch, tmp_path, capsys):
    """A 48 kHz WAV with no frames, recorded frames=0, digests updated: the
    frame check compared 0 == 0 and printed `24 verified`."""
    import io
    from scipy.io import wavfile

    def empty(_n, _b):
        buf = io.BytesIO()
        wavfile.write(buf, cap.rp.SR, np.zeros(0, dtype=np.float32))
        return buf.getvalue()

    def zero_frames(m):
        for meta in m["clips"].values():
            meta["frames"] = 0

    rc = _verify_with(monkeypatch, tmp_path, zero_frames, empty)
    out = capsys.readouterr().out
    assert rc == cap.FAIL and "verified, 24 failed" in out and "24 verified" not in out


def test_verify_refuses_one_takes_bytes_filed_under_every_condition(monkeypatch, capsys):
    """Copy only sha256 + archive_member of one clip to all 24 entries: hash and
    frame count agree, but the decoded peak/rms are another condition's."""
    manifest = cap.load_manifest()
    src = manifest["clips"][cap.clip_key("open20k", 0.25, 1)]

    def clone_bytes(m):
        for meta in m["clips"].values():
            meta["sha256"], meta["archive_member"] = src["sha256"], src["archive_member"]
            meta["frames"] = src["frames"]

    rc = _verify_with(monkeypatch, None, clone_bytes)
    out = capsys.readouterr().out
    assert rc == cap.FAIL and "disagrees with its capture evidence" in out
    assert "24 verified" not in out


def test_verify_still_accepts_legitimate_byte_sharing_between_repeated_takes(capsys):
    """Control: the unmodified archive shares bytes between takes (the two
    a0.2500 takes above carry the same digest) and must keep verifying 24/24."""
    assert cap.verify() == cap.OK
    assert "24 verified, 0 failed" in capsys.readouterr().out


def _copy_capture_evidence(clips, source, destination):
    # Leave the recipient's condition, take and commanded settings intact.
    for field in ("sha256", "archive_member", "frames", "peak", "rms", "bytes", "parts", "freqs_hz"):
        clips[destination][field] = clips[source][field]


@pytest.mark.parametrize("replay", ["all", "same-cutoff", "same-amplitude",
                                    "swap-second-takes", "one-bad-hash"])
def test_verify_refuses_audio_shared_across_capture_cells(monkeypatch, capsys, replay):
    manifest = cap.load_manifest()
    clips = manifest["clips"]
    source = cap.clip_key("open20k", 0.25, 1)
    if replay in ("all", "one-bad-hash"):
        for key in clips:
            _copy_capture_evidence(clips, source, key)
        if replay == "one-bad-hash":
            clips[cap.clip_key("cut250", 0.125, 2)]["sha256"] = "0" * 64
    elif replay == "swap-second-takes":
        # All 12 original digests remain present, but two cells each borrow a take.
        other = cap.clip_key("cut250", 0.125, 1)
        _copy_capture_evidence(clips, source, cap.clip_key("cut250", 0.125, 2))
        _copy_capture_evidence(clips, other, cap.clip_key("open20k", 0.25, 2))
        assert len({meta["sha256"] for meta in clips.values()}) == 12
    else:
        cond, amp = ("open20k", 0.125) if replay == "same-cutoff" else ("cut250", 0.25)
        for take in cap.TAKES:
            _copy_capture_evidence(clips, source, cap.clip_key(cond, amp, take))
    monkeypatch.setattr(cap, "load_manifest", lambda: manifest)
    assert cap.main([]) == cap.REFUSED
    out = capsys.readouterr().out
    assert "REFUSED" in out and "shared across capture cells" in out
    assert " verified," not in out


def test_surge_side_refuses_one_recording_for_all_levels():
    manifest = cap.load_manifest()
    clips = manifest["clips"]
    for key in clips:
        _copy_capture_evidence(clips, cap.clip_key("open20k", 0.25, 1), key)
    with pytest.raises(cap.Refused, match="shared across capture cells"):
        fls.surge_side(manifest)


@pytest.mark.parametrize("defect", ["at-eof", "overrun", "negative-start", "zero-length",
                                    "fractional-start", "one-sample", "missing-tone", "wrong-hz",
                                    "repeated-window", "shifted-step", "includes-tail"])
def test_verify_requires_usable_projection_windows(monkeypatch, capsys, defect):
    manifest = cap.load_manifest()
    key = cap.clip_key("open20k", 0.25, 1)
    clip = manifest["clips"][key]
    parts = clip["parts"]
    last = parts[-1]
    if defect == "at-eof":
        for part in parts:
            part[0] = clip["frames"]
    elif defect == "overrun":
        last[0] = clip["frames"] - last[1] + 1
    elif defect == "negative-start":
        last[0] = -1
    elif defect == "zero-length":
        last[1] = 0
    elif defect == "fractional-start":
        last[0] += 0.5
    elif defect == "one-sample":
        last[1] = 1
    elif defect == "repeated-window":
        for part in parts:
            part[:2] = parts[0][:2]
    elif defect == "shifted-step":
        last[0] -= 1
    elif defect == "includes-tail":
        last[1] = clip["frames"] - last[0]
    elif defect == "wrong-hz":
        last[2] = clip["freqs_hz"][-1] = parts[0][2]
    else:
        parts.pop()
    monkeypatch.setattr(cap, "load_manifest", lambda: manifest)
    assert cap.main([]) == cap.REFUSED
    out = capsys.readouterr().out
    assert "REFUSED" in out and key in out and "projection" in out
    assert "24 verified" not in out


def test_capture_curve_accepts_a_window_ending_exactly_at_eof(tmp_path, monkeypatch):
    import hashlib
    import io
    import zipfile
    from scipy.io import wavfile

    manifest = cap.load_manifest()
    clip = manifest["clips"][cap.clip_key("open20k", 0.25, 1)]
    _, original = cap.capture_curve("open20k", 0.25, 1, manifest)
    with zipfile.ZipFile(cap.ARCHIVE) as archive:
        sr, audio = wavfile.read(io.BytesIO(archive.read(clip["archive_member"])))
    end = sum(clip["parts"][-1][:2])
    assert 0 < end < len(audio)
    trimmed = audio[:end]
    wav = io.BytesIO()
    wavfile.write(wav, sr, trimmed)
    digest = hashlib.sha256(wav.getvalue()).hexdigest()
    clip.update(frames=end, sha256=digest, archive_member=digest[:16] + ".wav",
                bytes=len(wav.getvalue()),
                peak=float(np.max(np.abs(trimmed))),
                rms=float(np.sqrt(np.mean(np.square(trimmed.astype(np.float64))))))
    path = tmp_path / "trimmed.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(clip["archive_member"], wav.getvalue())
    monkeypatch.setattr(cap, "ARCHIVE", path)
    _, gains = cap.capture_curve("open20k", 0.25, 1, manifest)
    assert len(gains) == 32 and np.isfinite(gains).all()
    np.testing.assert_array_equal(gains, original)


def test_stereo_audio_with_mono_peak_and_rms_cannot_verify(monkeypatch, tmp_path, capsys):
    import io
    from scipy.io import wavfile

    def stereo(_name, raw):
        sr, y = wavfile.read(io.BytesIO(raw))
        buf = io.BytesIO()
        wavfile.write(buf, sr, np.column_stack((y, y)))
        return buf.getvalue()

    rc = _verify_with(monkeypatch, tmp_path, lambda m: None, stereo)
    out = capsys.readouterr().out
    assert rc == cap.FAIL and "expected mono audio" in out and "0 verified, 24 failed" in out


_EVIDENCE_FIELDS = ("sha256", "archive_member", "frames", "peak", "rms", "bytes", "parts", "freqs_hz")


def _swap_cells(manifest, first, second):
    clips = manifest["clips"]
    for take in cap.TAKES:
        a, b = clips[cap.clip_key(*first, take)], clips[cap.clip_key(*second, take)]
        for f in _EVIDENCE_FIELDS:
            a[f], b[f] = b[f], a[f]


def test_complete_level_swap_refuses_in_both_readers(monkeypatch, capsys):
    """Both takes' whole evidence exchanged between two levels: every digest is
    still distinct and each key keeps its own condition/settings/take, so only
    the frozen-clip anchor can see it (the gain moved by 6.02 dB)."""
    manifest = cap.load_manifest()
    _swap_cells(manifest, ("cut250", 0.0625), ("cut250", 0.125))
    assert len({m["sha256"] for m in manifest["clips"].values()}) == 12
    monkeypatch.setattr(cap, "load_manifest", lambda: manifest)
    assert cap.main([]) == cap.REFUSED
    out = capsys.readouterr().out
    assert "not made at that level" in out and " verified," not in out
    with pytest.raises(cap.LevelBindingError, match="not made at that level"):
        fls.surge_side(manifest)


@pytest.mark.parametrize("first,second,amp", [
    ("cut250", "cut1000", 0.25),     # the swap that exchanged the F1A and F1B corners
    ("cut4000", "open20k", 0.0625),  # the closest pair of frozen response curves
])
@pytest.mark.parametrize("reader", ["verify", "surge_side"])
def test_complete_condition_swap_refuses(monkeypatch, capsys, first, second, amp, reader):
    manifest = cap.load_manifest()
    _swap_cells(manifest, (first, amp), (second, amp))
    assert len({m["sha256"] for m in manifest["clips"].values()}) == 12
    if reader == "verify":
        monkeypatch.setattr(cap, "load_manifest", lambda: manifest)
        assert cap.main([]) == cap.REFUSED
        out = capsys.readouterr().out
        assert "cutoff response" in out and " verified," not in out
    else:
        with pytest.raises(cap.CaptureIdentityError, match="cutoff response"):
            fls.surge_side(manifest)


def test_level_anchor_accepts_the_committed_captures():
    """Measure the cutoff identity margin for all accepted levels and takes."""
    def shape(gains):
        return gains - np.median(gains[:4])

    def rms(a, b):
        return np.sqrt(np.mean((a - b) ** 2))

    # Project the independent references through the scorecard's own loader.
    frozen = {cond: shape(fls.rc.load_filter_reference(clip)[1])
              for cond, (_, clip) in cap.CONDITIONS.items()}
    closest_pair = min(rms(a, b) for a, b in itertools.combinations(frozen.values(), 2))
    assert 2 * cap.CONDITION_ANCHOR_DB < closest_pair

    manifest = cap.load_manifest()
    matching, other = [], []
    for cond, amp, take in itertools.product(frozen, cap.AMPS, cap.TAKES):
        _, gains = cap.capture_curve(cond, amp, take, manifest)
        captured = shape(gains)
        matching.append(rms(captured, frozen[cond]))
        other.extend(rms(captured, ref) for name, ref in frozen.items() if name != cond)
    print(f"{len(matching)} matching curves: worst {max(matching):.8f} dB RMS; "
          f"{len(other)} wrong cutoffs: closest {min(other):.5f} dB RMS")
    assert max(matching) < cap.CONDITION_ANCHOR_DB < min(other)


@pytest.mark.parametrize("field", ["amp", "parts"])
def test_unusable_frozen_curve_refuses(monkeypatch, capsys, field):
    load_clip = cap.rp.load_clip

    def damaged(*args, **kwargs):
        y, sr, meta = load_clip(*args, **kwargs)
        # A truncated grid still carries all four low-band tones.
        return y, sr, {**meta, field: 0 if field == "amp" else meta["parts"][:-1]}

    cap._frozen_gain_db.cache_clear()
    monkeypatch.setattr(cap.rp, "load_clip", damaged)
    try:
        assert cap.main([]) == cap.REFUSED
    finally:
        cap._frozen_gain_db.cache_clear()
    reason = "expected frozen amplitude" if field == "amp" else "one projection window per tone"
    assert reason in capsys.readouterr().out


def test_surge_side_refuses_digest_only_second_takes():
    manifest = cap.load_manifest()
    clips = manifest["clips"]
    for key in list(clips):
        if key.endswith("/take2"):
            clips[key] = {"sha256": clips[key]["sha256"]}
    with pytest.raises(cap.Refused):
        fls.surge_side(manifest)


@pytest.mark.parametrize("field", ["sha256", "archive_member", "frames"])
def test_missing_clip_field_refuses_instead_of_raising(monkeypatch, capsys, field):
    manifest = cap.load_manifest()
    del manifest["clips"][cap.clip_key("cut250", 0.125, 1)][field]
    monkeypatch.setattr(cap, "load_manifest", lambda: manifest)
    assert cap.main([]) == cap.REFUSED
    assert "REFUSED" in capsys.readouterr().out
    with pytest.raises(cap.Refused):
        fls.surge_side(manifest)


def test_absent_frozen_cache_refuses_through_main_instead_of_raising(monkeypatch, capsys):
    """refprofile.Refused is a sibling of cap.Refused, so an absent frozen
    cache used to escape verify()/main() as a traceback. Control: simulate the
    absent cache (independent of the host's cache state)."""
    def absent(*a, **k):
        raise cap.rp.Refused("the frozen reference audio is not in the cache")
    cap._frozen_gain_db.cache_clear()
    monkeypatch.setattr(cap.rp, "load_clip", absent)
    try:
        assert cap.main([]) == cap.REFUSED
    finally:
        cap._frozen_gain_db.cache_clear()
    out = capsys.readouterr().out
    assert "REFUSED" in out and "not in the cache" in out
