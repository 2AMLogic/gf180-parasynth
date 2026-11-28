import importlib.util
import json
import subprocess
import sys
from textwrap import dedent
from types import SimpleNamespace

import pytest

import selected_preset as preset
spec = importlib.util.spec_from_file_location("fpga_play", preset.ROOT / "fpga/play.py")
play = importlib.util.module_from_spec(spec)
spec.loader.exec_module(play)


def test_selected_presets_keep_effective_pulse_and_measured_release():
    saw, pulse = (preset.definition(name) for name in preset.NAMES)
    assert saw["required_build"] == pulse["required_build"] == {"OSC2X": 1, "FILTER2X": 1}
    assert pulse["patch"]["waves"] == ("pulse29",) * 3
    assert pulse["engine_profile"]["pulse_effective_duty_percent"] == pytest.approx(47.9, abs=0.001)
    assert saw["patch"]["cutoff"] == (20_000, 20_000)
    assert saw["patch"]["vol"] == pytest.approx(0.45 * 10 ** (-0.45428 / 20))
    assert saw["registers"]["amp"] == pulse["registers"]["amp"]


def test_named_preset_reaches_host_register_image_and_keeps_release_tail():
    args = SimpleNamespace(demo=False, fixture=None, preset="m5a-pulse", bpm=118,
                           pattern="none", bars=1, keys="72,84", decay=None)
    host, frames, _coverage = play.build(args)
    assert host.regs == preset.definition("m5a-pulse")["registers"]
    assert [w.data for w in host.w if w.tag == "wave"] == [7, 7, 7]
    assert frames >= max(w.frame for w in host.w) + 3 * play.sh.SR


def test_unknown_preset_refuses():
    with pytest.raises(ValueError, match="unknown"):
        preset.definition("unmeasured")


def test_player_paths_work_without_scipy():
    # A fresh interpreter keeps pytest's already-imported scorer out of the check.
    player = dedent("""\
        import sys
        assert "scipy.io" not in sys.modules
        sys.modules["scipy"] = None

        import midi_session
        import selected_preset
        import uart_device_sim as dev
        import uart_host
        import voice_fx

        clock = dev.SimClock()
        port = dev.SimSerial(dev.UartDeviceSim(clock=clock))
        session = midi_session.MidiSession(port, clock=clock, preset=None)
        assert session.regs == voice_fx.VoiceFx.patch_regs()
        for name in ("m5a-saw", "m5a-pulse"):
            expected = selected_preset.definition(name)["registers"]
            session = midi_session.MidiSession(port, clock=clock, preset=name)
            assert session.regs == expected
            assert uart_host.main(["--dry-run", "run", "--preset", name,
                                   "--note", "72", "--fixture", "none", "--image", "r1"]) == 0
    """)
    subprocess.run([sys.executable, "-E", "-c", player], cwd=preset.ROOT / "fpga", check=True)


# ---- a reference manifest that cannot be read is REFUSED, not a traceback (#1621)

@pytest.mark.parametrize("manifest,why", [
    pytest.param({}, "'audio'", id="empty"),
    pytest.param({"audio": None}, "'audio'", id="null-key"),
    pytest.param([], "not a JSON object", id="list"),
    pytest.param(5, "not a JSON object", id="number"),
    pytest.param(None, "not a JSON object", id="null"),
])
def test_a_reference_manifest_lacking_audio_is_refused(monkeypatch, tmp_path, capsys, manifest, why):
    """`manifest["audio"]` was read with nothing checking the manifest, so a
    partial one was a `KeyError` traceback (exit 1) where this module's own
    refusals are ValueError -- and the entry point reported neither."""
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(manifest))
    monkeypatch.setattr(preset.score, "MANIFEST", p)
    with pytest.raises(ValueError, match=why):
        preset.definition("m5a-saw")
    out = tmp_path / "preset.json"
    rc = preset.main(["m5a-saw", "--out", str(out)])
    assert rc == 2 and "REFUSED" in capsys.readouterr().out
    assert not out.exists()


def test_a_preset_whose_reference_audio_no_longer_hashes_is_refused_by_the_entry_point(
        monkeypatch, tmp_path, capsys):
    """The pre-existing refusal, now reported: the hash check's ValueError left
    the `__main__` block as a traceback (exit 1). The audio is read beside the
    manifest, so a stand-in file sits beside the scratch copy."""
    record = json.loads(preset.score.MANIFEST.read_text())
    p = tmp_path / "manifest.json"
    audio = tmp_path / record["audio"]["file"]
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"not the reference audio")
    p.write_text(json.dumps(record))
    monkeypatch.setattr(preset.score, "MANIFEST", p)
    out = tmp_path / "preset.json"
    rc = preset.main(["m5a-saw", "--out", str(out)])
    assert rc == 2 and "differs from frozen manifest" in capsys.readouterr().out
    assert not out.exists()


def test_the_committed_reference_manifest_carries_audio():
    # the guard above must be satisfiable against the manifest that ships
    assert json.loads(preset.score.MANIFEST.read_text())["audio"] is not None
