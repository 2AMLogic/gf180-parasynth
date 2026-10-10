# Recording R1: the physical capture of the player-preview image

This is the R1 variant of [capture-r0.md](capture-r0.md) (epic #282, Milestone
E1, issue #324). It records the published R1 bitstream through the real line
output and compares each recording with what the simulation says **R1** should
play. Read capture-r0.md first: the rig, the wiring, the recorder, the
analysis, its limits and its outcomes (PASS, FAIL, REFUSED, ERROR) are the
same, and this page does not repeat them. It lists only what is different, and
what stops R0 material being used for R1 (and the reverse).

Until a bundle exists, trial `T-PHYSICAL` mode `capture-r1` is **NO VERDICT
(operator-blocked)**, never PASS. No physical R1 capture has been made.

**R0 and R1 are different images and neither stands in for the other.** R0's
references are rendered from R0's RTL and bound to R0's bitstream. They are
**refused**, not relabelled, by the R1 procedure; R1's are refused by the R0
procedure. REFUSED is NO VERDICT: it is neither a pass nor a fail.

| | R0 | R1 |
|---|---|---|
| Bitstream | `fpga/reports/arty/integrated-baseline-2025.1/arty.bit` | `fpga/reports/arty/r1-player-preview-2025.1/arty.bit` |
| SHA-256 | `a66c9349ef9b5572f3c3453777f38e1b143136755620fe419e730d6f5c84cb95` | `544499e2c97061957004f999caf21d5caaf520f6b2682f84c6e6773e7d291d21` |
| Manifest | `fpga/release/baseline-2025.1.json` | `fpga/release/r1-2025.1.json` |
| RTL source commit | `d089c678` | `6864435aa6eb3ecb406d13cf86f6a6bb79b2b8db` |
| Host selection | default (`--image release`) | **`--image r1`**, on every command |
| Step-5 manifest check | `release_manifest.py` prints `release_manifest: BOUND` | `r1_release.py` prints `r1_release: BOUND` |
| Session file | schema `r0-capture-session/1` | schema `r1-capture-session/1`, `image_id` `r1` |
| Bundle | `R0_CAPTURE_BUNDLE`, default `captures/r0` | `R1_CAPTURE_BUNDLE`, default `captures/r1` |
| References | `fpga/release/evidence/r0-reference/` | `fpga/release/evidence/r1-reference/` |
| Trial | `T-PHYSICAL` mode `capture` | `T-PHYSICAL` mode `capture-r1` |

The two bundle variables are separate on purpose: an R0 bundle exported for R0
never becomes the R1 bundle by default.

---

## 0. The R1 references must exist first (build box, once)

The R1 reference set is **rendered by replaying R1's RTL**. That is a long
simulation, so it runs on the build box (CLAUDE.md, "Heavy work runs on the
build box"), not on a laptop or a shared dispatch worker. Until it is rendered
and committed, the R1 analysis REFUSES with `no reference at ...` and the mode
cannot PASS. This is the one step the operator does not do at the bench.

Why it needs a staged tree: main has moved past R1's freeze (`voice_dp.v`,
`synth_top.v` and three drum sources changed after `6864435`). A reference
rendered from main's RTL would describe a bitstream nobody built, so the
renderer REFUSES unless every simulated source hashes to the image's. `stage`
builds such a tree: a detached worktree at HEAD, with exactly the moved
sources taken from the image's source commit. The host stays at HEAD (it is the
shipped one, with `--image r1`), and the next step proves it still sends R1's
pinned bytes.

```sh
# 1. the shipped host, under --image r1, emits every pinned R1 command byte for byte
.venv/bin/python tools/r0_reference.py --image r1 bytes
# 2. a tree whose simulated sources are the image's (verified by hash, else REFUSED)
.venv/bin/python tools/r0_reference.py --image r1 stage --dest /tmp/r1-stage
# 3. render the six commands there (hours: demo and bar808-full replay ~1.5 minutes of audio
#    each; run held-* first with --commands to see it work)
cd /tmp/r1-stage && .venv/bin/python tools/r0_reference.py --image r1 render \
    --out fpga/release/evidence/r1-reference
# 4. the set is the release's: bytes, image, sources, replay verdict, hashes, silence
.venv/bin/python tools/r0_reference.py --image r1 check
```

