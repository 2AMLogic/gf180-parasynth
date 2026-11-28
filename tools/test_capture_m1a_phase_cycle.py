"""Known answers for the phase-cycle capture's precondition checks.

Each apparatus failure this repository has already met is synthesised here and
must REFUSE: demo clicks, a dropout, digital silence, a sub-audio octave, an
isolated oscillator whose level moves. A clean held saw must pass. These run
without the plugin."""
import json
import math

import numpy as np
import pytest

import capture_m1a_phase_cycle as c

SR = c.ref.SR
ON = .1
F36 = 440 * 2 ** ((36 - 69) / 12)


def saw(hz, amp=.1, seconds=None, partials=24):
    n = round((seconds or c.seconds_for(ON)) * SR)
    t = np.arange(n) / SR
    x = sum(np.sin(2 * np.pi * k * hz * t) / k for k in range(1, partials + 1) if k * hz < SR / 2)
    gate = (t >= ON) & (t < ON + c.GATE_S)
    return amp * x * gate


def test_clean_isolated_oscillators_pass():
    ev1 = c.check_take(saw(F36), SR, ON, "osc1_open")
    assert abs(ev1["cents_from_midi"]) < .1 and ev1["transient_events"] == 0
    ev2 = c.check_take(saw(2 * F36 * 2 ** (-3.49 / 1200)), SR, ON, "osc2_open")
    assert ev2["cents_from_octave"] == pytest.approx(-3.49, abs=.2)


def test_clicks_refused():
    x = saw(F36)
    rng = np.random.default_rng(1)
    for t in (3.3, 7.1):
        i = round(t * SR)
        x[i:i + 12] += .03 * rng.standard_normal(12)
    with pytest.raises(c.Refused, match="transient"):
        c.check_take(x, SR, ON, "full")


def test_dropout_refused():
    x = saw(F36)
    x[round(5 * SR):round(5.002 * SR)] = 0.
    with pytest.raises(c.Refused, match="exact zeros"):
        c.check_take(x, SR, ON, "full")


def test_silence_refused():
    with pytest.raises(c.Refused, match="silent"):
        c.check_take(np.zeros(round(c.seconds_for(ON) * SR)), SR, ON, "full")


def test_sub_audio_octave_refused():
    with pytest.raises(c.Refused, match="MIDI 36|below the commanded"):
        c.check_take(saw(F36 / 2), SR, ON, "osc1_open")
    with pytest.raises(c.Refused, match="octave|below the commanded"):
        c.check_take(saw(F36), SR, ON, "osc2_open")


def test_moving_isolated_level_refused():
    x = saw(F36)
    t = np.arange(len(x)) / SR
    x = x * (1 + .1 * np.sin(2 * np.pi * .3 * t))      # 1.7 dB wobble
    with pytest.raises(c.Refused, match="level moves"):
        c.check_take(x, SR, ON, "osc1_open")
    c.check_take(x, SR, ON, "full")                    # the full patch may beat


def test_isolated_sum_check_known_and_catches_a_click():
    a, b = saw(F36), saw(2 * F36, .05)
    assert c.sum_check(a + b, a, b, ON, SR)["overall_db"] < -200
    got = c.sum_check(a + b + .01 * a, a, b, ON, SR)["overall_db"]
    s0 = round((ON + c.SUSTAIN_FROM_S) * SR)
    s1 = round((ON + c.GATE_S - .05) * SR)
    want = 20 * math.log10(.01 * np.sqrt(np.mean(a[s0:s1] ** 2)) / np.sqrt(np.mean((a + b)[s0:s1] ** 2)))
    assert got == pytest.approx(want, abs=.05)
    clicked = a + b
    clicked[round(6 * SR):round(6 * SR) + 12] += .05
    check = c.sum_check(clicked, a, b, ON, SR)
    assert check["overall_db"] < c.SUM_RESIDUAL_DB          # one click hides in the overall figure
    with pytest.raises(c.Refused, match="click or dropout"):
        c.refuse_sum(check, "t")                             # and not in the worst block
    with pytest.raises(c.Refused, match="do not describe"):
        c.refuse_sum(c.sum_check(a + b, a, np.roll(b, 100), ON, SR), "t")   # phase differs


def test_expected_settings_follow_the_frozen_record():
    import json
    manifest = json.loads((c.FROZEN / "manifest.json").read_text())
    assert c.expected_settings(manifest, "full")["filter_cutoff"][2] == .5
    assert c.expected_settings(manifest, "osc1_open")["osc2_level"][2] == 0.
    both = c.expected_settings(manifest, "both_open")
    assert both["filter_cutoff"][2] == 1. and both["osc2_level"][2] == .3 and both["filter_contour"][2] == 0.


