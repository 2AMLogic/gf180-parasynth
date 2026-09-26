# Deadline re-captures on the revision-14 tree (clap L2)

Kept out of `README.md` on purpose: that file is the #248 deadline report the
published R1 release binds by digest (`fpga/release/baseline-2025.1.json`,
`evidence.deadline.readme_sha256`). The R1 image is revision-11 RTL and these
runs are not evidence about it, so they must not move its record.

`traces/prod-stress-saw-l2` and `traces/ctl-prod-stress-late15-l2` (records in
`runs/*-l2.json`, `runs/run_all-l2.json`: 2/2 exit 0, build box, commit
`0939a1f`) are the stress-saw clean run and its late:15 control on the
contract-revision-14 tree. The kit gained one write, so the stress-saw stimulus
is now 461 writes and 2158 required periods. The run passes with worst sample
slack 13; late:15 misses 32 frames and is caught. The pre-L2 captures in
`README.md` are unchanged and stay history. `tools/test_verify_deadline.py` now
refuses to judge a capture that was not driven by the current stimulus.
