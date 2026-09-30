# #369 cymbal: a qualified band measurement, and where the shipped cymbal differs from the 808

Everything here is model evidence against the Fischer TR-808 recordings (`cy8/CY{TONE}{DECAY}.WAV`, decoded as in
DR 0022). No kit, RTL or scorer changes. R1 is unchanged.

## 1. The measurement (`tools/cymbal_bands.py`, tests in `tools/test_cymbal_bands.py`)

**Bands.** The bands follow §10 of the reference:

| band | range | what it holds |
|---|---|---|
| L | 2–5 kHz | the low band |
| Ln | 2.9–4.1 kHz | the low band's 3.45 kHz peak, where the shared 7.1 kHz band-pass is about 17 dB down |
| H | 6–14 kHz | both high bands |

**Quantities, per band.**
- Energy share of the first 1 s after the strike, in dB relative to 200 Hz–20 kHz.
- EDT10 and a late T20 (−10 to −30 dB), taken off the floor-subtracted Schroeder curve.
- Every filter is zero-phase and run from `prepare()`'s guaranteed lead (#101).
- `thirds()` adds 1/3-octave energy, 1–20 kHz, in three time windows.

**Refusals.** The tool refuses a quantity rather than answering when:
- the curve does not reach −30 dB;
- the record ends less than 15 dB (of envelope) after the −30 dB point;
- the late residual exceeds 1.5 dB.

**Known answers (8 tests, passing).** These are synthetic three-band strikes with planted time constants and levels:
- single-exponential T20 to within 5 %;
- a two-slope high band, where EDT reads the short component and the late T20 reads the DECAY component to within 8 %;
- a planted 6 dB level change reads as 6 dB;
- invariance to scale and to prepended silence;
- swapped band decays move both bands;
- a truncated record refuses;
- the crosstalk control below (must pass at the real low-band decay, must fail its paired negative);
- the candidate bank's HP3 numerator is the planted third difference (`model/cymbal_candidate.py`, a separate module, not this measurement).

**Wrong-then-right 1.** The first version fitted the raw 5 ms envelope and refused **every** 808 file, because six
beating squares make the envelope wander 2–4 dB. It now uses the Schroeder curve.

