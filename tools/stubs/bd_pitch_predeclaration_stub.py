"""START RED stub for tools/bd_pitch_predeclaration.py (#557): the right names,
no behaviour.  `check` accepts anything, the rule evaluates to 0 and never
refuses.  BPP_IMPL=bd_pitch_predeclaration_stub PYTHONPATH=tools/stubs runs
tools/test_bd_pitch_predeclaration.py against it; every control must fail."""
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
RECORD = ROOT / "docs" / "bd-pitch-predeclaration.json"


class Refused(RuntimeError):
    pass


def check(rec):
    return []


def minimum_improvement_cents(rec, baseline):
    return 0.0


def satisfiable(rec, baseline):
    return {"satisfiable": True}


def load_baseline(path):
    return json.loads(pathlib.Path(path).read_text()) if pathlib.Path(path).is_file() else {}


def main(argv=None):
    return 0
