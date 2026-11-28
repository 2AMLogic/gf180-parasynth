"""tools/test_refusal_siblings_cli.py -- issue #1681.

A CLI handler that names its own module's `Refused` does not catch a SIBLING
-- another subclass of `tools/refusal.Refused` -- and the refusal leaves the
CLI as an exit-1 traceback where the exit contract owes REFUSED (2). The
class is #1612 / #1624 / #1643 / #1671's; #1671 repaired the four sites in its
own files, and the same-shape search in its closing comment found these
outside them.

SHAPE A, `tools/sweep_m1a_attack.py`: `main` catches `bass.Refused`
(`mono_m1a_score.Refused`), and `render_point` -> `compare_audio` ->
`attack_fit_v3.attack_fit_v3` raises `measure_mono_m5a_reference.Refused`.
The census reads that call site `covered(cli-exact)`: it resolves the
callee's own raises, not the ones a frame deeper.

SHAPE B, the release CLIs: `release_manifest.presets()` calls `uh.preset_regs`
directly -- not through `uart_host.main`, so #1671's CLI boundary does not
reach it -- and `selected_preset.definition` raises `mono_m5a_score.Refused`.
`release_manifest.build()`, `r1_release.host_binding()` and
`r1_candidate.build()` each reach `presets()` under a `check()` or
`main --write` handler that names only their own class. The census has no row
for that call site at all: `uh.preset_regs` raises nothing in its own def.

KNOWN-ANSWER INPUTS. Shape A is the frozen M1A reference with event 0's
attack-fit window zeroed, as the sweep's render (the audio #1671's
`test_rescore_m1a_attack_v3.py` uses): it passes every phrase-level guard
`compare_audio` applies, by construction, and refuses INSIDE one event's
attack fit, with the sibling class. Shape B is a programming error, not an
audio state -- an engine profile or waveform name missing from the tables --
and is injected as `uh.preset_regs` raising the sibling.

WHAT IS STOOD IN, AND WHERE SHAPE B IS ARMED. Shape A stands in one thing:
`voice_fx.render_mono_fx` returns the known-answer audio instead of rendering.
Everything else is the real CLI -- `load_grid`'s git checks on the committed
grid, the frozen reference and its hash, `engine_configuration`,
`compare_audio` and the estimator that refuses. `AUDIO` and `OUT` point at
tmp_path, so a test that ever got past the refusal writes nothing into the
tree. Shape B is armed from INSIDE `rm.presets()`. Armed before `build()`,
the capture loop of each `build()` has already run the real CLI
(`uart_host.main`) when `presets()` is reached, and a poison installed by
then is met by that loop's `_capture` first (or, once #1671 lands, by the
CLI's boundary, which the loop's handler already names): the refusal ends as
the module's own Refused without ever reaching `presets()`, and the test
passes without measuring the handler it exists for. Armed inside, the sibling
arrives at the CLI's own `presets()` call unconverted. `r1_release.main` and
`r1_candidate.main` were not driven by the issue's trace; they are here.

THE REVERT CONTROLS are built from each module's own source: `_reverted`
re-executes the module with ONE handler put back to the exact class it named
before #1681 and nothing else, and the same injection must then leave that CLI
as the sibling.
Each asserts that the escape ran through the named site
(`release_manifest.presets`, `sweep_m1a_attack.render_point`), so a refusal
arriving by another path cannot satisfy it; and each refuses when the source
holds no such handler, so a repair reverted entirely turns both its tests red.

THE READ-NOT-TRACED SITES, each traced to a verdict and pinned where a pin
holds:

  * `fpga/verify_rolling_playback.py:92` runs under `main`'s
    `except (r1c.Refused, ValueError)`, but only ever as
    `intended(fx, image=target)`: `preset` is None, and `uh.preset_regs(None)`
    takes the default-patch branch, which never reaches
    `selected_preset.definition`. The sibling cannot arrive. NOT AN INSTANCE,
    pinned as "main passes no preset": adding one turns the pin red before
    the handler is left naming a sibling.
  * `fpga/release/r1_candidate.py`'s `main` has a third handler, `init-check`,
    which the issue's table lists beside the two it repairs. `init-check`
    loads no named preset for any fixture, so it never reaches
    `selected_preset.definition`. NOT AN INSTANCE, pinned; left on its own class.
  * `fpga/midi_session.py:339` runs under NEITHER of `main`'s two
    `except uh.Refused`: `MidiSession(...)` is constructed at `:1166`, outside
    every `try`. The sibling escapes there with NO handler in its path -- the
    unguarded constructor #1614 repairs, not this issue's
    handler-names-a-sibling shape. Recorded on #1614 and in #1681's PR; not
    repaired or pinned here.
"""
from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in ("tools", "fpga/release", "fpga", "model", "audition", "rtl-sketch"):
    if str(ROOT / _p) not in sys.path:
        sys.path.insert(0, str(ROOT / _p))

