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
import refusal

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
def _scratch(monkeypatch, tmp_path, manifest=None, text=None):
    """A scratch FROZEN holding `manifest` -- or the raw `text` (#1985: a file
    that is not JSON at all) -- and a scratch OUT, both bound to the module.

    The clean-tree precondition is answered, not exercised. Returns the two
    directories so a test can plant a damaged file before `main()` runs."""
    frozen, out = tmp_path / "frozen", tmp_path / "out"
    frozen.mkdir()
    (frozen / "manifest.json").write_text(text if text is not None else json.dumps(manifest))
    monkeypatch.setattr(c, "FROZEN", frozen)
    monkeypatch.setattr(c, "OUT", out)
    monkeypatch.setattr(c, "_git", lambda *args: "")
    return frozen, out


def _capture(monkeypatch, tmp_path, capsys, manifest):
    """`main()` over a frozen manifest under a scratch FROZEN dir.

    The manifest and the reader are real. The #1604 controls pass a manifest
    the key check refuses, so nothing is rendered; the #1985 controls answer
    the identity check and stub the render, so the plugin is never loaded."""
    _scratch(monkeypatch, tmp_path, manifest)
    code = c.main()
    return code, capsys.readouterr().out, tmp_path / "out"


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


# ---- #1985: an execution error is not a refusal ----------------------------
# main()'s handler was typed (ref.Refused, OSError, ValueError, RuntimeError)
# over the WHOLE capture and wrote every one of them to refused.json as
# state "REFUSED". ref.Refused IS a RuntimeError (measure_mono_m5a_reference
# subclasses it), so that last name bought only the NON-refusal RuntimeErrors,
# and a ValueError from a bug in the capture was filed as a precondition that
# did not hold -- a record, which outlives the run. tools/refusal.py: an
# unexpected exception is an EXECUTION ERROR, never a refusal. RED FIRST
# against the shipped handler (2026-11-28): the three propagate controls over
# ValueError and RuntimeError failed with DID NOT RAISE (the KeyError and
# TypeError cells passed: the shipped tuple never held them), the base-class
# refusal control ESCAPED it -- it was typed with the LOCAL ref.Refused --
# and the rig, not-JSON and not-a-WAV controls were refused with the raw
# exception text instead of a stated reason. With the tuple narrowed and
# nothing else, the propagate controls passed and those three went red: each
# conversion below exists because of that run. The positive controls' teeth
# were checked by mutation: with the handler on this module's LOCAL Refused
# the base-class, render-module and not-JSON controls go red; without OSError
# the missing-manifest control does; with _render re-wrapping, or not
# converting, the render-module and rig controls do.
# NOT CLAIMED: dawdreamer is not installed on the host these were written on,
# so the rig's RuntimeError refusal is read from model/reference_rigs.py, not
# produced by a real MiniV3Rig, and the controls exercise main()'s class
# boundary with the render stubbed -- not a production path.

def _raise(exc, message):
    def f(*_args, **_kwargs):
        raise exc(message)
    return f


def _identity_matches(monkeypatch):
    # answer precondition 1 so the run reaches the first render, where the
    # controls inject; the plugin is never loaded
    monkeypatch.setattr(c.ref, "_plugin_metadata", lambda: dict(_committed_manifest()["identity"]))


@pytest.mark.parametrize(("target", "name", "exc"), [
    (c, "reproduce_frozen", ValueError),
    (c, "reproduce_frozen", RuntimeError),
    (c, "reproduce_frozen", KeyError),
    (c, "reproduce_frozen", TypeError),
    (c.m1a, "render", ValueError),
], ids=["capture ValueError", "capture RuntimeError", "capture KeyError",
        "capture TypeError", "render ValueError"])
def test_a_bug_in_the_capture_is_an_execution_error(monkeypatch, tmp_path, capsys, target, name, exc):
    _identity_matches(monkeypatch)
    monkeypatch.setattr(target, name, _raise(exc, "injected: a bug"))
    with pytest.raises(exc, match="injected"):
        _capture(monkeypatch, tmp_path, capsys, _committed_manifest())
    assert "REFUSED" not in capsys.readouterr().out
    assert not (tmp_path / "out" / "refused.json").exists()