**Wrong-then-right 2 (#376): the crosstalk control could not fail.** #371 added a control for the "recordings
contradict §10" finding below: hold the low band's own decay fixed and sweep only the high DECAY band's decay over
the 808's range, and check that the narrow low band (Ln) does not track it — ruling out "it's just the high band's
skirt leaking into Ln" as the explanation for Ln actually moving on the recordings. That control built its high band
out of a steep, synthetic 6th-order 7–12 kHz window (`strike()`'s default `hi_shape="wide"`, still used by every other
known-answer test above, where a clean high band is what's wanted). That window has essentially no energy at
2.9–4.1 kHz, so Ln could not move **no matter what** — the control passed, but vacuously; it could not have failed on
any input.

The fix (`tools/test_cymbal_bands.py::_q6_skirt_noise`) drives the control's high band through the 808's actual shape
instead: a constant-skirt-gain 2-pole/2-zero band-pass at 7.1 kHz, Q 6 (the RBJ cookbook form). Its analytic magnitude
response is **−17.74 dB at 4.1 kHz and −13.81 dB at 5 kHz** relative to its 7.1 kHz peak, which reproduces the "~17 dB
down (at 5 kHz only ~13 dB)" figures `tools/cymbal_bands.py`'s `BANDS` comment states for the real circuit's skirt to
**within ~0.8 dB** — close enough to be the right stand-in rather than an arbitrary choice, but not the "within 0.2 dB"
an earlier draft of this file claimed (#383 review: the real deviations are 0.74 dB and 0.81 dB, ~4× the figure
originally stated here). Both numbers are now asserted by
`test_q6_skirt_matches_the_documented_808_skirt_figures`, so the claim is checked rather than merely written down.
Two paired cases, both reported (`_skirt_growth`):

| low band's own decay | Ln EDT10, high-band DECAY short (250 ms) | Ln EDT10, high-band DECAY long (1,090 ms) | growth | bound |
|---|---:|---:|---:|---|
| 0.35 s (the real 808's, §10 "fixed, medium") | 390.8 ms | 403.8 ms | **1.03×** | < 1.25 — passes |
| 0.10 s (paired negative, unrealistically short) | 105.8 ms | 223.7 ms | **2.11×** | must be ≥ 1.25 — and does fail |

At the real 808's low-band decay the control still passes (skirt leakage alone cannot explain Ln's ~3.2× move on the
recordings), so the "recordings contradict §10" finding below is unchanged. But the control is no longer vacuous: at
an unrealistic 0.10 s it demonstrably **can** fail, which is the only way the passing case above is evidence rather
than a foregone conclusion.

**Checked against the recordings themselves** (`fischer.json`, all 25 settings). These are consistency checks against
the knobs' known physics, not a fit:
- **DECAY.** H's late T20 rises monotonically with DECAY in every TONE column, from 250–295 ms at DECAY 0 to about 1,090 ms at DECAY 10. At fixed DECAY it is nearly independent of TONE. 23 of 25 are measured; 2 refuse as non-exponential.
- **TONE.** H−L rises monotonically with TONE at every DECAY, for example 7.2 → 8.1 → 9.5 → 11.1 → 14.6 dB at DECAY 0.
- **The low band's own decay tracks DECAY.** Ln EDT rises from about 400 ms to about 1,280 ms in every TONE column. This is measured where the high bands' skirt is about 17 dB down, so **the recordings contradict §10's "DECAY changes only the middle band's RC"**. The kit's knob law already scales the low band's envelope with DECAY, which agrees with the machine.
- **The low band's late decay is mostly two-slope.** Its late T20 refuses at most settings, so the low band's qualified decay measure is EDT10.

## 2. The frozen development/confirmation split

This was fixed in `cymbal_bands.py` before any candidate existed.
- **Development (9 settings).** The knob laws' fit points (the TONE 5.0 column and the DECAY 5.0 row) plus the D14A anchor: CY0050, CY1050, CY2550, CY5000, CY5010, CY5025, CY5050, CY5075, CY7550.
- **Confirmation (16 settings).** All the others, including **CY2500**, which is D14B's holdout.

## 3. What the shipped cymbal gets wrong (`shipped-vs-CY5025.json`)

The shipped kit, which R1 plays with no TONE or DECAY control, compared with its anchor CY5025:

| quantity | 808 CY5025 | shipped |
|---|---:|---:|
| H − L | 8.16 dB | 10.82 dB |
| H EDT10 | 148 ms | 151 ms |
| Ln EDT10 | 591 ms | 598 ms |

The coarse band balance and decays are close, so they do not explain what the operator heard. The 1/3 octaves do
(shipped − 808, dB):

| window | 1.0 | 1.26 | 1.59 | 2.0 | 2.5 | 3.2 | 4.0 | 5.0 | 6.3 | 8.0 | 10 | 12.7 | 16 | 20 kHz |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0–50 ms | **+15.4** | **+11.2** | **+10.4** | **+9.1** | **+9.4** | +0.8 | −2.3 | −2.9 | −2.0 | +2.9 | −2.3 | −2.2 | +0.2 | **−7.0** |
| 50–300 ms | **+9.4** | +3.7 | +0.7 | −1.8 | −1.4 | −5.1 | −1.3 | −5.8 | −3.5 | +3.2 | +1.1 | +0.4 | −0.5 | **−7.9** |
| 300–1000 ms | **+8.8** | +3.9 | +1.7 | −2.4 | −1.7 | −3.4 | +4.1 | −2.1 | −3.2 | +3.4 | +2.2 | +1.4 | +1.5 | −1.1 |

- **Below 2.5 kHz the strike has 9–15 dB too much energy.** That is the shape of the **omitted Hh1**, the 2.5 kHz Q 0.97 high-pass on the low band that the kit dropped. The operator decision now allows restoring it.
- **The top octave is 7–8 dB short (16–20 kHz)**, and 8 kHz is about 3 dB too much. This is consistent with the short high band being routed through the closed hat's **11.7 kHz** 2-pole high-pass instead of its own **Hh3 (≈10.5 kHz, third-order)**, and with the missing level-stage +6 dB/oct tilt (§10, "LEVEL's buffer… differentiator"). This is inferred, not yet isolated.
- **3–6 kHz is 2–6 dB short** after the attack.

## 4. A second finding: the knob-law render is not the shipped cymbal

`test_discrimination.kit_at("CY", knobs, laws, "ours")`, which the knob experiments and DR 0022's probes render
through, gives H − L of **−2.4 dB** at CY5025 against the shipped kit's +10.8. Its DECAY also saturates: 7.5 and 10.0
render identically (`kit_at-map.json`, which is the file formerly written as `ours.json`).

That map is **not** a measurement of the instrument. Its knob laws need their own repair before any knob-tracking
claim can be made. Wrong-then-right 2: I rendered through `kit_at` first and nearly reported a 10 dB band error that
the shipped instrument does not have.

**Step 9 names the mechanism, which is worse than a miscalibration.** `_cy_hi_amp` solves in closed form for the
**808's own** measured 5–13 kHz / 2–5 kHz ratio at each TONE position, so a render driven through it reproduces the
reference's band ratio by construction and cannot be evidence about ours. Any TONE comparison drawn through
`kit_at`'s `CY.tone_ratio` law is therefore circular; `tone-knob/README.md` supplies the replacement, taken from
VR4's wiper rather than from the recordings.

**This blocker was cited four times as "#371" and #371 is a merged pull request, not an open issue** — step 1 of
this chain. So the thing gating acceptance item 3 of #396, the listening pack's other 24 settings and `gate-379`'s
§2 selection argument was tracked by nothing. It now has its own issue, **#413**; `balance/README.md` §6 lists the
four citations that need correcting to point at it.

> **Where this went.** Step 2 is `candidate/README.md` (the §10 candidate, negative); step 3 is
> `candidate2/README.md` (Hh2, Hh3 and the level stage read off W14b Figures 4 and 10 by
> `tools/werner_fig4.py`, and a second negative that eliminates the filter values as the cause).
> **Step 4 is `tone-stage/README.md`** — the tone stage read off W14b Figure 9 by
> `tools/werner_fig9.py`, and the first *positive* result of the chain: the tone stage tilts every band
> about −20 dB from 1 kHz to 20 kHz, which nearly cancels the LEVEL stage's +16.6 dB that the candidate
> applies on its own. That is the right sign and size for candidate 2's +24.0 dB excess tilt. No candidate
> is built there, because the same figure shows it does not determine the inter-band balance.
> **Step 5 is `candidate3/README.md`** — the tilt applied, and the chain's first candidate that beats the
> shipped kit on the defect the operator's A/B named: during the strike, no 1/3 octave is more than 5.7 dB
> from the 808 (shipped 15.4, candidate 2 15.3), and the residual tilt closes from +24.0 to +6.6 dB.
> It is **still not promoted**, because the tail windows remain worse than shipped — and the same
> measurement says why: the 1–2.5 kHz error that remains is **time-dependent** (our thirds fall 5–11 dB
> more than the 808's between the strike and 50–300 ms, a figure identical in the shipped kit, candidate 2
> and candidate 3), so no filter magnitude and no inter-band balance can be the answer to it.
>
> **Step 6 is `mid-band/README.md`** — the qualified 1–2.5 kHz decay that §6's own evidence-strength note
> said was missing (`tools/cymbal_mid.py`). It **refutes** the decay reading of §6: at CY5025 the 808's
> mid band decays 632 ms against the shipped kit's 528 ms, a 16 % difference inside the repo's ±50 % time
> tolerance. What it qualifies instead is a **level** difference — the shipped kit has ~6 dB more
> independent 1–1.8 kHz content than the machine and candidate 3 has ~4 dB less — which is where the
> omitted Hh1 acts.
>
> **Step 6/7 is `low-tail/README.md`** — the qualified 1–2.5 kHz decay #400 asked for, and the answer to
> step 5's unqualified finding. The within-record ratio rho(d) = T_M(d)/T_Ln(d) at six depths, with eight
> named properties, six injected defects, a measured zero point (rho is **not** 1.0 on a skirt-only
> record: Mn's zero is 0.89–0.99), three frozen windows whose union covers all 25 settings, and both
> confounds bounded rather than assumed. **The 808's 1–2.5 kHz outlasts its own low band at every one of
> the 25 settings (rho_M(−10) 1.021–1.139); ours dies first at every setting (0.866–0.894), and candidate
> 3 is worse than shipped in the leakage-proof band.** The recordings' noise floor is excluded (ours is
> 27 dB the *noisier*; adding our floor to the 808's record moves its rho by 0.0015 of a 0.249 gap) and
> the onset bounds at most a quarter of our deficit.
>
> And the cause is not a filter: the three-band chain §10 documents cannot reach the 808's 1–2.5 kHz
> **energy** at any inter-band balance — short by 9.4 dB in M and 16.6 dB in Mn, 20 of 20 settings above
> the bound — nor its decay (rho_M tops out at 0.97 over a −12…+30 dB balance sweep). The tone stage's
> shape, the obvious suspect, is worth 0.80 dB. §10 names one element it describes and does not quantify:
> the VCAs' asymmetric clipping. Wrong-then-right rate of that step: **7**.
>
> **Alongside steps 6 and 7 rather than after them, the inter-band balance is `balance/README.md`** — the half
> #396 left open, now **REFUSED with the blocking factor measured** rather than deferred. It carries no step
> number because it was measured in parallel with them, using neither's result and used by neither. Its obstacle
> is not the 9–18 dB of Figure 9 window this file and reference §18 blamed; it is the three swing VCAs' drive
> levels, which no W14b figure plots — a **+39.8 dB** gap in the short band against the shipped-kit level rule.
> Applying every resolved factor with the drives held equal was rendered and lands H−L at 25.07 dB against the
> 808's 8.16 (the rule it replaced reads 12.09), so **VR4's network alone would not unblock it**. That was a
> prediction, and #390/#417 plus #420 have now tested it end to end: the tone term is no longer bounded from
> Figure 9 (7.4 / 13.9 / 0.04 dB) but **resolved** from `tools/tone_stage_schematic.py`'s nodal solution, emitted
> as `sn-p13-vr4.json`, with a bound of **0.008 / 0.008 / 0.067 dB** — and the short-band gap **grew** from 38.2
> to 39.8 dB rather than closing. The refusal therefore stands on `vca-drive` alone, and it stands on evidence
> that could have overturned it. Step 7's finding is the sharper statement of the same limit: no
> inter-band balance at all reaches the 808's 1–2.5 kHz energy.
>
> **Both of those two sentences are corrected by step 8 below, and the gap is 1.3 dB rather than 9.4.**
> Read §5 of `low-tail/README.md` with §3 of `vca-clip/README.md` beside it.

> **Step 8 is `vca-clip/README.md`** — §10's asymmetric clipping, the one element step 7 named and did
> not test, put in the chain at three discrete positions, seven drives and four asymmetries and
> **eliminated**. It overshoots: by the drive at which its 1–2.5 kHz energy enters the 808's measured
> range, rho_M has reached 2.0–3.1 against the machine's 1.048–1.139. The prediction was written first
> and was wrong in sign, and why is recorded.
>
> The larger half of that step is a correction to step 7's own headline, from two preconditions step 7
> assumed rather than asserted. Its 9.4 dB gap compares an **analytic** bound with a **filtered**
> measurement: the analysis band-pass reads M **+3.67 and +9.49 dB high** on the two bands peaking at
> 7.1 kHz (it reads the low band true, because Ln *is* its peak), and §1.5's staircase source is not the
> flat one the bound assumed (+4.55 dB on the low band). Computing the bound the way the measurement was
> made — rendered, same instrument, documented source — moves it from −12.74 to **−5.07 dB** against the
> 808's −5.00…−3.45. Step 7's "no linear balance clears the skirt baseline" does not hold either: the
> rendered chain reaches rho_M 1.2413.
>
> What survives is **a joint constraint**, which is sharper than either half: the balances that raise rho
> are the balances that starve M, so `n_balances_in_808_box` is **0** for the linear chain at every
> balance and for the clipper at every position, drive and asymmetry. Wrong-then-right rate of step 8:
> **4**, one of them a verdict function that reported a match from two rows twelve dB apart.
>
> **Step 9 is `tone-knob/README.md`**, and it does not ask any of step 8's three candidates — it asks the
> question acceptance 3 needs answered before any of them can be judged across the knobs, and which §4 of
> this file says the existing map cannot answer. **The TONE knob is VR4's wiper, and the schematic predicts
> the machine's own TONE law**: with `alpha = TONE/100` read off the pot's "20K(B)" linear-taper marking —
> nothing fitted to the recordings — and ONE inter-band balance shared by all 25 settings, #417's nodal
> solution reproduces the 808's H − L versus TONE to **0.33 dB** against a 3.0 dB bound, over a measured
> span of 7.1–8.1 dB per DECAY column. The inverted wiper law (5.47), no tone network at all (5.46) and the
> low band on the top rail (6.95) each have the same two free numbers and are excluded. H's own EDT10 falls
> 1.4–2.4× with TONE while the low band's moves 2.5–9.7 %, which is the same structure seen in a decay
> rather than an energy.
>
> **And the residual is not by itself the evidence** — that half is worth more than the headline. The same
> two free numbers also reach a *flat* TONE law (0.38 dB, at a decay-dominated balance) and a straight ramp
> (0.34), so what is decisive is the three shapes and three structures they cannot reach. Three things stay
> unresolved with their numbers: the exact shape of alpha(TONE) (five of seven swept mappings are inside the
> bound), the Ht2/Ht3 rail assignment (**blind by construction** — both land in H), and the inter-band
> balance, whose admitted region still fills the swept grid, so **this does not lift #396's refusal**.
> Wrong-then-right rate of step 9: **7**, including an overstated headline that only a control written to
> break it found, and a `--require-corpus` escalation that two documents described and `main()` never read —
> so the two corpus-gated controls were documented as enforced and were enforced nowhere. The flag is now
> wired, `make reference-integration` is the gate that passes it, and it is the one of the seven that no
> control caught.
>
> **Step 10 is `tone-render/README.md`** — step 9's own next question, asked on a rendered candidate: *does a
> candidate driven by VR4's wiper law track the 808 as TONE moves?* **No, and the law is not why.** It carries a
> repair, a negative and a blocker with a number.
>
> The repair is that **revision 3's low-band tone realisation was outside its own bound and nobody had
> re-measured it.** Step 5 validated the realisation against Figure 9's 121–564 Hz window fit at 0.45 dB;
> against the nodal solution that #390/#417 put in its place it is **4.06–4.72 dB** off, at every TONE position,
> against the 3.0 dB bound revision 3 declared. The nodal route is not the suspect: over the **short** band, the
> one path Figure 9 plots across the whole audio band, the two routes agree to **0.10 dB**, and over the low
> band's 2–8 kHz they disagree by 5.62 dB — which is what a window a decade below the band predicts. Revision 3's
> "the tone pole and the LEVEL differentiator cancel" argument is *correct* for the decay and short bands, whose
> active ranges sit entirely above the network's 4219 Hz pole, and fails for the low band, which straddles it.
> Repaired with one discrete section carrying that pole — 0.56–0.88 dB — chosen by enumerating 42 realisations per
> band over the **network's own pole set**, with a stated rule that excludes poles too far outside a band to be
> distinguishable there (four sub-2 kHz poles read within 0.09 dB of each other over the short band; picking among
> them by 0.09 dB is #102's Q trap in a different coordinate). Budget **20 modes, 25 paths**, `N_NUMS` 11, no
> `HP3` decode — and **the margin is now exactly zero**: mode 19's `num` register *is* `A_RESET` (0xFF), which is
> survivable only because 19 ≥ `N_NUMS`, and a 21st mode has no address at all.
>
> The negative is that the rendered anchored H − L moves **+1.13 dB** across the whole knob where the machine
> moves **+7.3 dB** (worst deviation 4.47 dB against a 3.0 dB bound, at the *development* setting CY1050;
> development and confirmation agree). It is monotone, the right sign, and inside the bracket the circuit's own
> per-band levels allow — **pinned to that bracket's decay-dominated lower edge at every position.** H's own
> EDT10 moves 2.9 % where the machine's moves a factor of 1.4–2.4, and that half is a **decay**, so no
> inter-band balance can manufacture it.
>
> The mechanism is rendered rather than inferred, one band at a time: inside H the short band sits **10.59 dB
> below** the decay band and the **low band leaks in 6.02 dB above the short band**, so TONE's 51 dB of authority
> acts on 6 % of H. And the obvious deliverable — "the short band needs +X dB" — is **REFUSED**: a linear mix of
> the three separately-rendered bands misses the render's own curve by **1.26 dB** on a 1.13 dB quantity and
> *overpredicts* the knob's swing 2.3× at TONE 100, because the swing VCAs clip. That is the third independent
> route to the balance terminating on the same unmeasured quantity (#396's VCA drives, step 8's joint box, this).
> Preservation passes completely, including **OH and CH bit-identical at every one of the five TONE positions**.
> Wrong-then-right rate of step 10: **4**, one of them a 130 Hz pole that would have shipped with a better error
> figure and no circuit behind it.
>
> **Step 11 is `candidate4/README.md`** (#411) — the mid band's *level*, named by step 6 as the prime
> suspect: candidate 3's own amp register for Hh1 is matched to the *shipped kit's* mid band, not to the
> circuit's stated unity gain (`HH1_PASS_DB = 0.0`). Restoring the circuit value alone is **REFUSED**: the
> qualified over-skirt residual `tools/cymbal_mid.py` measures moves the *wrong* way (+0.04 → **−0.55 dB**,
> away from the 808's +4.41), and the strike-window guard step 5 uniquely held (0 thirds outside ±6 dB)
> gains one violation at 2.5 kHz (+8.3 dB). The tail *does* improve on the frozen `thirds()` instrument
> (50–300 ms worst case −11.6 → −9.0 dB, 7 → 4 of 14 thirds outside bound) — the two instruments disagree
> about direction, and both are reported rather than one being preferred. The reason is structural, not a
> render bug: Hh1's amp is a flat scalar on its *entire* post-filter output, and the over-skirt metric
> compares M against a prediction built from the *same render's* L band, so a flat gain on Hh1 alone is
> close to powerless to move that ratio — confirmed, not merely reasoned, by ruling out clipping directly
> (`tools/probes/cymbal_candidate4_gain_invariance.py`: 0 of 25,152,000 saturations in either render, ~11 dB
> of headroom in both). Not promoted; `--variant candidate4` stays a diagnostic ablation beside `notilt` and
> `balance`, and the shipped default is still candidate 3. **A distinct limit from steps 7, 8 and 10's VCA-drive
> finding, not the same one restated**: it holds regardless of the drives, because a flat scalar on Hh1's own
> output cannot move a ratio measured against a leak prediction built from that same output. A fix needs either
> a frequency-shaped correction to Hh1 (not a single register) or the inter-band balance itself — which does
> still route through the unresolved VCA drives. Wrong-then-right rate of step 11: **1** (clipping was the
> first-considered explanation for the wrong-direction result; ruled out by direct measurement before being
> reported as fact).

## 5. Where the structure stands after step 10, and the one question that is left

**§10's structure is built.** What this section asked for as "the next step" is done and measured; the list below
is the state, not a plan, and each row names the step that resolved it.

| §10 element | state |
|---|---|
| Low band: 3.45 kHz Q 6 → own VCA/envelope → **Hh1 2.5 kHz Q 0.97** | **in** (step 2), and its **tone section** repaired in step 10 (the network's 4219 Hz pole; revision 3 was 4.7 dB out of bound against the nodal target) |
| DECAY band: 7.1 kHz → VCA → **Hh2** | **in** and **resolved**: 8839 Hz, Q 1.00, +6.03 dB — *not* resonant. Read off W14b Fig. 4 in step 3; the "corner still unresolved" this section used to say is superseded |
| Short band: 7.1 kHz → VCA → **Hh3** | **in** and resolved: 2-pole 10323 Hz Q 5.64 **plus a 1-pole at 5195 Hz**, not at the same corner (step 3). No longer borrows the closed hat's 11.7 kHz high-pass |
| The level stage's +6 dB/oct | **in**, as a 1-pole differentiator cornered at 18972 Hz, *with* the tone stage against it (steps 4/5 — alone it makes things worse, §10 says so) |
| The **TONE knob** | **in**, from VR4's wiper with nothing fitted (steps 9/10). Rendered, and it moves H − L by 1.13 dB where the machine moves 7.3 |
| The **inter-band balance** | **NOT in, and it is the whole remaining gap** — but steps 11 and 12 remove the entire swing-VCA section as its explanation, on both its signal side (component-identical stages bar one collector-load resistor each; the short band's +39.79 dB would need a 2.15 MΩ collector load where the schematic prints 39 kΩ) and its collector-supply side (the ceiling's own most favourable duty is still 31.5 dB short) |
| The **DECAY knob's** own law | not started. VR2 is on the other rail and no step has read it |

**Budget, exact.** 16 → **20 modes, 25 paths**, `N_NUMS` 11, no `HP3` decode. The operator's accepted padding to 32
covers the area — but **mode 19 is the last mode contract 15.1 can address** (its `num` register is `A_RESET`), so
there is no margin for a 21st, and any further section needs a register-map revision rather than area.

> **Step 11 is `vca-drive/README.md`**, and it asks the one question step 10 named — *what sets the three swing
> VCAs' drive levels?* — off SN p.13, hash-pinned, with `--verify-source` and a refusal. **The premise is wrong,
> and that is the finding: there are not three drives.** The three stages are component-identical (the same
> 0.022 µF coupling cap, the same **2 MΩ series** base bias from B1 with no ground leg — so the same Ic and the
> same gm — the same 100 Ω emitter degeneration and the same series diode), and **Q16 and Q17 hang on the same
> node**, IC3 pin 7, so their signal drives are equal by construction. The only per-band element is the collector
> load: **R94 39 k / R90 33 k / R104 22 k**, i.e. **+4.97 and +3.52 dB** re the low band, or **+8.72 / +7.26 dB**
> as an upper bound that loads the low band with Hh1's measured input impedance and leaves the high bands
> unloaded. **The short band's +39.79 dB gap would need a 2.15 MΩ collector load and the schematic prints 39 k**
> — 34.82 dB short, a statement with no gain model in it at all. So the balance's missing factor is not in the
> VCA section.
>
> The read is qualified by an external known answer on its *newest* part: the two band-passes' **input networks**
> (C10/R52, C11/R55) are in no prior document here and set the filters' absolute gain, and solving both filters
> with them reproduces W14b Figure 4's digitised peaks to **0.01 dB** — while f0 and Q are asserted blind to
> those components and verified blind, so they cannot stand in for the check. Step 11 also corrects §10: its two
> band-pass rows carried each other's reference designators (no value or f0 moves), and `werner_fig4.py` printed
> the wrong capacitance beside the right ones. Wrong-then-right rate of step 11: **3**, and number 3 is a
> designator test that **passed on the exact string it was written to catch** — #376's vacuous control in a new
> coordinate.
>
> Two things it does not settle, both stated with the components: the three envelope generators' **peak collector
> voltages** (the last per-band freedom inside the VCA section, and the next question), and the high bands' own
> high-pass input loading, which is why the second convention is reported as a bound rather than an estimate.

> **Step 12 is `vca-supply/README.md`** (#432) — step 11's own next question, off the same scan and the same
> tool: *what are the three envelope generators' peak collector voltages?* **The hypothesis is half right.** The
> three reservoirs (C38/C40/C41, all 1 µF) ARE equal at the peak — **0.009 V spread against a 0.252 V bound**
> derived from the diodes' own forward-drop spread at their own load currents, which differ by two orders of
> magnitude. The three **collector** ceilings are NOT: **12.79 / 4.94 / 3.97 V** (short/low/DECAY), **+8.26 and
> −1.91 dB** re the low band, peaking **1 / 120 / 19 ms** apart, because only the short band's collector load
> hangs on its own reservoir — the other two sit behind a smoothing network the trigger pulse never catches up
> with. Even at its most favourable duty the short band's collector-supply advantage is **31.5 dB below**
> #396's +39.79 dB gap, so **no element inside the swing VCA section — signal side or supply side — closes it.**
>
> The step also corrects §10 a second time: VR2 (2 MΩ(B)) is in **series** with R93 470 kΩ, not parallel as the
> reference recorded, which makes the DECAY knob's timing resistance 470 k–2.47 MΩ and never zero (the
> reference's parallel reading puts a dead short across C41 at DECAY minimum, which is not a design). That
> correction **relocates the DECAY knob**: it reaches the **low** band's collector supply directly through Q20
> and R105, and the middle (DECAY) band only through R92/R89/R91 — which is exactly what the Fischer
> recordings already said and §10 denied (§1 above). The external known answer is the DECAY span against
> Roland's own chart (SN p.14) and two independent Fischer-recording measurements, inside a bound derived from
> those three sources' own mutual disagreement (not fitted), swept over duty 0.25–1.0 and beta 100–400.
> Wrong-then-right rate of step 12: **2**.

With step 12, the cymbal's VCA section is read completely — signal drive (step 11) and collector supply (this
step) — and **neither closes the balance's gap.** The missing factor is confirmed to sit outside the VCA
section entirely, which is where steps 8 and 10 each already pointed from a different direction.

Until the balance is resolved, nothing about it or the knobs' tracking can be promoted, and steps 7, 8, 10, 11 and
12 each say so from a different direction. What remains is what step 10 forbade itself: isolate why the **low
band leaks into H 6 dB above the short band's own contribution** (analysis skirt, its own chain, or the swing
VCA's clipping harmonics — measured, not isolated), and #400's tail question.

**Unchanged constraints for whatever comes next.**
- Select on the 9 development settings; confirm on the 16 untouched ones, including CY2500; report all 25.
- Preserve the hats: D15A, D16A and OH00–OH75. Step 10 holds OH and CH bit-identical at every TONE position.
- Then carry it through RTL, I²S and the deadlines, and produce the loudness-matched A/B pack (`tools/ab_808.py`, `tools/ab_808_loud.py`, committed here).