import attack_fit_v3                           # noqa: E402
import exit_contract                           # noqa: E402
import measure_mono_m5a_reference               # noqa: E402
import mono_m5a_score                           # noqa: E402
import r1_candidate as r1c                      # noqa: E402
import r1_release as r1r                        # noqa: E402
import refusal                                  # noqa: E402
import release_manifest as rm                   # noqa: E402
import selected_preset                          # noqa: E402
import sweep_m1a_attack as sweep                # noqa: E402
import uart_host as uh                          # noqa: E402

BASS = sweep.bass                               # mono_m1a_score: what the sweep names
VF = BASS.lead.vf                               # voice_fx: the sweep's render
ATTACK_SIBLING = attack_fit_v3.Refused          # what the attack fit raises
PRESET_SIBLING = selected_preset.score.Refused  # what selected_preset.definition raises
INJECTED = "injected: engine profile missing from the tables"
SILENT = "attack fit window is silent"

CLIS = [(rm, "--manifest"), (r1r, "--manifest"), (r1c, "--record")]
CLI_IDS = [mod.__name__ for mod, _ in CLIS]
#: (module, function, the handler's own class before #1681) -- the exact
#: expression each handler named, which is what a revert must put back
HANDLERS = [(mod, func, "Refused") for mod, _ in CLIS for func in ("check", "main")]
HANDLER_IDS = [f"{mod.__name__}.{func}" for mod, func, _ in HANDLERS]
SWEEP_HANDLER = (sweep, "main", "bass.Refused")   # it never had a `Refused` of its own


class _Globals:
    """A shipped module's globals, or a reverted copy's, addressed one way."""

    def __init__(self, ns):
        self.ns = ns

    def get(self, name):
        return self.ns[name] if isinstance(self.ns, dict) else getattr(self.ns, name)

    def patch(self, monkeypatch, name, value):
        if isinstance(self.ns, dict):
            self.ns[name] = value
        else:
            monkeypatch.setattr(self.ns, name, value)


def _reverted(module, func: str, own: str) -> dict:
    """A copy of `module`'s globals, re-executed from its own source with the
    ONE `except refusal.Refused` of function `func` put back to `own` -- the
    class that handler named before #1681 -- and nothing else. `own` is stated
    per site, not derived: it must be the EXACT shipped handler, and the
    first draft of this control wrote `Refused` into every module, which in
    `sweep_m1a_attack` (whose handler was `bass.Refused`) is a NameError --
    a different defect, raised instead of the escape being controlled for.
    Built from the source, it cannot drift from the shipped code. It refuses
    when the source holds no such handler, or more than one."""
    tree = ast.parse(inspect.getsource(module), module.__file__)
    body = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == func)
    hit = [h for h in ast.walk(body) if isinstance(h, ast.ExceptHandler)
           and isinstance(h.type, ast.Attribute) and h.type.attr == "Refused"
           and isinstance(h.type.value, ast.Name) and h.type.value.id == "refusal"]
    assert len(hit) == 1, (f"{module.__name__}.{func}: expected exactly one "
                           f"`except refusal.Refused`, found {len(hit)}: the repair "
                           "is missing, or its shape changed")
    hit[0].type = ast.parse(own, mode="eval").body
    ns = dict(vars(module))
    exec(compile(ast.fix_missing_locations(tree), module.__file__, "exec"), ns)
    return ns


