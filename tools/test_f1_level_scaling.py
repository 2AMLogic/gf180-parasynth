"""Controls for the F1 input-scaling experiment (tools/probes/f1_level_scaling.py)
and the matched-level captures (tools/f1_level_capture.py).

The load-bearing control is the register trap: a voice constructed with a
different `volts_per_unit` gets the GLOBAL configuration's gain/ogain from
`patch_regs`, so it renders the baseline's audio. The experiment must record
the words that actually enter the ladder and REFUSE a candidate whose words
equal the baseline's.
"""
from __future__ import annotations

import contextlib
import io
import json
import math
import pathlib
import sys
import types
import zipfile

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "probes"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))

import f1_level_scaling as fls       # noqa: E402
import f1_level_capture as cap       # noqa: E402
import f1_selected_path as f1        # noqa: E402
import exit_contract                 # noqa: E402
import refusal                       # noqa: E402
import refprofile as rp              # noqa: E402
import run_case as rc                # noqa: E402
import mono_m5a_score as m5          # noqa: E402

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


def test_verify_on_the_committed_captures_is_unchanged(capsys):
    try:
        cap.load_manifest()
    except refusal.Refused as e:
        pytest.skip(f"REFUSED: {e}")
    code = cap.verify()
    out = capsys.readouterr().out
    assert code == exit_contract.PASS, (code, out[-400:])
    assert out.splitlines()[-1] == "24 verified, 0 failed", out[-400:]
    assert sum(ln.startswith("ok ") for ln in out.splitlines()) == 24, out[-400:]
    assert not any(ln.startswith(("REFUSED", "FAIL")) for ln in out.splitlines()), out[-400:]


# --- verify(): an absent capture is REFUSED, a corrupted one FAIL (issue #1822) --
CAPTURE_KEYS = ("cut250/a0.2500/take1", "cut250/a0.1250/take1", "open20k/a0.0625/take2")
KEPT, ABSENT, TAMPERED = CAPTURE_KEYS


