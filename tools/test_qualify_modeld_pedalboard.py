#!/usr/bin/env python3
"""`tools/qualify_modeld_pedalboard.py`'s three outcomes, and the host-scoped
verdict table it writes into.

    python3 -m pytest tools/test_qualify_modeld_pedalboard.py -q

The tool is the thing an operator runs on a machine that HAS the Model D bundle
and `pedalboard`. Those cases are exercised here over the same fake host
`model/test_modeld_pedalboard_rig.py` uses, so the shipping tool -- its exit
codes, its record, its environment tuple -- runs without either.

THE CASE THIS FILE WAS WRITTEN FOR
----------------------------------
An earlier draft printed "issue #122's re-specification question REOPENS" on
every non-zero exit, including a plain `no pedalboard on this machine`. That is
a conclusion about a plugin the tool never loaded -- the
tool-that-answers-when-it-cannot defect, in the tool built to prevent it. The
distinction is asserted below in both directions.
"""
from __future__ import annotations

import json
import pathlib
import sys
import types

import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "audition"))

import qualify_modeld_pedalboard as qmp                             # noqa: E402
import reference_rigs as rr                                         # noqa: E402
import refprofile as rp                                             # noqa: E402
import rig_qualification as rq                                      # noqa: E402

sys.path.insert(0, str(ROOT / "model"))
import test_modeld_pedalboard_rig as fake                           # noqa: E402


@pytest.fixture
def bundle(tmp_path):
    """A Model D bundle with a binary in it, so `plugin_identity` can hash
    something -- the environment tuple refuses a plugin with no binary hash."""
    b = tmp_path / "Model D.vst3"
    (b / "Contents" / "MacOS").mkdir(parents=True)
    (b / "Contents" / "MacOS" / "Model D").write_bytes(b"not a real binary")
    (b / "Contents" / "Info.plist").write_bytes(
        b'<?xml version="1.0"?><!DOCTYPE plist><plist version="1.0"><dict>'
        b'<key>CFBundleShortVersionString</key><string>1.2.3</string>'
        b'<key>CFBundleIdentifier</key><string>com.moog.modeld</string>'
        b'</dict></plist>')
    return b


def with_host(monkeypatch, bundle, **kw):
    """Install a fake `pedalboard` (with a version, so the tuple can record it)
    and point the shipping rig class at `bundle`."""
    plug = fake._FakeModelD(**kw)
    mod = types.ModuleType("pedalboard")
    mod.__version__ = "0.9.25"
    mod.load_plugin = lambda path, **_kw: plug
    monkeypatch.setitem(sys.modules, "pedalboard", mod)
    monkeypatch.setattr(rr, "PATH_MODELD", str(bundle))
    monkeypatch.setattr(rr.ModelDPedalboardRig, "path", str(bundle))
    return plug


# ===========================================================================
# the three outcomes
# ===========================================================================
def test_no_pedalboard_is_REFUSED_and_says_nothing_about_issue_122(
        monkeypatch, capsys, tmp_path):
    """Exit 2 on a host without the plugin host. **And the #122 note must NOT
    be printed**: no measurement was attempted, so nothing follows about the
    plugin. This is the whole reason the tool has two different messages."""
    real = __import__

    def no_pedalboard(name, *a, **kw):
        if name == "pedalboard":
            raise ImportError("No module named 'pedalboard'")
        return real(name, *a, **kw)
    monkeypatch.setattr("builtins.__import__", no_pedalboard)
    out = tmp_path / "rec.json"
    code = qmp.main(["--json", str(out)])
    text = capsys.readouterr().out
    assert code == rp.REFUSED
    assert "REFUSED" in text and "no pedalboard on this machine" in text
    assert "REOPENS" not in text, "a host with no plugin measured nothing"
    assert "stated NO-VERDICT" in text
    rec = json.loads(out.read_text())
    assert rec["outcome"] == "REFUSED"
    assert "qualification" not in rec
    assert rec["rig"] == "modeld-pedalboard" and rec["host"] == "pedalboard"