def _ran_through(exc_info, module, func: str) -> bool:
    """Whether the exception's own traceback ran through `func` of `module`."""
    return any(fr.name == func and Path(fr.path) == Path(module.__file__)
               for fr in exc_info.traceback)


def test_the_two_siblings_are_refusal_refused_and_nothing_a_handler_named():
    """The precondition of every test below, asserted at the point of use: if
    the classes are ever re-based onto each other the escapes are gone and
    these tests no longer measure them."""
    assert ATTACK_SIBLING is measure_mono_m5a_reference.Refused
    assert issubclass(ATTACK_SIBLING, refusal.Refused)
    assert not issubclass(ATTACK_SIBLING, BASS.Refused)
    assert PRESET_SIBLING is mono_m5a_score.Refused
    assert issubclass(PRESET_SIBLING, refusal.Refused)
    for mod in (uh, rm, r1r, r1c):
        assert not issubclass(PRESET_SIBLING, mod.Refused), mod.__name__
    # `except refusal.Refused` reads the MODULE only while no local of the
    # function is named `refusal` (#1624: a local turned every exception
    # leaving the try into an UnboundLocalError)
    for mod, func, _ in HANDLERS + [SWEEP_HANDLER]:
        assert "refusal" not in getattr(mod, func).__code__.co_varnames, \
            f"{mod.__name__}.{func}"


# ---- shape A: the attack sweep ------------------------------------------------------
def _silent_attack_window() -> np.ndarray:
    """The frozen M1A reference as the sweep's render delivers it: int16
    scale, with event 0's attack-fit window zeroed -- [on - pre_ms,
    on - pre_ms + fit_ms), read from attack_fit_v3's own signature so the two
    cannot drift apart."""
    _manifest, reference = BASS.load_reference()
    event = BASS.reference.EVENTS[0]
    fit = inspect.signature(attack_fit_v3.attack_fit_v3).parameters
    sr = BASS.SR
    start = round(event["on_s"] * sr) - round(fit["pre_ms"].default * 1e-3 * sr)
    nfit = round(fit["fit_ms"].default * 1e-3 * sr)
    out = reference.copy()
    out[start:start + nfit] = 0.0
    return np.clip(np.round(out * 32767), -32768, 32767).astype(np.int16)


def _sweep_point(g: _Globals, tmp_path: Path, monkeypatch) -> int:
    """`sweep_m1a_attack main(["point", "10.0"])` -- the selected patch's own
    grid point -- on `g`, with the render stood in and both output
    directories under tmp_path."""
    monkeypatch.setattr(VF, "render_mono_fx", lambda *a, **k: _silent_attack_window())
    out = tmp_path / "out"
    g.patch(monkeypatch, "AUDIO", tmp_path / "audio")
    g.patch(monkeypatch, "OUT", out)
    rc = g.get("main")(["point", "10.0"])
    assert not out.exists() or not any(out.iterdir()), "the point's row must not be written"
    return rc


def test_the_degenerate_render_passes_the_phrase_guards_and_refuses_in_the_attack_fit():
    """Through the real scorer with no handler in the way: the phrase-level
    guards pass and attack_fit_v3 refuses with ITS class. The injection is
    live, and it is the known-answer input #1671 established."""
    pcm = _silent_attack_window().astype(np.float64) / 32768
    _manifest, reference = BASS.load_reference()
    with pytest.raises(ATTACK_SIBLING, match=SILENT):
        BASS.compare_audio(pcm, reference)


def test_a_silent_attack_window_in_the_sweep_point_is_refused_with_exit_2(tmp_path, monkeypatch,
                                                                         capsys):
    rc = _sweep_point(_Globals(sweep), tmp_path, monkeypatch)
    out = capsys.readouterr().out
    assert rc == exit_contract.REFUSED, out
    assert f"REFUSED: {SILENT}" in out


