#!/bin/sh
# Turn one nextpnr run into a report. Every number is labelled FPGA and says
# whether it came from synthesis (technology-mapped cell counts) or from
# place-and-route (utilisation against a real device, achieved Fmax).
set -u
F="$(cd "$(dirname "$0")/.." && pwd)"; B="$F/build"; T="$1"; TOP="${2:-fpga_top}"
case "$T" in
  ice40) DEV="iCE40 UP5K (SG48), iCEBreaker"; PNR="$B/ice40_pnr.log"; STAT="$B/ice40_stat.txt"; BIT="$B/ice40.bin";;
  ecp5)  DEV="ECP5 LFE5U-25F (CABGA381), ULX3S"; PNR="$B/ecp5_pnr.log"; STAT="$B/ecp5_stat.txt"; BIT="$B/ecp5.bit";;
  ecp5hr) DEV="ECP5 LFE5U-25F (CABGA381) -- HEADROOM PROBE, MODES=16 NUMS=11"; PNR="$B/ecp5hr_pnr.log"; STAT="$B/ecp5hr_stat.txt"; BIT="$B/ecp5hr.bit";;
esac
echo "=== $DEV : synth_top with the REAL drum section, $TOP wrapper ==="
echo
echo "   contents: synth_top + spi_ctl + voice_dp + recip_div + ladder_dp_n + i2s_tx"
echo "             + drum_regs + drum_kit (drum_dp + modal_dp).  No placeholder."
if [ "$T" = ecp5hr ]; then
echo
echo "   THIS IS NOT THE BUILD. It is fpga/rtl/headroom_top.v: the same wrapper"
echo "   and the same PLL with synth_top's drum parameters raised from the"
echo "   shipping MODES=12 NUMS=6 to MODES=16 NUMS=11, which is what completing"
echo "   the 808 (8 of 11 circuits today) costs. Nothing else differs, so the"
echo "   difference against ecp5_25f.txt is the drum growth and only that."
echo "   The extra modes are unpopulated and the register map does not yet have"
echo "   room for 16 of them -- see docs/fpga-build.md section 6."
fi
echo
echo "-- FPGA synthesised (yosys technology mapping; NOT place-and-routed) --"
awk -v t="=== $TOP ===" '$0==t{f=1} f' "$STAT" 2>/dev/null \
  | grep -E "^[[:space:]]+[0-9]+[[:space:]]+(SB_|TRELLIS|DP16KD|MULT|CCU2|L6MUX|PFUMX)" | sed 's/^ */   /'
awk -v t="=== $TOP ===" '$0==t{f=1} f && /cells$/ {print "   total mapped cells:", $1; exit}' "$STAT" 2>/dev/null
echo
echo "-- FPGA place-and-route --"
if [ ! -f "$PNR" ]; then echo "  nextpnr did not run (tool missing)"; exit 0; fi
if grep -q "Device utilisation" "$PNR"; then
  echo "  device utilisation:"
  sed -n '/Device utilisation/,/^Info: Placed/p' "$PNR" | grep -E "^Info:[[:space:]]+[A-Z]" | sed 's/^Info:/   /'
fi
echo
if grep -qE "ERROR|Error:" "$PNR"; then
  echo "  RESULT: DOES NOT FIT / did not complete."
  grep -E "ERROR|Error:" "$PNR" | head -5 | sed 's/^/    /'
else
  echo "  RESULT: routed."
fi
echo
echo "  clock constraints nextpnr DERIVED (ecp5: from the PLL instance, not from the LPF):"
grep -E "Input frequency of PLL|Derived frequency constraint|promoting clock net" "$PNR" | sed 's/^Info:/   /'
if ! grep -q "Derived frequency constraint" "$PNR"; then
  echo "   (none derived; the constraint is whatever --freq / the LPF asserted --"
  echo "    an asserted constraint is a promise the checker verifies, NOT a frequency"
  echo "    the board produces. See docs/fpga-clock.md.)"
fi
echo
echo "  achieved Fmax. nextpnr prints this twice: once after placement (an"
echo "  ESTIMATE) and once after \"Routing complete.\" (the RESULT). Both are shown"
echo "  so nobody quotes the estimate by accident."
grep -E "Max frequency for clock" "$PNR" | head -1 | sed 's/^Info:/   post-placement ESTIMATE: /'
grep -E "Max frequency for clock" "$PNR" | tail -1 | sed 's/^Info:/   post-route RESULT      : /'
echo
echo "  worst cross-domain delays (unconstrained I/O paths):"
grep -E "Max delay .*posedge" "$PNR" | tail -3 | sed 's/^Info:/   /'
echo
if [ -f "$BIT" ]; then echo "  bitstream: $(basename "$BIT") $(wc -c < "$BIT" | tr -d ' ') bytes"; else echo "  bitstream: none (no routed design)"; fi
echo
echo "  NOTE: this is a physical result only. It says nothing about whether the"
echo "  design computes the right samples; see docs/verification-rules.md rule 3."
