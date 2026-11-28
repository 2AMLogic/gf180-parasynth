"""tools/test_uart_host_kit_refusal.py -- #1644: a drifted frozen kit is
REFUSED (2) at the uart_host CLI boundary, never a traceback (1).

`uart_host.py run --fixture demo --image r1|r2 --dry-run` reads R1's frozen kit
(uart_host.r1_kit, which raises drums_fx.KitRefused when
fpga/release/r1-kit.json no longer hashes to R1_KIT_SHA256) from
fixture_split, ahead of every handler main() had. The refusal left the process
as a traceback, exit 1: a missing precondition rendered as a FAIL result
(tools/exit_contract.py), the class of #1160, #1162, #1183, #1569 and #1614.

Every test drives `uart_host.main` through the IMPORTED module (`import
uart_host as uh`; `uh.R1_KIT` pointed at a copy), never the file as __main__:
running fpga/uart_host.py as a script executes it as a SECOND module object,
so a seam set on the imported module never reaches the code that runs and the
probe reads exit 0. That was the issue's own first measurement, wrong before
right, and it is why the process-exit test below ships a driver that imports
the module rather than one that runs the file.

Known answers, none of them read back from the code under test:
  * the refusing line carries the kit's OWN reason; its drifted digest is
    recomputed here with hashlib from the drifted writes (not through
    drums_fx._kit_sha256) and pinned to the digest the issue measured;
  * the same seam with an UNCHANGED copy of the kit exits 0, so the refusal
    is the drift's and not the seam's;
  * the presets-only drift is shown to be real (the reader of that table
    refuses it) before this CLI is shown not to care: it never reads the table;
  * the handler names its class. A corrupt kit file (json.JSONDecodeError, a
    ValueError) and a plain RuntimeError raised at the same read still leave
    main(), which is exit 1 -- each is the input that would defeat an
    `except ValueError`, an `except RuntimeError` or an `except Exception`.

Controls that were red on origin/main a9fe1c53, before the repair:
  test_control_drifted_r1_kit_is_refused_not_a_traceback[r1|r2],
  test_control_release_image_frozen_kit_drift_is_refused_the_same_way,
  test_this_modules_own_refusal_at_the_phrase_read_exits_2,
  test_process_exit_status[kit-drift-r1|kit-drift-r2].
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in ("fpga", "model", "audition"):
    if str(ROOT / _p) not in sys.path:
        sys.path.insert(0, str(ROOT / _p))

import drums_fx as dx          # noqa: E402
import uart_host as uh         # noqa: E402

R1_FROZEN = "321a93546cfa5ffab03b3cf91557580ea7655ada933ce380c81cd07597a9b683"
DRIFTED = "a4201f038011"       # the first kit write's low bit flipped: the issue's figure
RUN = ["run", "--fixture", "demo", "--dry-run"]

DRIVER = '''\
import pathlib, sys
root, kit_path, mode, image = sys.argv[1:5]
for p in ("fpga", "model", "audition"):
    sys.path.insert(0, str(pathlib.Path(root) / p))
import uart_host as uh
if mode == "runtime-error":
    def boom():
        raise RuntimeError("plain runtime error at the kit read")
    uh.r1_kit = boom
else:
    uh.R1_KIT = pathlib.Path(kit_path)
raise SystemExit(uh.main(["run", "--fixture", "demo", "--image", image, "--dry-run"]))
'''


def _kit_digest(writes) -> str:
    return hashlib.sha256(",".join(str((int(a) << 32) | int(v)) for a, v in writes)
                          .encode()).hexdigest()


def _flip_first_write(rec) -> None:
    rec["writes"][0][1] ^= 1


def _drift_presets(rec) -> None:
    rec["presets"]["LT"][0][1] ^= 1


def _kit_file(tmp_path: Path, edit) -> Path:
    rec = json.loads(uh.R1_KIT.read_text())
    edit(rec)
    p = tmp_path / "r1-kit.json"
    p.write_text(json.dumps(rec))
    return p


@pytest.mark.parametrize("image", ["r1", "r2"])
def test_control_drifted_r1_kit_is_refused_not_a_traceback(tmp_path, monkeypatch, capsys, image):
    p = _kit_file(tmp_path, _flip_first_write)
    monkeypatch.setattr(uh, "R1_KIT", p)
    rec = json.loads(p.read_text())
    assert uh.R1_KIT_SHA256 == R1_FROZEN
    assert _kit_digest(rec["writes"])[:12] == DRIFTED != R1_FROZEN[:12]
    assert uh.main([*RUN, "--image", image]) == 2
    err = capsys.readouterr().err
    assert err.strip() == (f"uart_host: REFUSED -- r1-kit.json hashes to {DRIFTED}, "
                           f"not R1's frozen kit {R1_FROZEN[:12]}")


@pytest.mark.parametrize("image", ["r1", "r2"])
def test_unchanged_copy_through_the_same_seam_exits_0(tmp_path, monkeypatch, capsys, image):
    monkeypatch.setattr(uh, "R1_KIT", _kit_file(tmp_path, lambda rec: None))
    assert uh.main([*RUN, "--image", image]) == 0
    assert "preflight: FEASIBLE" in capsys.readouterr().out


def test_control_presets_only_drift_is_not_this_clis_refusal(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(uh, "R1_KIT", _kit_file(tmp_path, _drift_presets))
    with pytest.raises(dx.KitRefused, match="presets hash"):
        uh.image_sound_presets("r1")
    assert uh.main([*RUN, "--image", "r1"]) == 0
    assert "preflight: FEASIBLE" in capsys.readouterr().out


def test_control_corrupt_kit_file_is_an_execution_error_not_a_refusal(tmp_path, monkeypatch):
    p = tmp_path / "r1-kit.json"
    p.write_text("{not json")
    monkeypatch.setattr(uh, "R1_KIT", p)
    with pytest.raises(json.JSONDecodeError):
        uh.main([*RUN, "--image", "r1"])


def test_control_plain_runtime_error_at_the_kit_read_is_not_a_refusal(monkeypatch):
    def boom():
        raise RuntimeError("plain runtime error at the kit read")
    monkeypatch.setattr(uh, "r1_kit", boom)
    with pytest.raises(RuntimeError, match="plain runtime error") as exc:
        uh.main([*RUN, "--image", "r1"])
    assert not isinstance(exc.value, dx.KitRefused)


def test_this_modules_own_refusal_at_the_phrase_read_exits_2(monkeypatch, capsys):
    def refuse(fixture, image):
        raise uh.Refused("the phrase cannot be built")
    monkeypatch.setattr(uh, "phrase_static_and_events", refuse)
    assert uh.main([*RUN, "--image", "r1"]) == 2
    assert capsys.readouterr().err.strip() == "uart_host: REFUSED -- the phrase cannot be built"


def test_control_release_image_frozen_kit_drift_is_refused_the_same_way(monkeypatch, capsys):
    live = dx.kit_808()
    a, v = live[0]
    monkeypatch.setattr(dx, "kit_808", lambda: [(a, v ^ 1)] + live[1:])
    assert uh.main([*RUN, "--image", "release"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("uart_host: REFUSED -- kit_808_rev14() hashes to ")
    assert "Traceback" not in err


@pytest.mark.parametrize("mode,image,exit_code", [
    ("kit-drift", "r1", 2), ("kit-drift", "r2", 2),
    ("corrupt", "r1", 1), ("runtime-error", "r1", 1)])
def test_process_exit_status(tmp_path, mode, image, exit_code):
    if mode == "kit-drift":
        kit = _kit_file(tmp_path, _flip_first_write)
    elif mode == "corrupt":
        kit = tmp_path / "r1-kit.json"
        kit.write_text("{not json")
    else:
        kit = tmp_path / "unused"
    driver = tmp_path / "drive.py"
    driver.write_text(DRIVER)
    proc = subprocess.run([sys.executable, "-I", str(driver), str(ROOT), str(kit), mode, image],
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == exit_code, proc.stderr
    assert ("Traceback" in proc.stderr) == (exit_code == 1)
    assert ("uart_host: REFUSED -- " in proc.stderr) == (exit_code == 2)
    if exit_code == 2:
        assert (f"r1-kit.json hashes to {DRIFTED}, "
                f"not R1's frozen kit {R1_FROZEN[:12]}") in proc.stderr
