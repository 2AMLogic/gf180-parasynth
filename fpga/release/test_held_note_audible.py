"""held_note_audible refuses a CLI capture plan it cannot read (#1620).

--image tree|r1|r2 reads the plan the CLI has just written with [], and a
partial record was a KeyError traceback, exit 1. Each control goes through
main() with the plan the shipped CLI actually emits as the record: the CLI
writes it, the test damages it before the tool reads it. The replay is never
reached -- the read refuses first, and the clean-run control answers the replay
with NO VERDICT rather than running a simulator."""
import importlib
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE.parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import held_note_audible as hna       # noqa: E402

WHAT = "the CLI's capture plan at"


def _main(monkeypatch, tmp_path, capsys, mutate):
    capture = hna.capture

    def capture_then_mutate(preset, note, prefix, legacy=False, image="release", fixture="none"):
        rc = capture(preset, note, prefix, legacy=legacy, image=image, fixture=fixture)
        if rc == 0:
            path = Path(prefix + ".plan.json")
            path.write_text(json.dumps(mutate(json.loads(path.read_text()))))
        return rc
    monkeypatch.setattr(hna, "capture", capture_then_mutate)
    vub = importlib.import_module("verify_uart_bridge")
    monkeypatch.setattr(vub, "main", lambda argv: 2)     # NO VERDICT: no simulator here
    code = hna.main(["--preset", "default", "--image", "tree", "--outdir", str(tmp_path / "out")])
    return code, capsys.readouterr().out


def test_the_cli_emits_a_plan_carrying_the_key_the_tool_reads(tmp_path):
    # the guard below must be satisfiable against the record the CLI writes
    prefix = str(tmp_path / "capture")
    assert hna.capture("default", 45, prefix, image="tree") == 0
    assert json.loads(Path(prefix + ".plan.json").read_text())["rows"]


def test_an_intact_plan_is_read_not_refused(monkeypatch, tmp_path, capsys):
    # the control below damages the plan; the damage must be what refuses it
    # (rule 8). Intact, the read passes and the replay's verdict is reported.
    code, out = _main(monkeypatch, tmp_path, capsys, lambda plan: plan)
    assert code == 2 and "NO VERDICT" in out and "REFUSED" not in out, out
    assert (tmp_path / "out" / "held_note_audible.json").exists()


def test_a_plan_without_rows_is_refused(monkeypatch, tmp_path, capsys):
    # before the repair: `KeyError: 'rows'`, a traceback, exit 1
    code, out = _main(monkeypatch, tmp_path, capsys,
                      lambda plan: {k: v for k, v in plan.items() if k != "rows"})
    assert code == 2 and "REFUSED" in out and WHAT in out and "'rows'" in out, out
    # and nothing was reported as data
    assert not (tmp_path / "out" / "held_note_audible.json").exists()


@pytest.mark.parametrize("plan,needle", [({}, "'rows'"), ({"rows": None}, "'rows'"),
                                         ([], "not a JSON object"), (None, "not a JSON object")],
                         ids=["empty", "null-rows", "list", "null"])
def test_a_plan_that_cannot_be_read_is_refused(monkeypatch, tmp_path, capsys, plan, needle):
    code, out = _main(monkeypatch, tmp_path, capsys, lambda p: plan)
    assert code == 2 and "REFUSED" in out and WHAT in out and needle in out, out
