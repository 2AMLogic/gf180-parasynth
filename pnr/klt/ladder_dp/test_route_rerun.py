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

import pathlib
import sys

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
    assert any(name.endswith("ladder_dp.def") for name in route_rerun.KEPT_ARTEFACTS)
    assert any(name.endswith("ladder_dp_route.odb") for name in route_rerun.KEPT_ARTEFACTS)
