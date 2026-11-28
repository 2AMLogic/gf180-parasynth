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
import copy
import io
import json
import math
import pathlib
import struct
import sys
import types
import zipfile
import zlib

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
import voice_fx as vf                # noqa: E402

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
# `verify()` caught every `Refused` from `load_capture` as `FAIL`, so a capture
# the archive does not hold (no comparison ran) read exactly like one that is
# present and hashes otherwise (a comparison that ran and disagreed). The
# repair is at the raise site, not a string match: the two precondition raises
# are `Unbindable`, a subclass of the module's `Refused`, so every other caller
# (`except refusal.Refused` in tools/probes/f1_level_scaling.py) is unchanged.
#
# RED FIRST, this file against the module before the repair (`ed00ea2c`,
# 2026-11-28): 10 failed, 29 passed.
#   absent member            `FAIL     cut250/a0.1250/take1: ... is absent from the
#                            archive` / `2 verified, 1 failed`, returned 1
#   absent + corrupted       two `FAIL` lines / `1 verified, 2 failed`, no REFUSED
#   the raise site           no `Unbindable`
#   the seven wrong repairs  below: nothing to mutate until the repair exists,
#                            each refused to build (`mutation site gone`)
# The 29 are the 26 pre-existing tests and the three controls that are right
# before and after by design (corrupted -> FAIL, clean, the committed captures).
#
# MEASURED, the controls against the wrong repairs (2026-11-28), RED / green:
#                                      absent tampered mixed clean raise_site
#   the tree before the repair          RED    green    RED   green RED
#   plain Refused caught first          RED    green    RED   green green
#   every raise is the precondition     green  RED      RED   green RED
#   a refusal outranks a disagreement   green  green    RED   green green
#   refused counted as verified         RED    green    RED   green green
#   refused always on the summary line  green  RED      green RED   green
#   Unbindable leaves the Refused family green green   green green RED
# No control is blind to every repair and no repair is caught by none.
#
# WRONG-THEN-RIGHT (1, caught by running): the raise-site control first went
# red on the pre-fix module by LEAKING its plain `Refused` out of
# `pytest.raises(Unbindable)` -- an exception where a failed check belongs.
# It now catches the base class and asserts the exact type.
#
# NOT CLAIMED (each provoked, 2026-11-28): an archive that is not a zip, under a
# manifest that names a clip, escapes `verify()` as `zipfile.BadZipFile` -- not
# a `Refused`; and a manifest with no clips returns 0 (`0 verified, 0 failed`)
# without opening the archive at all. Both predate this repair.
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
            self.clips[key] = {"condition": cond, "take": int(take[4:]), "frames": len(y),
                               "sha256": cap._sha(blobs[key]),
                               "archive_member": cap._sha(blobs[key])[:16] + ".wav"}
        self.manifest, self.archive = root / "manifest.json", root / "audio.zip"
        self.manifest.write_text(
            json.dumps({"schema": "f1-level-captures/1", "clips": self.clips}))
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
    assert len(refused) == 1 and ABSENT in refused[0], lines
    assert "absent from the archive" in refused[0], lines
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
    disagreed raises `Differs` -- named since #1843 so the probe can return 1
    for it without handling a `Refused` class as a failure."""
    arc = _Archive(root, monkeypatch, mod)
    m = mod.load_manifest()
    assert hasattr(mod, "Unbindable"), "the precondition class does not exist"
    assert issubclass(mod.Unbindable, mod.Refused) and issubclass(mod.Unbindable, refusal.Refused)
    assert issubclass(mod.Differs, mod.Refused) and issubclass(mod.Differs, refusal.Refused)
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
    assert type(got.value) is mod.Differs, (type(got.value), got.value)
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


def _rewritten(mod, sites):
    """`mod` with one wrong repair applied, as a module of its own: its text
    with each `(old, new)` replaced. Each site must still exist exactly once:
    a mutant that no longer builds must not pass for a control that caught it
    (verification-rules 5, condition 2)."""
    source = pathlib.Path(mod.__file__).read_text()
    for old, new in sites:
        assert source.count(old) == 1, f"mutation site gone or ambiguous: {old!r}"
        source = source.replace(old, new)
    out = types.ModuleType(mod.__name__ + "_mutant")
    out.__file__ = mod.__file__
    exec(compile(source, mod.__file__, "exec"), out.__dict__)
    return out


def _mutant(sites):
    """`f1_level_capture` with one wrong repair applied, as a module."""
    return _rewritten(cap, sites)


@pytest.mark.parametrize("sites, red", [
    pytest.param([('raise Unbindable(f"{key} is not', 'raise Refused(f"{key} is not'),
                  ('raise Unbindable(f"{key}: {meta', 'raise Refused(f"{key}: {meta'),
                  (_ON_UNBINDABLE + _ON_REFUSED, _ON_REFUSED)],
                 ("absent", "mixed", "raise_site"), id="the-tree-before-the-repair"),
    pytest.param([(_ON_UNBINDABLE + _ON_REFUSED, _ON_REFUSED + _ON_UNBINDABLE)],
                 ("absent", "mixed"), id="plain-refused-caught-first"),
    pytest.param([('raise Differs(f"{key} hashes', 'raise Unbindable(f"{key} hashes')],
                 ("tampered", "mixed", "raise_site"), id="every-raise-is-the-precondition"),
    pytest.param([("return FAIL if bad else REFUSED if refused else OK",
                   "return REFUSED if refused else FAIL if bad else OK")],
                 ("mixed",), id="a-refusal-outranks-a-measured-disagreement"),
    pytest.param([("len(m['clips']) - bad - refused} verified",
                   "len(m['clips']) - bad} verified")],
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


# --- issue #1843, sites 1 and 2: the archive and the manifest are preconditions
# Two inputs left `verify()` without its three outcomes, provoked in-process on
# the tree after #1822's repair (`85e2e835`, 2026-11-28, Python 3.12.3):
#   an archive that is not a zip   `zipfile.BadZipFile` escaped `verify()` AND
#                                  `load_capture`: the open sat outside the
#                                  `try`. An empty or a truncated archive too,
#                                  and -- with only the open guarded -- a
#                                  member the archive holds but cannot deliver
#                                  (a bad CRC: `BadZipFile`; a broken deflate
#                                  stream: `zlib.error`)
#   a manifest naming no clip      `0 verified, 0 failed`, returned 0; the
#                                  archive was never opened
# The repair is at the raise site, as #1822's was: a container the reader can
# take no bytes from is `Unbindable` (nothing was compared), and `load_manifest`
# refuses a manifest that names no clip. Each control first asks RAW `zipfile`
# for the exception its crafted bytes raise: an input that does not produce the
# container error it claims to is not evidence about the guard.
#
# MEASURED, the ten controls against four wrong repairs (2026-11-28), RED / green:
#                                   not-a-zip empty trunc dir  crc  stream | no-clip x4
#   the tree before the repair     RED      RED   RED   RED  RED  RED    | RED
#   only the open is guarded       green    green green green RED  RED    | green
#   container failure is a disagreement RED  RED   RED   RED  RED  RED    | green
#   names-no-clip check absent     green    green green green green green | RED
# The second row is the repair as the issue's guidance states it; the CRC and
# stream controls are what it leaves open. The test holds every cell.
#
# PINNED (verification-rules 5): `tools/vacuous_guard_audit.py` BOUNDARIES
# names `load_manifest`'s population assertion, so its `--check` (in `make
# verify`) goes red if the guard is refactored away. That audit's own triage
# never flagged `verify` -- on either tree -- because its loop only counts
# and refuses nowhere: a blind spot of the audit, not evidence of absence.
#
# PROVOKED AND STATED, deliberately not caught (verification-rules 8):
# `NotImplementedError` (a compression type zipfile does not implement, or an
# extract version it does not know: both provoked) and `RuntimeError` (an
# encrypted member: provoked) are left as execution errors -- catching either
# class would relabel an abstract-method or invariant violation as a refusal,
# and `capture()` writes neither. `EOFError`, the short read of
# `ZipExtFile._read2`, was not provoked at all: a member whose declared
# compressed size overruns its file is refused first, at `ZipFile.open`, as
# `BadZipFile: Overlapped entries`.
#
# WRONG-THEN-RIGHT (1, caught by running this file against the repair): the
# first names-no-clip message called `MANIFEST.relative_to(ROOT)`, which itself
# raises `ValueError` for a manifest outside the tree -- a refusal that could
# not be raised. Its sibling, the no-captures message, had the same fault; both
# now print the path as it is when it is not under the tree.
def _raw_read(arc):
    """What `load_capture` did before the guard: open the archive, read the
    member, no handler in between."""
    with zipfile.ZipFile(arc.archive) as z:
        return z.read(arc.clips[KEPT]["archive_member"])


def _zipped(arc, method):
    """`arc`'s three members in one archive stored by `method`."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", method) as z:
        for key, b in arc.blobs.items():
            z.writestr(arc.clips[key]["archive_member"], b)
    return bytearray(buf.getvalue())