def test_a_missing_bundle_is_REFUSED_even_with_pedalboard_installed(
        monkeypatch, capsys, tmp_path):
    mod = types.ModuleType("pedalboard")
    mod.__version__ = "0.9.25"
    mod.load_plugin = lambda *a, **kw: pytest.fail("must not be reached")
    monkeypatch.setitem(sys.modules, "pedalboard", mod)
    monkeypatch.setattr(rr, "PATH_MODELD", str(tmp_path / "absent.vst3"))
    assert qmp.main([]) == rp.REFUSED
    text = capsys.readouterr().out
    assert "not installed at" in text and "REOPENS" not in text


def test_a_bundle_that_exists_but_does_not_load_is_REFUSED_not_a_traceback(
        monkeypatch, capsys, bundle, tmp_path):
    """The third refusal shape, and the one that was NOT handled until
    2026-09-30: a `.vst3` **directory** that exists, so the existence check
    passes, containing nothing a host can load.

    This is not hypothetical. A bundle in exactly this state was found on a
    Linux dispatch worker at `/tmp/mdb/Model D.vst3` -- one byte, macOS layout,
    on Linux -- and real `pedalboard` 0.9.25 answers it with
    `ImportError: ... unsupported plugin format or scan failure`. That escaped
    the rig constructor uncaught, so the tool exited with a **traceback** rather
    than one of its three documented outcomes, and the word `ImportError` in it
    reads like the host being missing.

    Exit 2, and the #122 note must NOT be printed: a bundle that never loaded
    has concluded nothing about Model D."""
    mod = types.ModuleType("pedalboard")
    mod.__version__ = "0.9.25"

    def wont_load(path, **_kw):
        raise ImportError(
            "Failed to load plugin as VST3Plugin. Errors were:\n\tVST3Plugin: "
            f"Unable to scan plugin {path}: unsupported plugin format or scan "
            "failure.")
    mod.load_plugin = wont_load
    monkeypatch.setitem(sys.modules, "pedalboard", mod)
    monkeypatch.setattr(rr, "PATH_MODELD", str(bundle))
    monkeypatch.setattr(rr.ModelDPedalboardRig, "path", str(bundle))

    out = tmp_path / "rec.json"
    code = qmp.main(["--json", str(out)])
    text = capsys.readouterr().out
    assert code == rp.REFUSED, "an unloadable bundle is a REFUSAL, not a FAIL"
    assert "could not load it" in text
    assert "unsupported plugin format" in text, \
        "the host's own error must survive into the refusal, not be swallowed"
    assert "REOPENS" not in text, "nothing was measured, so #122 does not reopen"
    assert "stated NO-VERDICT" in text
    rec = json.loads(out.read_text())
    assert rec["outcome"] == "REFUSED"
    assert "qualification" not in rec


def test_an_unloadable_bundle_is_distinguishable_from_an_absent_host(
        monkeypatch, capsys, bundle, tmp_path):
    """The control for the test above, and the reason it exists.

    `import pedalboard` failing and `load_plugin` failing both raise
    `ImportError`. If the two refusals read alike, an operator with a stub
    bundle is told to install a host they already have -- so the two messages
    are asserted to be DIFFERENT here, not merely each present."""
    mod = types.ModuleType("pedalboard")
    mod.__version__ = "0.9.25"
    mod.load_plugin = lambda path, **_kw: (_ for _ in ()).throw(
        ImportError("unsupported plugin format or scan failure"))
    monkeypatch.setitem(sys.modules, "pedalboard", mod)
    monkeypatch.setattr(rr, "PATH_MODELD", str(bundle))
    monkeypatch.setattr(rr.ModelDPedalboardRig, "path", str(bundle))
    assert qmp.main([]) == rp.REFUSED
    text = capsys.readouterr().out
    assert "no pedalboard on this machine" not in text, \
        "an unloadable BUNDLE must not be reported as a missing HOST"
    assert "could not load it" in text


