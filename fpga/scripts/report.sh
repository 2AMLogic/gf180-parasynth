#!/bin/sh
exec python3 "$(dirname "$0")/pnr_report.py" report "$@"
