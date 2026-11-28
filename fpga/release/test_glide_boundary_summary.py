"""glide_boundary --summarize refuses a run identity it cannot read (#1620).

The wrapper run's identity is read with [] and a partial record was a KeyError
traceback, exit 1 -- the benches' code for "compared and differed". Each
control goes through main() with the committed identity as the record, and the
summary lands under a scratch HERE, never in evidence/."""
import json
import shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE.parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import glide_boundary as gb            # noqa: E402

CASE = "accept-default"
COMMITTED = Path(gb.HERE) / "evidence" / "glide-boundary" / CASE   # gb.HERE is a str


def _committed_identity():
    return json.loads((COMMITTED / "run_identity.json").read_text())


def _summarize(monkeypatch, tmp_path, capsys, ident):
    """`main()` over a run_all record holding the one wrapper run whose
    identity is `ident`, under a scratch ROOT. The identity is the reader's
    real input; the replay that produced it is not exercised (it costs an
    iverilog run and has its own tests)."""
    d = tmp_path / "root" / "build" / "release-glide" / f"wrapper-{CASE}" / CASE
    d.mkdir(parents=True)
    (d / "run_identity.json").write_text(json.dumps(ident))
    for f in ("verification.json", "transcript.txt"):
        shutil.copy(COMMITTED / f, d)
    for f in ("capture.cmds", "capture.plan.json"):    # summarize() copies these from d's parent
        shutil.copy(COMMITTED / f, d.parent)
    runs = tmp_path / "run_all.json"
    runs.write_text(json.dumps([{"cmd": f"glide_boundary.py --level wrapper --case {CASE}",
                                 "rc": 0, "state": "PASS", "out": "PASS"}]))
    monkeypatch.setattr(gb, "ROOT", tmp_path / "root")
    monkeypatch.setattr(gb, "HERE", tmp_path / "here")
    code = gb.main(["--summarize", str(runs)])
    return code, capsys.readouterr().out


def test_the_committed_identity_carries_the_key_the_summary_reads():
    # the guard below must be satisfiable against the record that ships
    assert _committed_identity()["identity"] is not None


def test_an_intact_substituted_identity_is_summarised_not_refused(monkeypatch, tmp_path, capsys):
    # one run of thirteen: not every expectation is met, which is a verdict
    # (exit 1). The copy alone refuses nothing (rule 8).
    code, out = _summarize(monkeypatch, tmp_path, capsys, _committed_identity())
    assert code == 1 and "REFUSED" not in out, out
    assert (tmp_path / "here" / "evidence" / "glide-boundary" / "summary.json").exists()


def test_an_identity_without_identity_is_refused(monkeypatch, tmp_path, capsys):
    # before the repair: `KeyError: 'identity'`, a traceback, exit 1
    ident = _committed_identity()
    del ident["identity"]
    code, out = _summarize(monkeypatch, tmp_path, capsys, ident)
    assert code == 2 and "REFUSED" in out and "'identity'" in out, out
    # and nothing was reported as data
    assert not (tmp_path / "here" / "evidence" / "glide-boundary" / "summary.json").exists()


@pytest.mark.parametrize("ident,needle", [({}, "'identity'"), ({"identity": None}, "'identity'"),
                                          ([], "not a JSON object"), (None, "not a JSON object")],
                         ids=["empty", "null-identity", "list", "null"])
def test_an_identity_that_cannot_be_read_is_refused(monkeypatch, tmp_path, capsys, ident, needle):
    code, out = _summarize(monkeypatch, tmp_path, capsys, ident)
    assert code == 2 and "REFUSED" in out and needle in out, out