def test_a_qualifying_rig_exits_zero_and_records_the_whole_environment_tuple(
        monkeypatch, capsys, bundle, tmp_path):
    """Exit 0, and the record carries the #123 tuple in full: host and version,
    plugin and binary hash, block, sample rate, licence state and preset."""
    with_host(monkeypatch, bundle)
    out = tmp_path / "rec.json"
    code = qmp.main(["--json", str(out)])
    text = capsys.readouterr().out
    assert code == rp.OK, text
    assert "QUALIFIED" in text and "REOPENS" not in text
    rec = json.loads(out.read_text())
    assert rec["outcome"] == "QUALIFIED"
    env = rec["environment"]
    for field in rp.ENV_FIELDS:
        assert env.get(field) not in (None, "", {}), field
    assert env["host"] == "pedalboard" and env["host_version"] == "0.9.25"
    assert env["plugin"]["binary_sha256"] and env["plugin"]["bundle_version"] == "1.2.3"
    assert env["block"] == rr.BLOCK and env["sr"] == 48000
    assert env["block_rate_hz"] == pytest.approx(48000 / rr.BLOCK)
    assert env["automates_a_parameter"] is False
    assert env["licence"]["state"] == "unverified" and env["licence"]["how"]
    assert env["preset"]["source"]
    # ...and the qualification itself, check by check, with its numbers.
    q = rec["qualification"]
    assert q["verdict"] == "qualified" and q["n_passed"] == q["n_checks"]
    names = {c["check"] for c in q["checks"]}
    assert {"osc range calibration", "level trim", "pitch causality",
            "filter causality"} <= names
    assert rec["parameters_after_qualification"]
    assert rec["note_midi"] == 60
    assert rec["qualification_source_sha256"]


def test_a_qualifying_run_writes_the_clip_only_when_asked(
        monkeypatch, capsys, bundle, tmp_path):
    with_host(monkeypatch, bundle)
    wav = tmp_path / "clip.wav"
    assert qmp.main(["--wav", str(wav)]) == rp.OK
    assert wav.exists() and wav.stat().st_size > 1000
    from scipy.io import wavfile
    sr, y = wavfile.read(str(wav))
    assert sr == 48000 and len(y) > 0
    assert float(abs(y).max()) <= rq.PEAK_MAX


def test_an_uncorrectable_octave_exits_FAIL_and_reopens_122(
        monkeypatch, capsys, bundle, tmp_path):
    """The gate's failure branch as the operator sees it: exit 1, the sweep
    table in the record, and the #122 note printed -- because this run DID
    measure the plugin."""
    with_host(monkeypatch, bundle, ranges=(0.125, 0.25, 0.5, 0.5, 2.0, 4.0))
    out = tmp_path / "rec.json"
    code = qmp.main(["--json", str(out)])
    text = capsys.readouterr().out
    assert code == rp.FAIL
    assert "REFUSED to be built" in text
    assert "REOPENS" in text and "Route 1 / Route 2 / Route 3" in text
    rec = json.loads(out.read_text())
    assert rec["outcome"] == "FAIL"
    cal = [c for c in rec["qualification"]["checks"]
           if c["check"] == "osc range calibration"][0]
    assert cal["outcome"] == "fail"
    assert len(cal["detail"]["sweep"]) == len(rr.ModelDPedalboardRig.RANGE_GRID)


def test_an_uncorrectable_level_reopens_122_with_the_whole_grid_on_the_record(
        monkeypatch, capsys, bundle, tmp_path):
    """The other half of the gate. Exit 2 rather than 1, and that is the
    measurement and not a quirk: a plugin whose QUIETEST master volume still
    rails puts 99.7 % of samples there at its default, and at that point the
    waveform check can no longer name anything either -- so a REFUSAL outranks
    the level FAILURE in the verdict. Both are on the record; the level trim's
    thirteen-row grid is what says the clipping is uncorrectable."""
    with_host(monkeypatch, bundle, output_gain=400.0)
    out = tmp_path / "rec.json"
    code = qmp.main(["--json", str(out)])
    assert code == rp.REFUSED
    assert "REOPENS" in capsys.readouterr().out, "this run DID measure the plugin"
    rec = json.loads(out.read_text())
    assert rec["outcome"] == "REFUSED"
    got = {c["check"]: c for c in rec["qualification"]["checks"]}
    trim = got["level trim"]
    assert trim["outcome"] == "fail"
    assert "NOT correctable" in trim["why"]
    assert len(trim["detail"]["sweep"]) == len(rr.ModelDPedalboardRig.MASTER_GRID)
    assert all(r["outcome"] == "fail" for r in trim["detail"]["sweep"])
    # The octave WAS correctable here, so the finding is specifically about
    # level: a refusal that did not separate the two would send the next person
    # to the wrong control.
    assert got["osc range calibration"]["outcome"] == "pass"
    assert got["level"]["outcome"] == "fail"
    assert got["waveform"]["outcome"] == "refused"


