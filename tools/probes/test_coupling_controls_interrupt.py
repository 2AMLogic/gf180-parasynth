"""#615: `run_props` recorded a KeyboardInterrupt as a property's `error:` outcome
and carried on, so Ctrl-C could not stop a run."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "model"))
cc = pytest.importorskip("coupling_controls")


def _with(monkeypatch, exc):
    def prop():
        raise exc
    monkeypatch.setattr(cc.T, "test_coupling_fake", prop, raising=False)
    monkeypatch.setattr(cc, "PROPS", ["test_coupling_fake"])


@pytest.mark.parametrize("exc", [KeyboardInterrupt(), SystemExit(1)])
def test_an_interrupt_is_not_recorded_as_a_property_outcome(monkeypatch, exc):
    _with(monkeypatch, exc)
    with pytest.raises(type(exc)):
        cc.run_props()


def test_an_unanticipated_error_is_still_recorded_with_its_type(monkeypatch):
    _with(monkeypatch, KeyError("k"))
    assert cc.run_props()["test_coupling_fake"].startswith("error:KeyError")
