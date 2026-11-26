#!/usr/bin/env python3
"""`tools/render_miniv4_suite.py`'s outcomes, over a fake rig (#1050).

    python3 -m pytest tools/test_render_miniv4_suite.py -q

The tool is what an operator runs on a Mac that HAS Mini V4 4.0.4.6366 and a
plugin host. Every outcome -- the dry run, each REFUSED precondition, the
PASS record, the FAIL demo scan -- is exercised here through the shipping
`main()` with the rig faked, so the refusals are proven on this host without
the plugin, exactly the discipline `tools/test_qualify_modeld_pedalboard.py`
established. No render of real audio is attempted anywhere (#1048: fleet
workers do not render the commercial reference).
"""
from __future__ import annotations

import json
import pathlib
import sys
import types

import numpy as np
import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "audition"))

import audio_measure                                                # noqa: E402
import dsp                                                          # noqa: E402
import refprofile                                                   # noqa: E402
import reference_suites as rs                                       # noqa: E402
import render_miniv4_suite as rv                                    # noqa: E402

SR = 48000
GOOD_LICENCE = "serial activated; verified in the vendor's software center on this Mac"


def good_identity(version=None) -> dict:
    return {"path": rv.PATH_MINIV4, "present": True,
            "bundle_version": version or rs.V4_REQUIRED_VERSION,
            "bundle_id": "com.Arturia.Mini-V4.vst",
            "binary": "Mini V4", "binary_sha256": "ab" * 32, "binary_bytes": 1}


def fake_identity(monkeypatch, version=None, present=True):
    ident = good_identity(version)
    ident["present"] = present
    monkeypatch.setattr(refprofile, "plugin_identity", lambda _path: ident)


class FakeRig:
    """A rig whose note is a sine at the COMMANDED pitch, unless the test
    breaks one half of the calibration assertion."""

    host = "dawdreamer"
    octave_down = False
    silent = False
    clicks = 0

    def __init__(self, path=rv.PATH_MINIV4, block=rv.BLOCK, engine=None):
        self.path = path
        self.notes = []

    def identity(self):
        return good_identity()

    def render_note(self, note, seconds, velocity=rv.VELOCITY, note_at=0.02):
        self.notes.append((note, seconds))
        n = int(seconds * SR)
        t = np.arange(n) / SR
        hz = dsp.note_hz(note) / (2.0 if self.octave_down else 1.0)
        y = 0.5 * np.sin(2 * np.pi * hz * t)
        if self.silent:
            y = np.zeros(n)
        if self.clicks:
            rng = np.random.default_rng(7)
            for _ in range(self.clicks):
                i = rng.integers(int(0.6 * n), n - 16)
                y[i:i + 12] += 0.25 * y.max() * rng.standard_t(4, 12)
        return y


@pytest.fixture
def rig_ready(monkeypatch):
    fake_identity(monkeypatch)
    monkeypatch.setattr(rv, "_Rig", FakeRig)


# ===========================================================================
# the dry run: no plugin, anywhere
# ===========================================================================
def test_list_is_a_dry_run_that_needs_no_plugin(capsys):
    code = rv.main(["--list"])
    text = capsys.readouterr().out
    assert code == rv.OK
    assert "no renders yet" in text
    for cid in ("M1A", "M5B", "M8D"):
        assert cid in text
    waiting = [line for line in text.splitlines() if "awaiting a render" in line]
    assert waiting and "32 of 32" in waiting[0]


# ===========================================================================
# each REFUSED precondition, through the shipping main()
# ===========================================================================
def test_a_missing_bundle_is_refused_even_with_a_stated_licence(monkeypatch, capsys):
    fake_identity(monkeypatch, present=False)
    code = rv.main(["--render", "M1B", "--licence-established", GOOD_LICENCE])
    assert code == rv.REFUSED
    assert "not installed" in capsys.readouterr().out


def test_the_wrong_plugin_version_is_refused(monkeypatch, capsys, rig_ready):
    fake_identity(monkeypatch, version="4.0.5.6367")
    code = rv.main(["--render", "M1B", "--licence-established", GOOD_LICENCE])
    assert code == rv.REFUSED
    text = capsys.readouterr().out
    assert "4.0.5.6367" in text and "4.0.4.6366" in text


def test_an_unstated_licence_is_refused(monkeypatch, capsys, rig_ready):
    code = rv.main(["--render", "M1B"])
    assert code == rv.REFUSED
    assert "licence" in capsys.readouterr().out


def test_no_plugin_host_is_refused(monkeypatch, capsys):
    fake_identity(monkeypatch)
    real = __import__

    def no_dawdreamer(name, *a, **kw):
        if name == "dawdreamer":
            raise ImportError("No module named 'dawdreamer'")
        return real(name, *a, **kw)
    monkeypatch.setattr("builtins.__import__", no_dawdreamer)
    code = rv.main(["--render", "M1B", "--licence-established", GOOD_LICENCE])
    assert code == rv.REFUSED
    assert "no dawdreamer" in capsys.readouterr().out