def test_control_the_reverted_sweep_handler_lets_the_sibling_escape(tmp_path, monkeypatch):
    """With ONLY `sweep_m1a_attack.main`'s handler put back to
    `except bass.Refused`, the same point leaves the CLI as the sibling --
    through `render_point`, the site it names."""
    with pytest.raises(ATTACK_SIBLING, match=SILENT) as ei:
        _sweep_point(_Globals(_reverted(*SWEEP_HANDLER)), tmp_path, monkeypatch)
    assert _ran_through(ei, sweep, "render_point")


def test_a_non_refusal_error_in_the_sweep_point_still_propagates(tmp_path, monkeypatch):
    """The control for the handler: it catches refusals by class and nothing
    wider (tools/refusal.py). An implementation error at the same call site is
    an EXECUTION ERROR; it must leave `main` untouched, never become REFUSED."""
    def broke(ours, ref, **_):
        raise ValueError("not a refusal: the estimator broke")

    monkeypatch.setattr(BASS, "compare_audio", broke)
    with pytest.raises(ValueError, match="the estimator broke"):
        _sweep_point(_Globals(sweep), tmp_path, monkeypatch)


# ---- shape B: the three release CLIs -------------------------------------------------
def _poison(log: list):
    def preset_regs(preset=None):
        log.append(preset)
        raise PRESET_SIBLING(INJECTED)
    return preset_regs


def _arm_inside_presets(monkeypatch, owner: _Globals, preset_regs, log: list) -> None:
    """Swap `uh.preset_regs` for `preset_regs` from INSIDE `owner`'s
    `presets()` (the module docstring says why arming earlier measures
    nothing). `owner` is the shipped `release_manifest`, or a reverted copy of
    it, whose `build()` looks `presets` up in its own globals."""
    real = owner.get("presets")

    def presets():
        monkeypatch.setattr(uh, "preset_regs", preset_regs)
        log.append("armed")
        return real()

    owner.patch(monkeypatch, "presets", presets)


def _drive(g: _Globals, flag: str, entry: str, out: Path):
    """One entry of the release CLI `g` (a shipped module or a reverted copy),
    its record under tmp_path: check() returns (verdict, detail); --write
    returns its exit code."""
    if entry == "check":
        return g.get("check")(out)
    return g.get("main")(["--write", flag, str(out)])


@pytest.mark.parametrize("entry", ["check", "write"])
@pytest.mark.parametrize("mod, flag", CLIS, ids=CLI_IDS)
def test_a_sibling_from_presets_is_refused(mod, flag, entry, tmp_path, monkeypatch, capsys):
    """The issue's traced sites: a `mono_m5a_score.Refused` raised inside
    `rm.presets()` used to leave check() as a traceback instead of
    ("REFUSED", reason), and `main --write` as exit 1 -- each under a handler
    that named only its own class. The detail is the sibling's OWN reason,
    unconverted: a refusal met by the capture loop instead reads `the CLI
    refused a supported command ...`, and one met by r1_release's host
    binding reads `the host's R1 bytes are not the frozen R1 target: ...`."""
    log = []
    _arm_inside_presets(monkeypatch, _Globals(rm), _poison(log), log)
    out = tmp_path / "record.json"
    if entry == "check":
        verdict, detail = _drive(_Globals(mod), flag, entry, out)
        assert verdict == "REFUSED", (verdict, detail)
        assert detail == INJECTED
    else:
        assert _drive(_Globals(mod), flag, entry, out) == exit_contract.REFUSED
        assert not out.exists()
        assert f"REFUSED -- {INJECTED}" in capsys.readouterr().out
    assert log == ["armed", None], log