def test_the_rig_construction_refusal_is_still_refused(monkeypatch, tmp_path, capsys):
    # The one RuntimeError on this path that is a refusal: MiniV3Rig's
    # construction refuses a changed plugin with RuntimeError
    # (model/reference_rigs.py: _qualify_pins, "unchanged contract: RuntimeError
    # on mismatch", and check_names, "parameter indices moved"), and m1a.render
    # builds the rig directly, without _render_segment's except Exception ->
    # Refused. Converted where it is expected (_render), so main's handler
    # needs no RuntimeError.
    _identity_matches(monkeypatch)
    monkeypatch.setattr(c.m1a, "render",
                        _raise(RuntimeError, "injected: miniv3: pinned settings did not hold"))
    code, out, refused = _capture(monkeypatch, tmp_path, capsys, _committed_manifest())
    assert code == 2 and "REFUSED: Mini V3 apparatus refused: injected" in out, out
    record = json.loads((refused / "refused.json").read_text())
    assert record["state"] == "REFUSED" and "injected" in record["reason"]


@pytest.mark.parametrize("exc", [refusal.Refused, c.ref.Refused],
                         ids=["base-class Refused", "render module's Refused"])
def test_a_refusal_from_the_render_is_still_refused_unchanged(monkeypatch, tmp_path, capsys, exc):
    # The paired positive control. refusal.Refused is the BASE class: a
    # cross-module refusal that is neither this module's nor the render
    # module's, so a handler narrowed to a LOCAL class lets it out. The render
    # module's own Refused is also a RuntimeError and must cross _render under
    # its own text, not re-wrapped.
    _identity_matches(monkeypatch)
    monkeypatch.setattr(c.m1a, "render", _raise(exc, "injected: a refusal"))
    code, out, refused = _capture(monkeypatch, tmp_path, capsys, _committed_manifest())
    assert code == 2 and "REFUSED: injected: a refusal" in out.splitlines(), out
    assert json.loads((refused / "refused.json").read_text()) == {"state": "REFUSED",
                                                                 "reason": "injected: a refusal"}


def test_a_frozen_manifest_that_is_not_json_is_refused(monkeypatch, tmp_path, capsys):
    # REFUSED before #1985 too, but only because JSONDecodeError is a
    # ValueError the tuple caught. #1604's contract for this tool is that a
    # frozen manifest it cannot read is REFUSED, so the read now refuses in its
    # own right and names the file; red with the tuple narrowed and the read
    # left as json.loads (run that way, 2026-11-28).
    frozen, out = _scratch(monkeypatch, tmp_path, text="{")
    assert c.main() == 2
    assert "REFUSED: " in capsys.readouterr().out
    record = json.loads((out / "refused.json").read_text())
    assert record["state"] == "REFUSED"
    assert "is not valid JSON" in record["reason"] and str(frozen / "manifest.json") in record["reason"]


def test_a_frozen_reference_that_is_not_a_wav_is_refused(monkeypatch, tmp_path, capsys):
    # REFUSED before #1985 too, but only because scipy's "File format not
    # understood" is a ValueError the tuple caught. A damaged tracked record is
    # an unmet precondition (tools/exit_contract.py: exit 2), not a FAIL; red
    # with the tuple narrowed and the read left as wavfile.read (run that
    # way, 2026-11-28).
    manifest = _committed_manifest()
    _identity_matches(monkeypatch)
    monkeypatch.setattr(c.m1a, "render", lambda *a, **k: (np.zeros(16, dtype=np.float32), {}))
    frozen, out = _scratch(monkeypatch, tmp_path, manifest)
    (frozen / manifest["renders"][0]["file"]).write_text("not a wav")
    assert c.main() == 2
    assert "REFUSED: " in capsys.readouterr().out
    record = json.loads((out / "refused.json").read_text())
    assert record["state"] == "REFUSED" and manifest["renders"][0]["file"] in record["reason"]


def test_a_missing_frozen_manifest_is_refused(monkeypatch, tmp_path, capsys):
    # the reason OSError stays in the handler: the frozen record is read by
    # path, and a missing one is a missing precondition
    frozen, out = _scratch(monkeypatch, tmp_path, {})
    (frozen / "manifest.json").unlink()
    assert c.main() == 2
    assert "REFUSED: " in capsys.readouterr().out
    record = json.loads((out / "refused.json").read_text())
    assert record["state"] == "REFUSED" and "No such file" in record["reason"]