def test_exact_silence_is_refused_not_measured(monkeypatch, capsys, rig_ready):
    monkeypatch.setattr(FakeRig, "silent", True)
    code = rv.main(["--render", "M1B", "--licence-established", GOOD_LICENCE])
    assert code == rv.REFUSED
    assert "exact silence" in capsys.readouterr().out


def test_an_octave_default_is_refused(monkeypatch, capsys, rig_ready):
    # Mini V3's Range default was an octave down; Model D's is too. The
    # calibration assert is the check that exists because of them.
    monkeypatch.setattr(FakeRig, "octave_down", True)
    code = rv.main(["--render", "M1B", "--licence-established", GOOD_LICENCE])
    assert code == rv.REFUSED
    assert "cents" in capsys.readouterr().out


def test_an_unreadable_calibration_pitch_is_refused(monkeypatch, capsys, rig_ready):
    monkeypatch.setattr(FakeRig, "silent", True)
    monkeypatch.setattr(audio_measure, "dominant_frequency",
                        lambda *a, **k: types.SimpleNamespace(
                            ok=False, value=None, reason="no prominent line"))
    # silence refuses first -- unreadability is reached from a sounding rig
    monkeypatch.setattr(FakeRig, "silent", False)
    code = rv.main(["--render", "M1B", "--licence-established", GOOD_LICENCE])
    assert code == rv.REFUSED
    assert "unreadable" in capsys.readouterr().out


def test_a_sealed_holdout_case_refuses_the_render(monkeypatch, capsys, rig_ready):
    code = rv.main(["--render", "M1D", "--licence-established", GOOD_LICENCE])
    assert code == rv.REFUSED
    text = capsys.readouterr().out
    assert "sealed holdout" in text and "M1D" in text


def test_a_case_outside_the_suite_is_refused(monkeypatch, capsys, rig_ready):
    code = rv.main(["--render", "D01A", "--licence-established", GOOD_LICENCE])
    assert code == rv.REFUSED
    assert "not a case in the suite" in capsys.readouterr().out


# ===========================================================================
# the PASS record, and the FAIL demo scan
# ===========================================================================
def test_a_clean_render_passes_and_stamps_the_suite(monkeypatch, capsys,
                                                    tmp_path, rig_ready):
    out = tmp_path / "renders"
    code = rv.main(["--render", "M1B", "--licence-established", GOOD_LICENCE,
                    "--out", str(out)])
    assert code == rv.OK, capsys.readouterr().out
    rec_path = out / "M1B-miniv4-manifest.json"
    rec = json.loads(rec_path.read_text())
    assert rec["suite"] == rs.SUITE_V4
    assert rec["plugin_required_version"] == rs.V4_REQUIRED_VERSION
    env = rec["environment"]
    for field in ("host", "host_version", "plugin", "block", "sr", "licence", "preset"):
        assert field in env, f"environment tuple missing {field!r}"
    assert env["licence"]["stated_by_operator"] == GOOD_LICENCE
    assert rec["case_spec"]["base_midi_note"] == 24
    wav = out / rec["audio"]["file"]
    assert wav.exists() and wav.read_bytes()[:4] == b"RIFF"


def test_the_written_record_passes_the_v4_gate_and_refuses_the_v3_slot(
        monkeypatch, capsys, tmp_path, rig_ready):
    """The writer/reader join: the record the renderer ACTUALLY writes --
    not a hand-built stand-in with an assumed schema -- must classify as
    `miniv4` through `reference_suites.require_suite` (the call the V4
    scoring side makes once renders land) and refuse the frozen V3 slot.
    Without this test each half of the apparatus is tested against its own
    assumed schema and nothing joins them."""
    out = tmp_path / "renders"
    code = rv.main(["--render", "M1B", "--licence-established", GOOD_LICENCE,
                    "--out", str(out)])
    assert code == rv.OK, capsys.readouterr().out
    rec = json.loads((out / "M1B-miniv4-manifest.json").read_text())
    assert rs.require_suite(rec, rs.SUITE_V4) == rs.SUITE_V4
    with pytest.raises(rs.Refused, match="reference-suite mix"):
        rs.require_suite(rec, rs.SUITE_V3_FROZEN)


def test_an_unclassifiable_render_identity_refuses_before_measuring(tmp_path):
    """The writer checks the identity it is about to stamp through the
    reader's own gate before rendering: a version the registry never
    declared refuses having measured nothing, rather than writing a record
    no scorer can classify."""
    class UndeclaredRig:
        host = "dawdreamer"

        def identity(self):
            return {"bundle_version": "9.9.9.9999", "bundle_id": "x"}

        def render_note(self, *a, **k):
            raise AssertionError("must not render: the identity refused first")

    suite = rs.load_v4_suite()
    case = next(c for c in suite["cases"] if c["case_id"] == "M1B")
    with pytest.raises(rv.Refused, match="not classifiable"):
        rv.render_case(case, UndeclaredRig(), "licence stated", tmp_path / "renders")


