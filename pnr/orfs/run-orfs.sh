#!/usr/bin/env bash
# run-orfs.sh -- run OpenROAD-flow-scripts on one design in this directory, inside the pinned
# openroad/orfs Docker image (the same image prep/asic and klayout-tools use; it ships the full
# gf180 platform: 7t/9t libs, LEF, GDS, PDN strategy, tapcell script, fill config).
#
#   ./run-orfs.sh <design> [make args...]        e.g. ./run-orfs.sh ladder_dp
#   ./run-orfs.sh ladder_dp clean_all            e.g. wipe that design's results
#   ORFS_WORK=/somewhere ./run-orfs.sh ladder_dp  results/logs/reports go under $ORFS_WORK (default ./work)
#   ./run-orfs.sh ladder_dp DONT_USE_CELLS=       override a platform variable on the make command line
#
# Paths inside the container are identical to the host paths (bind mounts with source == target),
# so config.mk can use absolute host paths. Runs linux/amd64 under emulation on Apple Silicon.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO="$(cd "$HERE/../.." && pwd -P)"
DESIGN="${1:?usage: run-orfs.sh <design> [make args...]}"; shift
IMAGE="${ORFS_IMAGE:-openroad/orfs:26Q3-296-gda37dce1c@sha256:ebc8142da6d65d1a1e9a528aa2cedcde356243465dd859af8d3ade51075f8cb2}"
WORK="${ORFS_WORK:-$HERE/work}"; mkdir -p "$WORK"; WORK="$(cd "$WORK" && pwd -P)"
MOUNTS=(-v "$REPO:$REPO" -v "$WORK:$WORK")
# synth_core lives in the sibling gf180-polysynth checkout; mount it if present / overridden.
POLY="${POLYSYNTH_RTL:-$REPO/../gf180-polysynth/rtl}"
[ -d "$POLY" ] && { POLY="$(cd "$POLY" && pwd -P)"; MOUNTS+=(-v "$POLY:$POLY"); }
command -v docker >/dev/null || { echo "docker not on PATH" >&2; exit 1; }
exec docker run --rm --platform linux/amd64 "${MOUNTS[@]}" -w /OpenROAD-flow-scripts/flow \
  -e POLYSYNTH_RTL="$POLY" "$IMAGE" bash -lc \
  'source /OpenROAD-flow-scripts/env.sh >/dev/null 2>&1 && exec make "$@"' bash \
  "DESIGN_CONFIG=$HERE/$DESIGN/config.mk" "WORK_HOME=$WORK" "$@"
