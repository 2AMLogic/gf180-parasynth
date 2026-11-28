#!/usr/bin/env python3
"""Known-answer trace of the one walk row #1939's closing search left untraced:
`tools/pekonen_coloration_probe.py::build_residuals`, NARROWER (#2055).

Two callers, two handlers. `controls()` calls `build_residuals(mini, stale)`
under `except Refused`; `main()` calls `build_residuals(mini, ours)` under
`except (Refused, am.InsufficientEvidence)`. The walk lists the row because
they disagree. Whether `audio_measure.InsufficientEvidence` -- what a silent
oscillator render makes `harmonic_signature` raise; an AssertionError subclass
-- can leave the narrow one as a traceback is decided here by RUNNING both
paths with a stale `ours`, one fresh interpreter per case, with the silent
oscillator put below the tool the way `tools/test_insufficient_evidence_dispatch.py`
does it (#1763). The `--controls` cases go through the tool's real `__main__`
by `runpy`, with a direct invocation's argv; a case that must stale the
record calls `main([])` on the imported module, because `runpy` executes a
second copy of the probe whose `load_reference` a seam on the imported one
cannot reach -- which is how this trace's first run of case F reported a
clean exit 0 for a record it had not staled.

A stale `ours` reaches the two handlers differently, as the probe is written:
`controls()` builds its own (h3 + 1 dB) inside the `except Refused` it is
testing, and `main()` reads the record -- so for `main()`'s path the record's
`ours` rows are staled by a seam on `load_reference`, the same + 1 dB. Every
seam proves it took before the tool runs; a seam that did not is REFUSED.

The lines the issue cites (223 and 289 on 616b8d63) are re-found by AST and
printed; a tree where the two handlers are not those two is REFUSED.

Exit 0 every case matched its known answer; 1 a case did not; 2 REFUSED (the
apparatus did not pass its own controls, a seam did not take, or the handlers
moved).
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exit_contract import FAIL, PASS, REFUSED          # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
PROBE = "tools/pekonen_coloration_probe.py"
ESCAPED = 77          # a child's own marker: the exception left controls()
HANDLERS = {"controls": [(223, "Refused")], "main": [(289, "(Refused, am.InsufficientEvidence)")]}

PRELUDE = "import sys\nsys.path[:0] = %r\n" % [os.path.join(ROOT, p) for p in ("model", "tools")]
CLI_CONTROLS = "import runpy\nsys.argv = [%r, '--controls']\nrunpy.run_path(%r, run_name='__main__')\n" % (PROBE, PROBE)
CLI_REPORT = "import runpy\nsys.argv = [%r]\nrunpy.run_path(%r, run_name='__main__')\n" % (PROBE, PROBE)
CALL_MAIN = "import pekonen_coloration_probe as pk\nsys.exit(pk.main([]))\n"
CALL_CONTROLS = (
    "import pekonen_coloration_probe as pk\n"
    "try:\n"
    "    c = pk.controls()\n"
    "except BaseException as e:\n"
    "    print('ESCAPED', type(e).__module__ + '.' + type(e).__qualname__,\n"
    "          isinstance(e, pk.Refused), e)\n"
    "    sys.exit(%d)\n"
    "print('CONTROLS', sorted(c.items()))\n" % ESCAPED
)
_SILENT = "    return np.zeros(n, dtype=np.int64)\n"
_PROBE_BOTH = (
    "_a = vf.OscFx('saw', blep=True).render(4800, vf.phase_inc(440.0))\n"
    "_b = vf.OscFx('saw', blep=True).render(4800, vf.phase_inc(440.0))\n"
)
OSC_SILENT = (
    "import numpy as np, voice_fx as vf\n"
    "def _render(self, n, inc):\n" + _SILENT +
    "vf.OscFx.render = _render\n"
    "_a = vf.OscFx('saw', blep=True).render(4800, vf.phase_inc(440.0))\n"
    "assert not np.any(_a), 'SEAM FAILED: the oscillator still renders'\n"
)
#: silent from the SECOND render on: control 5 renders once (note 33, then the
#: stale row refuses), so `controls()` passes and `main()`'s own call at :280
#: is the first to meet a silent render
OSC_SILENT_LATER = (
    "import numpy as np, voice_fx as vf\n"
    "_render, _n = vf.OscFx.render, [0]\n"
    "def _render_sometimes(self, n, inc):\n"
    "    _n[0] += 1\n"
    "    return np.zeros(n, dtype=np.int64) if _n[0] > 1 else _render(self, n, inc)\n"
    "vf.OscFx.render = _render_sometimes\n" + _PROBE_BOTH +
    "assert np.any(_a) and not np.any(_b), 'SEAM FAILED: the oscillator is not silent from the second render'\n"
    "_n[0] = 0\n"
)
OSC_FAULT = (
    "import voice_fx as vf\n"
    "def _render(self, n, inc):\n"
    "    raise AssertionError('unrelated oscillator fault')\n"
    "vf.OscFx.render = _render\n"
    "try:\n"
    "    vf.OscFx('saw', blep=True).render(4800, vf.phase_inc(440.0))\n"
    "except AssertionError as e:\n"
    "    assert str(e) == 'unrelated oscillator fault', 'SEAM FAILED: another assertion fired'\n"
    "else:\n"
    "    raise AssertionError('SEAM FAILED: the oscillator still renders')\n"
)
STALE_OURS = (
    "import pekonen_coloration_probe as pk\n"
    "_load = pk.load_reference\n"
    "def _stale(path=pk.RESULTS):\n"
    "    mini, ours = _load(path)\n"
    "    return mini, {n: dict(r, h3=r['h3'] + 1.0) for n, r in ours.items()}\n"
    "pk.load_reference = _stale\n"
    "_, _staled = pk.load_reference()\n"
    "_, _fresh = _load()\n"
    "assert all(abs(_staled[n]['h3'] - _fresh[n]['h3'] - 1.0) < 1e-9 for n in _fresh), "
    "'SEAM FAILED: the record was not staled'\n"
)


def _line(p, start):
    return next((l for l in p.stdout.splitlines() if l.startswith(start)), "")


def _n_controls(p):
    return sum(l.startswith("control ") for l in p.stdout.splitlines())


def _controls(p, n=None):
    lines = [l for l in p.stdout.splitlines() if l.startswith("control ")]
    ok = sum(l.endswith(": ok") for l in lines)
    return (p.returncode == PASS and len(lines) >= 8 and ok == len(lines) and n in (None, len(lines)),
            f"exit {p.returncode}; {ok}/{len(lines)} controls ok")


def _decision(p, n):
    line = _line(p, "DECISION") or "no DECISION line"
    return p.returncode == PASS and line.endswith("MEASURED, NOT TAKEN"), f"exit {p.returncode}; {line}"


def _returned(p, n):
    try:
        c = dict(eval(p.stdout.removeprefix("CONTROLS "), {"__builtins__": {}}))    # the child's own repr
    except (SyntaxError, ValueError):
        c = {}
    return p.returncode == 0 and bool(c) and all(c.values()), f"exit {p.returncode}; {p.stdout.strip()[:150]}"


def _escaped(p, n):
    ok = p.returncode == ESCAPED and _line(p, "ESCAPED") == (
        "ESCAPED audio_measure.InsufficientEvidence False harmonic_signature: silent")
    return ok, f"exit {p.returncode}; {_line(p, 'ESCAPED')!r}"


def _refused(prefix, controls_lines):
    """REFUSED exit 2 on `prefix`, no traceback, and exactly `controls_lines` control lines first."""
    def check(p, n):
        expected = n if controls_lines == "A" else controls_lines
        ok = (p.returncode == REFUSED and _line(p, "REFUSED").startswith(prefix)
              and "Traceback" not in p.stderr and _n_controls(p) == expected)
        return ok, (f"exit {p.returncode}; {_line(p, 'REFUSED')[:96]!r}; control lines printed: "
                    f"{_n_controls(p)}; traceback: {'Traceback' in p.stderr}")
    return check


def _fault(p, n):
    ok = p.returncode == FAIL and "unrelated oscillator fault" in p.stderr and "REFUSED" not in p.stdout
    return ok, (f"exit {p.returncode}; fault in stderr: {'unrelated oscillator fault' in p.stderr}; "
                f"REFUSED printed: {'REFUSED' in p.stdout}")


#: id, the path, the seams, the entry, the known answer, its check. A check's
#: second argument is the number of controls case A saw (None until then).
CASES = (
    ("A", "apparatus", (), CLI_CONTROLS, "exit 0, every injected control ok", _controls),
    ("B", "apparatus", (), CLI_REPORT, "exit 0, DECISION: MEASURED, NOT TAKEN (record 0023)", _decision),
    ("C", ":223 controls()", (), CALL_CONTROLS, "returned; stale_ours_refused True, every control True", _returned),
    ("D", ":223 controls()", (OSC_SILENT,), CALL_CONTROLS,
     "ESCAPED controls() as audio_measure.InsufficientEvidence, not a Refused", _escaped),
    ("E", ":223 via main()", (OSC_SILENT,), CLI_CONTROLS, "REFUSED exit 2, no control line, no traceback", _refused("REFUSED: harmonic_signature: silent", 0)),
    ("F", ":280 main()", (STALE_OURS,), CALL_MAIN, "REFUSED exit 2 naming the stale row, after the controls", _refused("REFUSED: ours h3 at note ", "A")),
    ("G", ":223 via main()", (STALE_OURS, OSC_SILENT), CALL_MAIN, "REFUSED exit 2, the estimator's refusal, no traceback", _refused("REFUSED: harmonic_signature: silent", 0)),
    ("I", ":280 main()", (OSC_SILENT_LATER,), CALL_MAIN, "REFUSED exit 2, the estimator's refusal at main's own call", _refused("REFUSED: harmonic_signature: silent", "A")),
    ("H", "control", (OSC_FAULT,), CLI_CONTROLS, "exit 1, the fault in stderr, no REFUSED", _fault),
)


def handlers():
    """{function: [(line, spelling)]} for every handler in controls() and main()."""
    with open(os.path.join(ROOT, PROBE)) as f:
        tree = ast.parse(f.read())
    out = {}
    for fn in tree.body:
        if isinstance(fn, ast.FunctionDef) and fn.name in HANDLERS:
            out[fn.name] = [(h.lineno, ast.unparse(h.type) if h.type else "except:")
                            for node in ast.walk(fn) if isinstance(node, ast.Try) for h in node.handlers]
    return out


def main(argv=None) -> int:
    found = handlers()
    for name, expected in HANDLERS.items():
        if found.get(name) != expected:
            print(f"REFUSED: {PROBE} {name}() handles {found.get(name)}, not the {expected} the issue describes")
            return REFUSED
    print(f"{PROBE}: controls() guards build_residuals with `except {HANDLERS['controls'][0][1]}` at "
          f":{HANDLERS['controls'][0][0]}; main() with `except {HANDLERS['main'][0][1]}` at :{HANDLERS['main'][0][0]}")
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    rows, n_controls, refused = [], None, False
    for cid, path, seams, entry, known, check in CASES:
        p = subprocess.run([sys.executable, "-c", PRELUDE + "".join(seams) + entry], cwd=ROOT, env=env,
                           capture_output=True, text=True, timeout=900)
        if "SEAM FAILED" in p.stdout + p.stderr:
            rows.append((cid, path, f"SEAM FAILED: {_line(p.stdout + p.stderr, 'AssertionError')[:70]}", known))
            refused = True
            continue
        ok, got = check(p, n_controls)
        if cid == "A":
            n_controls = _n_controls(p)
        rows.append((cid, path, ("ok: " if ok else "WRONG: ") + got, known))
        refused = refused or (not ok and cid in "AB")
    print("case  path               got                                                               known answer")
    for cid, path, outcome, known in rows:
        print(f"{cid:<5} {path:<18} {outcome:<123} {known}")
    wrong = [r[0] for r in rows if not r[2].startswith("ok: ")]
    if refused:
        print("REFUSED: the apparatus did not pass its own controls, or a seam did not take")
        return REFUSED
    if wrong:
        print(f"FAIL: case(s) {', '.join(wrong)} did not match their known answer")
        return FAIL
    print("VERDICT: not reachable. `InsufficientEvidence` leaves controls() (D): its `except Refused` is narrower "
          "than\n  build_residuals on purpose, so a dead oscillator cannot pass `stale_ours_refused`. Through main() "
          "it is\n  REFUSED, exit 2, from controls() (E, G) and from main()'s own call (I); neither escapes as a "
          "traceback,\n  a stale record is REFUSED on main()'s path (F), and an unrelated fault stays exit 1 (H).")
    return PASS


if __name__ == "__main__":
    sys.exit(main())
