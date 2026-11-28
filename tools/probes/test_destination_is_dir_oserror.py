"""Rule-8 controls for the `--json`/`--out` destination guards (#2100, PR #2130 review).

Each probe's destination helper asks `path.is_dir()` before `mkdir`.  Python 3.12's
`Path.is_dir()` swallows only ENOENT/ENOTDIR/EBADF/ELOOP and RE-RAISES EACCES (an
unsearchable ancestor) and ENAMETOOLONG -- so with `is_dir()` outside the `try`, an
uncreatable destination exited 1 with a traceback instead of REFUSING (exit 2).

The input that defeats the guard is an `is_dir()` that raises.  It is injected here by
patching `pathlib.Path.is_dir`, so the control is the same under root (where a 0o000
directory does not deny) and as a user.  An overlong component is the second, natural
input: it raises ENAMETOOLONG even as root.

The second control: on the two `str`-typed CLIs `--json ""` parsed to `""`, which
`if args.json:` read as "no destination" -- exit 0, no record.
"""
import errno
import importlib
import os
import pathlib
import sys

import pytest

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))

# (module, helper, how a refusal presents: "str" = returned reason, "raise" = Refused)
HELPERS = [
    ("audio_distance_floor", "_prepare_destination", "str"),
    ("balance_input_invariance", "_require_writable", "raise"),
    ("cymbal_400_production_tail", "_require_writable_out", "raise"),
    ("dc_centroid_gate_qualification", "_prepare_destination", "str"),
    ("dc_t20_gate_qualification", "_prepare_destination", "str"),
    ("dominant_period_octave", "_prepare_destination", "str"),
    ("fold_rate_sweep", "_prepare_destination", "str"),
]
IDS = [m for m, _, _ in HELPERS]


def _call(mod, helper, how, dest):
    fn = getattr(mod, helper)
    if how == "str":
        return fn(dest)
    try:
        fn(dest)
    except mod.Refused as exc:
        return str(exc)
    return None


@pytest.mark.parametrize("modname,helper,how", HELPERS, ids=IDS)
@pytest.mark.parametrize("err", [errno.EACCES, errno.ENAMETOOLONG])
def test_an_is_dir_that_raises_is_a_refusal_not_a_crash(modname, helper, how, err, tmp_path, monkeypatch):
    mod = importlib.import_module(modname)

    def boom(self, *a, **k):
        raise OSError(err, os.strerror(err))

    monkeypatch.setattr(pathlib.Path, "is_dir", boom)
    dest = pathlib.Path(tmp_path / "locked" / "sub" / "out.json")
    dest = str(dest) if modname.startswith("dc_") else dest
    reason = _call(mod, helper, how, dest)
    assert reason, f"{modname}.{helper}: is_dir() raised {os.strerror(err)} and the guard did not refuse"


@pytest.mark.parametrize("modname,helper,how", HELPERS, ids=IDS)
def test_an_overlong_component_is_refused_even_as_root(modname, helper, how, tmp_path):
    mod = importlib.import_module(modname)
    dest = tmp_path / ("x" * 300) / "out.json"
    dest = str(dest) if modname.startswith("dc_") else dest
    assert _call(mod, helper, how, dest), f"{modname}.{helper}: ENAMETOOLONG destination was not refused"


@pytest.mark.parametrize("modname,helper,how", HELPERS, ids=IDS)
def test_a_directory_given_as_the_file_is_still_refused(modname, helper, how, tmp_path):
    mod = importlib.import_module(modname)
    dest = str(tmp_path) if modname.startswith("dc_") else tmp_path
    assert "is a directory" in _call(mod, helper, how, dest)


@pytest.mark.parametrize("modname", ["dc_centroid_gate_qualification", "dc_t20_gate_qualification"])
def test_an_empty_json_destination_is_refused_not_skipped(modname, monkeypatch, capsys):
    mod = importlib.import_module(modname)
    monkeypatch.setattr(mod, "case", lambda *a, **k: pytest.fail("a refused run must not measure"))
    assert mod.main(["--json", ""]) == 2
    assert "REFUSED" in capsys.readouterr().out
