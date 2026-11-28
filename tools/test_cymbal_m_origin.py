"""Known answers and must-fail controls for tools/cymbal_m_origin.py (#369).

The module's claim is a BOUND -- that no inter-band balance of the three bands
§10 documents can put the 1-2.5 kHz region within 9 dB of where the recordings
put it -- so what has to be tested is the bound, not a fit:

  * the mediant lemma the bound rests on, checked against brute force over the
    balance, and checked on a synthetic case whose answer is known by hand;
  * that the refuted hypothesis really is refuted by the numbers in the
    docstring, and that those numbers come from the artifacts rather than from
    the prose;
  * that the probe REFUSES rho on a band the instrument was not qualified for,
    which is the control the first version failed (it reported 0.006);
  * that the documented attack smoother is load-bearing -- without it the same
    sweep reports the garbage that motivated the refusal;
  * that `main` answers a failed shared control with the declared REFUSED
    status, not with a measured FAIL (#1789), and that passing controls do
    not refuse;
  * that a refusal RAISED inside the work -- a non-finite `--taus` reaching
    `run_case.prepare` -- is the same REFUSED status with its reason as the
    only line printed, while an execution error at the same boundary still
    exits 1 (#1891).
"""
import json
import math
import pathlib
import subprocess
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import cymbal_m_origin as mo  # noqa: E402
import cymbal_low_tail as lt  # noqa: E402
from exit_contract import PASS, REFUSED  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
RESULT = ROOT / "docs/scorecard/cymbal-369/low-tail/low-tail.json"


# --------------------------------------------------------------------------
# the bound, which is the whole claim
# --------------------------------------------------------------------------
def test_the_mediant_lemma_holds_on_a_case_whose_answer_is_known_by_hand():
    """(a1 X1 + a2 X2)/(a1 R1 + a2 R2) <= max(X1/R1, X2/R2), with equality only
    when one weight is zero or the ratios are equal. Checked on numbers rather
    than asserted, because the bound is the result."""
    rng = np.random.default_rng(0)
    for _ in range(200):
        x = rng.uniform(0.01, 10.0, 3)
        r = rng.uniform(0.01, 10.0, 3)
        a = rng.uniform(0.0, 5.0, 3)
        mix = float(np.sum(a * x) / np.sum(a * r))
        assert mix <= max(x / r) + 1e-12, (x, r, a, mix)


def test_the_bound_is_the_per_band_maximum_and_brute_force_cannot_beat_it():
    """The algebra against a 71x71 sweep of the inter-band balance. If the grid
    ever exceeded the bound, the lemma would be being applied to the wrong
    quantity -- and this is the only thing that would notice."""
    e = mo.band_energies()
    b = mo.bound(e)
    g = mo.bound_grid(e, step=2.0)
    for name in ("M", "Mn"):
        per = b[name]["per_band"]
        assert b[name]["bound_db"] == pytest.approx(max(per.values()), abs=1e-9), b[name]
        assert g[name]["best_db"] <= b[name]["bound_db"] + 1e-6, (name, g[name], b[name])


def test_the_bound_is_worse_than_the_recordings_by_the_stated_margin():
    """The headline, read off the artifacts and the committed measurement rather
    than out of the docstring. If either changes, this fails instead of the prose
    going quietly stale."""
    if not RESULT.is_file():
        pytest.skip(f"{RESULT} is not present")
    g = mo.gap(json.loads(RESULT.read_text()))
    assert g["bands"]["M"]["chain_bound_db"] == pytest.approx(-13.10, abs=0.2)
    assert g["bands"]["Mn"]["chain_bound_db"] == pytest.approx(-30.30, abs=0.3)
    # Every measured setting exceeds the bound -- not a median effect.
    for name in ("M", "Mn"):
        b = g["bands"][name]
        assert b["n_808_above_bound"] == g["n_808"], (name, b)
        assert b["gap_min_db"] > 5.0, (name, b)
    assert g["bands"]["M"]["gap_median_db"] == pytest.approx(9.4, abs=0.4), g["bands"]["M"]
    assert g["bands"]["Mn"]["gap_median_db"] == pytest.approx(16.6, abs=0.6), g["bands"]["Mn"]


def test_our_shipped_render_already_has_about_the_right_M_energy_and_cand3_moved_away():
    """The result that was NOT expected, and the reason this is a decay problem
    rather than a balance one. Pinned so a later candidate cannot be credited with
    fixing an energy error that was not there."""
    if not RESULT.is_file():
        pytest.skip(f"{RESULT} is not present")
    g = mo.gap(json.loads(RESULT.read_text()))["bands"]
    med = g["M"]["measured_median"]
    assert abs(g["M"]["shipped"] - med) < 1.0, g["M"]
    assert abs(g["M"]["candidate"] - med) > abs(g["M"]["shipped"] - med) + 2.0, g["M"]