def test_slow_psi_cycle_swell_is_not_a_click_but_clicks_on_it_are():
    # the full patch's high band swells ~2 dB once per 3.8 s psi cycle; the
    # unmodified detector flagged exactly that (wrong-then-right)
    x = saw(F36, partials=16)           # a full patch: the 1 kHz filter leaves ~16 partials
    t = np.arange(len(x)) / SR
    swell = 1 + .25 * np.exp(-((((t - 1.9) % 3.8) - 1.9) / .2) ** 2)
    ev = c.check_take(x * swell, SR, ON, "full")
    assert ev["transient_events"] == 0
    unmodified = c.ri.transient_report((x * swell)[round(1.1 * SR):round(12.1 * SR)], SR,
                                       skip_s=0., block_ms=c.TRANSIENT_BLOCK_MS)
    assert unmodified["n_events"] >= 2          # the defect the detrend removes
    y = x * swell
    y[round(4.0 * SR):round(4.0 * SR) + 12] += .15 * np.random.default_rng(2).standard_normal(12)
    with pytest.raises(c.Refused, match="transient"):
        c.check_take(y, SR, ON, "full")


# ---- a frozen manifest that cannot be read is REFUSED, not a traceback (#1604)
def _capture(monkeypatch, tmp_path, capsys, manifest):
    """`main()` over a frozen manifest under a scratch FROZEN dir.

    The clean-tree precondition is answered, not exercised. The manifest and
    the reader are real; the plugin is never reached, because the key check
    refuses before anything is rendered."""
    frozen, out = tmp_path / "frozen", tmp_path / "out"
    frozen.mkdir()
    (frozen / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(c, "FROZEN", frozen)
    monkeypatch.setattr(c, "OUT", out)
    monkeypatch.setattr(c, "_git", lambda *args: "")
    code = c.main()
    return code, capsys.readouterr().out, out


def _committed_manifest():
    return json.loads((c.FROZEN / "manifest.json").read_text())


def test_the_committed_frozen_manifest_carries_the_keys_the_capture_reads():
    # the guard below must be satisfiable against the record that ships
    assert {"sample_rate_hz", "block_size_samples", "identity"} <= set(_committed_manifest())


@pytest.mark.parametrize("key", ["sample_rate_hz", "block_size_samples", "identity"])
def test_a_frozen_manifest_missing_a_key_is_refused(monkeypatch, tmp_path, capsys, key):
    manifest = {k: v for k, v in _committed_manifest().items() if k != key}
    code, out, refused = _capture(monkeypatch, tmp_path, capsys, manifest)
    assert code == 2 and "REFUSED" in out and f"'{key}'" in out
    assert json.loads((refused / "refused.json").read_text())["state"] == "REFUSED"


def test_an_empty_frozen_manifest_is_refused_naming_every_key(monkeypatch, tmp_path, capsys):
    # #1604's own input: `KeyError: 'sample_rate_hz'`, exit 1, before the repair
    code, out, _ = _capture(monkeypatch, tmp_path, capsys, {})
    assert code == 2 and all(f"'{k}'" in out for k in ("sample_rate_hz", "block_size_samples", "identity"))


def test_a_frozen_manifest_with_a_null_identity_is_refused(monkeypatch, tmp_path, capsys):
    code, out, _ = _capture(monkeypatch, tmp_path, capsys, {**_committed_manifest(), "identity": None})
    assert code == 2 and "'identity'" in out


@pytest.mark.parametrize("identity", [5, "x", []])
def test_a_frozen_manifest_with_a_wrong_typed_identity_is_refused(monkeypatch, tmp_path, capsys,
                                                                  identity):
    # #1679: `manifest["identity"][key]` is a TypeError past #1619's guard, and
    # a TypeError is not in main()'s refusal handler -- exit 1 by traceback on
    # a capture host, where `_plugin_metadata()` returns (measured with that
    # call stubbed). Without the plugin -- here, and in CI -- the reader
    # refuses one line EARLIER, "Mini V3 bundle metadata is missing", naming
    # neither the record nor the key. Red before the repair on both kinds of
    # host, for a different reason on each; green after.
    code, out, refused = _capture(monkeypatch, tmp_path, capsys,
                                  {**_committed_manifest(), "identity": identity})
    assert code == 2 and "REFUSED" in out
    assert "'identity'" in out and "dict" in out
    assert json.loads((refused / "refused.json").read_text())["state"] == "REFUSED"


def test_a_frozen_manifest_with_a_wrong_typed_rate_is_refused_by_the_comparison(
        monkeypatch, tmp_path, capsys):
    # per key, not a blanket container rule: the rate is an int that is only
    # COMPARED, so it declares no kind -- any other type simply differs, and
    # the reader's own refusal names that. Pinned so the per-key design is a
    # measured decision and not an omission.
    code, out, _ = _capture(monkeypatch, tmp_path, capsys,
                            {**_committed_manifest(), "sample_rate_hz": str(c.ref.SR)})
    assert code == 2 and "REFUSED" in out and "host rate/block differ" in out
