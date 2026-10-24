#!/bin/sh
# Shim: the logic lives in synth_count.py (REFUSES instead of printing blanks, #568).
exec python3 "$(dirname "$0")/synth_count.py" "$@"
