#!/bin/sh
# klt 0.9.0 takes a layout and spec as positional arguments. The probe runs in this directory.
exec klt erc .klt/place-and-route/ladder_dp.gds erc_supply_spec.json \
    --top ladder_dp --deck gf180mcu --findings-only --omit-antenna-tables --format json