# --------------------------------------------------------------------------
# the refuted hypothesis, and the numbers that refute it
# --------------------------------------------------------------------------
def test_the_tone_stages_shape_barely_reaches_the_M_band():
    """The refutation, as a number. If the tone stage's shape ever did move M by
    a decibel or two relative to Ln, the hypothesis would be back in play and the
    docstring would be wrong -- so this is a bound, not a regression pin."""
    e = mo.band_energies()
    moved = {k: v["tone_shape_moves_M_by"] for k, v in e.items()}
    assert max(abs(v) for v in moved.values()) < 1.0, moved
    assert abs(moved["decay"]) == pytest.approx(0.80, abs=0.15), moved
    # And the DECAY band is the one it moves most, which is why it was plausible.
    assert abs(moved["decay"]) > abs(moved["low"]) and abs(moved["decay"]) > abs(moved["short"])


def test_Ht2_really_does_peak_inside_the_M_band_so_the_hypothesis_was_not_silly():
    """The premise was sound and measured, not invented: Ht2's real poles are at
    610 and 1549 Hz, whose geometric mean (972 Hz) is inside the M band. The
    hypothesis failed on the band's OWN filters, not on the tone stage."""
    p = mo.tr.tone_poles()["decay"]
    assert p[0] == pytest.approx(609.9, rel=0.02) and p[1] == pytest.approx(1549.1, rel=0.02), p
    lo, hi = mo.BANDS["M"]
    assert lo < math.sqrt(p[0] * p[1]) < hi, p


# --------------------------------------------------------------------------
# refusals: the control the first version failed
# --------------------------------------------------------------------------
def test_the_sweep_refuses_rho_on_a_band_the_instrument_was_not_qualified_for():
    """Wrong-then-right 2, reinstated as a control (verification rule 5). The
    documented cascade puts Mn about 30 dB below Ln -- 8 dB below the worst case
    the instrument has ever been read on -- so Mn must come back REFUSED with its
    measured level, not as a number."""
    rows = mo.sweep(levels=(0.0,))
    r = rows[0]
    assert r["rho_Mn_-10"] is None, r
    assert "Mn" in r["refused"] and "qualified" in r["refused"]["Mn"], r
    assert r["Mn_re_Ln"] < mo.MIN_BAND_RE_REF_DB, r
    # M is inside the qualified range and must still answer, or the refusal is
    # just the tool declining to work.
    assert r["rho_M_-10"] is not None and "M" not in r["refused"], r


def test_the_qualified_floor_admits_every_real_record_it_has_to():
    """A refusal threshold that also refused the recordings would be useless. The
    808's Mn sits at -13 to -15 dB re Ln and both our renders above -20, so all of
    them must clear MIN_BAND_RE_REF_DB with margin."""
    if not RESULT.is_file():
        pytest.skip(f"{RESULT} is not present")
    rows = mo.measured_energies(json.loads(RESULT.read_text()))["2.0s"]
    for label, r in rows.items():
        assert r["Mn_re_Ln"] > mo.MIN_BAND_RE_REF_DB, (label, r)


def test_measured_energies_refuses_a_result_whose_controls_did_not_pass():
    with pytest.raises(mo.Refused):
        mo.measured_energies({"windows": {}})


# --------------------------------------------------------------------------
# the attack smoother is load-bearing, not decoration
# --------------------------------------------------------------------------
def test_without_the_documented_attack_smoother_the_envelope_step_is_a_click():
    """§10's attack smoother is in the mixture because leaving it out is a
    measurement error. Paired: with it, the mixture's Mn level agrees with the
    spectral prediction; without it, the envelope's step at t = 0 injects
    broadband energy that lifts the measured Mn level by several dB and collapses
    its decay -- which is how the first version reported rho_Mn = 0.006."""
    bound_mn = mo.bound()["Mn"]["bound_db"]
    y, sr = mo.mixture(0.0)
    m = lt.measure(y, sr)
    with_a = 10 * math.log10(m["bands"]["Mn"]["energy_j"] / m["bands"]["Ln"]["energy_j"])
    assert with_a == pytest.approx(bound_mn, abs=2.0), (with_a, bound_mn)

    y0, sr0 = mo.mixture(0.0, attack_s=0.0)
    m0 = lt.measure(y0, sr0)
    without = 10 * math.log10(m0["bands"]["Mn"]["energy_j"] / m0["bands"]["Ln"]["energy_j"])
    assert without > with_a + 4.0, (without, with_a)
    # And the decay it reports is the click's, not the band's.
    assert m0["rho"]["Mn"]["-10"] < 0.2, m0["rho"]["Mn"]
    assert m["rho"]["Mn"]["-10"] is None or m["rho"]["Mn"]["-10"] > m0["rho"]["Mn"]["-10"]


