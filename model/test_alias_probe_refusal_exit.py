"""model/test_alias_probe_refusal_exit.py -- the probe's one refusal reaches
a caller as REFUSED, never as a result (issue #1807).

THE DEFECT, AS MEASURED ON main @ 03148399 (2026-11-28, red-first run of the
control below): `alias_probe.main` validates its estimator against three
known-answer signals (a band-limited saw with no inharmonic content, a
planted comb of known share, a drop-decimated saw whose alias content is
closed-form), and when that validation fails it prints "REFUSED: the
estimator did not reproduce a known alias content. No measurement below is
quoted." -- and returned 1.  tools/exit_contract.py reserves 1 for a
measurement that DISAGREED; a measuring instrument that failed its own
known-answer check is a PRECONDITION not met, nothing was measured, and the
probe says so in its own text.  tools/run_all.py rendered it as a RESULT.

WHAT IS DRIVEN.  `main()` for real, with `validate_estimator` returning a
row that is out of tolerance and every measurement function below the gate
replaced by a bomb: if the probe ever quotes a measurement after its
validation failed, the bomb says so instead of a number looking like data.
`provenance` is the real one (read-only git and file hashes).

Exit status is read from tools/exit_contract.py, never retyped.
"""
import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "audition"))
sys.path.append(str(ROOT / "tools"))          # last: it must shadow nothing model/ imports
import exit_contract                                             # noqa: E402

_spec = importlib.util.spec_from_file_location("alias_probe", ROOT / "model" / "alias_probe.py")
ap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ap)

MEASUREMENTS = ("chain_a", "chain_b", "report_chain_a", "report_blep_normalisation",
                "decimator_order_sweep", "report_decimator_orders")

# one planted-comb row read 10 dB off its known share; tolerance is 0.5 dB
OUT_OF_TOLERANCE = [dict(kind="planted", note=64, f0=1.0, floor=-90.0, want=-20.0,
                         got=-10.0, err=10.0)]


def test_an_estimator_that_fails_its_known_answer_check_refuses(tmp_path, monkeypatch, capsys):
    def bomb(*_a, **_k):
        pytest.fail("a measurement was quoted after the estimator's validation failed")
    for name in MEASUREMENTS:
        monkeypatch.setattr(ap, name, bomb)
    monkeypatch.setattr(ap, "validate_estimator", lambda: OUT_OF_TOLERANCE)

    assert ap.main(["--out", str(tmp_path / "artefact")]) == exit_contract.REFUSED

    out = capsys.readouterr().out
    assert "REFUSED: the estimator did not reproduce a known alias content" in out
    assert "No measurement below is quoted." in out
    assert "OUT OF TOLERANCE" in out            # the row that refused, still shown
    assert not list((tmp_path / "artefact").rglob("*.npz"))   # and nothing was written


def test_the_validation_row_is_what_decides(tmp_path, monkeypatch, capsys):
    """The control on the control: the same drive with the row WITHIN
    tolerance does not take the refusal path -- it reaches the measurements
    (the bomb fires), so the refusal above is the row's doing, not the
    drive's."""
    def bomb(*_a, **_k):
        raise RuntimeError("measurement reached")
    for name in MEASUREMENTS:
        monkeypatch.setattr(ap, name, bomb)
    within = [dict(r, got=r["want"], err=0.0) for r in OUT_OF_TOLERANCE]
    monkeypatch.setattr(ap, "validate_estimator", lambda: within)

    with pytest.raises(RuntimeError, match="measurement reached"):
        ap.main(["--out", str(tmp_path / "artefact")])
    assert "REFUSED" not in capsys.readouterr().out


def test_the_module_reads_the_status_from_the_contract():
    src = (ROOT / "model" / "alias_probe.py").read_text()
    assert "import exit_contract" in src
    i = src.index("REFUSED: the estimator did not reproduce a known alias content")
    assert "return exit_contract.REFUSED" in src[i:i + 300]