def test_a_silent_host_exits_REFUSED_not_FAIL(monkeypatch, capsys, bundle, tmp_path):
    """Exact silence is an absence of evidence. It is REFUSED (exit 2), not a
    measured failure -- which is exactly what the dawdreamer verdict for this
    plugin should have been scoped as from the start."""
    with_host(monkeypatch, bundle, always_silent=True)
    out = tmp_path / "rec.json"
    assert qmp.main(["--json", str(out)]) == rp.REFUSED
    rec = json.loads(out.read_text())
    assert rec["outcome"] == "REFUSED"
    snd = [c for c in rec["qualification"]["checks"] if c["check"] == "sounding"][0]
    assert snd["outcome"] == "refused" and snd["detail"]["peak"] == 0.0
    # ...and this one DOES reopen #122: the plugin was loaded and measured.
    assert "REOPENS" in capsys.readouterr().out


def test_a_failing_run_refuses_to_write_a_wav(monkeypatch, capsys, bundle, tmp_path):
    """A clip from a rig that did not qualify is not evidence, and putting one
    on disk is how it later gets used as though it were."""
    with_host(monkeypatch, bundle, ranges=(0.125, 0.25, 0.5, 0.5, 2.0, 4.0))
    wav = tmp_path / "must-not-exist.wav"
    assert qmp.main(["--wav", str(wav)]) == rp.FAIL
    assert not wav.exists()
    assert "NOT writing --wav" in capsys.readouterr().out


# ===========================================================================
# the record refuses after the rig qualified (#1980)
# ===========================================================================
_PLIST = (b'<?xml version="1.0"?><!DOCTYPE plist><plist version="1.0"><dict>'
          b'<key>CFBundleShortVersionString</key><string>1.2.3</string>'
          b'<key>CFBundleIdentifier</key><string>com.moog.modeld</string>'
          b'</dict></plist>')


def _bundle_at(root, binary_dir):
    """A Model D bundle whose only binary, if any, sits in `binary_dir` under
    `Contents/`. `None` is a bundle with nothing hashable anywhere."""
    b = root / "Model D.vst3"
    (b / "Contents").mkdir(parents=True)
    (b / "Contents" / "Info.plist").write_bytes(_PLIST)
    if binary_dir is not None:
        (b / "Contents" / binary_dir).mkdir()
        (b / "Contents" / binary_dir / "Model D").write_bytes(b"not a real binary")
    return b


