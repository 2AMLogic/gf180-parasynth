import hashlib
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


def run_cli(manifest, out):
    """The real command as a subprocess, reading `manifest` where it would read
    the frozen one. The frozen manifest and its audio live in the checkout,
    which a test must not drift, so the driver rebinds `mono_m5a_score.MANIFEST`
    before the script's own import finds that module."""
    driver = dedent("""\
        import runpy, sys
        from pathlib import Path
        script, manifest, out = sys.argv[1:4]
        root = Path(script).resolve().parents[1]
        sys.path[:0] = [str(root / "model"), str(root / "tools")]
        import mono_m5a_score as score
        score.MANIFEST = Path(manifest)
        sys.argv = ["selected_preset.py", "m5a-saw", "--out", out]
        runpy.run_path(script, run_name="__main__")
    """)
    return subprocess.run([sys.executable, "-E", "-c", driver, str(preset.__file__), str(manifest), str(out)],
                          capture_output=True, text=True)


def test_reference_audio_drift_is_refused_at_the_cli_and_writes_nothing(tmp_path):
    """The hash the manifest names is not the hash the audio has. The command
    parsed and the preset name is valid, so this is REFUSED -- exit 2, the
    reason on stderr, nothing written, not even --out's directory -- never a
    traceback at exit 1, which a caller reads as a FAIL (#1674)."""
    frozen = json.loads(preset.score.MANIFEST.read_text())
    frozen["audio"]["file"] = str(preset.score.MANIFEST.parent / frozen["audio"]["file"])
    drifted = hashlib.sha256(b"not the reference audio").hexdigest()
    assert drifted != frozen["audio"]["sha256"], "the drifted hash must differ from the frozen one"
    frozen["audio"]["sha256"] = drifted
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(frozen, indent=2) + "\n")
    out = tmp_path / "written" / "m5a-saw.json"
    done = run_cli(manifest, out)
    assert done.returncode == 2, done.stderr
    assert done.stderr == "REFUSED: selected preset reference audio differs from frozen manifest\n"
    assert done.stdout == ""
    assert not out.parent.exists(), "the refusal created --out's directory"


def test_an_unrelated_value_error_stays_an_execution_error_at_the_cli(tmp_path):
    """The control for the refusal above: the handler catches `Refused` and
    nothing wider. Invalid JSON in the manifest raises JSONDecodeError, which
    IS a ValueError, and it must leave as a traceback at exit 1 -- a handler
    written `except ValueError` would render this REFUSED, an execution error
    dressed as an unmet precondition."""
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"audio": ')
    out = tmp_path / "written" / "m5a-saw.json"
    done = run_cli(manifest, out)
    assert done.returncode == 1, done.stderr
    assert "Traceback" in done.stderr and "JSONDecodeError" in done.stderr
    assert "REFUSED" not in done.stderr
    assert not out.parent.exists()


def test_clean_run_writes_the_preset_and_exits_zero(tmp_path):
    """The positive control: the real command on the unmodified tree exits 0
    and writes exactly `definition()`'s preset."""
    out = tmp_path / "written" / "m5a-saw.json"
    done = subprocess.run([sys.executable, "-E", str(preset.__file__), "m5a-saw", "--out", str(out)],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert done.stdout == f"{out}\n"
    assert done.stderr == ""
    assert json.loads(out.read_text()) == json.loads(json.dumps(preset.definition("m5a-saw")))
