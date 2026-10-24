"""The preparation contract at the drum pair site (#163, first slice).

`tools/run_case.drum_measurements` hands two recordings -- the Fischer
reference and our render -- through the same `prepare()` and then through the
same estimators. That reads as symmetric. #101, #160 F2 and #161 were three
comparisons that read exactly that way and were not: the shared function's
behaviour depended on state (pre-onset lead, a pre-trim, the first sample)
that differed between the sides and that nothing at the call site could see.

So the pair now asserts the APPARATUS state of what `prepare()` produced, and
REFUSES -- naming every violating field -- when the two sides were not prepared
alike. It deliberately does NOT compare acoustic content (first sample, peak,
length, onset time into the record): two different sounds legitimately differ
in all four, and a contract that compared them would refuse valid pairs.

Layout of this file, because each block answers a different question:

  1. valid UNEQUAL pairs stay green           -- the contract is satisfiable
  2. refusal controls (a)-(e)                  -- each must turn REFUSED, and
                                                  for the reason it names
  3. the controls through `drum_measurements`  -- the thing that ships, not a
                                                  helper beside it
  4. what defeats the guard (rule 8)           -- stated as tests, so the blind
                                                  spots are on the record
  5. the boundary control (#160/#161 shape)    -- trimmed vs 10 ms-padded copy
                                                  of one known-answer hit

Run: `python3 -m pytest tools/test_preparation_contract.py -q`
"""
from __future__ import annotations

import math
import pathlib
import sys

import numpy as np
import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "model"))

import preparation_contract as pc                                   # noqa: E402
import run_case as rc                                               # noqa: E402

Side = pc.Side


# ===========================================================================
# Fixtures. Synthetic and analytic -- NOT our drum model -- so that what each
# one should do is known without asking the thing under test.
# ===========================================================================
def _strike(*, sr, f=220.0, tau=0.040, lead_ms=10.0, seconds=0.6, gain=1.0,
            click=0.05):
    """A drum-shaped hit: a decaying body plus a short 3 kHz click, after
    `lead_ms` of true digital silence (the same shape as
    `test_run_case._strike`, rebuilt here so this file owns its fixtures)."""
    n = int(seconds * sr)
    a = int(round(lead_ms * 1e-3 * sr))
    t = np.arange(n - a) / sr
    x = np.zeros(n)
    x[a:] = (np.exp(-t / tau) * np.sin(2 * math.pi * f * t)
             + click * np.exp(-t / 0.002) * np.sin(2 * math.pi * 3000.0 * t))
    return gain * x


def _prepared(x, sr, name):
    return Side(name, rc.prepare(x, sr, side=name), sr)


def _ours():
    """What our render looks like: 48 kHz, 10 ms of digital silence first."""
    return _strike(sr=48000, f=410.0, tau=0.030, lead_ms=10.0, seconds=0.8,
                   gain=0.3, click=0.2)


def _reference():
    """What a Fischer reference looks like: 44.1 kHz, the strike 0.16 ms into
    the record -- #101's figure for the reference side."""
    return _strike(sr=44100, f=180.0, tau=0.080, lead_ms=0.16, seconds=1.0,
                   gain=0.9, click=0.05)


def _fields(refusal) -> set:
    return {field for field, _side, _detail in refusal.violations}


def _prepare_pre_132(x, sr, *, side="the recording"):
    """`prepare()` exactly as it shipped before #132 (`git show
    ba14af29^:tools/run_case.py`), reinstated as a control: the clamp
    `max(0, onset - 1 ms)` that gave the reference 0.16 ms of lead and our
    render 1.00 ms, worth 6 dB against a 3 dB tolerance on the congas (#101).
    `docs/verification-rules.md` rule 5: a fixed bug is kept as an injection."""
    from audio_measure import is_silent
    x = np.asarray(x, dtype=np.float64)
    if is_silent(x):
        return x
    pk = float(np.abs(x).max())
    i = int(np.argmax(np.abs(x) > 0.02 * pk))
    lead = max(0, i - int(1e-3 * sr))
    if lead >= int(5e-3 * sr):
        x = x - float(x[:lead].mean())
    y = x[lead:]
    p = float(np.abs(y).max())
    return y / p if p > 0 else y


