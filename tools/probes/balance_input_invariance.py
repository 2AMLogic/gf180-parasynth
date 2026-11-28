#!/usr/bin/env python3
"""Does another commit's `model/drums_fx.py` change any INPUT to #396's balance
ablation render?

WHY THIS EXISTS. `docs/scorecard/cymbal-369/balance/balance-ablation.json` is
one render, recorded with the commit it came from (`cf2741e8`). When `main`
moves under the branch -- here #412/#388's rimshot work, which changed
`PEAK_RSG 0.343 -> 0.7728`, added `PEAK_RSG_REV14` and added
`RS_LO_X_ATT = 3` to the path word into `M_RS1` -- the question "does that
record now describe a tree that no longer exists?" has two possible answers and
only one of them is free. Reading the diff and reasoning that it is
"rimshot-only" is exactly the cheap internal check `CLAUDE.md` warns about: it
feels rigorous and it is not a measurement.

WHAT THIS MEASURES. The register images that are the renders' ONLY inputs, on
both versions of `drums_fx`, compared address by address:

  * `kit_808()` itself;
  * the six band-only calibration kits `calibrate()` renders (shipped and unit,
    three bands);
  * the ablation's own candidate kit, `kit_with_levels(amps)`, rebuilt from the
    amps the RECORD holds -- so this is the actual image that render consumed,
    not a re-derivation of it;
  * per sound, the two images `preservation()` compares (`render_drum_solo`'s
    shipped kit and `kit_with_levels(amps, sound)`).

Every render in `cymbal_candidate_eval.py` is a pure function of one of those
images plus the engine, so an address-identical image on both versions means a
bit-identical render -- for the CY renders, which is where every number in the
record except `preservation` comes from. The engine itself is in `drums_fx`
too, so this probe does NOT prove invariance for a sound whose image DID move
(RS): it says precisely which sounds are affected, and those have to be
re-rendered rather than argued about. That split -- what is proved and what is
merely narrowed -- is the deliverable.

REFUSES rather than answering when its own preconditions fail: the baseline rev
must exist, must contain `model/drums_fx.py`, the record's `amps` must be
readable, and every image value must be an int register word (a held JSON
`null` is allowed through -- the comparator must distinguish it from an absent
key, not refuse it -- but `bool`/`float`/`str` refuse). An invariance probe
that cannot load one of its two trees, or that compares a domain it has never
asserted, would report "no differences" for the most trivial possible wrong
reason.

`--json` is checked before the comparison runs: a missing parent is made, and
a directory given as the file, or a parent that cannot be made, is REFUSED
with nothing compared. A refusal is exit 2, on stderr.

Usage:
    python3 tools/probes/balance_input_invariance.py --baseline-rev f0c6816
    python3 tools/probes/balance_input_invariance.py --json /tmp/inv.json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
RECORD = ROOT / "docs" / "scorecard" / "cymbal-369" / "balance" / "balance-ablation.json"

# The comparison is fpga/record_compare's (#785) -- the one comparator in the
# tree with controls for absent-vs-null and for 1/True/1.0 -- not a copy of it.
# Module level, because it is compare() in THIS process that needs it:
# _install_drums_fx's sys.path edit runs only in the --emit subprocess.
sys.path.insert(0, str(ROOT / "fpga"))
import record_compare  # noqa: E402
_REFUSAL_TOOLS = str(pathlib.Path(__file__).resolve().parents[2] / "tools")
if _REFUSAL_TOOLS not in sys.path:
    sys.path.append(_REFUSAL_TOOLS)
import refusal  # noqa: E402


class Refused(refusal.Refused, RuntimeError):
    """A precondition of the comparison failed; no answer is given."""


# A JSON-representable marker for an address present on exactly one side of a
# pair. Distinct from every register value (ints) and from a held `null`, so
# moved_addresses never reports absence as a value (#803).
ABSENT = "absent"


# ---------------------------------------------------------------------------
# collection (runs in a subprocess so `drums_fx` can be swapped wholesale)
# ---------------------------------------------------------------------------


def _install_drums_fx(path: pathlib.Path) -> None:
    """Bind `drums_fx` to an arbitrary file BEFORE anything imports it.

    Done through `sys.modules` rather than `sys.path`, because every consumer
    here prepends `model/` to `sys.path` itself at import time and would
    otherwise win.
    """
    spec = importlib.util.spec_from_file_location("drums_fx", path)
    if spec is None or spec.loader is None:
        raise Refused(f"cannot load a drums_fx module from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["drums_fx"] = mod
    sys.path[:0] = [str(ROOT / "model"), str(ROOT / "tools"), str(ROOT / "audition")]
    spec.loader.exec_module(mod)


def _record_amps() -> dict:
    if not RECORD.exists():
        raise Refused(f"the ablation record is missing: {RECORD}")
    amps = json.loads(RECORD.read_text())["levels"]["amps"]
    out = {}
    for k, v in amps.items():
        out[int(k) if k.lstrip("-").isdigit() else k] = v
    if not any(isinstance(k, int) for k in out):
        raise Refused("the record's amps carry no mode levels")
    return out


def collect() -> dict:
    """Every register image the balance ablation renders, as {name: {addr: val}}."""
    import drums_fx as dx
    import cymbal_candidate as cc
    import cymbal_candidate_eval as cce
    import run_case as rc

    amps = _record_amps()
    shipped = dx.kit_808()
    unit = sorted(cce._variant(dict(cc.candidate_kit(
        {cc.M_CYH1: 0.25, dx.M_CYHI: 0.25, cc.M_CYH3B: 0.25}))).items())

    img = {"kit_808": dict(shipped), "candidate_unit": dict(unit)}
    for band in ("low", "decay", "short"):
        img[f"calib_shipped_{band}"] = dict(cc.band_only(shipped, band))
        img[f"calib_unit_{band}"] = dict(cc.band_only(unit, band))
    img["ablation_candidate_CY"] = dict(cce.kit_with_levels(amps))
    for s in dx.SOUND_NAMES:
        # `render_drum_solo`'s own kit, verbatim (run_case.py: `dx.kit_with_sounds(sound)`).
        img[f"solo_shipped_{s}"] = dict(dx.kit_with_sounds(s))
        img[f"solo_candidate_{s}"] = dict(cce.kit_with_levels(amps, s))

    names = _address_names(dx)
    # Which sound each address belongs to, from `preset_writes` -- drums_fx's own
    # per-sound write set, not a table maintained here. An address in exactly one
    # sound's preset writes is that sound's; the kit writes EVERY register before
    # any strike, so image-level difference alone cannot answer "whose render
    # moved" and this attribution is what does.
    owners: dict[int, list[str]] = {}
    for s in dx.SOUND_NAMES:
        for a, _ in dx.preset_writes(s):
            owners.setdefault(a, []).append(s)
    return {"images": {k: {str(a): v for a, v in sorted(v.items())} for k, v in img.items()},
            "address_names": {str(a): n for a, n in sorted(names.items())},
            "address_owners": {str(a): sorted(set(v)) for a, v in sorted(owners.items())},
            "cy_env_peak_addrs": [str(dx.A_ENV + e * dx.ENV_STRIDE + 1)
                                  for e in (dx.E_CYL, dx.E_CYD, dx.E_CYS)]}


def _address_names(dx) -> dict:
    """addr -> symbolic block name, so a differing address attributes itself.

    Built from `drums_fx`'s own `E_*` / `M_*` / `P_*` index symbols rather than
    a hand-written table, so a block added later cannot silently become
    "unknown" here.
    """
    out: dict[int, str] = {}
    for name in dir(dx):
        v = getattr(dx, name)
        if not isinstance(v, int) or isinstance(v, bool):
            continue
        if name.startswith("E_"):
            for i in range(dx.ENV_STRIDE):
                out.setdefault(dx.A_ENV + v * dx.ENV_STRIDE + i, f"{name}+{i}")
        elif name.startswith("M_"):
            for i in range(dx.MODE_STRIDE):
                out.setdefault(dx.A_MODE + v * dx.MODE_STRIDE + i, f"{name}+{i}")
        elif name.startswith("P_"):
            out.setdefault(dx.A_PATH + v, name)
    return out


# ---------------------------------------------------------------------------
# comparison
# ---------------------------------------------------------------------------


def _assert_int_images(images: dict, where: str) -> None:
    """Every image value is an int register word -- or REFUSE (#803).

    The measured domain, across every recorded baseline rev and the working
    tree, is int-only, and `drums_fx` encodes "no value" as ABSENCE
    (`if final_tau is not None`), never as a null; the old comparison's
    correctness silently rested on both facts without asserting either. A
    held JSON `null` is allowed through: the comparator distinguishes it from
    an absent key and moves the verdict, which is the loud direction.
    `bool` is refused although it subclasses `int` -- `1` and `True` are
    different records under `record_compare`'s type-strict equality.
    """
    bad = {}
    for key, img in images.items():
        hits = {a: v for a, v in img.items()
                if v is not None and (not isinstance(v, int) or isinstance(v, bool))}
        if hits:
            bad[key] = hits
    if bad:
        raise Refused(f"{where}: non-int register image value(s) "
                      f"{json.dumps(bad)[:400]}")


def _emit_for(drums_fx_path: pathlib.Path | None) -> dict:
    argv = [sys.executable, str(pathlib.Path(__file__).resolve()), "--emit"]
    if drums_fx_path is not None:
        argv += ["--drums-fx", str(drums_fx_path)]
    p = subprocess.run(argv, capture_output=True, text=True, cwd=str(ROOT))
    if p.returncode != 0:
        raise Refused(f"collection failed for {drums_fx_path or 'the working tree'}: "
                      f"{p.stderr.strip()[-2000:]}")
    return json.loads(p.stdout)


def compare(baseline_rev: str) -> dict:
    p = subprocess.run(["git", "show", f"{baseline_rev}:model/drums_fx.py"],
                       capture_output=True, text=True, cwd=str(ROOT))
    if p.returncode != 0 or not p.stdout:
        raise Refused(f"{baseline_rev} has no model/drums_fx.py (git: "
                      f"{p.stderr.strip()[:300]})")
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="drums_fx-baseline-")) / "drums_fx.py"
    tmp.write_text(p.stdout)

    old, new = _emit_for(tmp), _emit_for(None)
    if old["images"].keys() != new["images"].keys():
        raise Refused("the two trees collect different image sets")
    _assert_int_images(old["images"], f"baseline {baseline_rev}")
    _assert_int_images(new["images"], "the working tree")

    names, owners = new["address_names"], new["address_owners"]
    cy_peaks = set(new["cy_env_peak_addrs"])
    res = {"baseline_rev": baseline_rev, "images": {}, "moved_addresses": {}}
    for key in new["images"]:
        a, b = old["images"][key], new["images"][key]
        moved = record_compare.diff(a, b)
        res["images"][key] = {"identical": not moved, "n_moved": len(moved)}
        for k in moved:
            res["moved_addresses"].setdefault(k, {
                "name": names.get(k, "?"), "owners": owners.get(k, []),
                "old": a[k] if k in a else ABSENT,
                "new": b[k] if k in b else ABSENT,
                "images": []})
            res["moved_addresses"][k]["images"].append(key)

    # Whose renders moved. Every kit writes every register before any strike, so
    # an image diff is the same for all sixteen sounds and says nothing on its
    # own; the attribution below is what narrows it. A moved address owned by
    # exactly one sound can only reach that sound's render.
    owning = sorted({s for d in res["moved_addresses"].values() for s in d["owners"]})
    res["sounds_owning_moved_addresses"] = owning
    res["unattributed_moved_addresses"] = sorted(
        a for a, d in res["moved_addresses"].items() if not d["owners"])
    res["cy_owns_a_moved_address"] = "CY" in owning
    res["cy_env_peak_registers_moved"] = sorted(cy_peaks & set(res["moved_addresses"]))
    res["cy_render_inputs_identical"] = (
        not res["cy_owns_a_moved_address"]
        and not res["cy_env_peak_registers_moved"]
        and not res["unattributed_moved_addresses"])
    return res


def _require_writable(path: pathlib.Path) -> None:
    """REFUSE a `--json` destination that cannot receive the record, before the
    comparison runs.

    A missing parent is made here.  A directory given as the file, or a parent
    that cannot be made, is a refusal with nothing compared; found after the
    comparison instead, it was a `FileNotFoundError` and exit 1 (#2040)."""
    # is_dir() is inside the try: it re-raises EACCES (unsearchable ancestor) and
    # ENAMETOOLONG, which would exit 1 with a traceback instead of REFUSING.
    try:
        is_dir = path.is_dir()
        if not is_dir:
            path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise Refused(f"--json {path}: its parent directory cannot be created "
                      f"({exc.strerror or exc})") from exc
    if is_dir:
        raise Refused(f"--json {path} is a directory; name the file to write")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--baseline-rev", default="f0c6816",
                    help="the rev whose model/drums_fx.py is the baseline "
                         "(default: this PR's head before the merge)")
    ap.add_argument("--emit", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--drums-fx", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--json", type=pathlib.Path, default=None)
    a = ap.parse_args(argv)

    try:
        if a.emit:
            _install_drums_fx(pathlib.Path(a.drums_fx) if a.drums_fx
                              else ROOT / "model" / "drums_fx.py")
            json.dump(collect(), sys.stdout)
            return 0
        if a.json:
            _require_writable(a.json)
        res = compare(a.baseline_rev)
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2

    print(f"baseline {res['baseline_rev']} -> working tree: "
          f"{len(res['moved_addresses'])} register address(es) moved\n")
    for addr, d in sorted(res["moved_addresses"].items(), key=lambda kv: int(kv[0])):
        print(f"  addr {addr:>5}  {d['name']:<14} {d['old']} -> {d['new']}"
              f"   owned by {','.join(d['owners']) or 'NO SOUND (unattributed)'}")
    print(f"\nSounds owning a moved address : {res['sounds_owning_moved_addresses'] or 'none'}")
    print(f"Unattributed moved addresses  : {res['unattributed_moved_addresses'] or 'none'}")
    print(f"CY owns a moved address       : {res['cy_owns_a_moved_address']}")
    print(f"CY envelope peak regs moved   : {res['cy_env_peak_registers_moved'] or 'none'}")
    print(f"CY render inputs identical    : {res['cy_render_inputs_identical']}")
    if a.json:
        try:
            a.json.write_text(json.dumps(res, indent=1) + "\n")
        except OSError as e:
            print(f"REFUSED: --json {a.json} was not written ({e.strerror or e}); "
                  "the comparison above was made, its record does not exist", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
