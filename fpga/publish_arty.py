"""Publish routed Arty evidence, preserving unqualified I/O and DSP warnings.

The publication binds its proofs at publish time (refusing drift):

  * the digital verification record is derived from the WRAPPER the build
    actually compiled (parsed from build.tcl's -top), never a free-form
    path; a wrapper with no bound evidence is refused, and the record is
    hash-validated against the wrapper's COMPILED source set -- so a
    record captured before a compiled source changed cannot bind, and
    VERIFICATION_BY_WRAPPER must name a run of the CURRENT tree;
  * the compiled source set (read_verilog list) must equal the build
    record's source_sha256 -- an artifact whose build.tcl reads sources
    the record (and proof) never covered is refused;
  * the compiled XDC must carry exactly the approved external-I/O
    constraint set (fpga/ext_io_timing.py::xdc_contract_drift), the
    routed report's output-delay exceptions must be exactly the permitted
    list, and UART ports may appear only with their constraints and
    disposition (uart_gate_drift).
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import re
import shutil
import build_arty as build
import ext_io_timing as iotime

ROOT = Path(__file__).resolve().parents[1]

# wrapper top -> the digital proof bound to it. Not a free-form argument:
# a wrapper absent from this map has NO evidence and publication refuses.
# Since the UART bridge merged, arty_a7_top IS the UART wrapper (uart_rxd/
# uart_txd ports, uart_bridge.v in the compiled set), so its proof is the
# UART-bridge clean run. No arty_a7_uart_top module exists in this tree --
# a build.tcl claiming it has no evidence.
#
# The proof must cover the tree it is bound to, so this entry MOVES whenever
# a compiled source changes; validate_verification hash-checks it against the
# live source set and refuses otherwise. Superseded runs stay where they are:
#   reports/arty/clean            the pre-uart SPI wrapper (fpga/verify_arty.py)
#   reports/arty/uart-clean       the pre-drift UART wrapper -- still the proof
#                                 the PUBLISHED integrated baseline bitstream
#                                 cites by hash, so it is never rewritten in place
#   reports/arty/drift-clean      per-oscillator drift in voice_dp.v (contract
#                                 6.11, DR 0019) moved the frame's sample strobe
#                                 from cycle 175 to 176 and left the audio
#                                 byte-identical. See that directory's README.
#   reports/arty/shark-blamp-clean  this tree: the shark-tooth's polyBLAMP
#                                 correction (DR 0017) appended states after
#                                 drift's S_DR1 and left this bench's audio,
#                                 timing and transcript byte-identical. See
#                                 that directory's README.
VERIFICATION_BY_WRAPPER = {
    "arty_a7_top": ROOT / "fpga/reports/arty/shark-blamp-clean/verification.json",
}


def compiled_inputs(build_tcl_text):
    """(top, [repo-relative sources], [repo-relative xdc]) from build.tcl."""
    ver = re.search(r"^read_verilog \[list(.*)\]\s*$", build_tcl_text, re.M)
    xdc = re.search(r"^read_xdc \{([^}]+)\}", build_tcl_text, re.M)
    top = re.search(r"^synth_design -top (\S+)", build_tcl_text, re.M)
    if not (ver and xdc and top):
        raise ValueError("build.tcl does not declare the compiled inputs")
    def rel(p):
        parts = p.split("/inputs/", 1)
        if len(parts) != 2:
            raise ValueError("build.tcl reads an unsnapshotted input: " + p)
        return parts[1]
    sources = [rel(p) for p in re.findall(r"\{([^}]+)\}", ver.group(1))]
    return top.group(1), sources, [rel(xdc.group(1))]


# the evidence set a DSP disposition must carry, beside the analysis
DSP_EVIDENCE_FILES = ("MANIFEST.sha256", "dsp_cells_dump.txt",
                      "drc_dpreg_names.txt", "drc_rpt.sha256",
                      "dsp-opmode-analysis.json")
# the files the extraction host's manifest must pin (routed_dcp.sha256 is
# checked separately so its absence refuses for its own reason)
DSP_MANIFEST_PINNED = ("dsp_cells_dump.txt", "drc_dpreg_names.txt",
                       "drc_rpt.sha256", "routed_dcp.sha256")


def _analyser():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "dsp_dpreg_analyse", ROOT / "tools" / "dsp_dpreg_analyse.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _p_selecting(rec):
    """First reachable OPMODE of an analysed cell that selects P, or None."""
    for v in rec.get("reachable_opmode_values", []):
        if v["x"] == "P" or v["z"].startswith("P"):
            return v
    return None


def dsp_disposition(artifact: Path, record: dict | None = None) -> dict:
    """Derive the DPREG-4 DSP feedback review state for THIS implementation.

    There is no manual flip. The review is complete only when, in order:

      1. the evidence set is present (dsp-dpreg-evidence/ with the dump,
         names file, DRC digest, manifest and the accepted analysis);
      2. the extraction host's manifest validates and pins the dump, the
         names file and BOTH digest records;
      3. the evidence names -- by sha256 -- the routed.dcp this publication
         names (report.json artifact_sha256["routed.dcp"]), the dump opened
         that same checkpoint path, and a routed.dcp present beside the
         artifact hashes to it; its drc.rpt digest is this build's;
      4. the accepted analysis (dsp-opmode-analysis.json) covers exactly the
         target set DERIVED from this artifact's drc.rpt DPREG-4 findings,
         and is of this dump;
      5. a fresh structural derivation (tools/dsp_dpreg_analyse.derive)
         answers, agrees with the accepted analysis cell by cell, and finds
         no reachable OPMODE that selects P.

    Anything else is REFUSED, recorded as data with its reason -- never a
    crash, never a silent true, and never a new routing run."""
    ev = artifact / "dsp-dpreg-evidence"

    def refused(reason: str) -> dict:
        return {"complete": False, "reason": reason}

    if record is None:
        rp = artifact / "report.json"
        if not rp.is_file():
            return refused("no build record (report.json) names the routed checkpoint")
        record = json.loads(rp.read_text())
    named = record.get("artifact_sha256", {})
    dcp_named, drc_named = named.get("routed.dcp"), named.get("drc.rpt")
    if not dcp_named or not drc_named:
        return refused("the build record names no routed.dcp/drc.rpt digest to bind to")

    # 1. evidence present
    if not ev.is_dir():
        return refused("missing evidence: no dsp-dpreg-evidence directory bound to this artifact")
    missing = [n for n in DSP_EVIDENCE_FILES if not (ev / n).is_file()]
    if missing:
        return refused(f"missing evidence file(s): {missing}")
    # the checkpoint digest is required before anything else is trusted
    dcp_rec = ev / "routed_dcp.sha256"
    if not dcp_rec.is_file():
        return refused(
            "evidence records no routed.dcp digest (dsp-dpreg-evidence/"
            "routed_dcp.sha256): the extraction names its checkpoint by path "
            "only, so it cannot be bound to the routed.dcp this publication "
            f"names ({dcp_named}); a bounded read-only re-extraction on the "
            "build host must record it")
    an = _analyser()
    # 2. manifest validates and pins what is bound
    try:
        an.verify_manifest(ev)
    except an.Refused as exc:
        return refused(f"evidence manifest refused: {exc}")
    pinned = {line.split(None, 1)[1].strip().lstrip("*")
              for line in (ev / "MANIFEST.sha256").read_text().splitlines()
              if line.strip()}
    # 3. the routed checkpoint, by digest
    unpinned = [n for n in DSP_MANIFEST_PINNED if n not in pinned]
    if unpinned:
        return refused(f"evidence manifest does not pin {unpinned}")
    words = dcp_rec.read_text().split()
    if len(words) != 2 or not re.fullmatch(r"[0-9a-f]{64}", words[0]):
        return refused("malformed routed_dcp.sha256 (want '<sha256>  <path>')")
    dcp_have, dcp_path = words
    if dcp_have != dcp_named:
        return refused(
            f"wrong routed checkpoint: evidence was extracted from routed.dcp "
            f"{dcp_have[:16]}, this publication names {dcp_named[:16]}")
    head = (ev / "dsp_cells_dump.txt").read_text().splitlines()[:3]
    if f"DCP {dcp_path}" not in head:
        return refused(
            "wrong routed checkpoint: the dump did not open the checkpoint "
            f"the digest record names ({dcp_path})")
    local = artifact / "routed.dcp"
    if local.is_file() and build.sha(local) != dcp_named:
        return refused("wrong routed checkpoint: routed.dcp beside this artifact "
                       "does not hash to the digest the build record names")
    drc_have = (ev / "drc_rpt.sha256").read_text().split()[0].strip()
    if drc_have != drc_named:
        return refused(
            f"evidence DRC is not this build's: drc.rpt {drc_have[:16]} vs "
            f"the build record's {drc_named[:16]}")
    # 4. the accepted analysis covers the DRC-derived target set, this dump
    drc = artifact / "drc.rpt"
    try:
        an.verify_drc_identity(ev, drc)
        targets = an.parse_drc_targets(drc.read_text())
    except an.Refused as exc:
        return refused(f"DRC-derived target set refused: {exc}")
    try:
        accepted = json.loads((ev / "dsp-opmode-analysis.json").read_text())
        acc_targets = list(accepted["required_instances"])
        acc_cells = {c["name"]: c for c in accepted["cells"]}
    except (ValueError, KeyError, TypeError) as exc:
        return refused(f"accepted analysis unreadable: {exc!r}")
    covered = [t for t in targets if t in acc_cells and t in acc_targets]
    if len(covered) != len(targets) or set(acc_targets) != set(targets):
        absent = [t for t in targets if t not in covered]
        return refused(
            f"incomplete target set: accepted analysis covers {len(covered)} of "
            f"{len(targets)} DRC-derived DPREG-4 targets (missing {absent[:3]})")
    if accepted.get("dump_sha256") != build.sha(ev / "dsp_cells_dump.txt"):
        return refused("accepted analysis is not of this dump (dump_sha256 differs)")
    # 5. re-derive, agree, and look for a counterexample
    try:
        fresh_targets, fresh, _ = an.derive(ev, drc)
    except an.Refused as exc:
        return refused(f"analyser refused (NO VERDICT): {exc}")
    if fresh_targets != targets:
        return refused("analyser derived a different target set")
    for rec in fresh:
        acc = acc_cells[rec["name"]]
        if acc.get("p_feedback_reachable") != rec["p_feedback_reachable"]:
            return refused(
                f"accepted analysis disagrees with re-derivation on {rec['name']}")
        hit = _p_selecting(rec)
        if rec["p_feedback_reachable"] or hit:
            hit = hit or {"opmode": "?", "x": "?", "z": "?"}
            return refused(
                f"reachable P-feedback counterexample: {rec['name']} OPMODE "
                f"{hit['opmode']} (X={hit['x']}, Z={hit['z']}) selects P")
    verdict = (f"VERDICT: all {len(fresh)} cells: P-feedback unreachable on "
               f"every reachable OPMODE")
    return {"complete": True, "reason": "", "verdict": verdict,
            "targets": len(fresh), "routed_dcp_sha256": dcp_named,
            "drc_rpt_sha256": drc_have,
            "analysis_dump_sha256": accepted["dump_sha256"]}


def apply_dsp(summary: dict, dsp: dict) -> None:
    """Carry a derived disposition into a publication summary: the flag,
    the disposition record, and -- when refused -- the DPREG-4 line in
    remaining_review with its reason. The only writer of the flag."""
    review = [r for r in summary.get("remaining_review", [])
              if "DPREG-4 DSP feedback warnings" not in r]
    if not dsp["complete"]:
        n = summary["drc"].get("DPREG-4", {}).get("count", 0)
        review.append(f"{n} DPREG-4 DSP feedback warnings ({dsp['reason']})")
    summary["remaining_review"] = review
    summary["dsp_feedback_review_complete"] = dsp["complete"]
    summary["dsp_disposition"] = dsp


def copy_evidence(src: Path, dst: Path) -> dict:
    """Ship the DSP evidence bundle with the publication, so the verdict can
    be re-derived from the published directory alone. Integrity-checked
    after copying: every copied file must hash to its source, and the copy's
    own manifest must validate. Any difference refuses publication."""
    dst.mkdir()
    files = sorted(f for f in src.iterdir() if f.is_file())
    for f in files:
        shutil.copyfile(f, dst / f.name)
    digests = {}
    for f in files:
        have = build.sha(dst / f.name)
        if have != build.sha(f):
            raise ValueError("evidence copy differs from the artifact: " + f.name)
        digests[f.name] = have
    # a source bundle that fails its own manifest is already a DSP refusal
    # (recorded by dsp_disposition) and must not block the publication; a
    # source that validates must still validate once copied
    an = _analyser()
    try:
        an.verify_manifest(src)
    except an.Refused:
        return digests
    try:
        an.verify_manifest(dst)
    except an.Refused as exc:
        raise ValueError(f"published evidence manifest refused: {exc}") from exc
    return digests


def rederive_dsp(publication: Path) -> dict:
    """Re-derive the DSP disposition of a COMMITTED publication from its own
    committed evidence and rewrite only those fields. This is how a record
    is reconciled with its checker without re-publishing (the committed
    directory carries no routed.dcp): what it says is what it derives."""
    path = publication / "publication.json"
    summary = json.loads(path.read_text())
    record = {"artifact_sha256": summary["original_artifact_sha256"]}
    apply_dsp(summary, dsp_disposition(publication, record))
    path.write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def publish(artifact, output):
    record = json.loads((artifact / "report.json").read_text())
    expected = {str(p.relative_to(ROOT)): build.sha(p)
                for p in build.sources() + build.roms() + [build.XDC]}
    if (record.get("state") != "BUILT_REQUIRES_TIMING_REVIEW" or record.get("exit_code") != 0
            or record.get("part") != build.PART or record.get("configuration") != build.CONFIG
            or record.get("source_sha256") != expected):
        raise ValueError("build is not a successful, current, selected Arty implementation")
    top, compiled_sources, compiled_xdc = compiled_inputs(
        (artifact / "build.tcl").read_text())
    # the compiled set must be exactly the recorded set minus the ROM data
    # files (hex parameters are not read_verilog'd): a build.tcl that reads
    # sources the record (and the proof) never covered is refused
    rom_keys = {str(p.relative_to(ROOT)) for p in build.roms()}
    non_rom = sorted(set(expected) - rom_keys)
    if sorted(compiled_sources + compiled_xdc) != non_rom:
        raise ValueError("compiled input set differs from the build record's "
                         "source_sha256")
    ver_path = VERIFICATION_BY_WRAPPER.get(top)
    if ver_path is None:
        raise ValueError("no bound verification evidence for wrapper " + top)
    proof = build.validate_verification(ver_path,
                                        build.sources() + build.roms())
    if proof != record.get("verification"):
        raise ValueError("digital verification binding differs")
    required = {"arty.bit", "timing.rpt", "clocks.rpt", "utilization.rpt", "drc.rpt", "routed.dcp"}
    if set(record.get("artifact_sha256", {})) != required:
        raise ValueError("build artifact set incomplete")
    for name, digest in record["artifact_sha256"].items():
        path = artifact / name
        if not path.is_file() or not path.stat().st_size or build.sha(path) != digest:
            raise ValueError("build artifact missing or changed: " + name)
    if build.sha(artifact / "build.tcl") != record["script_sha256"]:
        raise ValueError("build script changed")
    summary = inspect_reports(artifact)
    # constraint identity at publication: the XDC the build actually used
    # must be the approved budget, the exceptions the permitted list, and
    # any UART port fully constrained with a recorded disposition
    for rel in compiled_xdc:
        xdc_snap = artifact / "inputs" / rel
        if not xdc_snap.is_file():
            raise ValueError("compiled XDC snapshot missing: " + rel)
        drift = iotime.xdc_contract_drift(xdc_snap.read_text(),
                                          exceptions=summary["output_delay_exceptions"])
        drift += iotime.uart_gate_drift(xdc_snap.read_text())
        if drift:
            raise ValueError("external-I/O constraint drift: " + "; ".join(drift))
    remaining = ["physical programming, control and audio capture"]
    dsp = dsp_disposition(artifact, record)
    if summary["external_io_timing_qualified"]:
        remaining.insert(0, "spi_miso status readback is qualified only at SCK <= 1.4 MHz, "
                            "not at the 2.0 MHz write ceiling (fpga/ext_io_timing.py)")
    else:
        remaining.insert(0, "DAC/controller output timing")
    summary.update(state="BUILT_INTERNAL_TIMING_PASS_REVIEW_REQUIRED", configuration=build.CONFIG,
                   part=build.PART, bitstream_sha256=record["artifact_sha256"]["arty.bit"],
                   source_sha256=expected, verification=proof,
                   tool=record["vivado_version"], build_seconds=record["seconds"],
                   original_artifact_sha256=record["artifact_sha256"],
                   report_transformation="Host header omitted; numerical report contents unchanged",
                   remaining_review=remaining)
    # derived, never flipped (dsp_disposition above)
    apply_dsp(summary, dsp)
    manifest = artifact.parent / "input-bundle.json"
    if manifest.is_file():
        summary["input_bundle"] = json.loads(manifest.read_text())
    if output.exists() and any(output.iterdir()):
        raise ValueError("publication directory must be empty")
    output.mkdir(parents=True, exist_ok=True)
    for name in ("report.json", "build.tcl", "arty.bit"):
        shutil.copyfile(artifact / name, output / name)
    for name in required - {"arty.bit", "routed.dcp"}:
        content = (artifact / name).read_text()
        content = re.sub(r"^\| Host\s*:.*$", "| Host         : omitted from public report",
                         content, flags=re.MULTILINE)
        (output / name).write_text(content)
    evidence = artifact / "dsp-dpreg-evidence"
    if evidence.is_dir():
        summary["published_evidence_sha256"] = copy_evidence(
            evidence, output / "dsp-dpreg-evidence")
        # the publication must reproduce its own verdict from what it ships
        if dsp["complete"] and dsp_disposition(output, record) != dsp:
            raise ValueError("published evidence does not reproduce the DSP disposition")
    summary["published_sha256"] = {p.name: build.sha(p) for p in output.iterdir()
                                   if p.is_file()}
    (output / "publication.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def inspect_reports(directory):
    try:
        texts = {name: (directory / name).read_text() for name in
                 ("timing.rpt", "clocks.rpt", "utilization.rpt", "drc.rpt")}
    except OSError as exc:
        raise ValueError("missing implementation report") from exc
    timing = texts["timing.rpt"]
    for content in texts.values():
        if (not re.search(r"\| Design\s*: arty_a7_top\s*$", content, re.MULTILINE)
                or not re.search(r"\| Design State\s*: (Fully )?Routed\s*$", content, re.MULTILINE)):
            raise ValueError("report is not the routed Arty design")
    try:
        section = timing.split("| Design Timing Summary", 1)[1].split("| Clock Summary", 1)[0]
        rows = [s.split() for s in section.splitlines() if re.match(r"^\s*[-+]?\d+\.\d+\s", s)]
        if len(rows) != 1 or len(rows[0]) != 12:
            raise ValueError("unexpected timing summary shape")
        names = ("wns_ns", "tns_ns", "setup_failing", "setup_endpoints", "whs_ns", "ths_ns",
                 "hold_failing", "hold_endpoints", "wpws_ns", "tpws_ns", "pulse_failing", "pulse_endpoints")
        metrics = dict(zip(names, map(float, rows[0])))
        for kind in ("no_clock", "unconstrained_internal_endpoints", "loops", "generated_clocks"):
            counts = re.findall(r"checking " + kind + r" \((\d+)\)", timing)
            if not counts or any(int(n) for n in counts):
                raise ValueError("internal timing is incomplete: " + kind)
        missing = int(re.search(r"checking no_output_delay \((\d+)\)", timing)[1])
        # The check_timing verbose block classifies the unconstrained output
        # ports: unconstrained (HIGH), excused by false paths, and ports that
        # carry a timing clock -- the forwarded-clock class. Only that last
        # class is a justified exception; the sentences must be present and
        # are refused if the tool stops reporting them.
        def no_output_delay_sentence(marker):
            match = re.search(r"There (?:are|is) (\d+) ports? " + marker, timing)
            if match is None:
                raise ValueError("report does not classify no_output_delay ports: " + marker)
            return int(match[1])
        unconstrained = no_output_delay_sentence("with no output delay specified")
        false_pathed = no_output_delay_sentence(
            "with no output delay but user has a false path constraint")
        clock_carriers = no_output_delay_sentence(
            "with no output delay but with a timing clock defined on it")
        if unconstrained + false_pathed + clock_carriers != missing:
            raise ValueError("no_output_delay classes do not sum to the count")
        exceptions = []
        if clock_carriers:
            listed = re.search(r"with no output delay but with a timing clock defined on it"
                               r"[^\n]*\n\n(.*?\n)\n", timing, re.DOTALL)
            if listed is None or len(listed[1].split()) != clock_carriers:
                raise ValueError("forwarded-clock exception ports are not listed")
            exceptions = listed[1].split()
        match = re.search(r"^hardware_clock\.clock_raw\s+([\d.]+)\s+.*P,G,A\s+"
                          r"\{hardware_clock\.mmcm/CLKOUT0\}", texts["clocks.rpt"], re.MULTILINE)
        period = float(match[1])
        if not math.isclose(period, 1000 / 12.288, abs_tol=0.0005, rel_tol=0):
            raise ValueError("wrong core clock period")
        resources = {}
        for name in ("Slice LUTs", "Slice Registers", "DSPs", "Block RAM Tile", "Bonded IOB"):
            row = re.search(r"^\|\s*" + re.escape(name) + r"\s*\|([^\n]+)",
                            texts["utilization.rpt"], re.MULTILINE)
            columns = [float(s.strip()) for s in row[1].split("|") if s.strip()]
            used, available = columns[0], columns[3]
            if not 0 <= used <= available:
                raise ValueError("resource overflow: " + name)
            resources[name] = {"used": used, "available": available}
        drc = {m[1]: {"severity": m[2].strip(), "count": int(m[3])} for m in re.finditer(
            r"^\|\s*([A-Z][A-Z0-9-]+)\s*\|\s*([^|]+)\|[^|]+\|\s*(\d+)\s*\|",
            texts["drc.rpt"], re.MULTILINE)}
        total = int(re.search(r"Checks found:\s*(\d+)", texts["drc.rpt"])[1])
        if sum(v["count"] for v in drc.values()) != total:
            raise ValueError("DRC table incomplete")
    except (IndexError, TypeError, AttributeError) as exc:
        raise ValueError("unrecognized implementation report") from exc
    if (not all(math.isfinite(v) and v >= 0 for v in metrics.values())
            or any(metrics[k] != 0 for k in ("tns_ns", "ths_ns", "tpws_ns", "setup_failing",
                                             "hold_failing", "pulse_failing"))
            or any(metrics[k] <= 0 for k in ("setup_endpoints", "hold_endpoints", "pulse_endpoints"))):
        raise ValueError("internal timing does not pass")
    if any(v["severity"] not in ("Warning", "Advisory") for v in drc.values()):
        raise ValueError("DRC error or critical warning")
    return {"internal_timing_pass": True, "timing": metrics, "core_period_ns": period,
            "resources": resources, "missing_output_delays": missing, "drc": drc,
            # External I/O timing counts as qualified only when the routed
            # report itself shows no unconstrained output port and no output
            # port excused by a false path. The one permitted remainder is the
            # forwarded-clock class (a port carrying a timing clock), recorded
            # here as data; the timing metrics above already exclude any
            # failing endpoint.
            "external_io_timing_qualified": unconstrained == 0 and false_pathed == 0,
            "output_delay_exceptions": exceptions,
            "hardware_playback_tested": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--rederive-dsp", action="store_true",
                        help="re-derive the DSP disposition of the committed "
                             "publication directory ARTIFACT in place")
    args = parser.parse_args()
    if args.rederive_dsp:
        print(json.dumps(rederive_dsp(args.artifact)["dsp_disposition"], indent=2))
    elif args.out is None:
        parser.error("--out is required to publish")
    else:
        print(json.dumps(publish(args.artifact, args.out), indent=2))