# ===========================================================================
# 1. Valid unequal pairs stay green
# ===========================================================================
def test_a_valid_unequal_pair_is_accepted():
    """Two DIFFERENT strikes -- tone, decay, gain, click, first sample, natural
    length, onset time into the record and sample rate all differ -- each
    prepared normally. This is every real drum case's shape (references at
    44.1 kHz, renders at 48 kHz), so a contract that refused it would be an
    unsatisfiable gate."""
    ours_x, ref_x = _ours(), _reference()
    ours, ref = _prepared(ours_x, 48000, "ours"), _prepared(ref_x, 44100, "reference")

    # The fixture must actually be unequal in everything the contract is
    # forbidden to compare, or this test proves nothing.
    assert ours.sr != ref.sr
    assert len(ours.y) != len(ref.y)
    assert not math.isclose(float(ours.y[-1]), float(ref.y[-1]))
    assert not math.isclose(float(np.abs(ours_x).max()), float(np.abs(ref_x).max()))
    assert rc._onset_index(ours_x) != rc._onset_index(ref_x)

    state = pc.check_prepared_pair(ref, ours)
    assert state["reference"]["lead_samples"] == rc.required_lead_samples(44100)
    assert state["ours"]["lead_samples"] == rc.required_lead_samples(48000)


@pytest.mark.parametrize("lead_ms", [0.16, 1.0, 5.0, 10.0, 200.0])
def test_every_lead_a_record_arrives_with_is_accepted_after_prepare(lead_ms):
    """Whatever lead the RECORD had, `prepare()` guarantees the same one, so
    the contract must accept every one of them -- including the 5 ms case,
    where `prepare` also takes a DC estimate, and the 200 ms case, where it
    slices instead of manufacturing silence."""
    ref = _prepared(_strike(sr=44100, lead_ms=lead_ms), 44100, "reference")
    ours = _prepared(_ours(), 48000, "ours")
    pc.check_prepared_pair(ref, ours)


# ===========================================================================
# 2. Refusal controls. Each must REFUSE and name the field it failed on.
# ===========================================================================
def test_a_sr_whose_windowing_differs_in_samples_is_refused():
    """(a) The issue's control (a) was "44100 vs 48000 must REFUSE". That
    cannot be the rule: every real drum pair IS 44100 vs 48000 (see the test
    above), so it would refuse every #282 drum case. What the sample rate
    actually governs here is that `required_lead_samples` is a SAMPLE count,
    and at 44.1 and 48 kHz it is 540 on both sides. At 96 kHz it is 960, so the
    two sides' zero-phase filters meet different boundaries -- that is refused,
    under `sr` and under `lead`."""
    ref = _prepared(_reference(), 44100, "reference")
    ours96 = _prepared(_strike(sr=96000, lead_ms=10.0), 96000, "ours at 96 kHz")
    with pytest.raises(pc.Refused) as e:
        pc.check_prepared_pair(ref, ours96)
    assert {"sr", "lead"} <= _fields(e.value)
    assert "960" in str(e.value) and "540" in str(e.value)


def test_a_misdeclared_sample_rate_is_refused():
    """(a) A side prepared at one rate and declared at another: `prepare` put
    the onset 540 + 48 samples in, and read at 44.1 kHz that is 544 samples of
    lead before t = 0, not 540. The sample rate is wrong; the lead says so."""
    ours = rc.prepare(_ours(), 48000, side="ours")
    ref = _prepared(_reference(), 44100, "reference")
    with pytest.raises(pc.Refused) as e:
        pc.check_prepared_pair(ref, Side("ours, declared 44.1 kHz", ours, 44100))
    assert "lead" in _fields(e.value)


@pytest.mark.parametrize("sr", [0, -48000, 48000.5, True, None])
def test_an_unusable_sample_rate_is_refused(sr):
    ours = rc.prepare(_ours(), 48000, side="ours")
    ref = _prepared(_reference(), 44100, "reference")
    with pytest.raises(pc.Refused) as e:
        pc.check_prepared_pair(ref, Side("ours", ours, sr))
    assert "sr" in _fields(e.value)