def test_the_attack_smoother_is_the_references_own_value():
    """0.1 ms, from §10's "attack smoother (Q19, tau ~ 0.1 ms)". A fitted attack
    would make the probe's conclusion depend on a free parameter."""
    assert mo.ATTACK_S == pytest.approx(1e-4)
    e = mo.envelope(0.1, mo.SR)
    i = int(mo.ATTACK_S * mo.SR)
    assert e[0] == pytest.approx(0.0, abs=1e-9)
    assert 0.5 < e[i] < 0.75, e[i]          # 1 - 1/e at one time constant
    assert e[int(0.1 * mo.SR)] == pytest.approx(math.exp(-1.0), rel=0.02)


# --------------------------------------------------------------------------
# the decay verdict
# --------------------------------------------------------------------------
def test_no_linear_balance_reaches_the_808s_weakest_rho_and_the_tool_says_so():
    """The verdict must be a REFUSAL to name a level, not a silently absent one --
    "no linear balance does this" is the answer the probe exists to give."""
    rows = mo.sweep()
    v = mo.verdict(rows)
    assert v["reaches_target_at_db"] is None, v
    assert "no swept DECAY level" in v["refused"], v
    assert v["max_rho_M_-10"] < v["target"], v
    # It does not even clear the instrument's own skirt baseline, which is what
    # "the documented chain's M is nothing but skirt" predicts.
    assert v["n_above_baseline"] == 0, v
    assert v["n_answered"] == len(rows), v          # M answers everywhere; only Mn refuses


def test_the_verdict_can_fire_positive_so_it_is_not_a_control_that_cannot_pass():
    """A gate that cannot be satisfied is worth nothing (#376). Hand `verdict` a
    row that DOES reach the target and check it names the level -- otherwise the
    refusal above proves only that the function always refuses."""
    v = mo.verdict([{"decay_rel_db": -6.0, "rho_M_-10": 0.9, "refused": {}},
                    {"decay_rel_db": 3.0, "rho_M_-10": 1.2, "refused": {}}])
    assert v["reaches_target_at_db"] == 3.0, v
    assert "refused" not in v, v


# --------------------------------------------------------------------------
# the exit contract: a failed control is a refusal, not a measured FAIL
# --------------------------------------------------------------------------
def test_main_reports_the_shared_instruments_failed_controls_as_refused(
        monkeypatch, capsys, tmp_path):
    """#1789. `lt.check()` returns one bool, so a caller cannot tell a
    measured failure from a refusal; for THIS caller every `ok is False`
    means the shared instrument's own controls did not pass and nothing
    below can be measured -- the condition `cymbal_low_tail.main` already
    returns REFUSED for. This `main` printed REFUSED and returned 1, which
    `tools/run_all.py` reads as a RESULT (tools/exit_contract.py). The
    controls are monkeypatched, so no render runs."""
    monkeypatch.setattr(mo.lt, "check", lambda: (False, ["  injected: a control that failed"]))
    rc = mo.main(["--no-grid", "--result", str(tmp_path / "no-such-result.json")])
    out = capsys.readouterr().out
    assert rc == REFUSED
    assert "  injected: a control that failed" in out
    assert out.endswith("REFUSED: the shared instrument's own controls do not pass\n")


def test_control_passing_controls_do_not_refuse(monkeypatch, capsys, tmp_path):
    """The injected defect for the test above: a guard that always returned
    REFUSED would satisfy it. Same call, one input different -- the controls
    pass -- and `main` runs through to PASS without printing the refusal."""
    monkeypatch.setattr(mo.lt, "check", lambda: (True, []))
    rc = mo.main(["--no-grid", "--result", str(tmp_path / "no-such-result.json")])
    out = capsys.readouterr().out
    assert rc == PASS
    assert "REFUSED: the shared instrument's own controls do not pass" not in out