def _not_a_zip(arc):
    arc.archive.write_bytes(b"this is not a zip")


def _empty_file(arc):
    arc.archive.write_bytes(b"")


def _truncated(arc):
    raw = arc.archive.read_bytes()
    arc.archive.write_bytes(raw[: len(raw) // 2])


def _a_directory(arc):
    arc.archive.unlink()
    arc.archive.mkdir()


def _bad_crc(arc):
    """Present and decompresses; no longer what the zip's own CRC says."""
    raw = _zipped(arc, zipfile.ZIP_STORED)
    b = arc.blobs[KEPT]
    i = raw.index(b)
    raw[i + len(b) - 1] ^= 0x01
    arc.archive.write_bytes(raw)


def _broken_stream(arc):
    """The member's deflate stream opens on block type 11 (byte 0x07): zlib
    refuses before any CRC is compared."""
    raw = _zipped(arc, zipfile.ZIP_DEFLATED)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        off = z.getinfo(arc.clips[KEPT]["archive_member"]).header_offset
    name_len, extra_len = struct.unpack_from("<HH", raw, off + 26)
    raw[off + 30 + name_len + extra_len] = 0x07
    arc.archive.write_bytes(raw)


#: (id, the corruption, what raw `zipfile` raises on it, clips still comparable)
_CORRUPTIONS = [
    ("not-a-zip", _not_a_zip, zipfile.BadZipFile, 0),
    ("empty", _empty_file, zipfile.BadZipFile, 0),
    ("truncated", _truncated, zipfile.BadZipFile, 0),
    ("directory", _a_directory, OSError, 0),
    ("bad-crc", _bad_crc, zipfile.BadZipFile, 2),
    ("broken-stream", _broken_stream, zlib.error, 2),
]


def _ran(fn):
    """`fn()`, as (value, None), or (None, exception) when it escaped. An
    escape is the defect under test; the control turns it into ITS OWN failed
    assertion, so a red control is always an assertion and never a traceback
    that merely happens to be non-green (verification-rules 5, condition 3)."""
    try:
        return fn(), None
    except Exception as escaped:
        return None, escaped


def _verify_now(mod):
    """`mod.verify()` as (code, lines); an escape is a failed assertion."""
    out = io.StringIO()

    def call():
        with contextlib.redirect_stdout(out):
            return mod.verify()

    _code, escaped = _ran(call)
    assert escaped is None, f"verify() escaped: {escaped!r}"
    return _code, out.getvalue().splitlines()


def _archive_cannot_be_read_is_refused(mod, root, monkeypatch, corrupt, raises, kept):
    """Both the open and the read are preconditions: `load_capture` raises
    `Unbindable`, never the container's own exception, and `verify()` counts
    it REFUSED -- never FAIL, never a pass."""
    arc = _Archive(root, monkeypatch, mod)
    corrupt(arc)
    with pytest.raises(raises):                   # the input is what it claims to be
        _raw_read(arc)
    m = mod.load_manifest()
    _value, e = _ran(lambda: mod.load_capture("cut250", 0.25, 1, m))
    assert e is not None, "load_capture returned: the member was read"
    assert isinstance(e, refusal.Refused), f"load_capture escaped: {e!r}"
    assert type(e) is mod.Unbindable, (type(e), e)
    needle = "cannot be opened" if kept == 0 else "could not deliver"
    assert needle in str(e), str(e)
    code, lines = _verify_now(mod)
    assert code == exit_contract.REFUSED, (code, lines)
    refused = [ln for ln in lines if ln.startswith("REFUSED")]
    assert len(refused) == 3 - kept, lines
    assert all(needle in ln for ln in refused), lines
    assert not any(ln.startswith("FAIL") for ln in lines), lines
    assert [ln.split()[1] for ln in lines if ln.startswith("ok ")] == (
        [] if kept == 0 else [ABSENT, TAMPERED]), lines
    assert lines[-1] == f"{kept} verified, 0 failed, {3 - kept} refused", lines


_NO_CLIPS = [pytest.param({}, id="clips-absent"),
             pytest.param({"clips": {}}, id="clips-empty"),
             pytest.param({"clips": None}, id="clips-null"),
             pytest.param({"clips": []}, id="clips-list")]


def _nothing_to_compare_is_refused(mod, root, monkeypatch, manifest):
    """A manifest that names no clip is refused at the raise site, before the
    archive is touched. The archive here is not a zip: a `verify()` that
    reached it would say so -- or, unrepaired, escape."""
    root.mkdir(parents=True, exist_ok=True)
    man, arc = root / "manifest.json", root / "audio.zip"
    man.write_text(json.dumps({"schema": "f1-level-captures/1", **manifest}))
    arc.write_bytes(b"this is not a zip")
    monkeypatch.setattr(mod, "MANIFEST", man)
    monkeypatch.setattr(mod, "ARCHIVE", arc)
    _m, e = _ran(mod.load_manifest)
    assert isinstance(e, refusal.Refused), f"load_manifest returned {_m!r}, did not refuse"
    assert type(e) is mod.Refused, (type(e), e)     # a precondition of the manifest, not of one clip
    assert "names no clip" in str(e) and "nothing was compared" in str(e), str(e)
    code, lines = _verify_now(mod)
    assert code == exit_contract.REFUSED, (code, lines)
    assert len(lines) == 1 and lines[0].startswith("REFUSED"), lines   # no summary line:
    assert "names no clip" in lines[0], lines                     # nothing was counted
    out = io.StringIO()

    def cli():
        with contextlib.redirect_stdout(out):
            return mod.main([])

    code, escaped = _ran(cli)
    assert escaped is None, f"main escaped: {escaped!r}"
    assert code == exit_contract.REFUSED, (code, out.getvalue().splitlines()[-3:])


CONTROLS_1843 = {
    **{name: (lambda mod, root, mp, c=c, r=r, k=k: _archive_cannot_be_read_is_refused(
        mod, root, mp, c, r, k)) for name, c, r, k in _CORRUPTIONS},
    **{p.id: (lambda mod, root, mp, m=p.values[0]: _nothing_to_compare_is_refused(
        mod, root, mp, m)) for p in _NO_CLIPS},
}


@pytest.mark.parametrize("corrupt, raises, kept", [(c, r, k) for _n, c, r, k in _CORRUPTIONS],
                         ids=[n for n, _c, _r, _k in _CORRUPTIONS])
def test_an_archive_the_reader_cannot_take_bytes_from_is_refused(tmp_path, monkeypatch,
                                                                 corrupt, raises, kept):
    _archive_cannot_be_read_is_refused(cap, tmp_path, monkeypatch, corrupt, raises, kept)


@pytest.mark.parametrize("manifest", _NO_CLIPS)
def test_a_manifest_that_names_no_clip_is_refused_not_a_pass(tmp_path, monkeypatch, manifest):
    _nothing_to_compare_is_refused(cap, tmp_path, monkeypatch, manifest)


_OPEN_GUARDED = ('    try:\n'
                 '        z = zipfile.ZipFile(ARCHIVE)\n'
                 '    except ARCHIVE_ERRORS as e:\n'
                 '        raise Unbindable(f"{ARCHIVE.name} cannot be opened as the capture archive: {e}")\n'  # noqa: E501
                 '    with z:\n')
_OPEN_BARE = '    with zipfile.ZipFile(ARCHIVE) as z:\n'
_DELIVER_GUARD = ('        except ARCHIVE_ERRORS as e:\n'
                  '            raise Unbindable(f"{key}: the archive could not deliver '
                  '{meta[\'archive_member\']}: {e}")\n')
_NO_CLIP_GUARD = ('    clips = m.get("clips")\n'
                  '    if not isinstance(clips, dict) or not clips:\n'
                  '        raise Refused(f"the manifest at {_shown(MANIFEST)} names no clip: "\n'
                  '                      f"nothing was compared")\n')


@pytest.mark.parametrize("sites, red", [
    pytest.param([(_OPEN_GUARDED, _OPEN_BARE), (_DELIVER_GUARD, ""), (_NO_CLIP_GUARD, "")],
                 list(CONTROLS_1843), id="the-tree-before-the-repair"),
    pytest.param([(_DELIVER_GUARD, "")], ["bad-crc", "broken-stream"],
                 id="only-the-open-is-guarded"),
    pytest.param([('raise Unbindable(f"{ARCHIVE.name} cannot be opened',
                   'raise Differs(f"{ARCHIVE.name} cannot be opened'),
                  ('raise Unbindable(f"{key}: the archive could not deliver',
                   'raise Differs(f"{key}: the archive could not deliver')],
                 [n for n, _c, _r, _k in _CORRUPTIONS], id="container-failure-is-a-disagreement"),
    pytest.param([(_NO_CLIP_GUARD, "")], [p.id for p in _NO_CLIPS],
                 id="names-no-clip-check-absent"),
])
def test_control_each_wrong_archive_repair_reddens_the_control_that_owns_it(sites, red,
                                                                            tmp_path, monkeypatch):
    mod = _mutant(sites)
    for name, control in CONTROLS_1843.items():
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


# --- issue #1843, site 3: one handler rendered every outcome REFUSED -----------
# `main` caught `refusal.Refused` and returned 2 for all of it, so a capture
# that is PRESENT and hashes to something else -- a comparison that ran and
# disagreed -- read exactly like a missing input, with no report to say so.
# Provoked in-process on `85e2e835` (2026-11-28), the committed archive with
# one member's last byte flipped:
#   fls.main(["--json", report])  ->  "REFUSED  cut250/a0.2500/take1 hashes
#                                     1fcfb038741d6963, the manifest says
#                                     bd2279368babf5b6", exit 2, no report
# The inverse of #1822. The repair types the raise sites: a check that ran and
# disagreed raises `Differs` (the capture reader's hash and sr/frames
# comparisons, the probe's own takes-differ) and `main` returns 1 for it, "a
# check failed" in the module's exit contract; every precondition raises what
# it did and still returns 2. The clause that returns 1 names no `Refused`
# class on purpose: `tools/refusal_exit_census.py --check` (in `make verify`)
# reads a handler naming one as a refusal site and turns red on a status
# outside {2, 3, 126}, so "catch the plain `Refused` from `load_capture`" is
# not buildable here -- the disagreement needs a name of its own. MEASURED
# (2026-11-28): with that literal clause in place the census exits 1 on
# `['tools/probes/f1_level_scaling.py', 'main', 1, ...]`; as shipped it sees
# the probe's one refusal site, at exit 2.
#
# RED FIRST, this file against both modules before the repair (`85e2e835`):
# the hash-mismatch and takes-differ controls read exit 2 where 1 is owed; the
# unopenable archive left `main` as `BadZipFile` and the empty manifest as
# `KeyError('cut250/a0.2500/take1')`; no `Differs` existed for the raise-site
# control; the mutant rows had nothing to mutate. Green before and after by
# design: the absent member and the wrong ladder path were already REFUSED.
#
# MEASURED, the six `main` controls against eight wrong repairs (2026-11-28),
# RED / green, each cell driven through the real `main`:
#                                            hash takes absent unopen no-clip ladder
#   the tree before the repair              RED  RED   green  green  green   green
#   disagreement clause behind the base    RED  RED   green  green  green   green
#   takes-differ typed a precondition       green RED   green  green  green   green
#   probe refusals rendered failures        green green green  green  green   RED
#   a disagreement returns refused          RED  RED   green  green  green   green
#   a precondition returns failure          green green RED    RED    RED     RED
#   [capture] hash typed a precondition     RED  green green  green  green   green
#   [capture] no-clip typed a disagreement  green green green  green  RED     green
# The last two are written into the capture reader, which `main` then reads.
# The test holds every cell.
COMMITTED = {"MANIFEST": cap.MANIFEST, "ARCHIVE": cap.ARCHIVE}     # at import, before any redirect
PROBE_KEY, PROBE_TAKE2 = "cut250/a0.2500/take1", "cut250/a0.2500/take2"


@pytest.fixture(scope="module")
def committed(tmp_path_factory):
    """The committed captures as (manifest, a copy of the archive with
    PROBE_KEY's member last byte flipped), built once: the controls drive the
    real `main`, which builds two voices before it reads a capture."""
    try:
        m = cap.load_manifest()
    except refusal.Refused as e:
        pytest.skip(f"REFUSED: {e}")
    tampered = tmp_path_factory.mktemp("f1-level") / "audio.zip"
    member = m["clips"][PROBE_KEY]["archive_member"]
    with zipfile.ZipFile(COMMITTED["ARCHIVE"]) as zi, \
            zipfile.ZipFile(tampered, "w", zipfile.ZIP_DEFLATED) as zo:
        for info in zi.infolist():
            data = zi.read(info)
            if info.filename == member:
                data = data[:-1] + bytes([data[-1] ^ 0x01])
            zo.writestr(info, data)
    return m, tampered


def _probe(mod, capmod, work, monkeypatch, redirect):
    """`mod.main --json <work>/report.json` with `mod` reading `capmod`, whose
    MANIFEST / ARCHIVE are then exactly what `redirect` names. A control names
    both: its own redirect must not leak into the next control's run."""
    monkeypatch.setattr(mod, "cap", capmod, raising=False)
    for name, path in {**COMMITTED, **redirect}.items():
        monkeypatch.setattr(capmod, name, path)
    work.mkdir(parents=True, exist_ok=True)
    report = work / "report.json"
    out = io.StringIO()

    def call():
        with contextlib.redirect_stdout(out):
            return mod.main(["--json", str(report)])

    code, escaped = _ran(call)
    assert escaped is None, f"main escaped: {escaped!r}"
    return code, out.getvalue().splitlines(), report


def _verdict_line(code, lines, prefix, needle):
    assert code == (exit_contract.FAIL if prefix == "FAIL" else exit_contract.REFUSED), \
        (code, lines[-3:])
    verdict = [ln for ln in lines if ln.startswith(("FAIL", "REFUSED"))]
    assert len(verdict) == 1 and verdict[0].startswith(prefix) and needle in verdict[0], \
        (lines[-3:], needle)


def _manifest_file(work, m):
    work.mkdir(parents=True, exist_ok=True)
    p = work / "manifest.json"
    p.write_text(json.dumps(m))
    return p


def _probe_hash_mismatch(mod, capmod, work, mp, _m, tampered):
    """The committed captures, one member's last byte flipped: the hash was
    computed and is not what the manifest says."""
    code, lines, report = _probe(mod, capmod, work, mp, {"ARCHIVE": tampered})
    _verdict_line(code, lines, "FAIL", f"{PROBE_KEY} hashes")
    assert not report.exists(), "a failed check is not a report"


def _probe_takes_differ(mod, capmod, work, mp, m, _t):
    m = copy.deepcopy(m)
    h = m["clips"][PROBE_TAKE2]["sha256"]
    m["clips"][PROBE_TAKE2]["sha256"] = ("0" if h[0] != "0" else "1") + h[1:]
    code, lines, report = _probe(mod, capmod, work, mp, {"MANIFEST": _manifest_file(work, m)})
    _verdict_line(code, lines, "FAIL", "Surge takes differ for cut250 at 0.25")
    assert not report.exists(), "a failed check is not a report"


def _probe_absent_member(mod, capmod, work, mp, m, _t):
    m = copy.deepcopy(m)
    m["clips"][PROBE_KEY]["archive_member"] += ".absent"
    code, lines, report = _probe(mod, capmod, work, mp, {"MANIFEST": _manifest_file(work, m)})
    _verdict_line(code, lines, "REFUSED", "is absent from the archive")
    assert not report.exists(), "a refused run must not write a report"


def _probe_unopenable_archive(mod, capmod, work, mp, _m, _t):
    work.mkdir(parents=True, exist_ok=True)
    junk = work / "audio.zip"
    junk.write_bytes(b"this is not a zip")
    code, lines, report = _probe(mod, capmod, work, mp, {"ARCHIVE": junk})
    _verdict_line(code, lines, "REFUSED", "cannot be opened as the capture archive")
    assert not report.exists(), "a refused run must not write a report"


def _probe_names_no_clip(mod, capmod, work, mp, m, _t):
    m = copy.deepcopy(m)
    m["clips"] = {}
    code, lines, report = _probe(mod, capmod, work, mp, {"MANIFEST": _manifest_file(work, m)})
    _verdict_line(code, lines, "REFUSED", "names no clip")
    assert not report.exists(), "a refused run must not write a report"


def _probe_wrong_ladder_path(mod, capmod, work, mp, _m, _t):
    """The precondition at `main`'s own raise: the voice built is not the
    selected path. `check_identity` is answered for it and restored; nothing
    else is touched."""
    real = f1.check_identity
    mp.setattr(f1, "check_identity", lambda voice: {
        "ladder_class": "<control>", "factor": None, "causal": None,
        "problems": ["<control> the ladder is not the selected path"]})
    try:
        code, lines, report = _probe(mod, capmod, work, mp, {})
    finally:
        mp.setattr(f1, "check_identity", real)
    _verdict_line(code, lines, "REFUSED", "wrong ladder path")
    assert not report.exists(), "a refused run must not write a report"


PROBE_CONTROLS = {"hash_mismatch": _probe_hash_mismatch, "takes_differ": _probe_takes_differ,
                  "absent_member": _probe_absent_member,
                  "unopenable_archive": _probe_unopenable_archive,
                  "names_no_clip": _probe_names_no_clip, "wrong_ladder_path": _probe_wrong_ladder_path}


@pytest.mark.parametrize("name", list(PROBE_CONTROLS))
def test_the_probe_renders_each_outcome_through_the_real_main(name, tmp_path, monkeypatch,
                                                              committed):
    PROBE_CONTROLS[name](fls, cap, tmp_path / name, monkeypatch, *committed)


def _probe_raise_sites_are_typed_by_what_ran(mod, capmod, root, mp):
    """Every `raise` the probe's handler reaches, as the exact class its site
    gives it: a precondition raises its module's plain `Refused` (`Unbindable`
    for one capture), a comparison that ran and disagreed `Differs`. Each
    block asserts the exact type because `pytest.raises(Sub)` lets a parent
    class escape, and an escaped exception is not a failed check."""
    def raised(fn):
        _v, e = _ran(fn)
        assert e is not None, "did not raise"
        return e

    mp.setattr(mod, "cap", capmod, raising=False)
    arc = _Archive(root, mp, capmod)
    m = capmod.load_manifest()
    # the capture reader: the preconditions of one clip, then its comparisons
    e = raised(lambda: capmod.load_capture("cut4000", 0.25, 1, m))
    assert type(e) is capmod.Unbindable and "is not a capture in the manifest" in str(e), str(e)
    arc.drop(ABSENT)
    e = raised(lambda: capmod.load_capture("cut250", 0.125, 1, m))
    assert type(e) is capmod.Unbindable and "is absent from the archive" in str(e), str(e)
    e = raised(lambda: capmod.load_capture("cut250", 0.25, 1, m, inject="TAMPERED"))
    assert type(e) is capmod.Differs and "hashes" in str(e), str(e)
    arc.clips[KEPT]["frames"] += 1          # present, hashes right, not what the manifest says
    arc.manifest.write_text(json.dumps({"schema": "f1-level-captures/1", "clips": arc.clips}))
    e = raised(lambda: capmod.load_capture("cut250", 0.25, 1))
    assert type(e) is capmod.Differs and "frames, manifest says" in str(e), str(e)
    arc.archive.write_bytes(b"this is not a zip")
    e = raised(lambda: capmod.load_capture("cut250", 0.25, 1))
    assert type(e) is capmod.Unbindable and "cannot be opened" in str(e), str(e)
    # the manifest: preconditions of every clip at once
    empty = root / "empty.json"
    empty.write_text(json.dumps({"schema": "f1-level-captures/1", "clips": {}}))
    mp.setattr(capmod, "MANIFEST", empty)
    e = raised(capmod.load_manifest)
    assert type(e) is capmod.Refused and "names no clip" in str(e), str(e)
    mp.setattr(capmod, "MANIFEST", root / "absent.json")
    e = raised(capmod.load_manifest)
    assert type(e) is capmod.Refused and "no level captures" in str(e), str(e)
    # the probe's own raises, in `main` and in the pool
    cfg = vf.LADDER_CFG
    mp.setattr(vf, "LADDER_CFG", {**cfg, "volts_per_unit": 0.1})
    e = raised(mod.base_vpu_asserted)
    assert type(e) is mod.Refused and "now sets volts_per_unit" in str(e), str(e)
    mp.setattr(vf, "LADDER_CFG", cfg)
    mp.setattr(mod, "BASE_VPU", 0.14)
    e = raised(mod.base_vpu_asserted)
    assert type(e) is mod.Refused and "not 0.14" in str(e), str(e)
    e = raised(lambda: mod.assert_registers("half", 0.5, {(1, 2)}, (3, 4), (5, 6)))
    assert type(e) is mod.Refused and "not the candidate's predicted" in str(e), str(e)
    e = raised(lambda: mod.assert_registers("half", 0.5, {(3, 4)}, (3, 4), (3, 4)))
    assert type(e) is mod.Refused and "equal the baseline" in str(e), str(e)
    mp.setattr(mod.am, "tone_amplitude",
               lambda y, f: types.SimpleNamespace(ok=False, reason="<control> not stationary"))
    e = raised(lambda: mod.project(np.zeros(480), [(0, 480, 1000.0)], 8000.0))
    assert type(e) is mod.Refused and "projection refused" in str(e), str(e)
    k1, k2 = cap.clip_key("cut250", 0.25, 1), cap.clip_key("cut250", 0.25, 2)
    e = raised(lambda: mod.surge_side({"clips": {k1: {"sha256": "a"}, k2: {"sha256": "b"}}}))
    assert type(e) is mod.Differs and "Surge takes differ" in str(e), str(e)
    mp.setattr(capmod, "load_manifest", lambda: {
        "clips": {cap.clip_key(mod.OPEN, 0.25, 1): {"freqs_hz": [200.0, 1000.0]}}})
    mp.setattr(f1, "build_voice", lambda name: (None, object()))
    mp.setattr(f1, "check_identity", lambda voice: {"problems": ["<control>"]})
    e = raised(lambda: mod.run_job(("baseline", 1.0, 0.25, "")))
    assert type(e) is mod.Refused and "wrong ladder path" in str(e), str(e)


def test_every_raise_the_probe_handler_reaches_is_typed_by_what_ran(tmp_path, monkeypatch):
    _probe_raise_sites_are_typed_by_what_ran(fls, cap, tmp_path, monkeypatch)


_TAKES_RAISE = 'raise Differs(f"Surge takes differ'
_DIFFERS_CLAUSE = ('    except (cap.Differs, Differs) as e:\n'
                   '        print(f"FAIL     {e}")\n'
                   '        return 1\n')
_BASE_CLAUSE = ('    except refusal.Refused as e:\n'
                '        print(f"REFUSED  {e}")\n'
                '        return 2\n')

#: (the module the wrong repair is written into, its text substitutions, the
#: controls it must redden). `main` is driven for real, ~0.5 s a run, so each
#: row states its red set and the test holds it to exactly that: a control the
#: row leaves green must stay green, or the row is not the repair it claims.
_PROBE_MUTANTS = [
    pytest.param("probe", [(_TAKES_RAISE, 'raise Refused(f"Surge takes differ'),
                           (_DIFFERS_CLAUSE + _BASE_CLAUSE, _BASE_CLAUSE)],
                 ["hash_mismatch", "takes_differ"], id="the-tree-before-the-repair"),
    pytest.param("probe", [(_DIFFERS_CLAUSE + _BASE_CLAUSE, _BASE_CLAUSE + _DIFFERS_CLAUSE)],
                 ["hash_mismatch", "takes_differ"], id="disagreement-clause-behind-the-base"),
    pytest.param("probe", [(_TAKES_RAISE, 'raise Refused(f"Surge takes differ')],
                 ["takes_differ"], id="takes-differ-typed-a-precondition"),
    pytest.param("probe", [(_DIFFERS_CLAUSE,
                           _DIFFERS_CLAUSE.replace("cap.Differs, Differs", "cap.Differs, Refused"))],
                 ["wrong_ladder_path"], id="probe-refusals-rendered-failures"),
    pytest.param("probe", [(_DIFFERS_CLAUSE, _DIFFERS_CLAUSE.replace("return 1", "return 2"))],
                 ["hash_mismatch", "takes_differ"], id="a-disagreement-returns-refused"),
    pytest.param("probe", [(_BASE_CLAUSE, _BASE_CLAUSE.replace("return 2", "return 1"))],
                 ["absent_member", "unopenable_archive", "names_no_clip", "wrong_ladder_path"],
                 id="a-precondition-returns-failure"),
    pytest.param("capture", [('raise Differs(f"{key} hashes', 'raise Refused(f"{key} hashes')],
                 ["hash_mismatch"], id="hash-disagreement-typed-a-precondition"),
    pytest.param("capture", [('raise Refused(f"the manifest at', 'raise Differs(f"the manifest at')],
                 ["names_no_clip"], id="names-no-clip-typed-a-disagreement"),
]


@pytest.mark.parametrize("target, sites, red", _PROBE_MUTANTS)
def test_control_each_wrong_probe_repair_reddens_the_control_that_owns_it(
        target, sites, red, tmp_path, monkeypatch, committed):
    mod, capmod = (_rewritten(fls, sites), cap) if target == "probe" else (fls, _mutant(sites))
    assert red and set(red) <= set(PROBE_CONTROLS), red
    for name, control in PROBE_CONTROLS.items():
        args = (mod, capmod, tmp_path / name, monkeypatch, *committed)
        if name in red:
            with pytest.raises(AssertionError):
                control(*args)
        else:
            control(*args)


def test_no_probe_control_is_blind_to_every_wrong_repair():
    """Rule 4: a control red under no mutant proves nothing, and a mutant that
    reddens nothing is not a repair anyone could make."""
    seen = {name for p in _PROBE_MUTANTS for name in p.values[2]}
    assert seen == set(PROBE_CONTROLS), set(PROBE_CONTROLS) - seen
