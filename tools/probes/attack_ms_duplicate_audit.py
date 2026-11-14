#!/usr/bin/env python3
"""Is the second `attack_ms` a defect?  Two hypotheses, both REFUTED.  (Hermit)

    python3 -I tools/probes/attack_ms_duplicate_audit.py <repo-root>

WHY THIS FILE EXISTS

This is a record of a result that looked good and was wrong, kept because
CLAUDE.md says that record is worth more than one that was right first time.
A Hermit pass over `origin/main` 4fee563b found that the same documented rule
-- "2 % of the envelope peak up to the envelope peak" -- is written twice, and
that only one of the two refuses:

  * `tools/run_case.py:998  attack_ms(x, sr, ...) -> am.Estimate`
    Refuses silence, refuses an envelope that peaks on its first sample,
    refuses a non-positive rise.  Ground truth:
    `test_attack_ms_finds_a_known_linear_rise`,
    `test_attack_ms_cannot_resolve_an_attack_shorter_than_its_window`,
    `test_attack_ms_refuses_silence` (tools/test_run_case.py).
  * `model/drum_verify.py:289  attack_ms(env, sr) -> float`
    No refusal of any kind.  Returns a bare float, so a caller cannot tell
    0.0 ms ("the rise was instantaneous") from 0.0 ms ("there was no rise to
    find").  It is the copy the drum receipts use
    (`tools/stage_case.py:385`, method string at `tools/stage_case.py:532`).

CASE 1 below confirms the divergence exists: on exact silence the drum-side
copy answers 0.0 and the `run_case` one refuses `silent`.  That much is real.

HYPOTHESIS A -- REFUTED.  "A reference clip trimmed to its own transient makes
the envelope peak on sample 0, where `run_case` refuses and the drum-side copy
answers 0.0."  It does not.  `drum_verify.envelope` convolves with
`mode="same"`, which centres the moving RMS, so a hit that starts on sample 0
peaks about half a window in -- index 49 for the 2 ms RS window, not 0.  CASE 2
measures it: the two implementations agree to 0.02 ms (1.0208 vs 1.0417), and
neither refuses.  The condition `run_case` guards is not reachable through
`drum_verify.envelope`.

HYPOTHESIS B -- REFUTED.  "So `tools/stage_case.py` publishes a
provenance-complete `attack = 0.0 ms` for a silent render, because
`_fit_region` diagnoses the silence (`tools/stage_case.py:331-332`, "the
envelope peak is zero: a silent render") and `measure` carries on."  It does
not publish anything.  Silence never reaches `measure`: `do_render` asserts the
precondition and raises `manifest.Refused("... exact silence ...")`, that
refusal is in `GUARDS` as "render precondition"
(`tools/stage_case.py:833`), it carries a registered injected-defect
control `INJECTIONS["RENDER_SILENCE"]` (`tools/stage_case.py:315`), no
case can opt out because `allow_silence=False` is hardcoded at
`tools/stage_case.py:260`, and
`tools/test_stage_case.py:397` pins it.  Called directly past that guard,
`measure` raises `ZeroDivisionError` at `tools/stage_case.py:395` -- a
one-sided ratio guard, `max(..., 1e-20)` on the numerator and a bare
denominator -- which CASE 4 shows.  Loud, and unreachable in the shipped path.

WHAT SURVIVES

The duplication is real and the drum-side copy is genuinely unrefusing, but no
gated number disagrees because of it: `drum_verify.measure` tests
`np.abs(x).max() <= 0` before it calls `attack_ms`
(`model/drum_verify.py:379`), `model/sound_report.py:262` reads the
`silent` flag before the value, and `stage_case` is guarded upstream.  Under
the #282 milestone rule that makes it a parked maintenance item, not a repair:
the same ground on which #749 (three K-weighting copies) and #569 (sixteen
sha256 copies) were parked.  Re-open the question if any new caller reads
`drum_verify.attack_ms` without first testing the `silent` flag, or if
`drum_verify.envelope` stops centring its window.

Writes only into a fresh temporary directory; touches no repository file.
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import traceback

SR = 48000


def _imports(root: pathlib.Path):
    for p in ("model", "tools", "audition"):
        sys.path.insert(0, str(root / p))
    import drum_verify as dv          # noqa: PLC0415
    import run_case as rc             # noqa: PLC0415
    import stage_case as sc           # noqa: PLC0415
    return dv, rc, sc


def _line(k, v):
    print(f"  {k:<52} {v}")


def _rc(e):
    return f"ok={e.ok} reason={e.reason!r} value={e.value!r}"


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    root = pathlib.Path(argv[0] if argv else
                        pathlib.Path(__file__).resolve().parents[2]).resolve()
    dv, rc, sc = _imports(root)
    import numpy as np                # noqa: PLC0415
    from scipy.io import wavfile      # noqa: PLC0415

    print(f"repo-root {root}")
    print()
    print("CASE 1 -- exact silence.  Ground truth: there is no rise to measure,")
    print("          so the only correct outcome is a refusal.")
    _line("drum_verify.attack_ms(zeros)", repr(dv.attack_ms(np.zeros(SR // 4), SR)))
    _line("run_case.attack_ms(zeros)", _rc(rc.attack_ms(np.zeros(SR // 4), SR)))
    print("          -> the divergence is real.")

    print()
    print("CASE 2 -- HYPOTHESIS A.  A hit that starts on sample 0.  Predicted the")
    print("          envelope would peak on its first sample.  It does not.")
    t = np.arange(int(0.20 * SR)) / SR
    x2 = np.exp(-t / 0.006) * np.sin(2 * np.pi * 1700 * t)      # rimshot-like
    trimmed = dv.trim_onset(x2, SR)
    env2 = dv.envelope(trimmed, SR, win_ms=dv.ENV_WIN_MS["RS"])
    _line("trim_onset dropped (samples)", len(x2) - len(trimmed))
    _line("envelope peak index", int(np.argmax(env2)))
    _line("drum_verify.attack_ms(env)", f"{dv.attack_ms(env2, SR):.4f} ms")
    _line("run_case.attack_ms(signal)",
          _rc(rc.attack_ms(trimmed, SR, window_ms=dv.ENV_WIN_MS["RS"])))
    print("          -> REFUTED: mode='same' centres the moving RMS.")

    print()
    print("CASE 3 -- HYPOTHESIS B.  The guard that makes it unreachable.")
    d = pathlib.Path(tempfile.mkdtemp(prefix="hermit-attack-"))
    try:
        sc.do_render(d, "BD", None, "smoke", silent=True)
    except Exception as exc:                                    # noqa: BLE001
        _line("stage_case.do_render(silent=True)", f"{type(exc).__name__}: {exc}")
    else:
        _line("stage_case.do_render(silent=True)", "DID NOT REFUSE -- re-open this")
    _line("registered injected-defect control",
          sc.INJECTIONS["RENDER_SILENCE"][0])
    _line("in GUARDS as", sc.GUARDS.get("exact silence"))
    _line("stage_case asks manifest for", "allow_silence=False "
          "(hardcoded, tools/stage_case.py:260) -- no case can opt in")
    print("          -> REFUTED: silence never reaches measure().")

    print()
    print("CASE 4 -- measure() called past that guard: loud, not silent.")
    zx = np.zeros(SR // 2, dtype=np.float64)
    wavfile.write(str(d / "silent.wav"), SR, np.zeros(SR // 2, dtype=np.int16))
    _line("_fit_region diagnoses",
          json.dumps(sc._fit_region(dv.envelope(zx, SR, win_ms=dv.ENV_WIN_MS["BD"]), SR)))
    for analyser_id in sorted(sc.ANALYSERS):
        try:
            sc.measure(zx, SR, "BD", analyser_id)
        except Exception as exc:                                # noqa: BLE001
            tb = traceback.extract_tb(exc.__traceback__)[-1]
            _line(f"{analyser_id}", f"{type(exc).__name__} at "
                                    f"{pathlib.Path(tb.filename).name}:{tb.lineno}")
        else:
            _line(f"{analyser_id}", "measured a silent render -- re-open this")
    print("          -> a one-sided ratio guard, unreachable in the shipped path.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