`render` writes, per command, `<cmd>.wav`, `<cmd>.json` (identity: schema
`r1-reference/1`, `image_id` `r1`, release, the pinned `cmds_sha256`, the image's
bitstream and source commit, every compiled source's hash, the replay
comparison) and `<cmd>.plan.json` (the host's own plan for the pinned bytes),
plus `silence.json`. For `demo` and `bar808-full` it also REFUSES unless the
replayed stimulus equals the `stimulus` digest in R1's recorded run identity
(`fpga/reports/r1-candidate/rtl-run-identities/`, itself pinned by
`r1-candidate.json`). `commands.live-midi` has no pinned bytes and no
reference: it is a controller session, not a playback command.

Copy the new `*.wav` files into the repository: they are git-ignored by
default and `.gitignore` carries an exception for this directory.

---

## 1-4. What you need, power-on, wiring, the M4

Exactly as capture-r0.md sections 1 to 4, with `export S=captures/r1`.

```sh
cd ~/dev/gf180-parasynth
export S=captures/r1              # the bundle directory (R1_CAPTURE_BUNDLE)
export P=/dev/cu.usbserial-XXXX1  # set in step 6
```

## 5. Program R1 and record the transcript

```sh
.venv/bin/python fpga/release/r1_release.py > $S/program.txt 2>&1; echo "exit $?" >> $S/program.txt
shasum -a 256 fpga/reports/arty/r1-player-preview-2025.1/arty.bit >> $S/program.txt
openFPGALoader --Version >> $S/program.txt 2>&1
openFPGALoader -b arty_a7_100t fpga/reports/arty/r1-player-preview-2025.1/arty.bit >> $S/program.txt 2>&1; echo "exit $?" >> $S/program.txt
cat $S/program.txt
```

Pass conditions: `r1_release` printed `BOUND` followed by `exit 0`; the `shasum`
line starts `544499e2c9706195…291d21`; the programmer's last line is `exit 0`;
the **DONE** LED is lit. The block rules, the retry rule and the "this is not
a readback" statement are capture-r0.md's. The analysis refuses an R0
transcript here: R0's check line is `release_manifest: BOUND`, and an `arty.bit`
digest of `a66c9349…` is not R1's.

## 6. Find the UART port and check the link

As capture-r0.md section 6.

## 7. The diagnostic set: exact commands

```sh
.venv/bin/python tools/r0_capture.py new-session --image r1 --bundle $S
open -e $S/session.json
```

The skeleton carries R1's schema, `image_id` and bitstream digest, and each
take's `command` is copied from the R1 manifest. Fill in the same three fields
as for R0. Every take follows capture-r0.md's pattern (record, wait one
second, run **one** command with `--capture`, `wait`). Every command carries
`--image r1`: without it the host sends **R0's** bytes, which are not R1's, and
the take is refused because its host log is not the released command.

| # | Take id | Manifest command | Exact command |
|---|---|---|---|
| 1 | `silence-1` | `silence` (sends nothing) | `sox -D -t coreaudio M4 -b 24 $S/takes/silence-1.wav trim 0 10` |
| 2 | `demo-1` | `demo`, the **calibration take** | `sox -D -t coreaudio M4 -b 24 $S/takes/demo-1.wav trim 0 12 & sleep 1; .venv/bin/python fpga/uart_host.py --port $P run --fixture demo --image r1 --capture $S/host/demo-1; wait` |
| 3 | `tone-1` | `held-m5a-saw` | `sox -D -t coreaudio M4 -b 24 $S/takes/tone-1.wav trim 0 6 & sleep 1; .venv/bin/python fpga/uart_host.py --port $P run --preset m5a-saw --note 72 --fixture none --image r1 --capture $S/host/tone-1; wait` |
| 4 | `pulse-1` | `held-m5a-pulse` | `sox -D -t coreaudio M4 -b 24 $S/takes/pulse-1.wav trim 0 6 & sleep 1; .venv/bin/python fpga/uart_host.py --port $P run --preset m5a-pulse --note 72 --fixture none --image r1 --capture $S/host/pulse-1; wait` |
| 5 | `held-1` | `held-default` | `sox -D -t coreaudio M4 -b 24 $S/takes/held-1.wav trim 0 6 & sleep 1; .venv/bin/python fpga/uart_host.py --port $P run --note 45 --fixture none --image r1 --capture $S/host/held-1; wait` |
| 6 | `phrase-1` | `run-m5a` | `sox -D -t coreaudio M4 -b 24 $S/takes/phrase-1.wav trim 0 8 & sleep 1; .venv/bin/python fpga/uart_host.py --port $P run --note 45 --fixture m5a --image r1 --capture $S/host/phrase-1; wait` |
| 7 | `drums-1` | `bar808-full` | `sox -D -t coreaudio M4 -b 24 $S/takes/drums-1.wav trim 0 12 & sleep 1; .venv/bin/python fpga/uart_host.py --port $P run --fixture bar808-full --image r1 --capture $S/host/drums-1; wait` |
| 8 | `demo-2` | `demo`, repeat | as take 2, with `demo-2` |
| 9 | `held-2` | `held-default`, repeat | as take 5, with `held-2` |

