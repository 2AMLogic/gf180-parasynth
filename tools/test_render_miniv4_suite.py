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

import dsp                                                          # noqa: E402
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
    monkeypatch.setattr(rv.rp, "plugin_identity", lambda _path: ident)


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
    monkeypatch.setattr(rv.am, "dominant_frequency",
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