@pytest.mark.parametrize("asked_for_wav", [False, True], ids=["json only", "json and wav"])
def test_a_rig_that_qualifies_with_no_hashable_binary_is_REFUSED_not_a_traceback(
        monkeypatch, capsys, tmp_path, asked_for_wav):
    """Issue #1980. `report(dev, qual)` sat in main()'s `else:` clause, which
    the `except rp.Refused` above it does not cover, and `report` refuses
    through `rp.environment_tuple` when `plugin_identity` finds no binary to
    hash. That refusal left main() uncaught: exit 1 by traceback, no --json
    record, after the rig had QUALIFIED.

    The fake host loads anything, so a bundle with no binary at all reaches
    the record step -- and the record must come back as a REFUSAL, with the
    sweep table still on stdout because the rig DID qualify. With `--wav`, no
    clip is written: one nobody can attribute to a binary is not evidence, and
    the reason given must be THAT, not "the rig did not qualify"."""
    with_host(monkeypatch, _bundle_at(tmp_path, None))
    out, wav = tmp_path / "rec.json", tmp_path / "clip.wav"
    argv = ["--json", str(out)] + (["--wav", str(wav)] if asked_for_wav else [])
    code = qmp.main(argv)
    text = capsys.readouterr().out
    assert code == rp.REFUSED
    assert "REFUSED  " in text and "no binary hash" in text
    rec = json.loads(out.read_text())
    assert rec["outcome"] == "REFUSED"
    assert "no binary hash" in rec["why"]
    assert "environment" not in rec and "qualification" not in rec
    # The table is the evidence of the attempt and must not be lost with the
    # record -- but nothing that follows a QUALIFICATION may be printed.
    assert "QUALIFIED" in text
    assert "NEXT:" not in text
    assert "the clip that qualified" not in text
    # ...and #122 must not reopen: the plugin was measured and did fine. What
    # was refused is the RECORD, and this file's third note says so.
    assert "REOPENS" not in text
    assert "NO-VERDICT" in text
    assert not wav.exists()
    if asked_for_wav:
        assert "NOT writing --wav" in text
        assert "record was refused" in text
        assert "did not qualify" not in text


def test_a_plain_AssertionError_out_of_report_still_escapes(
        monkeypatch, capsys, bundle, tmp_path):
    """The control for the guard above: it is `except rp.Refused` and nothing
    wider. A bug in `report` must surface as a bug, not be filed as a
    refusal -- a guard that swallowed it would hide the next defect of this
    class behind a clean exit 2."""
    with_host(monkeypatch, bundle)

    def broken(*a, **kw):
        raise AssertionError("a bug in report, not a precondition")
    monkeypatch.setattr(qmp, "report", broken)
    with pytest.raises(AssertionError, match="a bug in report"):
        qmp.main(["--json", str(tmp_path / "rec.json")])


@pytest.mark.parametrize("platform, platforms_own, in_the_bundle, exits", [
    ("linux", "x86_64-linux", "x86_64-linux", rp.OK),
    ("win32", "x86_64-win", "x86_64-win", rp.OK),
    ("darwin", "MacOS", "x86_64-linux", rp.REFUSED),
], ids=["linux build on linux", "windows build on windows",
        "linux build on a mac"])
def test_the_binary_the_record_hashes_is_the_one_this_platform_loads(
        monkeypatch, capsys, tmp_path, platform, platforms_own, in_the_bundle,
        exits):
    """The other half of #1980: `plugin_identity` looked only under
    `Contents/MacOS`, so a host holding a NATIVE build of the plugin loaded
    it, qualified, and then found "no binary" in its own bundle. The
    directories are the VST3 spec's, hardcoded here rather than read from
    `rr.VST3_BINARY_DIR`, so the test is not the fixture agreeing with itself;
    the production table is checked against them instead.

    The third row is a bundle this platform cannot load, where "no binary
    hash" is now a TRUE reason: a clean REFUSED, not a traceback. `sys.platform`
    is pinned so each row means the same thing on every runner."""
    assert rr.VST3_BINARY_DIR[platform] == f"Contents/{platforms_own}"
    monkeypatch.setattr(sys, "platform", platform)
    with_host(monkeypatch, _bundle_at(tmp_path, in_the_bundle))
    out = tmp_path / "rec.json"
    code = qmp.main(["--json", str(out)])
    text = capsys.readouterr().out
    rec = json.loads(out.read_text())
    assert code == exits, text
    if exits == rp.OK:
        assert rec["outcome"] == "QUALIFIED"
        assert rec["environment"]["plugin"]["binary_sha256"]
        assert "NEXT:" in text and "REOPENS" not in text
    else:
        assert rec["outcome"] == "REFUSED" and "no binary hash" in rec["why"]
        assert "NEXT:" not in text and "REOPENS" not in text