**Differences from the R0 table.** Take 6 is `run --note 45 --fixture m5a`, not
`play --fixture m5a`: R1 removed the `play` form because it sends no patch
image, so what it played depended on whatever the device held before. Every
command ends `--image r1`. The recording lengths are R0's; the analysis REFUSES
a take that ends before its R1 reference does, and the R1 references'
`periods` (in each `<cmd>.json`) are the check. If an R1 reference is longer
than its R0 counterpart, lengthen that take's `trim` and say so in the session
notes.

After each take, `uart_host.py` must end with `uart_host: done; ... drops 0,
errs 0`, and the take's `"started"` time goes into `session.json`.

**Not in this set:** `live-midi` (the Launchkey session,
`fpga/midi_session.py --image r1`). Its evidence is a controller session, not
a pinned byte stream, so it has no rendered reference; capturing it needs its
own procedure.

## 8. Analyse

```sh
.venv/bin/python tools/r0_capture.py analyse --image r1 --bundle $S
R1_CAPTURE_BUNDLE=$S .venv/bin/python tools/trial.py run T-PHYSICAL --mode capture-r1
```

The analysis, its limits and its exit codes are capture-r0.md section 8's. In
addition, for R1:

- **REFUSED** (exit 2, NO VERDICT) for any reference that is not R1's: a
  different schema or `image_id`, a different release string, a different
  bitstream or source commit, pinned bytes that differ from R1's manifest, or a
  compiled source whose hash is not R1's. These are checked on the **content**
  as well as the labels, so R0's set with R1's labels is refused for its
  bytes and sources. `silence.json` is bound the same way.
- **REFUSED** for a session that is not R1's: an R0 session schema or
  `image_id`, an R0 bitstream digest, an R0 transcript.
- the analysis record carries `image_id: "r1"`, and the trial reads it: a
  record of another image is never this mode's verdict.

R1 is dual-mono, like R0 (`rtl-sketch/i2s_tx.v` is the same file, same hash, and
`render` REFUSES a reference whose channels differ), so a left/right swap is
UNOBSERVABLE here too.

## 9. Keep the evidence

As capture-r0.md section 9, in `captures/r1`, reporting on #324's successor
capture issue. The bundle also records the manifest hash in `analysis.json`
(`manifest_sha256`).

## 10. How the R1 inputs are checked before any hardware

```sh
.venv/bin/python -m pytest tools/test_r1_capture.py -q
.venv/bin/python tools/r0_reference.py --image r1 bytes
.venv/bin/python tools/r0_capture.py cross-image-controls --image r1 --out build/cross-r1
.venv/bin/python tools/r0_capture.py cross-image-controls --image r0 --out build/cross-r0
```

`cross-image-controls` presents the other image's material to each procedure
and requires REFUSED **for the declared cause** (a refusal for an unreadable
file is not a catch):

| Case | Presented to | Must be refused because |
|---|---|---|
| `other-references` | image X | the other image's real reference set: wrong bitstream or schema |
| `other-labels` | image X | X's own bytes labelled as the other image: wrong schema or image |
| `other-labels-same-bytes` | image X | the other image's bytes labelled as X: pinned bytes and sources differ (a label-only check passes this) |
| `other-session` | image X | a session stamped with the other image |
| `other-transcript` | image X | the other image's programming transcript |
| `own-references-accepted` | image X | (the twin) X's own set is accepted |

A case that needs a reference set that is not committed (R1's, until step 0 is
done) is listed under `not_run` in `cross-image.json` and is **not** counted as
caught. The control was shown able to fail: with the reference binding removed
(`reference_problems` returning no problems), the same inputs are not caught
(`tools/test_r1_capture.py`).