@pytest.mark.parametrize("mod, func, own", HANDLERS, ids=HANDLER_IDS)
def test_control_the_reverted_handler_lets_the_sibling_escape(mod, func, own, tmp_path,
                                                                monkeypatch):
    """The revert control for `mod.func`: with ONLY that handler put back to
    its own Refused, the same injection leaves the CLI as the sibling --
    through `release_manifest.presets`, the site it names. A reverted
    `release_manifest` looks `presets` up in its own globals; the other two
    reach the shipped `rm.presets()`."""
    g = _Globals(_reverted(mod, func, own))
    log = []
    _arm_inside_presets(monkeypatch, g if mod is rm else _Globals(rm), _poison(log), log)
    out = tmp_path / "record.json"
    with pytest.raises(PRESET_SIBLING, match=INJECTED) as ei:
        _drive(g, dict(CLIS)[mod], "check" if func == "check" else "write", out)
    assert _ran_through(ei, rm, "presets")
    assert log == ["armed", None], log
    assert not out.exists()


@pytest.mark.parametrize("mod, flag", CLIS, ids=CLI_IDS)
def test_a_non_refusal_error_from_presets_still_propagates(mod, flag, tmp_path, monkeypatch):
    """The control for both handlers of each CLI: they catch refusals by class
    and nothing wider (tools/refusal.py). An implementation error at the same
    site is an EXECUTION ERROR; it must leave check() and main() untouched,
    never become a REFUSED verdict or exit 2."""
    def preset_regs(preset=None):
        raise KeyError("engine profile")

    _arm_inside_presets(monkeypatch, _Globals(rm), preset_regs, [])
    g, out = _Globals(mod), tmp_path / "record.json"
    with pytest.raises(KeyError, match="engine profile"):
        _drive(g, flag, "check", out)
    with pytest.raises(KeyError, match="engine profile"):
        _drive(g, flag, "write", out)
    assert not out.exists()


# ---- the read-not-traced sites: each verdict, pinned ----------------------------------
class _PastTheGuard(Exception):
    """Raised by the stood-in run_cli: main got past its guard block."""


def test_control_rolling_verifier_passes_no_preset_so_its_handler_meets_no_sibling(
        monkeypatch, tmp_path):
    """`verify_rolling_playback.py:92`, traced: NOT AN INSTANCE. It runs under
    `main`'s `except (r1c.Refused, ValueError)`, so the shape would be live
    if `main` ever passed a preset; it passes none. Pinned, because that is
    what keeps the handler right: if this goes red, `:92` now reaches
    `selected_preset.definition` and `:673` must catch `refusal.Refused`."""
    import verify_rolling_playback as vrp
    presets, fired = [], []
    real_intended = vrp.intended

    def intended(*args, **kwargs):
        presets.append(kwargs.get("preset"))
        return real_intended(*args, **kwargs)

    def definition(name):
        fired.append(name)
        raise PRESET_SIBLING(INJECTED)

    def stop(*args, **kwargs):
        raise _PastTheGuard()

    monkeypatch.setattr(vrp, "intended", intended)
    monkeypatch.setattr(selected_preset, "definition", definition)
    monkeypatch.setattr(vrp, "run_cli", stop)
    with pytest.raises(_PastTheGuard):
        vrp.main(["--outdir", str(tmp_path / "vrp"), "--epochs", "0"])
    assert presets and not any(presets), presets
    assert fired == []
    # the poison IS live where a preset is passed: :92 reaches it
    with pytest.raises(PRESET_SIBLING, match=INJECTED):
        real_intended(vrp.FIXTURES[0], preset="m5a-saw")


@pytest.mark.parametrize("fixture", ["demo", "bar808-full", "m5a", "none"])
def test_control_r1_candidate_init_check_loads_no_named_preset(fixture, monkeypatch):
    """`r1_candidate.main`'s third handler (`init-check`), which the issue's
    table lists beside the two it repairs: traced NOT AN INSTANCE and left on
    its own class, because `init-check` loads no named preset for any fixture
    and so never reaches `selected_preset.definition`. Pinned: if this goes
    red, the init-check path reaches a sibling and that handler must catch
    `refusal.Refused`."""
    fired = []

    def definition(name):
        fired.append(name)
        raise PRESET_SIBLING(INJECTED)

    monkeypatch.setattr(selected_preset, "definition", definition)
    assert r1c.main(["init-check", "--fixture", fixture]) in (0, 1)
    assert fired == []