def test_a_side_prepared_without_the_guaranteed_lead_is_refused():
    """(b) #101's exact values. The reference prepared the pre-#132 way keeps
    0.16 ms of lead (7 samples at 44.1 kHz, then the strike's zero first
    sample); our render, which begins in 10 ms
    of silence, keeps 1.00 ms. Both are refused, and so is the pair."""
    ref_y = _prepare_pre_132(_reference(), 44100)
    ours_y = _prepare_pre_132(_ours(), 48000)
    # 7 samples of silence (0.16 ms at 44.1 kHz) + the strike's own zero first sample
    assert rc._onset_index(ref_y) == 8
    assert rc._onset_index(ours_y) == 48                        # 1.00 ms at 48 kHz
    with pytest.raises(pc.Refused) as e:
        pc.check_prepared_pair(Side("reference", ref_y, 44100), Side("ours", ours_y, 48000))
    sides = {side for field, side, _ in e.value.violations if field == "lead"}
    assert {"reference", "ours"} <= sides, e.value.violations


def test_one_side_without_the_lead_against_one_with_it_is_refused():
    """(b) The asymmetric version: one side prepared properly, the other not.
    The violation names the side that was not."""
    ref_y = _prepare_pre_132(_reference(), 44100)
    ours = _prepared(_ours(), 48000, "ours")
    with pytest.raises(pc.Refused) as e:
        pc.check_prepared_pair(Side("reference", ref_y, 44100), ours)
    assert ("lead", "reference") in {(f, s) for f, s, _ in e.value.violations}
    assert ("lead", "ours") not in {(f, s) for f, s, _ in e.value.violations}