# ===========================================================================
# the host-scoped verdict table (#123)
# ===========================================================================
def test_the_pedalboard_verdict_sits_beside_the_dawdreamer_one(monkeypatch):
    """Never in place of it. The dawdreamer entry records that this bundle
    renders exact silence under that host; overwriting it with a pedalboard
    result would erase the measurement the rest of the profile was built
    against."""
    v = rp.RIG_VERDICTS
    assert "modeld" in v and "modeld-pedalboard" in v
    assert v["modeld"]["host"] == "dawdreamer"
    assert v["modeld-pedalboard"]["host"] == "pedalboard"
    assert "exact silence" in v["modeld"]["why"]
    assert "DAWDREAMER" in v["modeld"]["why"], "the verdict must name its host"
    assert v["modeld"]["qualified"] is False
    assert v["modeld-pedalboard"]["builder"] == "reference_rigs.ModelDPedalboardRig()"


def test_every_verdict_names_the_host_it_was_measured_under():
    for name, v in rp.RIG_VERDICTS.items():
        assert v.get("host"), f"{name} has a verdict and no host"
        assert v.get("why"), f"{name} has a verdict and no reason"


def test_an_unrun_rig_is_None_and_not_False():
    """"nobody has run it" and "it cannot be used" are different facts and
    writing either one as the other is what #123 was about. `qualified_rigs`
    must not pick up a None, and neither may it be read as a rejection."""
    assert rp.RIG_VERDICTS["modeld-pedalboard"]["qualified"] is None
    assert "modeld-pedalboard" not in rp.qualified_rigs()
    assert rp.qualified_rigs() == ["surge-type2"]
    assert rp.qualified_rigs(host="pedalboard") == []


def test_the_list_renderer_does_not_print_a_None_verdict_as_a_rejection(capsys,
                                                                       tmp_path,
                                                                       monkeypatch):
    """**Wrong before it was right, and caught by review rather than by a
    test.** `--list` rendered the column as `"yes" if r.get("qualified") else
    "NO"`, so the first `qualified: None` entry to reach a profile would have
    printed **NO** -- the tool announcing a rejection nobody measured, in the
    one place a reader goes to find out what this profile rejects. That is the
    whole point of host-scoped verdicts leaking straight back out through the
    renderer.

    Three words, three states, asserted through `cmd_list` itself rather than
    through the helper, because the defect was in the renderer."""
    assert rp.verdict_word(True) == "yes"
    assert rp.verdict_word(False) == "NO"
    assert rp.verdict_word(None) == "no verdict"
    # Not one of the three states: `?`, never a verdict. A renderer that maps an
    # unexpected value onto one of the three is how a verdict gets invented.
    assert rp.verdict_word("probably") == "?"
    assert rp.verdict_word(1) == "?"

    prof = {"schema": rp.SCHEMA, "clips": {}, "built": {}, "rigs": {
        "surge-type2": {"qualified": True, "host": "dawdreamer", "why": "open source"},
        "modeld": {"qualified": False, "host": "dawdreamer", "why": "exact silence"},
        "modeld-pedalboard": {"qualified": None, "host": "pedalboard",
                              "why": "nobody has run it"}}}
    p = tmp_path / "profile.json"
    p.write_text(json.dumps(prof), encoding="utf-8")
    monkeypatch.setattr(rp, "PROFILE_JSON", p)
    assert rp.cmd_list() == rp.OK
    out = capsys.readouterr().out
    rows = {ln.split()[0]: ln for ln in out.splitlines()
            if ln.split() and ln.split()[0] in prof["rigs"]}
    assert "no verdict" in rows["modeld-pedalboard"]
    assert "NO" not in rows["modeld-pedalboard"]
    assert "NO" in rows["modeld"]
    assert "yes" in rows["surge-type2"]
    # And the host it was measured under is on the row, since that is what makes
    # two rows for one bundle readable at all.
    assert "pedalboard" in rows["modeld-pedalboard"]
    assert "dawdreamer" in rows["modeld"]


def test_a_verdict_asked_for_under_the_wrong_host_is_refused():
    """The accessor is the point: `RIG_VERDICTS['modeld']` used under whatever
    host happens to be loaded is the mistake #123 found."""
    assert rp.verdict_for("modeld", "dawdreamer")["qualified"] is False
    with pytest.raises(rp.Refused, match="scoped to host"):
        rp.verdict_for("modeld", "pedalboard")
    with pytest.raises(rp.Refused, match="scoped to host"):
        rp.verdict_for("modeld-pedalboard", "dawdreamer")
    with pytest.raises(rp.Refused, match="not a rig"):
        rp.verdict_for("nonesuch", "pedalboard")