def test_demo_artefacts_on_the_calibration_note_fail_the_run(monkeypatch, capsys,
                                                             tmp_path, rig_ready):
    # Sparse impulsive transients are what both Arturia's and u-he's demo
    # modes insert; the detector is the ground-truthed one from
    # model/reference_integrity.py. The render happened, so this is FAIL
    # (exit 1), not REFUSED.
    monkeypatch.setattr(FakeRig, "clicks", 4)
    code = rv.main(["--render", "M1B", "--licence-established", GOOD_LICENCE,
                    "--out", str(tmp_path / "renders")])
    assert code == rv.FAIL
    assert "transient" in capsys.readouterr().out


def test_a_bad_suite_manifest_is_refused_before_anything(monkeypatch, capsys,
                                                         tmp_path):
    p = tmp_path / "suite.json"
    p.write_text("{}")
    monkeypatch.setattr(rv.rs, "V4_SUITE_MANIFEST", p)
    code = rv.main(["--render", "M1B"])
    assert code == rv.REFUSED


def test_every_suite_case_is_listable_and_carries_render_settings():
    suite = rs.load_v4_suite()
    for c in suite["cases"]:
        assert isinstance(c["phrase_s"], (int, float))
        assert c["render_state"] == "awaiting-render"


@pytest.mark.parametrize("flag", ["--help", "--list"])
def test_help_and_list_run_without_numpy(flag):
    """#1292: the fleet-worker dry run must work on stdlib Python. numpy and
    the render-only modules are blocked in an isolated interpreter, so a
    regression to an eager top-level import fails here."""
    import subprocess
    script = pathlib.Path(rv.__file__).resolve()
    code = (
        "import runpy, sys\n"
        "for m in ('numpy', 'scipy', 'audio_measure', 'dsp', 'refprofile'):\n"
        "    sys.modules[m] = None\n"
        f"sys.argv = [{str(script)!r}, {flag!r}]\n"
        f"runpy.run_path({str(script)!r}, run_name='__main__')\n")
    r = subprocess.run([sys.executable, "-ISB", "-c", code],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    if flag == "--list":
        assert "32 of 32 cases" in r.stdout
    else:
        assert "usage" in r.stdout.lower()


# ===========================================================================
# the shipping adapter against a processor that has DawDreamer's API (#1403)
# ===========================================================================
class _DawProcessor:
    """Exposes ONLY the DawDreamer PluginProcessor methods the adapter may
    use, so a call to a method that class lacks (`add_note`) raises
    AttributeError exactly as on a real host."""

    def __init__(self, log, latency=0):
        self.log, self.latency = log, latency

    def clear_midi(self):
        self.log.append(("clear_midi",))

    def add_midi_note(self, note, velocity, start, duration):
        self.log.append(("add_midi_note", note, velocity, start, duration))

    def get_latency_samples(self):
        return self.latency


class _DawEngine:
    def __init__(self, log, audio):
        self.log, self.audio = log, audio

    def render(self, seconds):
        self.log.append(("render", seconds))

    def get_audio(self):
        self.log.append(("get_audio",))
        return self.audio


def _shipping_rig(log, audio, latency=0):
    rig = rv._Rig.__new__(rv._Rig)
    rig.p = _DawProcessor(log, latency)
    rig.eng = _DawEngine(log, audio)
    return rig


def test_render_note_dispatches_midi_through_the_daw_api_in_order():
    log = []
    audio = np.vstack([np.arange(100.0), -np.arange(100.0)]).astype(np.float32)
    rig = _shipping_rig(log, audio, latency=10)
    y = rig.render_note(64, 0.5, velocity=90, note_at=0.03)
    assert log == [("clear_midi",), ("add_midi_note", 64, 90, 0.03, 0.5),
                   ("render", 0.5), ("get_audio",)]
    # first channel only, host latency removed
    assert np.array_equal(y, np.arange(10.0, 100.0))


def test_render_note_refuses_a_non_finite_host_buffer():
    audio = np.zeros((2, 50), dtype=np.float32)
    audio[0, 7] = np.nan
    rig = _shipping_rig([], audio)
    with pytest.raises(audio_measure.NonFiniteAudio, match=r"\(1 NaN, 0 Inf; first at index 7\)"):
        rig.render_note(60, 0.1)


def test_the_old_method_name_fails_at_note_dispatch():
    """Injected-bug control: a processor that offers only `add_note` (the
    pre-#1403 call) must NOT satisfy the adapter."""
    log = []
    rig = _shipping_rig(log, np.ones((2, 10), dtype=np.float32))
    rig.p = types.SimpleNamespace(
        clear_midi=rig.p.clear_midi,
        add_note=lambda *args: log.append(("add_note", *args)),
        get_latency_samples=rig.p.get_latency_samples,
    )
    with pytest.raises(AttributeError) as exc:
        rig.render_note(60, 0.1)
    assert exc.value.name == "add_midi_note"
    assert log == [("clear_midi",)]