@pytest.mark.parametrize("which", ["reference", "ours"])
@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_a_non_finite_side_is_refused(which, bad):
    """(c) NaN and +/-Inf in either side. Asserted BEFORE silence, because a
    NaN defeats `is_silent` rather than tripping it (#133, #134)."""
    ref = _prepared(_reference(), 44100, "reference")
    ours = _prepared(_ours(), 48000, "ours")
    target = ref if which == "reference" else ours
    y = np.array(target.y, copy=True)
    y[len(y) // 2] = bad
    bent = Side(target.name, y, target.sr)
    pair = (bent, ours) if which == "reference" else (ref, bent)
    with pytest.raises(pc.Refused) as e:
        pc.check_prepared_pair(*pair)
    assert ("finite", which) in {(f, s) for f, s, _ in e.value.violations}


def test_an_all_nan_side_is_refused_as_non_finite_and_not_as_silent():
    ref = _prepared(_reference(), 44100, "reference")
    ours = Side("ours", np.full(48000, np.nan), 48000)
    with pytest.raises(pc.Refused) as e:
        pc.check_prepared_pair(ref, ours)
    assert ("finite", "ours") in {(f, s) for f, s, _ in e.value.violations}
    assert ("silent", "ours") not in {(f, s) for f, s, _ in e.value.violations}


def test_a_side_cut_into_the_strike_is_refused_not_clamped():
    """(d) A prepared-looking side whose first sample is already at or above
    the onset threshold. It has no lead at all; the contract refuses rather
    than reading the missing lead as zero and carrying on."""
    ours = _prepared(_ours(), 48000, "ours")
    i = rc._onset_index(ours.y)
    cut = Side("ours", ours.y[i:], 48000)
    assert abs(cut.y[0]) >= rc.ONSET_FRAC * np.abs(cut.y).max()
    with pytest.raises(pc.Refused) as e:
        pc.check_prepared_pair(_prepared(_reference(), 44100, "reference"), cut)
    assert ("lead", "ours") in {(f, s) for f, s, _ in e.value.violations}
    assert "cut into the strike" in str(e.value)


@pytest.mark.parametrize("which", ["reference", "ours"])
def test_a_silent_side_is_refused(which):
    """(e) `prepare()` returns a silent record UNCHANGED -- it does not refuse
    it (the issue's table said it did; the source says otherwise). The contract
    refuses it, so a silent render cannot be scored as a comparison."""
    ref = _prepared(_reference(), 44100, "reference")
    ours = _prepared(_ours(), 48000, "ours")
    if which == "reference":
        ref = Side("reference", rc.prepare(np.zeros(22050), 44100), 44100)
    else:
        ours = Side("ours", rc.prepare(np.zeros(48000), 48000), 48000)
    with pytest.raises(pc.Refused) as e:
        pc.check_prepared_pair(ref, ours)
    assert ("silent", which) in {(f, s) for f, s, _ in e.value.violations}


def test_every_violating_field_is_named_in_one_refusal():
    """A refusal that names only the first problem sends the reader to fix it
    and come back for the next. Non-finite reference, silent render at a rate
    whose lead differs in samples: three fields, one refusal."""
    ref_y = np.array(rc.prepare(_reference(), 44100), copy=True)
    ref_y[100] = math.nan
    with pytest.raises(pc.Refused) as e:
        pc.check_prepared_pair(Side("reference", ref_y, 44100),
                               Side("ours", np.zeros(96000), 96000))
    assert {"finite", "silent", "sr"} <= _fields(e.value)
    for word in ("finite", "silent", "sr"):
        assert word in str(e.value)


def test_the_refusal_is_run_case_refused():
    """The pair site's caller turns `run_case.Refused` into a REFUSED case
    record (`run_case.run_one`, `score_drum_i2s.main`). A different exception
    type would surface as a traceback instead of a first-class outcome."""
    assert pc.Refused is rc.Refused


def test_the_silence_floor_is_audio_measures():
    """Restated in the contract to avoid an import-time dependency on
    `model/`; pinned here so the two cannot drift."""
    import inspect
    from audio_measure import is_silent
    assert inspect.signature(is_silent).parameters["floor"].default == pc.SILENCE_FLOOR


# ===========================================================================
# 3. The same controls through the code that ships: `drum_measurements`.
# ===========================================================================
def test_drum_measurements_accepts_a_valid_unequal_pair():
    metrics, _ = rc.drum_measurements("LC", _ours(), 48000, _reference(), 44100,
                                      "synthetic-reference", [])
    assert metrics and all(m["valid"] for m in metrics.values()), metrics


@pytest.mark.parametrize("which", ["reference", "ours"])
def test_drum_measurements_refuses_a_silent_side(which):
    """(e) through the pair site. Before #163 this produced a set of metrics."""
    ours, ref = _ours(), _reference()
    if which == "reference":
        ref = np.zeros_like(ref)
    else:
        ours = np.zeros_like(ours)
    with pytest.raises(rc.Refused) as e:
        rc.drum_measurements("LC", ours, 48000, ref, 44100, "synthetic-reference", [])
    assert "silent" in str(e.value)


def test_drum_measurements_refuses_the_pre_132_preparation(monkeypatch):
    """(b) through the pair site: reinstate #101's clamp in `prepare` and the
    pair must REFUSE before any metric runs. Before #163 it measured, and the
    numbers carried the 0.16 ms / 1.00 ms asymmetry with nothing saying so."""
    monkeypatch.setattr(rc, "prepare", _prepare_pre_132)
    calls = []
    monkeypatch.setattr(rc, "measure_pair", lambda *a, **k: calls.append(a) or {})
    with pytest.raises(rc.Refused) as e:
        rc.drum_measurements("LC", _ours(), 48000, _reference(), 44100,
                             "synthetic-reference", [])
    assert "lead" in str(e.value)
    assert calls == [], "a metric ran before the contract refused"


def test_drum_measurements_refuses_an_asymmetric_prepare(monkeypatch):
    """(b) An injected asymmetry in `prepare` that touches ONE side: the
    reference loses 100 samples of its guaranteed lead, ours is untouched."""
    real = rc.prepare

    def lopsided(x, sr, *, side="the recording"):
        y = real(x, sr, side=side)
        return y[100:] if "reference" in side else y

    monkeypatch.setattr(rc, "prepare", lopsided)
    with pytest.raises(rc.Refused) as e:
        rc.drum_measurements("LC", _ours(), 48000, _reference(), 44100,
                             "synthetic-reference", [])
    assert ("lead", "the reference recording synthetic-reference") in \
        {(f, s) for f, s, _ in e.value.violations}


def test_drum_measurements_still_refuses_a_record_cut_into_the_strike():
    """(d) through the pair site. `prepare` refuses this itself (#132); the
    contract is the second line, tested directly above."""
    ref = _reference()
    cut = ref[rc._onset_index(ref) + 3:]
    with pytest.raises(rc.Refused, match="cut into the strike"):
        rc.drum_measurements("LC", _ours(), 48000, cut, 44100, "synthetic-reference", [])


# ===========================================================================
# 4. Rule 8: what satisfies this check while violating its intent.
#    These PASS by design -- they record blind spots, not features.
# ===========================================================================
def test_blind_spot_a_misdeclared_rate_with_the_same_sample_counts():
    """The contract checks that both sides were WINDOWED alike, in samples. It
    cannot check that a declared rate is TRUE: 44000 and 44100 give the same
    lead (540) and the same 1 ms trim (44 samples), so a 44.1 kHz record
    declared as 44 kHz passes. Every frequency it reports would be 0.2 % off.
    Catching that needs the rate carried by the file, which `load_reference`
    reads from the WAV header and `render_drum_solo` from `drums_fx.SR`."""
    y = rc.prepare(_reference(), 44100)
    pc.check_prepared_pair(Side("reference, declared 44 kHz", y, 44000),
                           _prepared(_ours(), 48000, "ours"))


def test_blind_spot_a_pedestal_under_the_onset_threshold():
    """#161's shape: a constant pedestal in front of the strike at 1.5 % of
    peak -- under the 2 % onset threshold, so it is "lead" by the contract's
    definition, and the first sample is 0.015 instead of 0. The contract does
    not compare first samples (different sounds legitimately differ), so this
    passes; the boundary control below is what covers this class."""
    y = np.array(rc.prepare(_ours(), 48000), copy=True)
    i = rc._onset_index(y)
    y[:i] += 0.015
    pc.check_prepared_pair(_prepared(_reference(), 44100, "reference"),
                           Side("ours with a pedestal", y, 48000))


# ===========================================================================
# 5. The boundary control: #160 F2 / #161's shape, on a known-answer fixture.
# ===========================================================================
#: The fixture: an attack-ramped, exponentially decaying sinusoid -- analytic,
#: not our drum model. Its answers are known in closed form: the pitch is F0
#: and the 20 dB decay time is TAU * ln(10).
F0, TAU, TAU_ATTACK, SR = 330.0, 0.040, 0.0005, 48000
#: A preparation effect is admissible only below this fraction of the metric's
#: own verdict tolerance. Derivation in `test_a_trimmed_and_a_padded_copy_...`.
BOUNDARY_FRACTION_OF_TOLERANCE = 0.01


def _known_answer_hit(seconds=0.6):
    t = np.arange(int(seconds * SR)) / SR
    return ((1.0 - np.exp(-t / TAU_ATTACK)) * np.exp(-t / TAU)
            * np.sin(2 * math.pi * F0 * t))


@pytest.mark.parametrize("voice", ["LC", "BD", "SD", "CH"])
def test_a_trimmed_and_a_padded_copy_of_one_hit_compare_as_identical(voice):
    """Ours = the hit trimmed at its first sample (onset at sample 4, so the
    record keeps four REAL below-threshold samples, the first of them 0); the
    reference = the same samples behind 10 ms of digital silence. #160 F2 was
    exactly this pair (our clip pre-trimmed, the machine's not), worth +32 dB.

    **Why the known answer is known:** both sides are the same samples, so the
    true error of every metric is exactly zero -- independent of any estimator
    and of our model. Anything nonzero is the preparation.

    **Why 1 % of the metric's own tolerance:** a metric's verdict is
    |error| <= tolerance. A preparation effect of at most 1 % of that can move
    a verdict only for a metric already within 1 % of its edge, which is below
    the resolution any tolerance here is stated to (`tolerance_basis`). It is
    a property of the verdict, not a fit to what this code happens to produce.

    **What actually remains, measured:** `prepare` takes a DC estimate only
    when a record supplies 5 ms of pre-onset, so the padded side gets one and
    the trimmed side does not -- a constant 1.0e-4 of peak here. Worst effect
    over these four plans: CH band energy 0.0066 dB against 3.0 dB (0.22 %).
    The reinstated pre-#132 clamp moves LC body spectrum 1.25 dB of 3.0 (42 %),
    which is the control below."""
    hit = _known_answer_hit()
    assert 0 < rc._onset_index(hit) < int(5e-3 * SR)            # real pre-onset samples
    assert abs(hit[0]) < rc.ONSET_FRAC * np.abs(hit).max()      # not cut into the strike
    padded = np.concatenate([np.zeros(int(0.010 * SR)), hit])
    metrics, _ = rc.drum_measurements(voice, hit, SR, padded, SR, "known-answer hit", [])

    valid = {k: m for k, m in metrics.items() if m["valid"]}
    assert len(valid) >= 2, f"{voice}: too few metrics measured to mean anything: {metrics}"
    for name, m in valid.items():
        assert abs(m["error"]) <= BOUNDARY_FRACTION_OF_TOLERANCE * m["tolerance"], \
            f"{voice} {name}: trimmed vs padded differ by {m['error']} {m['units']} " \
            f"against a tolerance of {m['tolerance']}"
    # The fixture is inside the estimators' domain: the known answers come back.
    if voice == "LC":
        assert abs(metrics["Pitch"]["value"] - F0) < 1e-3 * F0
        assert abs(metrics["decay"]["value"] - 1e3 * TAU * math.log(10)) < 1e-3 * 1e3 * TAU * math.log(10)


def test_the_boundary_control_turns_red_under_the_pre_132_clamp(monkeypatch):
    """The control for the control: with #101's clamp reinstated AND the
    contract bypassed, the same trimmed/padded pair must exceed the bound --
    otherwise the bound above could not see the defect it exists for."""
    monkeypatch.setattr(rc, "prepare", lambda x, sr, *, side="": _prepare_pre_132(x, sr))
    monkeypatch.setattr(pc, "check_prepared_pair", lambda *a, **k: {})
    hit = _known_answer_hit()
    padded = np.concatenate([np.zeros(int(0.010 * SR)), hit])
    metrics, _ = rc.drum_measurements("LC", hit, SR, padded, SR, "known-answer hit", [])
    worst = max(abs(m["error"]) / m["tolerance"] for m in metrics.values() if m["valid"])
    assert worst > BOUNDARY_FRACTION_OF_TOLERANCE, worst


# --- the current-state probe must not report success for pairs it skipped ----
# Control for the false-green path: a refusal upstream of the contract used to
# skip the pair without counting it, so "contract refusals: 0" exited 0.

def _probe(monkeypatch, tmp_path, *, prepare_refuses_for=()):
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "probes"))
    import preparation_contract_current_state as probe
    monkeypatch.setenv(rc.REFS_ENV, str(tmp_path))
    monkeypatch.setattr(rc, "REF_MAIN", {"a": 0, "b": 0, "c": 0})
    ok = np.concatenate([np.zeros(48), np.ones(480) * 0.5])
    monkeypatch.setattr(rc, "load_reference", lambda v, d: (ok, 48000, f"{v}.wav", None))
    monkeypatch.setattr(rc, "render_drum_solo", lambda v: (ok, 48000))

    def prep(x, sr, side=""):
        if any(v in side for v in prepare_refuses_for):
            raise rc.Refused("injected upstream refusal")
        return x
    monkeypatch.setattr(rc, "prepare", prep)
    monkeypatch.setattr(pc, "check_prepared_pair", lambda r, o: {
        "reference": {"lead_samples": 1, "lead_samples_required": 1},
        "ours": {"lead_samples": 1, "lead_samples_required": 1}})
    return probe


def test_probe_clean_all_accepted_exits_zero(monkeypatch, tmp_path, capsys):
    probe = _probe(monkeypatch, tmp_path)
    assert probe.run(tmp_path) == (3, 0, 0, 3)
    assert probe.main() == 0


def test_probe_one_upstream_refusal_is_not_success(monkeypatch, tmp_path, capsys):
    probe = _probe(monkeypatch, tmp_path, prepare_refuses_for=("our b render",))
    assert probe.run(tmp_path) == (2, 0, 1, 3)
    assert probe.main() == 2
    assert "accepted 2 of 3" in capsys.readouterr().out


def test_probe_all_upstream_refusals_is_not_success(monkeypatch, tmp_path, capsys):
    probe = _probe(monkeypatch, tmp_path, prepare_refuses_for=("our ",))
    assert probe.run(tmp_path) == (0, 0, 3, 3)
    assert probe.main() == 2
    assert "accepted 0 of 3" in capsys.readouterr().out