# ===========================================================================
# the environment tuple refuses rather than reports
# ===========================================================================
GOOD_PLUGIN = {"present": True, "path": "/x/Model D.vst3", "binary_sha256": "ab" * 32}


def _env(**kw):
    base = dict(host="pedalboard", host_version="0.9.25", plugin=dict(GOOD_PLUGIN),
                block=512, sr=48000,
                licence={"state": "unverified", "how": "no probe exists"},
                preset={"source": "defaults"})
    base.update(kw)
    return rp.environment_tuple(**base)


def test_the_environment_tuple_has_every_field_123_names():
    e = _env()
    assert set(rp.ENV_FIELDS) <= set(e)
    assert e["block_rate_hz"] == pytest.approx(93.75)
    assert "94 Hz" in e["block_note"]


@pytest.mark.parametrize("field", rp.ENV_FIELDS)
def test_an_absent_environment_field_is_refused_not_omitted(field):
    """A tuple with a field missing looks identical afterwards to a tuple whose
    field was never asked about, and one of those is a gap somebody should
    close. So every field must be present."""
    with pytest.raises(rp.Refused, match=field):
        _env(**{field: None})


#: Which fields a caller may honestly mark "unstated", and which it may not.
#: `block` and `sr` were handed TO the host by the caller, so not knowing them
#: is not a gap in the evidence -- it is a caller that did not pin them, which
#: is the 94 Hz artefact waiting to happen. `plugin` cannot be a string either:
#: a path does not answer "what produced this audio".
MAY_BE_UNSTATED = ("host", "host_version", "licence", "preset")
MUST_BE_REAL = ("block", "sr", "plugin")


@pytest.mark.parametrize("field", MAY_BE_UNSTATED)
def test_a_field_nobody_has_established_may_be_recorded_as_unstated(field):
    assert _env(**{field: "unstated"})[field] == "unstated"


@pytest.mark.parametrize("field", MUST_BE_REAL)
def test_the_fields_that_cannot_honestly_be_unstated_are_refused(field):
    with pytest.raises(rp.Refused):
        _env(**{field: "unstated"})


def test_the_two_lists_together_are_every_field():
    """So a field added later cannot quietly be neither."""
    assert set(MAY_BE_UNSTATED) | set(MUST_BE_REAL) == set(rp.ENV_FIELDS)


def test_a_non_positive_block_or_rate_is_refused():
    for field in ("block", "sr"):
        with pytest.raises(rp.Refused, match="not a size"):
            _env(**{field: 0})


def test_a_plugin_that_is_absent_or_unhashed_is_refused():
    with pytest.raises(rp.Refused, match="not present"):
        _env(plugin={"present": False, "path": "/x"})
    with pytest.raises(rp.Refused, match="no binary hash"):
        _env(plugin={"present": True, "path": "/x"})


def test_the_block_rate_is_computed_and_not_taken_on_trust():
    """The 94 Hz artefact, as a number the record carries: 512 at 48 kHz is
    93.75 Hz and pedalboard's own default 8192 is 5.86 Hz. A reader must not
    have to work that out."""
    assert _env(block=512)["block_rate_hz"] == pytest.approx(93.75)
    assert _env(block=8192)["block_rate_hz"] == pytest.approx(5.8594, abs=1e-3)
    assert _env(block=16)["block_rate_hz"] == pytest.approx(3000.0)


def test_the_preset_identity_hashes_state_where_the_host_exposes_it():
    class _WithPreset:
        preset_data = b"\x01\x02\x03" * 40

    class _Without:
        pass
    got = rp.preset_identity(_WithPreset(), source="s")
    assert len(got["preset_data_sha256"]) == 64 and got["preset_data_bytes"] == 120
    assert "not exposed by this host" in rp.preset_identity(_Without(), source="s")["preset_data"]
