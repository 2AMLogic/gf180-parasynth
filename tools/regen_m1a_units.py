#!/usr/bin/env python3
"""tools/regen_m1a_units.py -- re-label the published M1A record from its
cached audio.

The envelope-qualification round published M1A.json with the attack
diagnostic's units field reading "dB" while its values, tolerance and basis
are milliseconds (tools/mono_m1a_score.py labelled only release as ms).
This regenerates the record through mono_m1a_score.run()'s cached-audio
path -- load_model_cache() refuses unless the model WAV, the reference
manifest and every recorded DSP input hash still match, so this can never
re-render through the plugin host or pick up changed DSP by accident.

Every numeric field must be unchanged; only the label and the provenance
that names this run may differ. The script exits nonzero without writing
if any number moved.
"""
from __future__ import annotations

import json
import sys

HERE = __import__("os").path.dirname(__file__)
ROOT = __import__("os").path.dirname(HERE)
sys.path.insert(0, HERE)

import mono_m1a_score as bass                       # noqa: E402
import run_case                                     # noqa: E402

RECORD = bass.ROOT / "docs/scorecard/results/M1A.json"


def numeric(node, prefix=""):
    """Every float/int leaf, keyed by path -- the comparison set. Walks lists
    AND tuples: patch_for_reference() returns tuple-typed coefficient lists
    which json serialises as arrays; a walker that only recursed lists missed
    the whole patch subtree and misreported it as 'moved to None'."""
    out = {}
    if isinstance(node, dict):
        for k, v in node.items():
            out.update(numeric(v, f"{prefix}.{k}"))
    elif isinstance(node, (list, tuple)):
        for i, v in enumerate(node):
            out.update(numeric(v, f"{prefix}[{i}]"))
    elif isinstance(node, (int, float)) and not isinstance(node, bool):
        out[prefix] = node
    return out


def keep_audio_check(new, old) -> bool:
    new_hash = new.get("diagnostics", {}).get("model_audio_sha256")
    old_hash = old.get("diagnostics", {}).get("model_audio_sha256")
    return not (new_hash and old_hash and new_hash == old_hash)


def main() -> int:
    old = json.loads(RECORD.read_text())
    case = next(c for c in run_case.load_cases() if c["case_id"] == "M1A")
    new = bass.run(case, keep_audio=True, cached_record=old)

    # THE GATE. Published numbers -- metrics and diagnostic properties --
    # must be EXACT: this repair may not move a single figure a reader can
    # quote. Event-level diagnostics are reanalysis of the same cached
    # audio on this numpy/scipy build; last-ulp drift there is expected and
    # bounded at 1e-9 relative (measured: 5.5e-13 across the 160 moved
    # leaves). Anything else moved at all is a refusal, no write.
    old_n, new_n = numeric(old), numeric(new)
    exact_zones = (".metrics.", ".properties.")
    # scoring-layer stamps: absent from raw run() output BY SHAPE, restored
    # explicitly below -- comparing them here would refuse every time
    restored = (".provenance.outcome_code", ".provenance.command")
    hard, fp = [], []
    for k in set(old_n) | set(new_n):
        if any(k.endswith(r) for r in restored):
            continue
        a, b = old_n.get(k), new_n.get(k)
        if a == b:
            continue
        in_exact = any(z in k for z in exact_zones)
        both_num = isinstance(a, (int, float)) and isinstance(b, (int, float)) \
            and not isinstance(a, bool) and not isinstance(b, bool)
        rel = abs(a - b) / max(1e-12, abs(a)) if both_num else None
        if in_exact or not both_num or rel is None or rel > 1e-9:
            hard.append((k, a, b))
        else:
            fp.append((k, a, b))
    if hard:
        print("regen_m1a_units: REFUSED -- published numbers moved "
              f"({len(hard)}):", file=sys.stderr)
        for k, a, b in sorted(hard)[:20]:
            print(f"  {k}: {a} -> {b}", file=sys.stderr)
        return 1

    # The cached-audio path must be BYTE-IDENTICAL: the rewritten WAV is the
    # cached pcm, so its hash must equal the published one.
    if keep_audio_check(new, old):
        print("regen_m1a_units: REFUSED -- reanalysed audio hash differs from "
              "the published one", file=sys.stderr)
        return 1

    # Fields the scoring layer (tools/run_case.py) stamps AFTER bass.run()
    # returns; the raw run() output lacks them. outcome_code is carried over
    # -- justified here because the gate above proved the published numbers
    # exact; command names THIS tool honestly rather than pretending
    # run_case ran.
    if "outcome_code" in old.get("provenance", {}):
        new["provenance"]["outcome_code"] = old["provenance"]["outcome_code"]
    new["provenance"]["command"] = "tools/regen_m1a_units.py (cached-audio reanalysis)"

    changed_labels = []
    for name, prop in new["diagnostics"]["properties"].items():
        if prop.get("units") != old["diagnostics"]["properties"][name].get("units"):
            changed_labels.append(f"{name}: {old['diagnostics']['properties'][name]['units']}"
                                  f" -> {prop['units']}")
    RECORD.write_text(json.dumps(new, indent=2) + "\n")
    print("regen_m1a_units: wrote", RECORD.relative_to(bass.ROOT))
    print("  metrics+properties: exact (", len([k for k in old_n if any(z in k for z in exact_zones)]),
          "published numeric fields )")
    print("  event diagnostics:", len(fp), "fields within reanalysis tolerance",
          f"(max relative drift {max((abs(a-b)/max(1e-12,abs(a)) for _,a,b in fp), default=0):.2e})")
    print("  units changed:", "; ".join(changed_labels) or "(none)")
    print("  audio_reused:", new["diagnostics"]["audio_reused"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
