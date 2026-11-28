#!/usr/bin/env python3
"""tools/r0_reference.py: the replay's run identity is validated where it is
read (#1604, #1623).

`render_one()` reads `rid["identity"]` with `[]`. A run identity without that
key was already REFUSED before #1623 -- incidentally, by `identity_problems()`
on the next line, whose `.get("identity") or {}` reads as "no source in the
replay's compiled set" -- so the refusal did not name the key, and it rested
on the manifest naming at least one simulated source. The guard now sits at the
read, so the control below is red on the pre-repair tree by the key it names.

Through the tool's own `main()`: the host CLI's dry run, the release manifest
and the reader are real; the RTL replay (minutes of iverilog) is the only thing
stubbed, and it leaves a run identity on disk as the real one does.
"""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import r0_reference as rr  # noqa: E402
import verify_uart_bridge as vub  # noqa: E402

COMMAND = "held-default"      # a pinned command with no rolling fixture: one replay
SHIPPED = ROOT / "fpga" / "reports" / "r1-candidate" / "rtl-run-identities"


def _render(monkeypatch, tmp_path, capsys, run_identity):
    def replay(prefix, replay_dir, **kw):
        pathlib.Path(replay_dir).mkdir(parents=True, exist_ok=True)
        (pathlib.Path(replay_dir) / "run_identity.json").write_text(json.dumps(run_identity))
        return {}                      # never analysed: the read under test comes first

    # this tree is not the image's staged sources; that refusal has its own path
    monkeypatch.setattr(rr, "image_source_problems", lambda manifest, root=rr.ROOT: [])
    monkeypatch.setattr(vub, "simulate_replay", replay)
    rc = rr.main(["--image", "r0", "render", "--out", str(tmp_path), "--commands", COMMAND])
    return rc, capsys.readouterr().out


def test_the_shipped_run_identities_carry_the_key_the_render_reads():
    """The guard must be satisfiable against the record the replay writes."""
    shipped = sorted(SHIPPED.glob("*.run_identity.json"))
    assert shipped
    for p in shipped:
        assert json.loads(p.read_text()).get("identity") is not None, p.name


def test_a_complete_run_identity_passes_the_key_guard(monkeypatch, tmp_path, capsys):
    # `identity` present: the key guard is satisfied and the NEXT check speaks
    # (an identity that compiled nothing is not the image's sources)
    rc, out = _render(monkeypatch, tmp_path, capsys, {"identity": {"sources": {}, "roms": {}}})
    assert rc == 2 and "REFUSED" in out and "did not compile the image's sources" in out
    assert "required key" not in out


def test_a_run_identity_without_identity_is_refused_and_names_the_key(monkeypatch, tmp_path, capsys):
    # before #1623: REFUSED, but as "not in the replay's compiled set" -- the key unnamed
    rc, out = _render(monkeypatch, tmp_path, capsys, {"schema": "rtl-run-identity/1"})
    assert rc == 2 and "REFUSED" in out and "'identity'" in out
    assert not (tmp_path / f"{COMMAND}.json").exists()      # no reference was written


def test_a_run_identity_with_null_identity_is_refused(monkeypatch, tmp_path, capsys):
    rc, out = _render(monkeypatch, tmp_path, capsys, {"identity": None})
    assert rc == 2 and "REFUSED" in out and "'identity'" in out


@pytest.mark.parametrize("run_identity", [[], 5, None])
def test_a_run_identity_that_is_not_an_object_is_refused(monkeypatch, tmp_path, capsys, run_identity):
    rc, out = _render(monkeypatch, tmp_path, capsys, run_identity)
    assert rc == 2 and "REFUSED" in out and "not a JSON object" in out