class _Archive:
    """A known-answer stand-in for the committed captures: three short clips
    written by the real `rp.write_clip`, zipped and hashed as `capture()` does,
    with `MANIFEST` / `ARCHIVE` of the module under test pointed at it. It
    never reads the committed 19 MB blob, so every control below states its own
    population."""

    def __init__(self, root, monkeypatch, mod):
        root.mkdir(parents=True, exist_ok=True)
        self.clips, blobs = {}, {}
        for i, key in enumerate(CAPTURE_KEYS):
            cond, _tag, take = key.split("/")
            y = 0.25 * np.sin(2 * np.pi * (300.0 + 100.0 * i) * np.arange(480) / rp.SR)
            wav = root / f"clip{i}.wav"
            rp.write_clip(wav, y)
            blobs[key] = wav.read_bytes()
            self.clips[key] = {"condition": cond, "take": int(take[4:]), "frames": int(len(y)),
                               "sha256": cap._sha(blobs[key]),
                               "archive_member": cap._sha(blobs[key])[:16] + ".wav"}
        self.manifest, self.archive = root / "manifest.json", root / "audio.zip"
        self.manifest.write_text(json.dumps({"schema": "f1-level-captures/1", "clips": self.clips}))
        self.blobs = blobs
        self.write_archive()
        monkeypatch.setattr(mod, "MANIFEST", self.manifest)
        monkeypatch.setattr(mod, "ARCHIVE", self.archive)

    def write_archive(self):
        with zipfile.ZipFile(self.archive, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for key, b in self.blobs.items():
                z.writestr(self.clips[key]["archive_member"], b)

    def drop(self, key):
        del self.blobs[key]
        self.write_archive()

    def corrupt(self, key):
        """The member stays present and readable; its bytes are no longer what
        the manifest hashed."""
        self.blobs[key] = self.blobs[key][:-1] + bytes([self.blobs[key][-1] ^ 0x01])
        self.write_archive()


def _verify(mod, root, monkeypatch, *mutations):
    """`mod.verify()` over a fresh archive after `mutations`, as (code, lines)."""
    arc = _Archive(root, monkeypatch, mod)
    for mutate in mutations:
        mutate(arc)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = mod.verify()
    return code, out.getvalue().splitlines()


def _absent_is_refused(mod, root, monkeypatch):
    code, lines = _verify(mod, root, monkeypatch, lambda a: a.drop(ABSENT))
    assert code == exit_contract.REFUSED, (code, lines)
    refused = [ln for ln in lines if ln.startswith("REFUSED")]
    assert len(refused) == 1 and ABSENT in refused[0] and "absent from the archive" in refused[0], lines
    assert not any(ln.startswith("FAIL") for ln in lines), lines
    assert [ln.split()[1] for ln in lines if ln.startswith("ok ")] == [KEPT, TAMPERED], lines
    assert lines[-1] == "2 verified, 0 failed, 1 refused", lines


def _tampered_still_fails(mod, root, monkeypatch):
    code, lines = _verify(mod, root, monkeypatch, lambda a: a.corrupt(TAMPERED))
    assert code == exit_contract.FAIL, (code, lines)
    failed = [ln for ln in lines if ln.startswith("FAIL")]
    assert len(failed) == 1 and TAMPERED in failed[0] and "hashes" in failed[0], lines
    assert not any(ln.startswith("REFUSED") for ln in lines), lines
    assert lines[-1] == "2 verified, 1 failed", lines


def _measured_disagreement_outranks_a_refusal(mod, root, monkeypatch):
    code, lines = _verify(mod, root, monkeypatch, lambda a: a.drop(ABSENT),
                          lambda a: a.corrupt(TAMPERED))
    assert code == exit_contract.FAIL, (code, lines)
    assert sum(ln.startswith("REFUSED") for ln in lines) == 1, lines
    assert sum(ln.startswith("FAIL") for ln in lines) == 1, lines
    assert lines[-1] == "1 verified, 1 failed, 1 refused", lines


def _clean_is_unchanged(mod, root, monkeypatch):
    code, lines = _verify(mod, root, monkeypatch)
    assert code == exit_contract.PASS, (code, lines)
    assert lines[-1] == "3 verified, 0 failed", lines
    assert not any(ln.startswith(("REFUSED", "FAIL")) for ln in lines), lines


def _raise_site_types_the_precondition(mod, root, monkeypatch):
    """The repair lives at the raise site: a precondition raises `Unbindable`,
    which is still a `Refused` for every other caller (`except refusal.Refused`
    in tools/probes/f1_level_scaling.py), and a comparison that ran and
    disagreed still raises the plain class."""
    arc = _Archive(root, monkeypatch, mod)
    m = mod.load_manifest()
    assert hasattr(mod, "Unbindable"), "the precondition class does not exist"
    assert issubclass(mod.Unbindable, mod.Refused) and issubclass(mod.Unbindable, refusal.Refused)
    # each block catches the BASE and asserts the exact type: `pytest.raises(Unbindable)`
    # lets a plain `Refused` escape, and an escaped exception is not a failed check
    with pytest.raises(mod.Refused) as got:
        mod.load_capture("cut4000", 0.25, 1, m)
    assert type(got.value) is mod.Unbindable, (type(got.value), got.value)
    arc.drop(ABSENT)
    with pytest.raises(mod.Refused) as got:
        mod.load_capture("cut250", 0.125, 1, m)
    assert type(got.value) is mod.Unbindable, (type(got.value), got.value)
    assert "absent from the archive" in str(got.value), str(got.value)
    with pytest.raises(mod.Refused) as got:
        mod.load_capture("cut250", 0.25, 1, m, inject="TAMPERED")
    assert type(got.value) is mod.Refused, (type(got.value), got.value)
    assert "hashes" in str(got.value), str(got.value)


CONTROLS = {"absent": _absent_is_refused, "tampered": _tampered_still_fails,
            "mixed": _measured_disagreement_outranks_a_refusal, "clean": _clean_is_unchanged,
            "raise_site": _raise_site_types_the_precondition}


def test_verify_reports_an_absent_archive_member_as_refused(tmp_path, monkeypatch):
    _absent_is_refused(cap, tmp_path, monkeypatch)


def test_verify_still_fails_a_capture_whose_bytes_hash_to_something_else(tmp_path, monkeypatch):
    _tampered_still_fails(cap, tmp_path, monkeypatch)


def test_verify_a_measured_disagreement_outranks_a_refusal(tmp_path, monkeypatch):
    _measured_disagreement_outranks_a_refusal(cap, tmp_path, monkeypatch)


def test_verify_clean_run_is_unchanged(tmp_path, monkeypatch):
    _clean_is_unchanged(cap, tmp_path, monkeypatch)


def test_load_capture_types_what_could_not_be_compared_apart_from_what_disagreed(
        tmp_path, monkeypatch):
    _raise_site_types_the_precondition(cap, tmp_path, monkeypatch)


_ON_UNBINDABLE = ('        except Unbindable as e:\n'
                  '            refused += 1\n'
                  '            print(f"REFUSED  {e}")\n')
_ON_REFUSED = ('        except Refused as e:\n'
               '            bad += 1\n'
               '            print(f"FAIL     {e}")\n')


def _mutant(sites):
    """`f1_level_capture` with one wrong repair applied, as a module. Each site
    must still exist exactly once: a mutant that no longer builds must not pass
    for a control that caught it (verification-rules 5, condition 2)."""
    source = pathlib.Path(cap.__file__).read_text()
    for old, new in sites:
        assert source.count(old) == 1, f"mutation site gone or ambiguous: {old!r}"
        source = source.replace(old, new)
    mod = types.ModuleType("f1_level_capture_mutant")
    mod.__file__ = cap.__file__
    exec(compile(source, cap.__file__, "exec"), mod.__dict__)
    return mod


@pytest.mark.parametrize("sites, red", [
    pytest.param([('raise Unbindable(f"{key} is not', 'raise Refused(f"{key} is not'),
                  ('raise Unbindable(f"{key}: {meta', 'raise Refused(f"{key}: {meta'),
                  (_ON_UNBINDABLE + _ON_REFUSED, _ON_REFUSED)],
                 ("absent", "mixed", "raise_site"), id="the-tree-before-the-repair"),
    pytest.param([(_ON_UNBINDABLE + _ON_REFUSED, _ON_REFUSED + _ON_UNBINDABLE)],
                 ("absent", "mixed"), id="plain-refused-caught-first"),
    pytest.param([('raise Refused(f"{key} hashes', 'raise Unbindable(f"{key} hashes')],
                 ("tampered", "mixed", "raise_site"), id="every-raise-is-the-precondition"),
    pytest.param([("return FAIL if bad else REFUSED if refused else OK",
                   "return REFUSED if refused else FAIL if bad else OK")],
                 ("mixed",), id="a-refusal-outranks-a-measured-disagreement"),
    pytest.param([("len(m['clips']) - bad - refused} verified", "len(m['clips']) - bad} verified")],
                 ("absent", "mixed"), id="refused-counted-as-verified"),
    pytest.param([("class Unbindable(Refused):", "class Unbindable(Exception):")],
                 ("raise_site",), id="precondition-class-leaves-the-refusal-family"),
    pytest.param([('+ (f", {refused} refused" if refused else ""))', '+ f", {refused} refused")')],
                 ("tampered", "clean"), id="refused-always-on-the-summary-line"),
])
def test_control_each_wrong_repair_reddens_the_control_that_owns_it(sites, red, tmp_path,
                                                                    monkeypatch):
    mod = _mutant(sites)
    for name, control in CONTROLS.items():
        if name in red:
            with pytest.raises(AssertionError):
                control(mod, tmp_path / name, monkeypatch)
        else:
            control(mod, tmp_path / name, monkeypatch)


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


# --- the CLI handler: a cross-module refusal must exit REFUSED (#1624) --------
# `main`'s try reaches two families its handler did not list: `refprofile.
# Refused` (through `surge_vs_frozen` -> `run_case.load_filter_reference`, for
# an absent, short, mis-hashed or silent frozen clip) and `mono_m5a_score.
# Refused` (through `f1.build_voice`). Both are siblings of the four classes
# the handler enumerated, so each escaped as an exit-1 traceback where the
# exit contract owes REFUSED (2) -- a refusal rendered as FAIL. The controls
# drive the real `main` with the level-capture manifest and
# `base_vpu_asserted()` satisfied for real, and point `refprofile.PROFILE_DIR`
# at an empty directory: the committed profile.json still names the clip, so
# only the audio is gone, a host's state before `tools/refprofile_restore.py`.
#
# MEASURED, the seven controls against five handler variants (2026-11-28),
# as failed / passed:
#   the old four-class tuple               4 / 3   absent cache, mono_m5a_score,
#                                                   both --expect-refused params
#   `except refusal.Refused`, alone        6 / 1   WORSE: `main` bound a LOCAL list
#                                                  named `refusal` in its
#                                                  --expect-refused branch, so the
#                                                  clause raised UnboundLocalError
#                                                  for every exception leaving the
#                                                  try. The repair renames that
#                                                  local; do not re-shadow it.
#   `except Exception`                    1 / 6   the ValueError control, "DID NOT
#                                                  RAISE": relabelled REFUSED
#   the tuple with `rp.Refused` appended   1 / 6   the mono_m5a_score control: that
#                                                  repair fixes one sibling, not
#                                                  the family
#   the shipped repair                     0 / 7
FIRST_FROZEN_CLIP = cap.CONDITIONS[next(iter(fls.CASES.values()))][1]


def _known_answer_is_reachable() -> None:
    """REFUSE, rather than report, when the committed inputs the controls
    read are not on this tree: without them `main` refuses for another reason
    before it reaches the boundary under test."""
    try:
        cap.load_manifest()
        clips = rp.load_profile()["clips"]
    except refusal.Refused as e:
        pytest.skip(f"REFUSED: {e}")
    if FIRST_FROZEN_CLIP not in clips:
        pytest.skip(f"REFUSED: {FIRST_FROZEN_CLIP!r} is not in the committed frozen profile")


def test_the_refusal_families_main_reaches_share_only_the_base():
    """The relation a base-class handler rests on: `refprofile.Refused` and
    `mono_m5a_score.Refused` are none of the four classes the handler used to
    enumerate, and all six are `refusal.Refused`."""
    listed = (fls.Refused, cap.Refused, f1.Refused, rc.Refused)
    for klass in listed:
        assert not issubclass(rp.Refused, klass) and not issubclass(klass, rp.Refused)
        assert not issubclass(m5.Refused, klass) and not issubclass(klass, m5.Refused)
    for klass in listed + (rp.Refused, m5.Refused):
        assert issubclass(klass, refusal.Refused), klass


def test_absent_frozen_cache_exits_refused_2_and_names_the_clip(tmp_path, monkeypatch, capsys):
    _known_answer_is_reachable()
    empty = tmp_path / "frozen-cache"
    empty.mkdir()
    monkeypatch.setattr(rp, "PROFILE_DIR", empty)
    report = tmp_path / "report.json"
    code = fls.main(["--json", str(report)])
    out = capsys.readouterr()
    assert code == exit_contract.REFUSED, (code, out.out[-400:])
    refused = [ln for ln in out.out.splitlines() if ln.startswith("REFUSED")]
    assert len(refused) == 1, out.out[-400:]
    assert FIRST_FROZEN_CLIP in refused[0] and "is not in the cache" in refused[0], refused[0]
    assert not report.exists(), "a refused run must not write a report"


def test_a_run_case_refusal_at_the_same_boundary_still_exits_refused_2(tmp_path, monkeypatch,
                                                                     capsys):
    _known_answer_is_reachable()

    def not_projectable(clip_id, inject=""):
        raise rc.Refused(f"the frozen clip {clip_id} could not be projected: <control>")
    monkeypatch.setattr(rc, "load_filter_reference", not_projectable)
    report = tmp_path / "report.json"
    code = fls.main(["--json", str(report)])
    out = capsys.readouterr()
    assert code == exit_contract.REFUSED, (code, out.out[-400:])
    assert "REFUSED  the frozen clip" in out.out and "could not be projected" in out.out, \
        out.out[-400:]
    assert not report.exists(), "a refused run must not write a report"


def test_a_mono_m5a_score_refusal_from_build_voice_exits_refused_2(tmp_path, monkeypatch, capsys):
    """The input that defeats the repair the issue rejected -- appending
    `rp.Refused` to the tuple. `main` also calls `f1.build_voice`, which reaches
    `mono_m5a_score.engine_configuration`: a third sibling, at another line of
    the same try."""
    _known_answer_is_reachable()

    def unsupported_engine(profile_name):
        raise m5.Refused(f"unsupported Mono engine configuration {profile_name!r}")
    monkeypatch.setattr(f1, "build_voice", unsupported_engine)
    report = tmp_path / "report.json"
    code = fls.main(["--json", str(report)])
    out = capsys.readouterr()
    assert code == exit_contract.REFUSED, (code, out.out[-400:])
    assert "REFUSED  unsupported Mono engine configuration 'selected'" in out.out, out.out[-400:]
    assert not report.exists(), "a refused run must not write a report"


def test_an_unrelated_error_at_the_same_boundary_is_an_execution_error(tmp_path, monkeypatch,
                                                                     capsys):
    """The input that defeats the wrong repair (verification-rules rule 8):
    `except Exception` relabels every traceback REFUSED, and this is the
    control that goes red when it is swapped in. A refusal is asserted by the
    exception propagating out of `main`, never by a caught `Exception`."""
    _known_answer_is_reachable()

    def unreadable(clip_id, inject=""):
        raise ValueError("<control> the probe grid is not a number")
    monkeypatch.setattr(rc, "load_filter_reference", unreadable)
    report = tmp_path / "report.json"
    with pytest.raises(ValueError, match="the probe grid is not a number") as err:
        fls.main(["--json", str(report)])
    assert "surge_vs_frozen" in [fr.name for fr in err.traceback], \
        "the control must fire at the frozen-clip boundary"
    out = capsys.readouterr()
    assert "REFUSED" not in out.out, "an execution error must not be relabelled a refusal"
    assert not report.exists(), "an execution error must not write a report"


@pytest.mark.parametrize("reason, code, marker", [
    ("is not in the cache", exit_contract.PASS, "CONTROL OK"),
    ("a reason this run does not refuse for", exit_contract.FAIL, "CONTROL FAILED"),
])
def test_expect_refused_control_mode_sees_the_frozen_cache_refusal(tmp_path, monkeypatch, capsys,
                                                                   reason, code, marker):
    _known_answer_is_reachable()
    empty = tmp_path / "frozen-cache"
    empty.mkdir()
    monkeypatch.setattr(rp, "PROFILE_DIR", empty)
    got = fls.main(["--expect-refused", reason])
    out = capsys.readouterr()
    assert got == code, (got, out.out[-400:])
    assert marker in out.out.splitlines()[-1], out.out[-400:]
