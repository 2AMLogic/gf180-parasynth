cd ~/work/r2i
export PATH="$(cat ~/work/oss-path.txt):$PATH"
# PROVENANCE: the run recorded in runall.log/runall.json beside this script was
# executed with GF180_TR808_REFS=/home/ubuntu/dev/refs (the corpus PARENT). The
# export below was corrected AFTER the run by #522, to the Fischer repo root.
# The variable was unused by every job in this run: none of them reads the drum
# corpus (verify_synth_top imports run_case only for its renders and stimuli), so
# no recorded result depended on it.
export GF180_TR808_REFS=/home/ubuntu/dev/refs/sounds-tr808-fischer
P=/home/ubuntu/work/venv/bin/python
O=build/r2s
DL="$P rtl-sketch/verify_deadline.py"
VV="$P rtl-sketch/verify_voice.py"
$P tools/run_all.py --jobs 3 --timeout 7200 --json $O/runall.json \
  "$VV --set full --osc2x --filter2x --pulse2x --outdir $O/voice-full-p2x" \
  "$P tools/verify_m5a_filter2x_i2s.py --case M5A --pulse2x --timeout 5400 --outdir $O/full-M5A --verification $O/M5A.txt --wav $O/M5A-i2s.wav --record $O/M5A.json --audio $O/M5A-scored.wav" \
  "$P tools/verify_m5a_filter2x_i2s.py --case M5B --pulse2x --timeout 5400 --outdir $O/full-M5B --verification $O/M5B.txt --wav $O/M5B-i2s.wav --record $O/M5B.json --audio $O/M5B-scored.wav" \
  "$VV --set full --only extremes --osc2x --filter2x --pulse2x --define INJECT_BUG_LADDER_XG25 --expect-fail --outdir $O/ctl-ladder-xg25" \
  "$P fpga/verify_uart_bridge.py --pulse2x --scenario phrase --outdir $O/uart-r2" \
  "$P fpga/verify_uart_bridge.py --pulse2x --start-red --outdir $O/uart-r2-startred" \
  "$P rtl-sketch/verify_synth_top.py --osc2x --filter2x --pulse2x --outdir $O/top-fullkit-p2x" \
  "$DL --scenario stress-pulse --pulse2x --outdir $O/dl-stress-pulse-p2x --json $O/dl-stress-pulse-p2x.json" \
  "$DL --scenario stress-saw --outdir $O/dl-stress-saw --json $O/dl-stress-saw.json" \
  "$DL --scenario arty-uart --pulse2x --outdir $O/dl-arty-uart-p2x --json $O/dl-arty-uart-p2x.json" \
  "$DL --scenario arty-uart --outdir $O/dl-arty-uart --json $O/dl-arty-uart.json" \
  "$DL --scenario stress-pulse --pulse2x --mutant late:20 --outdir $O/dl-late20 --json $O/dl-late20.json" \
  "$DL --scenario stress-pulse --pulse2x --mutant late:21 --expect-fail --outdir $O/dl-late21 --json $O/dl-late21.json" \
  "$DL --scenario stress-pulse --pulse2x --mutant full2xwin --outdir $O/dl-ref-full2xwin --json $O/dl-ref-full2xwin.json" \
  "$DL --scenario stress-saw --mutant skipallwin --outdir $O/dl-ctl-skipallwin --json $O/dl-ctl-skipallwin.json" \
  "$DL --scenario arty-uart --pulse2x --inject VOICE_PULSE2X_RECT_HEADROOM --outdir $O/dl-arty-uart-p2x-ctl --json $O/dl-arty-uart-p2x-ctl.json" \
  "$VV --set quick --only waves3 --filter2x --pulse2x --osc2x --outdir $O/voice-waves3-p2x" \
  "$VV --set quick --only waves3 --filter2x --pulse2x --osc2x --inject PULSE2X_RECT_HEADROOM --expect-fail --outdir $O/ctl-rect-headroom" \
  "$P tools/trial.py run T-RELEASE-BOUND" \
  "$P tools/trial.py run T-RELEASE-BOUND-R1" \
  "$P -m pytest -q -p no:cacheprovider fpga/release tools/test_verify_deadline.py tools/test_ladder_xg_width.py fpga/test_build_arty.py fpga/test_xdc_bindings.py" \
  > $O/runall.log 2>&1
echo "RUNALL_EXIT=$?" >> $O/runall.log
