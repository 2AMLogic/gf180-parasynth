#!/usr/bin/env bash
# run-librelane.sh -- implement synth_top into the wafer.space gf180mcu project
# template (half-height 1x0.5 slot) with LibreLane, inside a pinned container.
#
#   ./run-librelane.sh floorplan          # synth + floorplan only: measures utilisation
#   ./run-librelane.sh full               # whole Chip flow, sign-off checkers skipped
#   ./run-librelane.sh signoff            # whole Chip flow including DRC/LVS/XOR/IR-drop
#   ./run-librelane.sh full --run-tag foo # extra args go to librelane
#
# The upstream template drives LibreLane from a Nix shell (`nix-shell; make librelane`).
# There is no Nix on this host, so this runs the SAME LibreLane version the template's
# flake.lock pins -- librelane/librelane f18a07a == 3.1.0.dev2 -- from its official
# container, natively on arm64 (no emulation).
#
# Env:
#   PDK_ROOT   where ciel put gf180mcuD           (default: $LL_SCRATCH/pdk)
#   RUNS_DIR   where LibreLane writes its run dir (default: $LL_SCRATCH/runs)
#   DENSITY    PL_TARGET_DENSITY_PCT, an INPUT    (default: unset -> LibreLane's own default)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO="$(cd "$HERE/../.." && pwd -P)"
MODE="${1:?usage: run-librelane.sh {floorplan|full|signoff} [librelane args...]}"; shift || true

IMAGE="${LL_IMAGE:-ghcr.io/librelane/librelane:3.1.0.dev2-aarch64}"
LL_SCRATCH="${LL_SCRATCH:-/private/tmp/gf180-shuttle}"
PDK_ROOT="${PDK_ROOT:-$LL_SCRATCH/pdk}"
RUNS_DIR="${RUNS_DIR:-$LL_SCRATCH/runs}"
PDK="${PDK:-gf180mcuD}"
SCL="${SCL:-gf180mcu_fd_sc_mcu7t5v0}"   # 7-track 5 V, same library as pnr/orfs
PAD="${PAD:-gf180mcu_fd_io}"
mkdir -p "$PDK_ROOT" "$RUNS_DIR"

# PL_TARGET_DENSITY_PCT is an INPUT. It is written to its own file so that it is
# never mistaken for something the flow measured.
DENSITY_YAML="$HERE/librelane/density.yaml"
if [ -n "${DENSITY:-}" ]; then
  printf '# INPUT, written by run-librelane.sh. Global-placement spreading target,\n# not a measurement of anything.\nPL_TARGET_DENSITY_PCT: %s\n' "$DENSITY" > "$DENSITY_YAML"
fi
CONFIGS=("$HERE/librelane/slots/slot_1x0p5.yaml" "$HERE/librelane/macros/macros_5v.yaml" "$HERE/librelane/config.yaml")
[ -f "$DENSITY_YAML" ] && CONFIGS+=("$DENSITY_YAML")

case "$MODE" in
  floorplan) STAGE=(--to OpenROAD.Floorplan) ;;   # no --save-views-to: there are no final views yet
  # The sign-off checkers are skipped, not silenced: KLayout DRC, Magic DRC,
  # Netgen LVS, the GDS XOR and the IR-drop report. Anything this mode reports as
  # "DRC" is the DETAILED ROUTER'S OWN violation count and nothing else.
  full)      STAGE=(--skip KLayout.DRC --skip Checker.KLayoutDRC
                    --skip KLayout.Antenna --skip Checker.KLayoutAntenna
                    --skip KLayout.Density --skip Checker.KLayoutDensity
                    --skip Magic.DRC --skip Checker.MagicDRC
                    --skip Netgen.LVS --skip Checker.LVS
                    --skip KLayout.XOR --skip Checker.XOR
                    --skip OpenROAD.IRDropReport
                    --save-views-to "$RUNS_DIR/final") ;;
  signoff)   STAGE=(--save-views-to "$RUNS_DIR/final") ;;
  *) echo "unknown mode: $MODE" >&2; exit 2 ;;
esac

command -v docker >/dev/null || { echo "docker not on PATH" >&2; exit 1; }
# Host paths == container paths, so dir:: relatives in the configs resolve either side.
exec docker run --rm --platform linux/arm64 \
  -v "$REPO:$REPO" -v "$PDK_ROOT:$PDK_ROOT" -v "$RUNS_DIR:$RUNS_DIR" \
  -w "$RUNS_DIR" "$IMAGE" \
  librelane "${CONFIGS[@]}" \
    --pdk "$PDK" --pdk-root "$PDK_ROOT" --manual-pdk --scl "$SCL" --pad "$PAD" \
      "${STAGE[@]}" "$@"