# --------------------------------------------------------------------------
# a refusal RAISED inside the work, not returned by `lt.check()` (#1891)
# --------------------------------------------------------------------------
@pytest.mark.parametrize("taus", ["nan,0.5,0.1", "0,0.5,0.1"], ids=["nan-parses", "zero-tau"])
def test_main_refuses_a_non_finite_taus_rather_than_traceback(monkeypatch, capsys, taus):
    """Red first on ff1532c5. `float("nan")` parses, and a tau of 0 makes `-t/0`
    NaN at t=0, so `envelope()` returns NaN, `mixture()`'s `peak <= 0`
    silent-check is False (NaN compares False against any bound, #133), and
    `run_case.prepare` refuses the mix as non-finite audio. That is a
    `preparation_contract.Refused` -- a SIBLING of this module's own `Refused`,
    sharing only the base -- and it left `main` as an uncaught exit-1 traceback
    AFTER all 15 lines of the energy report had printed, "the chain is SHORT by
    9.36 dB" among them: a refusal rendered as FAIL, with a verdict on top.
    Now the REFUSED line IS the report, so exit status and stdout agree that
    nothing was measured (tools/exit_contract.py)."""
    monkeypatch.setattr(mo.lt, "check", lambda: (True, []))
    rc = mo.main(["--taus", taus, "--no-grid"])
    out = capsys.readouterr().out
    assert rc == REFUSED
    lines = out.splitlines()
    assert len(lines) == 1 and lines[0].startswith("REFUSED: "), out
    assert "non-finite" in lines[0] and "NaN" in lines[0], lines[0]


def test_main_refuses_on_a_sibling_modules_refusal_too(monkeypatch, capsys):
    """The handler catches the family BASE, not just the two classes #1891
    met. `band_energies` reaches `cymbal_tone_realisation.level_corner_hz`,
    whose `Refused` is a third sibling (#1878's escape); with the handler
    narrowed to the local class and `preparation_contract.Refused`, every other
    test here would still pass and that path would traceback again. Planted at
    the callee by name; the real artifact preconditions are #1878's to drive."""
    monkeypatch.setattr(mo.lt, "check", lambda: (True, []))

    def _artifact_absent(*args, **kwargs):
        raise mo.tr.Refused("injected: werner_fig4 is absent")

    monkeypatch.setattr(mo.tr, "level_corner_hz", _artifact_absent)
    rc = mo.main(["--no-grid"])
    assert rc == REFUSED
    assert capsys.readouterr().out == "REFUSED: injected: werner_fig4 is absent\n"


def test_control_a_plain_assertion_error_is_not_refused(monkeypatch, capsys):
    """The injected defect for the test above: a handler written as
    `except Exception` would pass it. Forced where the refusal is raised -- in
    `mixture`, called from inside the guarded region -- a plain `AssertionError`
    must propagate out of `main` untouched: through `raise SystemExit(main())`
    that is exit 1 with a traceback, an EXECUTION ERROR, and nothing prints
    REFUSED (tools/refusal.py's "inverse defect")."""
    monkeypatch.setattr(mo.lt, "check", lambda: (True, []))

    def _not_a_refusal(*args, **kwargs):
        raise AssertionError("injected: an execution error, not a refusal")

    monkeypatch.setattr(mo, "mixture", _not_a_refusal)
    with pytest.raises(AssertionError, match="injected"):
        mo.main(["--no-grid"])
    assert "REFUSED" not in capsys.readouterr().out


def test_control_a_malformed_taus_is_not_refused(monkeypatch, capsys):
    """`--taus 1,2` names two bands and `zip` truncates silently, so -- contrary
    to the issue's expectation -- the parse does not raise. The `KeyError` for
    the missing `short` band surfaces INSIDE the guarded region, in `envelope`,
    which is exactly where an over-wide handler would relabel it.
    (`--taus a,b,c` is a `ValueError` at the parse, before the handler.)"""
    monkeypatch.setattr(mo.lt, "check", lambda: (True, []))
    with pytest.raises(KeyError, match="short"):
        mo.main(["--taus", "1,2", "--no-grid"])
    assert "REFUSED" not in capsys.readouterr().out


def test_cli_refuses_a_non_finite_taus_with_exit_2_through_the_real_entry_point(tmp_path):
    """The same input through `__main__` and `raise SystemExit(main())`, with
    the real `lt.check()` (synthetic controls, no render), because the exit
    STATUS is what `tools/run_all.py` reads: 2, not 1. A refused run must also
    leave no `--out` result behind -- a file is data."""
    out_file = tmp_path / "result.json"
    p = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "cymbal_m_origin.py"),
         "--taus", "nan,0.5,0.1", "--no-grid", "--out", str(out_file)],
        capture_output=True, text=True, timeout=600, cwd=str(ROOT))
    assert p.returncode == REFUSED, (p.returncode, p.stdout[-400:], p.stderr[-400:])
    assert "Traceback" not in p.stderr, p.stderr[-400:]
    lines = p.stdout.splitlines()
    assert len(lines) == 1 and lines[0].startswith("REFUSED: "), p.stdout[-400:]
    assert not out_file.exists(), "a refused run wrote a result file"
