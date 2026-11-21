"""Tests for the #1006 DEF re-run wrapper.

The wrapper is orchestration around the committed driver, and the part that
decides is small: which PDK root it will run against, and which artefacts it
promises. Both are pinned here against fakes, because a wrapper that answers
when its preconditions failed is the failure mode this repository keeps
meeting: a wrong PDK pin would make the re-run a second variable (#808
correction 6), and a driver that exits 0 with no DEF on disk would promise a
DEF question it cannot answer.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import route_rerun  # noqa: E402

PIN = route_rerun.PDK_PIN
CELL_LIB = "gf180mcu_fd_sc_mcu7t5v0"


def _fake_pdk_store(tmp_path: pathlib.Path, pin: str = PIN) -> pathlib.Path:
    root = (
        tmp_path / "store" / "ciel" / "gf180mcu" / "versions" / pin
        / route_rerun.PDK / "libs.ref" / CELL_LIB
    )
    root.mkdir(parents=True)
    return tmp_path / "store"


def test_resolve_pdk_root_finds_the_version_dir(tmp_path: pathlib.Path) -> None:
    store = _fake_pdk_store(tmp_path)
    root, refusal = route_rerun.resolve_pdk_root(store, PIN, route_rerun.PDK)
    assert refusal is None
    assert root is not None
    assert root.name == PIN


def test_resolve_pdk_root_refuses_a_missing_pin(tmp_path: pathlib.Path) -> None:
    """A pin ciel did not install must refuse, never fall back to another one."""
    _fake_pdk_store(tmp_path, pin="f6eeac7d" + "0" * 56)
    root, refusal = route_rerun.resolve_pdk_root(tmp_path / "store", PIN, route_rerun.PDK)
    assert root is None
    assert refusal is not None and PIN in refusal


def test_resolve_pdk_root_refuses_a_pin_without_the_cell_library(tmp_path: pathlib.Path) -> None:
    """The right pin layout without the 7t library is not runnable."""
    store = tmp_path / "store" / "ciel" / "gf180mcu" / "versions" / PIN
    store.mkdir(parents=True)  # version dir exists, cell library does not
    root, refusal = route_rerun.resolve_pdk_root(tmp_path / "store", PIN, route_rerun.PDK)
    assert root is None
    assert refusal is not None and CELL_LIB in refusal


def test_missing_artefacts_lists_everything_on_an_empty_tree(tmp_path: pathlib.Path) -> None:
    got = route_rerun.missing_artefacts(tmp_path)
    assert sorted(got) == sorted(route_rerun.KEPT_ARTEFACTS)
    assert len(got) == len(set(got)), "no duplicates in the promised list"


def test_missing_artefacts_empty_when_all_present(tmp_path: pathlib.Path) -> None:
    for name in route_rerun.KEPT_ARTEFACTS:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    assert route_rerun.missing_artefacts(tmp_path) == []


def test_the_def_is_among_the_kept_artefacts() -> None:
    """The wrapper exists to keep the DEF; drop it from the list and nothing here works."""
    assert any(name.endswith("ladder_dp.def") for name in route_rerun.ROUTE_ARTEFACTS)
    assert any(name.endswith("ladder_dp_route.odb") for name in route_rerun.ROUTE_ARTEFACTS)


@pytest.mark.parametrize("field", ["gds_path", "verilog_path"])
def test_full_route_requires_each_signoff_input(tmp_path: pathlib.Path, field: str) -> None:
    response = json.loads((route_rerun.HERE / "par_response.json").read_text())
    emitted = pathlib.Path(response[field]).relative_to(pathlib.Path(response["def_path"]).parent)
    name = str(pathlib.Path(".klt/place-and-route") / emitted)
    for other in route_rerun.ROUTE_ARTEFACTS:
        path = tmp_path / other
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")

    target = tmp_path / name
    target.unlink(missing_ok=True)
    assert route_rerun.missing_artefacts(tmp_path) == [name]
    target.touch()
    assert route_rerun.missing_artefacts(tmp_path) == [name]
    target.write_bytes(b"routed output")
    assert route_rerun.missing_artefacts(tmp_path) == []
    assert name not in route_rerun.CTS_ARTEFACTS


def test_the_cts_mode_keeps_the_stage_tcls_and_pre_route_defs() -> None:
    """The fill-provenance run lives on the stage records the route run drops."""
    kept = route_rerun.CTS_ARTEFACTS
    assert any(name.endswith("pnr_ladder_dp_floorplan.tcl") for name in kept)
    assert any(name.endswith("pnr_ladder_dp_cts.tcl") for name in kept)
    assert any(name.endswith("ladder_dp.cts.def") for name in kept)
    assert not any(name.endswith("ladder_dp.def") for name in kept), (
        "the final route DEF does not exist in a cts-bounded run"
    )


@pytest.fixture
def staged_route(tmp_path, monkeypatch):
    """A fresh batch shard including the packet copied from the route job."""
    for name in (
        "route_rerun.py",
        "drc_request.json",
        "lvs_request.json",
        "erc_request.sh",
        "erc_supply_spec.json",
    ):
        shutil.copy(route_rerun.HERE / name, tmp_path)
    report = ".klt/place-and-route/ladder_dp_route_drc.rpt"
    artefacts = {}
    for name in (*route_rerun.SIGNOFF_INPUTS, ".klt/place-and-route/ladder_dp.def", report):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
        artefacts[name] = {"sha256": route_rerun.sha256_file(path)}
    (tmp_path / "route_def_manifest.json").write_text(
        json.dumps(
            {
                "pdk_pin": PIN,
                "pdk": route_rerun.PDK,
                "artefacts": artefacts,
            }
        )
    )
    (tmp_path / "route_drc_extract.json").write_text(
        json.dumps(
            {
                "inputs": {"drc_report": {"sha256": artefacts[report]["sha256"]}},
            }
        )
    )
    _fake_pdk_store(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, source in {
        "ciel": "pass",
        "klt": (
            "import json, os, sys\n"
            "if sys.argv[1] == 'version': print('klt fixture')\n"
            "else:\n"
            "    print(json.dumps({'argv': sys.argv[1:], 'root': os.environ['PDK_ROOT']}))\n"
            "    print('diagnostic on stderr', file=sys.stderr)\n"
            "    sys.exit(int(os.environ.get('PROBE_TEST_EXIT', '0')))"
        ),
    }.items():
        exe = bin_dir / name
        exe.write_text(f"#!{sys.executable}\n{source}\n")
        exe.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    return tmp_path


def run_staged_probe(staged_route, verb):
    return subprocess.run(
        [
            sys.executable,
            str(staged_route / "route_rerun.py"),
            "--probe",
            verb,
            "--pdk-store",
            str(staged_route / "store"),
        ],
        cwd=staged_route.parent,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("verb", ["drc", "lvs", "erc"])
@pytest.mark.parametrize("returncode", [0, 3])
def test_probe_preserves_tool_output_and_exit(staged_route, monkeypatch, verb, returncode):
    monkeypatch.setenv("PROBE_TEST_EXIT", str(returncode))
    result = run_staged_probe(staged_route, verb)
    assert result.returncode == returncode, result.stderr
    assert "klt fixture" in result.stdout
    assert "diagnostic on stderr" in result.stderr
    report = json.loads((staged_route / ".klt" / f"{verb}_report.json").read_text())
    assert report["argv"][0] == verb
    assert report["argv"][-2:] == ["--format", "json"]
    assert pathlib.Path(report["root"]).name == PIN
    gds, netlist = route_rerun.SIGNOFF_INPUTS
    if verb == "erc":
        assert report["argv"][1:3] == [gds, "erc_supply_spec.json"]
    else:
        request = json.loads((staged_route / report["argv"][1]).read_text())
        if verb == "lvs":
            assert request["reference"]["netlist"] == netlist
            assert request["layout"]["def_pins"] == ".klt/place-and-route/ladder_dp.def"
            assert request["layout"]["file"] == gds
        else:
            assert request["file"] == gds


@pytest.mark.parametrize(
    "changed", ["ladder_dp.v", "ladder_dp.gds", "ladder_dp.def", "ladder_dp_route_drc.rpt"]
)
def test_probe_refuses_swapped_route_inputs(staged_route, changed):
    (staged_route / ".klt/place-and-route" / changed).write_bytes(b"from another route")
    result = run_staged_probe(staged_route, "lvs")
    assert result.returncode == 3
    assert "REFUSED" in result.stderr and changed in result.stderr
    assert not (staged_route / ".klt/lvs_report.json").exists()


def test_probe_refuses_a_consistently_rebound_but_different_route(staged_route):
    report = ".klt/place-and-route/ladder_dp_route_drc.rpt"
    (staged_route / report).write_bytes(b"report of a different route")
    path = staged_route / route_rerun.MANIFEST_NAME
    manifest = json.loads(path.read_text())
    manifest["artefacts"][report]["sha256"] = route_rerun.sha256_file(staged_route / report)
    path.write_text(json.dumps(manifest))
    result = run_staged_probe(staged_route, "drc")
    assert result.returncode == 3
    assert "original" in result.stderr and report in result.stderr
    assert not (staged_route / ".klt/drc_report.json").exists()


def test_probe_refuses_an_unbound_gds_even_when_the_file_exists(staged_route):
    path = staged_route / route_rerun.MANIFEST_NAME
    manifest = json.loads(path.read_text())
    del manifest["artefacts"][".klt/place-and-route/ladder_dp.gds"]
    path.write_text(json.dumps(manifest))
    result = run_staged_probe(staged_route, "erc")
    assert result.returncode == 3
    assert "REFUSED" in result.stderr and "ladder_dp.gds" in result.stderr


def test_probe_refuses_a_manifest_from_another_pdk_pin(staged_route):
    path = staged_route / route_rerun.MANIFEST_NAME
    manifest = json.loads(path.read_text())
    manifest["pdk_pin"] = "f6eeac7dad085ffcc829ccfd721f7b4ce39edcf7"
    path.write_text(json.dumps(manifest))
    result = run_staged_probe(staged_route, "drc")
    assert result.returncode == 3
    assert "REFUSED" in result.stderr and PIN in result.stderr
