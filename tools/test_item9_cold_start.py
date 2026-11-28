"""Static checks on the item 9 cold-start list (the run itself is ~8 min, attested
by `klt signoff --attest 9`, not repeated here)."""
import json
import pathlib
import shlex
import subprocess
import sys

import item9_cold_start as m
import pytest

ROOT = m.ROOT


def test_every_command_names_an_existing_script():
    for c in m.BENCHES + m.CONTROLS:
        assert (ROOT / shlex.split(c)[0]).is_file(), c


def test_every_control_inverts_its_verdict():
    # Without --expect-fail a control that is "caught" (exit 1) reads as a failure and
    # one that runs clean (exit 0) reads as a pass: the polarity is the whole point.
    for c in m.CONTROLS:
        assert "--expect-fail" in c, c


def test_no_bench_is_a_control_and_none_is_duplicated():
    assert not any("--inject" in c or "--expect-fail" in c for c in m.BENCHES)
    assert len(set(m.BENCHES + m.CONTROLS)) == len(m.BENCHES + m.CONTROLS)


def test_inventory_declares_this_command_and_lists_every_script_it_runs():
    inv = json.loads((ROOT / "manifests/inventories/item-9.json").read_text())
    assert inv["cold_start"]["argv"] == ["python3", "tools/item9_cold_start.py"]
    listed = set(inv["testbenches"])
    for c in m.BENCHES + m.CONTROLS:
        assert shlex.split(c)[0] in listed, c


def _fake_tree(tmp_path, monkeypatch, *, venv_present=False, bootstrap_rc=0):
    """An empty tree for the cold start, and a subprocess.run that records every
    command and stands in for the tools: `uv venv` / `-m venv` and a succeeding
    `trial_env.py bootstrap --venv` each create the venv, and only bootstrap
    creates the pinned iverilog."""
    venv = tmp_path / "build" / "item9-venv"
    py = venv / "bin" / "python"
    pinned = tmp_path / "build" / "oss-cad-pinned" / "oss-cad-suite" / "bin"
    monkeypatch.setattr(m, "ROOT", tmp_path)
    monkeypatch.setattr(m, "VENV", venv)
    monkeypatch.setattr(m, "PINNED_BIN", pinned)
    if venv_present:
        py.parent.mkdir(parents=True)
        py.write_text("#!/bin/sh\n")
    calls = []

    def mk_venv():
        py.parent.mkdir(parents=True, exist_ok=True)
        py.write_text("#!/bin/sh\n")

    def run(cmd, **_):
        cmd = [str(c) for c in cmd]
        calls.append(cmd)
        if cmd[1:2] == ["venv"] or cmd[1:3] == ["-m", "venv"]:
            mk_venv()
        bootstrap = "bootstrap" in cmd
        if bootstrap and bootstrap_rc == 0:
            mk_venv()
            pinned.mkdir(parents=True, exist_ok=True)
            (pinned / "iverilog").write_text("#!/bin/sh\n")
        return subprocess.CompletedProcess(cmd, bootstrap_rc if bootstrap else 0)

    monkeypatch.setattr(m.subprocess, "run", run)
    return venv, py, calls


@pytest.mark.parametrize("venv_present", [False, True], ids=["venv-absent", "venv-present"])
def test_trial_env_bootstrap_is_the_first_child_and_the_only_venv_creator(tmp_path, monkeypatch,
                                                                         venv_present):
    """#1583: the cold start no longer pre-creates build/item9-venv. Its first
    child is `trial_env.py bootstrap --venv`, run by THIS interpreter, and
    trial_env creates the venv when it is absent (#1582 made that correct)."""
    venv, py, calls = _fake_tree(tmp_path, monkeypatch, venv_present=venv_present)
    assert m.main() == 0, calls
    bootstrap = [sys.executable, str(tmp_path / "tools" / "trial_env.py"),
                 "bootstrap", "--venv", str(venv)]
    why = "the cold start ran something before bootstrap -- the pre-create workaround is back"
    assert calls[0] == bootstrap, (why, calls)
    assert [c for c in calls if "bootstrap" in c] == [bootstrap]
    assert any(c[0] == str(py) and c[1] == "tools/run_all.py" for c in calls), calls


def test_a_refusing_bootstrap_refuses_the_cold_start_before_any_bench(tmp_path, monkeypatch,
                                                                     capsys):
    """trial_env could not create or satisfy the venv: REFUSED (exit 2), and no
    bench runs under an environment nobody vouched for."""
    _, _, calls = _fake_tree(tmp_path, monkeypatch, bootstrap_rc=2)
    assert m.main() == 2
    assert "REFUSED -- tools/trial_env.py bootstrap" in capsys.readouterr().err
    assert not any("run_all.py" in c for c in calls), calls
