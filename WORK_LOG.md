# Work log

Recorded forge events; merge or closure alone does not establish sound qualification or hardware delivery. Initial coverage: the twenty most recently merged PRs, excluding Guide maintenance, and recently closed issues since 2026-10-01. Earlier events are outside this initial log.

## Archives

- [2026-09](work_log/WORK_LOG_2026-09.md)

### 2026-11-28

- **Issue #1796** (closed): mono_artifact_probe sweep: a row whose stages all refused is written with no numbers and the sweep still exits 0 (found by #1770)
- **PR #1829**: fix: mono_artifact_probe sweep exits REFUSED when any row holds no output number
- **Issue #1921** (closed): Untraced #1782 candidate: `tools/score_f1_rtl.py` under Refused-only handlers (run_case.run_filter_case)
- **Issue #1886** (closed): Untraced #1782 candidate: `tools/clap_d12a_probe.py` under Refused-only handlers (NonFiniteAudio via run_case)
- **Issue #1889** (closed): Untraced #1782 candidate: `tools/cymbal_candidate_eval.py` under Refused-only handlers (NonFiniteAudio via run_case)
- **Issue #1885** (closed): Untraced #1782 candidate: `tools/clap_burst_timing_qual.py` under Refused-only handlers (NonFiniteAudio via run_case)
- **Issue #1916** (closed): Untraced #1782 candidate: `tools/probes/perturbation_ladder.py` under Refused-only handlers (NonFiniteAudio via run_case)
- **Issue #1896** (closed): Untraced #1782 candidate: `tools/diagnose_tom_body.py` under Refused-only handlers (NonFiniteAudio via run_case)
- **Issue #1915** (closed): Untraced #1782 candidate: `tools/probes/hihat/hh_probe5.py` under Refused-only handlers (NonFiniteAudio via run_case)
- **Issue #1806** (closed): cymbal_m_origin: "REFUSED: ... controls do not pass" is returned on exit 1 (sibling of #1786)
- **Issue #1789** (closed): cymbal_m_origin: "REFUSED: the shared instrument's own controls do not pass" is printed on exit 1 (same shape as #1769)
- **PR #1817**: fix: cymbal_m_origin and cymbal_vca_drive refuse with exit 2, not exit 1
- **Issue #1631** (closed): publish_selected: a refused stored simulation escapes as a traceback (exit 1); the stale M5A simulation record is not checked on main
- **PR #1661**: fix: publish a refused stored simulation as NO-VERDICT, not a traceback
- **Issue #1855** (closed): audit_tracked_json_writers credits an inline site for 'await <sync>.write_text(json.dumps(...))', which raises and never writes (pre-existing)
- **Issue #1794** (closed): cymbal_low_tail (#1765): confirm exit 0 on the real Fischer corpus, default and --all-settings, and exit 2 on one damaged record
- **Issue #1684** (closed): refaudio: a member name that is also a directory prefix kills the extraction loop with an uncaught FileExistsError instead of that member's FAIL
- **PR #1703**: fix: return a refaudio member's landing OSError as its FAIL reason
- **Issue #1699** (closed): audio_distance_floor: decide the full-run exit code when an experiment refuses (exit 0 vs 2)
- **PR #1773**: fix: audio_distance_floor exits 2 when any experiment refused
- **Issue #1718** (closed): refusal_sibling_scan: an async def callee is invisible to the census (ModInfo.funcs registers only ast.FunctionDef)
- **PR #1723**: fix: refusal_sibling_scan sees an async def callee and credits it only where awaited
- **Issue #1693** (closed): measure_pulse2x_headroom.py decimator/fine --gains 0.0 raise RuntimeError (exit 1); mix --gains 0.0 writes -240.0 dBFS rows (exit 0)
- **PR #1715**: fix: exit REFUSED on silent output in measure_pulse2x_headroom
- **Issue #1669** (closed): Same-shape leftovers beside #1636: two scorecard CLI tracebacks, a null result read as not run, an OverflowError in the m5a comparator
- **PR #1682**: fix: scorecard no-verdicts malformed records; m5a refuses overflow
- **Issue #1728** (closed): fpga/sweep_live_midi.py: a tampered r1-kit.json escapes main() as drums_fx.KitRefused (traceback at exit 1), a class the escape census cannot see
- **PR #1735**: fix: sweep_live_midi REFUSES a tampered R1 kit, not a traceback
- **Issue #1690** (closed): fpga/: a child-process failure or timeout escapes main() as a traceback in four tools (verify_late_events, build_selected, publish_selected, bringup_arty)
- **PR #1719**: fix: refuse a failed or hung child at exit 2 in four fpga tools
- **Issue #1705** (closed): loom tool finding: worktree.sh leases an untargeted claim with a renewal loop watching a transient tool-call shell, so the lease expires mid-task and the pre-push fence aborts (exit 3)
- **PR #1716**: docs: record worktree.sh $PPID lease-watch finding in tool-findings
- **Issue #1752** (closed): Census after #1742: 157 git-subprocess sites in 101 tools are unguarded against a missing git; 4 reproduced at exit 1
- **Issue #1692** (closed): Five measure_point readers index a row's stages without checking the row (same class as #1628; latent, no input reaches a silent row)
- **PR #1710**: fix: refuse an unmeasured measure_point row in five stage-indexing readers
- **Issue #1664** (closed): Pre-transfer rjwalters/loom references outside the five install-time .loom files: the live file-upstream instruction and Loom marker blocks in CLAUDE.md, and the scaffolded issue templates
- **PR #1673**: docs: name 2AMLogic/loom in the live Loom references outside .loom/
- **Issue #1604** (closed): tier-3 record readers: a required key read with [] exits 1 by traceback on a partial record (class sweep from #1597)
- **PR #1619**: fix: refuse records lacking a required key instead of a traceback
- **Issue #1652** (closed): r1_candidate.py: a presets-only drift of r1-kit.json refuses live_midi_init's MidiSession outside the Refused handler (exit 1 by traceback)
- **PR #1666**: fix: refuse a presets-only kit drift at r1_candidate's live_midi_init
- **Issue #1680** (closed): compare_m5a_i2s_candidate still exits 1 on 10**400 in a harmonic partial: _compare_incremental's float() is unguarded
- **PR #1689**: fix: refuse unreadable harmonic partials in _compare_incremental
- **Issue #1694** (closed): measure_m5_operating_level.py and measure_m5_saw_drive.py: --drives outside the frozen budget exits 1 with REFUSED text (SystemExit), not 2
- **PR #1704**: fix: raise the M5 --drives budget fence as a module Refused
- **Issue #1726** (closed): refusal_candidate_trace: a stop inside an except* clause is not read as an except handler (ast.TryStar is not ast.Try)
- **Issue #1697** (closed): refaudio: a member named like another member's .part file is reported FETCHED, then deleted by that member's cleanup (exit 0)
- **PR #1713**: fix: refuse refaudio members named like another member's .part file
- **Issue #1595** (closed): Consolidate M5A SPI-to-I2S transcript checks in run_case, verify_mono_case, and build_selected onto i2s_transcript
- **PR #1633**: fix: route M5A SPI-to-I2S transcript checks through i2s_transcript
- **Issue #1628** (closed): measure_pulse2x_headroom.py probe: a gain that silences the rectangles dies with KeyError instead of REFUSED
- **PR #1696**: fix: pulse2x probe refuses an unmeasured row instead of KeyError
- **Issue #1724** (closed): refaudio: cross-request .part collision overwrites a previously cached *.part member
- **Issue #1576** (closed): refusal_sibling_scan: trace the candidate-escape inventory #1573 surfaced (23 new in tools/model/pnr, 14 in fpga/)
- **PR #1635**: feat: trace every candidate-escape row through its entry point and record where its refusal goes
- **Issue #1641** (closed): refusal_sibling_scan.py does not credit a CLI boundary guard that lives in a wrapper function
- **PR #1660**: fix: credit a refusal guard in a wrapper the CLI dispatch calls
- **Issue #1610** (closed): audio_distance_floor.py: a malformed reference refuses at run_case.prepare as an exit-1 traceback (4 sites)
- **PR #1646**: fix: catch refusal.Refused from prepare in audio_distance_floor
- **Issue #1580** (closed): Ensemble control reuse credits a two-run negative-control report even when the latest run escaped
- **PR #1608**: fix: refuse a multi-run capture when reusing an ensemble control run
- **Issue #1643** (closed): score_f1_rtl: refprofile.Refused escapes the CLI handler (same shape as #1612, #1624)
- **PR #1672**: fix: catch refusal.Refused in the F1 RTL scorer's CLI handler
- **Issue #1640** (closed): hh_probe5.Refused's SystemExit base makes an uncaught local refusal exit 0 silently; the #815 pin test's 'message to stderr' claim is false
- **PR #1651**: fix: drop SystemExit base from hh_probe5.Refused so refusals exit 1
- **Issue #1617** (closed): f1_rtl_filter_path.py: its default --frames 20000 is below the first analysis window, so the default invocation always REFUSES
- **PR #1637**: fix: raise the F1 RTL filter-path CLI's default --frames to 30000
- **Issue #1614** (closed): midi_session.py: a drifted R1 sound table refuses MidiSession.__init__ outside the guarded region (exit 1 by traceback)
- **PR #1649**: fix: report a drifted R1 kit as REFUSED at the MIDI session entry points
- **Issue #1698** (closed): fix: --out outside the repository writes the report and then exits 1 in three M5A measure tools
- **Issue #1624** (closed): f1_level_scaling: refprofile.Refused escapes the CLI handler (same shape as #1612)
- **PR #1648**: fix: catch refusal.Refused in the F1 level-scaling CLI handler
- **Issue #1613** (closed): measure_pulse2x_headroom.py phrases: every refusal from mono_m5a_score.measure is an exit-1 traceback
- **PR #1630**: fix: catch refusal.Refused at measure_pulse2x_headroom's CLI boundary
- **Issue #1577** (closed): refusal_sibling_scan: remaining import-scope blind spots (function-local family borrow; rtl-sketch/ and audition/ outside SCAN_ROOTS)
- **PR #1598**: fix: refusal_sibling_scan sees Refused borrowed in a def; 2 new roots
- **Issue #1657** (closed): CI flake in spi-i2s dependency install when a pip download ends early
- **Issue #1634** (closed): M5A recorded simulation is stale against current RTL; no routine check reports it
- **Issue #1597** (closed): clap_d12a_probe: a partial committed D12A record exits 1 (FAIL) by traceback, not 2 (REFUSED)
- **PR #1605**: fix: refuse a partial committed D12A record before it is scored
- **Issue #1626** (closed): tool finding: Loom config-resolver dies with 'jq: Argument list too long' (defaults tier 208,882 B over the 128 KiB argument cap)
- **Issue #1609** (closed): parse_no_output_delay does not count ports placed on the sentence line itself
- **Issue #1569** (closed): Consolidate r2_release check, stale_control, and main onto r1_release and catch refusal.Refused
- **PR #1574**: fix: catch refusal.Refused in the R1 and R2 stale-control handlers
- **Issue #1570** (closed): Consolidate M5A frozen-reference loading, partial comparison, and selected-voice construction onto mono_m5a_score
- **PR #1586**: fix: route three M5A tools through the shared frozen-reference gate
- **Issue #1562** (closed): SPI-I2S transcript gate accepts a two-run capture; score_m5a_i2s pairs the first run's controls with the second run's WAV
- **PR #1581**: fix: refuse multi-run captures in the SPI-I2S transcript gate
- **Issue #1599** (closed): create-pr.sh exits 126 on this host: config-resolver.sh passes a 208 KB defaults.json as one jq --argjson (same root cause as #1331)
- **Issue #1596** (closed): loom create-pr.sh exits 126 (config-resolver.sh: jq: Argument list too long) once defaults.json exceeds 128 KiB
- **Issue #1573** (closed): refusal_sibling_scan cannot see the fpga/ release checkers: SCAN_ROOTS and function-local imports
- **PR #1579**: fix: refusal_sibling_scan binds function-local imports per scope and scans fpga/
- **Issue #1582** (closed): trial_env bootstrap reads system package versions for a symlinked target venv
- **PR #1585**: fix: query the named interpreter for package versions in trial_env
- **Issue #1568** (closed): Consolidate measure_partial_balance reference loading and solo rendering onto run_case
- **PR #1575**: refactor: load partial-balance references and solos through run_case

### 2026-11-27

- **Issue #1578** (closed): docs: record the unfiled Loom finding from #1573 (config-resolver passes each config tier as one jq argument)
- **Issue #1556** (closed): Consolidate the three drifted SPI-I2S transcript validators in score_m5a_i2s, score_drum_i2s, and score_ensemble_i2s
- **PR #1563**: fix: refuse non-clean SPI-I2S transcripts in all three RTL scorers
- **Issue #1557** (closed): Consolidate drifted DPREG-4 DRC parser in tools/dsp_dpreg_extract.py onto dsp_dpreg_analyse.parse_drc_targets
- **PR #1559**: fix: delegate dsp_dpreg_extract DRC parsing to the analyser
- **Issue #1551** (closed): Consolidate the two drifted indexed_sizes TSV parsers in refaudio_fetch and tom_drop_compare onto refaudio_local
- **PR #1558**: refactor: read the refaudio index sizes through one shared reader
- **Issue #1552** (closed): Retained-evidence 16-bit WAV encoders disagree: run_case truncates where manifest rounds, and the component writer discards the clip count
- **PR #1561**: fix(measure_mono_case): REFUSE over-range audio before writing WAVs
- **Issue #1554** (closed): spi_host.MusicHost.keys() silently drops an unrecognised KeyHost op (last instance of the #1546 class)
- **PR #1555**: fix(spi_host): reject unhandled KeyHost ops in MusicHost.keys
- **Issue #1553** (closed): config-resolver.sh exceeds MAX_ARG_STRLEN: a 208 KB defaults.json breaks create-issue.sh and create-pr.sh (file upstream to rjwalters/loom)
- **Issue #1468** (closed): Empty-record audit, tier 2: 21 gated non-probe verdict tools (from #1466)
- **PR #1506**: fix: refuse empty evidence in sound and player verification
- **Issue #1540** (closed): Physical capture interpreter accepts repeated demo takes with no fixture declaration
- **PR #1542**: fix: require declared diagnostic commands for physical capture PASS
- **Issue #1535** (closed): Use one pairing rule for the mono pulse summary and R2 comparator
- **PR #1536**: fix: pair pulse reports by scheduled drive and expose missing baselines
- **Issue #1537** (closed): interpret_live_midi_record treats a missing fixtures declaration as zero required replays
- **PR #1541**: fix: require replay fixtures for live MIDI RTL verdicts
- **Issue #1532** (closed): Retire the private aliasing estimator in the oscillator width sweep
- **PR #1534**: fix: report refused oscillator alias measurements in the width sweep
- **Issue #1528** (closed): Require replay fixtures for T-LIVE-MIDI RTL receipts
- **PR #1530**: fix: require replay fixtures for live MIDI RTL receipts
- **Issue #1538** (closed): Tool defect (forward upstream): config-resolver.sh jq --argjson breaks at the 128 KiB argv cap; create-issue.sh exits 126
- **Issue #1527** (closed): Share the ladder noise-response calculation and refuse a silent wide-open reference
- **PR #1529**: fix: refuse silent wide-open ladder references
- **Issue #1503** (closed): Sound mutation suite credits test IDs from whole output, not each mutant's own group
- **PR #1531**: test: attribute sound mutant credits to each mutant's output group
- **Issue #1511** (closed): Report observed axis and inventory rows in estimator ground-truth summaries
- **PR #1516**: fix: count observed axis and inventory rows in ground-truth check
- **Issue #1507** (closed): rounded_verdict_census: a skipped symlinked subdirectory is documented but invisible in the report
- **PR #1524**: feat: report skipped directory links in the rounded-verdict census
- **Issue #1441** (closed): Read the rounded-verdict census: 30 candidate fields on main as of 70d5449e, none read
- **PR #1500**: docs: audit rounded verdicts in cymbal and M1A measurements
- **Issue #1519** (closed): Restore exact README.md and docs/milestones.md bytes after check_doc_claims_789_marker_controls.py injects claims
- **PR #1521**: fix: restore exact target bytes in doc-claims marker-controls probe
- **Issue #1477** (closed): crash_rate.py --runs 0 (or a negative count) dies with IndexError instead of its REFUSED exit 2
- **PR #1481**: fix: refuse --runs below 1 in crash_rate.py before the empty batch reaches the summary
- **Issue #1479** (closed): Refuse empty playback receipts, rig pin checks, sound reports and ladder flow verdicts
- **PR #1508**: fix: refuse empty rolling receipts, rig pins, reports and ladder steps
- **Issue #1505** (closed): Use the full-precision balance verdict in cymbal_tone_knob
- **PR #1510**: fix: use admitted balances for 808 cymbal tone-law verdicts
- **Issue #1518** (closed): Restore exact README bytes after the document-scope probe injects a claim
- **PR #1520**: fix: restore exact target bytes in doc-claims scope probe
- **Issue #1463** (closed): bd_pitch_predeclaration_mutants credits any focused exit 1 as RED without attributing it to the intended control
- **PR #1471**: fix: credit a bd-pitch mutant red only at its recorded assertion
- **Issue #1473** (closed): Frozen oscillator-drift report accepts altered audio when its WAV digest is null or empty
- **PR #1475**: fix: require a matching WAV digest for oscillator drift measurements
- **Issue #1488** (closed): Check credited test IDs under each FPGA mutant’s own output header
- **PR #1498**: test: attribute FPGA mutant credits to each mutant's own output group
- **Issue #1513** (closed): Cymbal TONE revision 3 control and report lose an out-of-bound result to rounding
- **PR #1515**: fix: evaluate cymbal TONE rev3 bound checks on unrounded errors
- **Issue #1514** (closed): Trial registry loader returns FAIL exit code on truncated JSON or a missing trials key
- **PR #1517**: fix: refuse invalid trial registry JSON and missing trials key
- **Issue #1523** (closed): File upstream (rjwalters/loom): config-resolver.sh passes config tiers as jq argv, so a >128 KiB defaults tier breaks create-issue.sh
- **Issue #1482** (closed): Refuse mismatched write counts and truncated I2S captures in fpga/verify_fixture.py
- **PR #1489**: fix: refuse mismatched write counts and truncated I2S captures in verify_fixture
- **Issue #1474** (closed): Exercise the FPGA missing-returncode guard on a diagnosed no-fit log
- **PR #1496**: test: catch a missing nextpnr return code in the headroom reporter
- **Issue #1491** (closed): rounded_verdict_census: documented search boundary differs from the actual one (pytest.ini patterns; symlinked subdirectory)
- **PR #1501**: docs: pin the rounded-verdict census scan boundaries
- **Issue #1499** (closed): cymbal_tone_nodal.choose re-decides the shape bound from a rounded worst_db
- **PR #1502**: fix: refuse cymbal TONE realisations at full precision, not rounded worst_db
- **Issue #1470** (closed): Published-WAV-path guards now enumerate records by hand; a new audio-bearing row goes unchecked
- **PR #1497**: test: check every live scorecard model WAV against its runner
- **Issue #1509** (closed): Preserve full-precision M1A attack failures in property selection
- **PR #1512**: fix: preserve M1A attack precision through scorecard grading
- **Issue #1480** (closed): An interrupted mutation can truncate model/sound_report.py in the checkout
- **PR #1483**: fix: restore the sound probe's target from its entry bytes
- **Issue #1460** (closed): Simplify Arty verifier summaries onto three-state exit_contract so REFUSED runs exit 2 instead of FAIL
- **PR #1472**: fix: make Arty verifier suites exit 2, not FAIL, on refused runs
- **Issue #1492** (closed): Derive the F1 engine label from the filter path
- **PR #1494**: fix: derive the F1 engine label from the filter path
- **Issue #1430** (closed): Clean drum, ensemble and filter runs given --results build/scorecard still write the published artefact; the --expect baseline writes the published M5 WAV
- **PR #1462**: fix: refuse a clean run whose artefact would be the published one, and let --no-audio reach the M5 writer
- **Issue #1484** (closed): Keep inventory-generator diagnostics off stdout
- **PR #1493**: fix: keep reference inventory diagnostics off stdout
- **Issue #1478** (closed): trial.py run <unknown trial id> exits 1 with a RegistryError traceback, this tool's FAIL code
- **PR #1490**: fix: return exit 2 for trial registry errors
- **Issue #1451** (closed): Remove the fabricated passing record from the tom pitch-drop known-answer check
- **PR #1456**: fix: read the known-answer verdict off the rows, not n_ok == n
- **Issue #1504** (closed): Upstream (rjwalters/loom): config-resolver.sh passes config tiers in argv — a >128 KiB defaults tier bricks every Loom script
- **Issue #1457** (closed): tools/test_mono_m5a_score.py rewrites build/scorecard/M5A-model.wav, which the archived M5A model row names
- **PR #1465**: test: keep M5A cutoff-override renders off the archived model WAV
- **Issue #1434** (closed): Commit the rounded-verdict cross-reference census as a tool with its validation case (from PR #1421)
- **PR #1443**: feat: add rounded-verdict census tool and validation tests
- **Issue #1401** (closed): fpga mutation record credits any nonzero pytest exit as KILLED (14 collection errors read as 14 kills)
- **PR #1464**: fix: credit an FPGA mutation record's kill only at a recorded assertion
- **Issue #1422** (closed): Mono runners write a clean off-board run's WAV to the published path instead of beside its record
- **PR #1431**: fix: write a clean mono run's model WAV beside its record and refuse the published path
- **Issue #1427** (closed): Two more readers refuse only when empty: From Mars takes in tom_drop_measure and loops in measure_repeatability
- **PR #1437**: fix: refuse an incomplete From Mars cache in the tom and loop readers
- **Issue #1485** (closed): run_klt.verdict_exit_code([]) returns EXIT_OK: an empty step list reads as success (latent, unreachable from main today)
- **Issue #1454** (closed): verify_109_claims exits 0 when no check was executed (prints 0 of 0)
- **PR #1461**: fix: verify_109_claims exits 2 when no check was executed
- **Issue #1448** (closed): Remove the second foldback floor that gives damaged M5A note evidence a passing score
- **PR #1449**: fix: refuse invalid per-note foldback in M5A comparisons
- **Issue #1433** (closed): The frozen M5A MIDI-84 pair compares independent renders but is reported as a long-term drift bound
- **PR #1442**: fix: refuse the M5A saw/pulse drift pair and withdraw its 13.6 s bound
- **Issue #1450** (closed): An off-board filter run splits its response curve from its record when the results directory holds an RTL anchor
- **PR #1452**: fix: write the fixed-model filter curve beside its redirected model record
- **Issue #1415** (closed): run_case.py states an absolute curve path for a relative --results, score_f1_rtl.py states a relative one
- **PR #1418**: fix: resolve a relative --results against the cwd in run_case.py
- **Issue #1455** (closed): Loom config-resolver fails on this worker: defaults.json (209 KB) exceeds the 128 KiB per-argument cap
- **Issue #1400** (closed): Reuse the holdout verdict in the sound probe so collection errors cannot count as mutant kills
- **PR #1402**: fix: sound probe reuses holdout verdict; a pytest refusal is no kill
- **Issue #1444** (closed): measure_repeatability: audit and cross_session still take a file count for the whole 6x6 grid
- **PR #1447**: fix: check indexed bass-drum recordings before repeatability measurements
- **Issue #1426** (closed): Run the before/after report comparison on the Fischer corpus for the reference-inventory change
- **Issue #1453** (closed): create-pr.sh exits 126: loom_resolve_config hands a 138 KB config tier to jq as one argument
- **Issue #1436** (closed): measure_repeatability --measure: knob_travel aggregates whatever the 6x6 grid folder holds, with no completeness check
- **PR #1446**: fix: refuse an incomplete Digital grid before measuring knob travel
- **Issue #1414** (closed): Write drum and ensemble WAVs beside their off-board scorecard results
- **PR #1424**: fix: write drum and ensemble WAVs beside their own results instead of the shared path
- **Issue #1435** (closed): run_case.py records an unresolved curve symlink for an absolute --results
- **PR #1440**: fix: name an F1 curve record by the file its write landed in
- **Issue #1423** (closed): Stop M1A v3 scoring from running and embedding the retired v2 attack qualification
- **PR #1438**: fix: drop the retired v2 attack qualification from M1A v3 scoring
- **Issue #1420** (closed): model: three reference-set reads built by glob refuse only when empty (#1309/#1334 shape)
- **PR #1428**: fix: refuse an incomplete BD/SD reference set instead of shrinking it
- **Issue #1419** (closed): Preserve the captured M5A inter-segment audio in the scored I2S WAV
- **PR #1429**: fix: write the captured I2S phrase as the scored listening WAV, gap included

### 2026-11-26

- **Issue #1425** (closed): Clarify how the frozen MIDI-84 oscillator drift pair is selected
- **PR #1432**: docs: say the MIDI-84 drift pair is selected by note, not by prefix
- **Issue #1334** (closed): measure_conga_body_spread: descent_test still globs the Fischer set it compares against
- **PR #1421**: fix: conga descent reads named takes, keeps the harness verdict
- **Issue #1407** (closed): Refuse across-note drift comparisons with different nominal pitches or sample rates
- **PR #1410**: fix: refuse across-note drift on mismatched declared pitch or rate
- **Issue #1309** (closed): Refuse incomplete Fischer reference sets in the conga body-spread report
- **PR #1322**: fix: refuse an incomplete Fischer conga set instead of shrinking the spread
- **Issue #1412** (closed): Remove the shared F1 response destination from off-board RTL runs
- **PR #1413**: fix: write each F1 response curve beside its own results instead of one shared path
- **Issue #1405** (closed): Remove the partial-window fallback from across_note_drift
- **PR #1408**: fix: refuse across-note drift when any requested window is unusable
- **Issue #1403** (closed): Reuse the reference rig’s MIDI dispatch so Mini V4 calibration can play its first note
- **PR #1404**: fix: route Mini V4 calibration note through shared DawDreamer MIDI helper
- **Issue #1396** (closed): main is red: nine tools/test_check_workflows.py failures from stale open_issue exemptions for the closed #1222
- **Issue #1314** (closed): measure_mac_midi_latency.py REFUSED exits need NumPy, so they report FAIL instead (adjacent to #1297)
- **PR #1344**: fix: defer the MIDI verifier import until the latency report is written
- **Issue #1330** (closed): gate_between/tom_conga_gate print a literal (None) for a zero worst ratio at three more sites
- **PR #1343**: fix: omit absent worst-feature labels in 808 gate reports
- **Issue #1339** (closed): D10A/D13A line-shape probe computes the floorless fit in both comparison columns
- **PR #1340**: fix: distinguish pedestal and floorless fits in the partial-balance probe
- **Issue #310** (closed): Sweep hygiene: a killed sweep leaves its nohup'd heavy job running, and nothing reaps or surfaces it
- **Issue #1316** (closed): worst_column_move's nonfinite guard is defeated by a length mismatch or an empty row
- **PR #1341**: fix: refuse empty or unequal band-share rows in the bass-drum probe

### 2026-11-25

- **Issue #1317** (closed): gate_calibrate: the stated `missing` residual reaches CALIBRATED (9/9), not just a shifted k
- **PR #1326**: probe: random take loss reaches CALIBRATED in 66/5000 seeded trials
- **Issue #1222** (closed): nightly.yml: the verify_ctl and verify_synth_top mutant steps have no clean run anywhere in the workflow
- **PR #1227**: fix(nightly): gate the ctl and synth_top mutant steps on a clean run
- **Issue #1302** (closed): Honor --image in fpga/play_song.py render instead of silently rendering R1 for --image r2
- **Issue #1329** (closed): gate_calibrate/gate_between: a REFUSED decision still exits 0, next to the branch that now exits 2
- **Issue #1319** (closed): gate_between table labels a perfect render with worst ratio 0 as “no render”
- **Issue #1289** (closed): max() accumulators elsewhere drop a NaN the same way #1284's separator did
- **Issue #1338** (closed): gate_calibrate: evidence_refusal's new 'rows' guard arm cannot fire through calibrate() (regroup dereferences rows first)
- **Issue #1306** (closed): gate_calibrate: a non-UTF-8 index or a table without `missing` raises out of calibrate() instead of refusing
- **Issue #1331** (closed): Upstream (rjwalters/loom): config-resolver.sh jq --argjson exceeds MAX_ARG_STRLEN, breaking create-issue.sh on this host
- **Issue #1325** (closed): create-issue.sh is broken on this host: config-resolver.sh exceeds jq's per-argument limit (upstream, needs operator to forward)
- **Issue #1323** (closed): record_refusal: a 1-row-per-sound record still calibrates (the blind spot #1298 discloses)
- **Issue #1297** (closed): fpga/measure_mac_midi_latency.py --help needs NumPy it never uses (same class as #1282)
- **Issue #1301** (closed): A complete sound set with empty per-sound tables still calibrates on one sound's evidence (rule-8 sibling of #1296)
- **Issue #1315** (closed): create-issue.sh is dead on hosts whose defaults.json exceeds 128 KiB (config-resolver passes tiers as argv) — forward to rjwalters/loom
- **Issue #1221** (closed): rungs.yml rtl job: the default-config verify_synth_top mutants (VOICE_MASTER_PRESHIFT, I2S_SHIFT) have no config-matched clean twin
- **Issue #1263** (closed): Remove the missing-neighbour fallback from 808 gate calibration
- **Issue #1302 (closed)**: Honor --image in fpga/play_song.py render instead of silently rendering R1 for --image r2
- **PR #1310**: fix: honor --image in play_song render (r2 uses pulse_2x)
- **Issue #1329 (closed)**: gate_calibrate/gate_between: a REFUSED decision still exits 0, next to the branch that now exits 2
- **PR #1333**: fix: gate CLIs exit REFUSED (2) when the decision is REFUSED
- **Issue #1319 (closed)**: gate_between table labels a perfect render with worst ratio 0 as “no render”
- **PR #1321**: fix: show a zero worst-ratio render in the gate_between table instead of 'no render'
- **Issue #1289 (closed)**: max() accumulators elsewhere drop a NaN the same way #1284's separator did
- **PR #1291**: fix: refuse nonfinite readings in two max() accumulators
- **Issue #1338 (closed)**: gate_calibrate: evidence_refusal's new 'rows' guard arm cannot fire through calibrate() (regroup dereferences rows first)
- **Issue #1306 (closed)**: gate_calibrate: a non-UTF-8 index or a table without `missing` raises out of calibrate() instead of refusing
- **PR #1324**: fix: refuse on non-UTF-8 index or table lacking 'missing' instead of raising
- **Issue #1331 (closed)**: Upstream (rjwalters/loom): config-resolver.sh jq --argjson exceeds MAX_ARG_STRLEN, breaking create-issue.sh on this host
- **Issue #1325 (closed)**: create-issue.sh is broken on this host: config-resolver.sh exceeds jq's per-argument limit (upstream, needs operator to forward)
- **Issue #1323 (closed)**: record_refusal: a 1-row-per-sound record still calibrates (the blind spot #1298 discloses)
- **Issue #1297 (closed)**: fpga/measure_mac_midi_latency.py --help needs NumPy it never uses (same class as #1282)
- **PR #1299**: fix: defer NumPy-pulling imports in the Mac latency probe so --help works
- **Issue #1301 (closed)**: A complete sound set with empty per-sound tables still calibrates on one sound's evidence (rule-8 sibling of #1296)
- **PR #1305**: fix: refuse calibration on incomplete per-sound evidence (index-backed)
- **PR #1264**: ci: gate default-config synth mutants behind a clean default verify_synth_top run
- **PR #1279**: fix: refuse incomplete 808 neighbour WAV sets in gate calibration
- **Issue #1315 (closed)**: create-issue.sh is dead on hosts whose defaults.json exceeds 128 KiB (config-resolver passes tiers as argv) — forward to rjwalters/loom
- **Issue #1221 (closed)**: rungs.yml rtl job: the default-config verify_synth_top mutants (VOICE_MASTER_PRESHIFT, I2S_SHIFT) have no config-matched clean twin
- **Issue #1263 (closed)**: Remove the missing-neighbour fallback from 808 gate calibration
- **PR #1295**: fix: validate sound selections before measuring calibration tables
- **PR #1293**: fix: defer render-only imports so render_miniv4_suite --help/--list run on stdlib Python
- **PR #1288**: fix: share one qualification rule between snare separator CLI and test
- **PR #1287**: fix: defer qualified_domain import so MIDI port discovery needs no NumPy
- **PR #1286**: fix: reject empty or unknown --scenario in the late-event verifier
- **PR #1285**: test: retire workflow waivers when matching clean runs appear
- **PR #1283**: fix: check all qualified ground-truth citations in acceptance gate
- **PR #1277**: fix: load M5A audio dependencies only during scoring
- **PR #1274**: fix: skip the RTL control when the rolling replay was refused
- **PR #1273**: fix: catch cross-module Refused at six M1A CLI boundaries
- **PR #1272**: ci: install soundfile in tools job so the refusal control executes
- **PR #1270**: fix: return the refusal exit status from M5 measurement tools
- **PR #1269**: test: pin same-name Refused re-export against narrowed origin guard
- **PR #1259**: fix: let the R1 quick-start dry run work without SciPy
- **PR #1201**: controls: real-verifier mixed-baseline evidence, end-to-end make controls runs, drums outdir fix
- **Issue #1294** (closed): gate_calibrate measure --sounds with no value writes an empty table record and exits 0
- **Issue #1292** (closed): Defer render-only dependencies in render_miniv4_suite so --list works on stdlib Python
- **Issue #1290** (closed): Nothing pins share_bound's published values, so widening it would pass both separator entry points
- **Issue #1284** (closed): Remove the duplicate verdict from the snare separator self-test
- **Issue #1282** (closed): Defer the voice-model dependency during MIDI port discovery
- **Issue #1278** (closed): Remove the module-name filter that skips 808 ground-truth citations
- **Issue #1276** (closed): Require a named scenario when selecting UART late-event checks
- **Issue #1268** (closed): Defer SciPy and V3-reader imports in mono_m5a_score so named-preset UART, live MIDI, and demo players run without SciPy
- **Issue #1267** (closed): refusal_sibling_scan: the `nm == FAMILY_NAME` guard has no control for the explicit re-export `from lender import Refused as Refused`
- **Issue #1266** (closed): Catch cross-module Refused at M5A / M5B / mono_artifact_probe CLI boundaries (7 modules)
- **Issue #1265** (closed): Catch cross-module Refused at M1A / phase-cycle CLI boundaries (6 modules)
- **Issue #1262** (closed): measure_repeatability's only refusal control never runs in CI (soundfile absent by omission, not decision)
- **Issue #1258** (closed): Remove the SciPy requirement from default-patch M5A UART playback
- **Issue #1252** (closed): STEP_GATED_ALLOWLIST open_issue exemptions have no mechanical retirement, unlike the pending run_all entry
- **Issue #1229** (closed): A refused rolling RTL replay now runs the rtl_control stage it used to skip
- **Issue #1202** (closed): Triage the mono_m1a/mono_m5a raiser family: 11 modules whose local-subclass Refused handlers miss the parent class (#1183 remainder)
- **Issue #1126** (closed): Require a matching clean run before make controls credits an RTL mismatch

- **Issue #1235** (closed): No mechanical guard that workflow injected-defect controls are gated; the run_controls guards read the Makefile recipe only
- **PR #1240**: test: guard workflow RTL mutant gating and baseline config match
- **Issue #1248** (closed): uart_host --dry-run status/abort renders the apply_frame sentinel -1 in the 'applies' column
- **Issue #1207** (closed): Four log10-over-ratio sites in tools/cymbal_*.py were not individually named in #1197's class sweep
- **PR #1254**: Audit four cymbal log ratios and fix invalid balance verdicts
- **Issue #1234** (closed): refusal_sibling_scan: an ALIASED borrow sets refused_origin, so a dotted `except <mod>.Refused` is credited for an attribute that does not exist
- **PR #1261**: fix: refusal_sibling_scan credits <mod>.Refused only for a binding named Refused
- **Issue #1219** (closed): Consolidate duplicated UART RTL capture/replay in verify_live_midi and verify_rolling_playback (fixes --image r2 running with PULSE2X=0)
- **PR #1228**: fix: share the UART RTL capture/replay pipeline; replay live-midi r2 bytes at PULSE2X=1
- **Issue #1253** (closed): render_plan prints the -1 sentinel in the applies column for status/abort rows
- **PR #1255**: fix: render unset apply_frame as '-' in dry-run plan and echo lines
- **Issue #1183** (closed): Triage the remaining cross-module refusal escape candidates from the refusal_sibling_scan census
- **PR #1203**: fix: catch cross-module corpus refusals at five run_case-caller boundaries (run_case.* family of #1183)
- **Issue #1239** (closed): Tighten the audition help control: `>= 2` lets a silently-no-op replace retire one of its three injected defects
- **PR #1251**: test: require all three findings in audition help control
- **Issue #1243** (closed): Use one packet-duration calculation for the UART player’s budget and preflight
- **PR #1247**: fix: give the UART budget packet durations in audio frames
- **Issue #1217** (closed): rungs.yml pulse2x job: the VOICE_PULSE2X_OFF control's clean twin is an adjacent run_all row, which cannot gate it
- **PR #1223**: ci: gate the pulse2x VOICE_PULSE2X_OFF control on its clean twin via run_controls
- **PR #1246**: fix: stop UART status and abort opening the bridge before the dry-run decision
- **PR #1226**: docs: make MIDI and UART packages optional in the keyboard audition install
- **PR #1218**: tools: hash tools/delta_report.py in each comparator's SOURCES
- **PR #1209**: fix: refuse duplicate step names in the roadmap pair gate instead of last-wins
- **Issue #1245** (closed): Remove early UART dispatch that sends abort and status during --dry-run
- **Issue #1215** (closed): Make MIDI and UART packages optional in the keyboard audition instructions
- **Issue #1208** (closed): Decide whether tools/delta_report.py belongs in each comparator's SOURCES (provenance now computed by unhashed code)
- **Issue #1206** (closed): check_workflows: the #906 pair order gate lets a duplicate guarded step name through (dict-overwrite, no ambiguity refusal)
- **Issue #1236** (closed): CI: give the full tools pytest job enough time to report its verdict
- **PR #1242**: ci: raise the tools job budget to 105 minutes so its verdict can report
- **Issue #1232** (closed): Remove conflicting key assignments from the audition player’s keyboard guide
- **PR #1233**: fix: correct audition player help keys for glide, hats and filter envelope
- **Issue #1249** (closed): Harden the audition help control: a silent str.replace no-op still satisfies it

### 2026-11-24

- **Issue #1214** (closed): refusal_sibling_scan: ambiguous-callee reports family-less callees; --json leaks absolute paths
- **PR #1230**: fix: filter non-raising stem collisions and make refusal census candidates repo-relative
- **Issue #1212** (closed): rungs.yml rtl-full: the Verilator VOICE_FILTER2X_OFF control has no clean --m5a-smoke twin in that job
- **PR #1220**: ci: pair rtl-full's Verilator M5A mutant with a clean smoke baseline
- **Issue #1211** (closed): Use the ensemble scorer’s existing file hash checks for cached negative controls
- **PR #1216**: Bind ensemble control-run reuse to the sidecar digests the report carries
- **Issue #1192** (closed): check_workflows roadmap order gates key on step name, so the SET of compile_dag.py --run invocations is unasserted
- **PR #1196**: ci: assert the compile_dag.py invocation set, not just the named order-gate steps
- **Issue #1182** (closed): Wire in tools/performance_suite.py: the playing-behaviour gate that nothing runs
- **Issue #1193** (closed): refusal_sibling_scan: family classes borrowed by from-import are invisible (9 census sites misclassified)
- **PR #1224**: fix: track Refused families borrowed by from-import in refusal_sibling_scan
- **PR #1204**: fix: refuse empty and near-silent windows in cymbal_bands.thirds
- **Issue #1197** (closed): cymbal_bands.thirds divides by an unguarded e_tot; its zero case is prevented only by call-site ordering
- **PR #1194**: tools: fix the three refusal_sibling_scan blind-spot gaps (try bodies, stem collisions, import aliases)
- **Issue #1188** (closed): Complete refusal_sibling_scan's blind-spot list: three unrecorded modelling gaps (all zero-instance today)
- **PR #1191**: Extract the shared compare-candidate tail into tools/delta_report.py
- **PR #1190**: fix: refuse a silent corpus WAV in cymbal_tone_knob's windowed measurement
- **Issue #1187** (closed): Dedupe provenance block and output guard copy-pasted across three compare_*_candidate scripts
- **Issue #1163** (closed): cymbal_tone_knob: a silent corpus WAV crashes measure_windows with ZeroDivisionError instead of refusing
- **PR #1186**: test: gate every --pulse2x rtl-full step on the identity outcome
- **Issue #1177** (closed): rtl-full identity gate: the pytest guard pins the one named phrase step, not that no OTHER step runs the pulse2x phrases ungated
- **PR #1184**: fix: catch refusal.Refused at the rimshot probes' CLI dispatch, with the cross-module census
- **PR #1145**: controls: require a clean baseline run before crediting an RTL mutant
- **PR #1144**: fix: refuse non-16-bit WAVs in five unguarded reference readers
- **PR #1131**: fix: refuse injected-defect controls whose suite is red before any injection
- **Issue #1162** (closed): Handle cross-module refusals in the rimshot probe and check other CLI callers
- **Issue #1125** (closed): Three injected-defect harnesses have no unmutated-suite baseline: inject_manifest_defects, control_capability_verdicts, control_dag_churn
- **Issue #1117** (closed): Twelve more unguarded `wavfile.read` + /32768 readers survive #817, including `run_case.py`'s reference loader
- **PR #1179**: check_workflows: refuse a step if: that cannot be shown to run on a pull_request
- **PR #1176**: test: require exact success-polarity gate in verilator identity phrase check
- **PR #1175**: ci: refuse rather than report simulator identity in pulse2x-rtl
- **PR #1161**: fix: refuse by name on null provenance/config in m5a compare and m1a regen tools
- **Issue #1178** (closed): step_would_block_pull_request's step arm misses an `if:` that keeps the step off pull requests (the #1168 shape, one level down)
- **Issue #1171** (closed): pulse2x-rtl's simulator-identity step reports rather than refuses; it is safe only by default success() gating
- **Issue #1170** (closed): rtl-full identity gate: the pytest guard accepts any mention of steps.verilator_identity.outcome, not the 'success' polarity
- **Issue #1165** (closed): observe the rtl-full verilator_identity gate on a real dispatch (post-merge follow-up to #1154)
- **Issue #1155** (closed): Same-shape sweep from #1100: three provenance reads still crash on a present-but-null field
- **PR #1174**: fix: refuse any job-level if: in the signoff harness (_locate)
- **PR #1169**: refactor: adopt control_dag_lib harness in control_capability_verdicts
- **Issue #1168** (closed): check_workflows: the job and step `if:` arms still disagree (the `continue-on-error` half was #1158)
- **PR #1166**: ci: gate the rtl-full phrase run on a Verilator identity refusal step
- **PR #1164**: fix: catch refusal.Refused at cymbal_bands callers' cross-module call sites
- **Issue #1160** (closed): cymbal_bands.Refused is caught by nobody: its three callers each define a sibling Refused, so a cymbal refusal exits 1 (FAIL), not 2
- **PR #1159**: fix: treat expression-form continue-on-error as neutralising at job and step level
- **Issue #1158** (closed): check_workflows: the job/step `continue-on-error` neutralisation check misses the `${{ ... }}` expression form
- **Issue #1154** (closed): rtl-full's pulse2x phrase step can run under the wrong Verilator: assert the simulator identity at the point of use
- **PR #1105**: refactor: consolidate the triplicated control_dag harness into control_dag_lib
- **Issue #1099** (closed): Adopt control_dag_lib in control_capability_verdicts.py (same harness shape found by #1097's closing search)
- **Issue #1097** (closed): Consolidate duplicated control_dag harness (RUNNER/run_one/main) into one shared helper
- **PR #1150**: test: refuse job-level if/continue-on-error in the regrade harness
- **Issue #1152** (closed): check_workflows: job-level continue-on-error only matches the bare YAML boolean (rule-8 input)
- **Issue #1143** (closed): test_signoff_klt_version REFUSES on a step-level kill switch but reports green on a job-level one
- **PR #1147**: fix: normalise trajectory() cells to a share of clip energy, independent of sample rate
- **Issue #1129** (closed): trajectory() cells carry an n_seg/2 length multiplier, biasing every ours-minus-machine cell +0.368 dB (48 kHz vs 44.1 kHz)
- **PR #1142**: fix(probe): refuse a holdout control whose red is outside its recorded tests
- **Issue #1136** (closed): holdout probe: a FIRED control's red is still not attributed to its injection (candidate 2 from #1124)
- **PR #1151**: fix: refuse malformed provenance and read quoted klt pins in signoff regrade
- **Issue #1100** (closed): Fix malformed-provenance refusals and quoted-pin comparisons in signoff
- **PR #1134**: refactor: remove five unreferenced defs, moving their explanations to live code
- **Issue #800** (closed): Remove 6 unreferenced top-level defs in model/ and tools/: each is a dead duplicate of logic inlined at its call site
- **PR #1133**: ci: re-home full pulse2x phrase run into rtl-full, delete pulse_audio workflow
- **Issue #760** (closed): Retire or re-home pulse_audio.yml: its only push trigger is a branch that merged in September

### 2026-11-23

- **PR #1141**: fix: baseline refusal names pytest's real exit status
- **Issue #1128** (closed): holdout probe's baseline refusal names "pytest exited 1" when pytest exited 2 with a parsed FAILED line
- **PR #1114**: fix: ignore .loom/account-ranking.* daemon state file
- **Issue #1112** (closed): check-main-clean.sh exits 3 on every sweep: the daemon state file .loom/account-ranking.codex is not in the loom-managed .gitignore block
- **PR #1101**: test: run the pinned-klt envelope step locally, not only in CI
- **Issue #1095** (closed): the pinned-klt envelope gate has no committed control and no local half: it is runnable only by pushing to CI
- **PR #1139**: chore: regenerate loom-managed gitignore block
- **Issue #1122** (closed): loom-managed .gitignore block is two patterns stale vs installed loom-daemon 0.20.20 (/node_modules/, /pnpm-lock.yaml)
- **PR #1138**: fix: refuse two mutation records when the unmutated suite is red
- **Issue #1130** (closed): Two unwired mutation records read any non-zero pytest as killed with no unmutated run: mutate_pnr_report, probe_injection_discrimination
- **PR #1140**: docs: link DAG tag criterion to generated evidence table
- **Issue #1137** (closed): Link the capability DAG’s stamping criteria to the generated tag evidence
- **PR #1135**: docs: refresh Foundation/Minimoog status cells from dag-results
- **Issue #1121** (closed): docs/capability-dag.md F2 status still says the modal bank is on the unmerged `drums` branch
- **PR #1127**: fix: holdout probe refuses on a red unmutated baseline before any control runs
- **Issue #1124** (closed): holdout_injected_controls has no unmutated-tree baseline: a pre-existing red in test_holdout.py reads as 'every control fired'
- **PR #1111**: docs: record the unfiled judge fast-path gh field finding
- **Issue #820** (closed): Post-merge live verification of #818: the nightly's records land on main, and the [dag] loop guard holds
- **PR #1119**: fix: holdout probe maps pytest's exit status to its own outcome space
- **Issue #1118** (closed): holdout_injected_controls: pytest's own exit status is read as the probe's outcome — a deleted test_holdout.py reports 'every control fired' (exit 0)
- **PR #1107**: Remove stale UART repair probe, record transcript provenance
- **Issue #762** (closed): Remove fpga/probe_uart_findings.py: it reports 2 false FAILs on the repaired host and nothing runs it
- **PR #1104**: test: require the drum model in TR-808 acceptance
- **Issue #1094** (closed): Remove the obsolete optional-model skips from TR-808 acceptance
- **PR #1103**: fix: refuse non-16-bit WAVs in the two unguarded reference-corpus readers
- **Issue #817** (closed): Consolidate the seven WAV readers: four conventions, and two answer 96 dB wrong on a 24-bit file instead of refusing
- **PR #1115**: fix: holdout probe exits 2 for REFUSED instead of folding it into exit 1
- **Issue #1113** (closed): make controls records holdout_injected_controls' REFUSED as FAIL, and its Makefile comment names an exit 2 the probe never emits
- **PR #1108**: probes: dispositions for the four zero-reference orphan probes (wire, pin, delete)
- **Issue #1106** (closed): Unreferenced probe scripts under tools/ may drift the way fpga/probe_uart_findings.py did
- **PR #1093**: Prepare DRC, LVS and supply ERC probes for the routed ladder
- **PR #1098**: chore: remove legacy unsaturating 2x decimator prototypes and unrun Verilog scratch benches
- **Issue #1096** (closed): Remove 2 legacy unsaturating 2x decimator prototypes and 5 unrun Verilog scratch benches from rtl-sketch/
- **PR #1090**: signoff: fail regrade on an evidence envelope whose klt_version is not the pin
- **Issue #1089** (closed): regrade cannot tell a re-attested envelope from a stale one: no gate binds evidence/item-{9,10}.json provenance.klt_version to the pinned klt

### 2026-11-20

- **PR #1087**: signoff: bump the klt pin 0.8.0 -> 0.9.2, re-attest items 9 and 10, record the item 8 refusal
- **Issue #1084** (closed): T1 item 8: bump the signoff klt pin 0.8.0 -> 0.9.2, re-attest items 9 and 10, and attest the committed characterization report

### 2026-11-19

- **Issue #1075** (closed): Remove the Arty bring-up retry loop that hides a refused held note
- **PR #1076**: fix: stop bringup_arty's play_note retrying a refused hold
- **PR #1074**: State the foreign-host URL residual of the slow-record gate and pin it
- **Issue #1073** (closed): check_nightly_slow_records: the artifact-record URL prefix is `\S*`, so a foreign host or bare path in a complete record line still attributes the run
- **Issue #1066** (closed): check_nightly_slow_records: --run-id is optional but load-bearing, and control mode exits 0 over a printed FAIL
- **PR #1067**: fix: require --run-id and return the worse of verdict/control exits in the nightly slow-record gate
- **PR #1072**: fix: preserve quarter-slot geometry and voice parameters in ORFS
- **Issue #1070** (closed): Nightly slow-record gate accepts an Actions dependency SHA as the repository head
- **PR #1071**: fix: nightly gate attributes head sha to the checkout step's own output, not any line naming it
- **Issue #1033** (closed): Two pre-existing claim markers split their paragraph in rendered Markdown (docs/tr808-reference.md)
- **PR #1065**: docs: trail six mid-paragraph claim markers; refuse the splitting placement
- **Issue #1058** (closed): AC (a)'s sha-equality discriminator in #820 cannot pass on a quiet night under record_unchanged() (#967) — rederive the nightly slow-record check
- **PR #1059**: add check_nightly_slow_records: per-node corroboration gate for #820 AC (a)
- **Issue #1056** (closed): Say why eight of the ten files hashed into the frozen Mini V3 records are deliberately NOT guarded
- **PR #1060**: docs: correct Mini V3 re-bind guidance and narrow the reader-pin rationale
- **Issue #706** (closed): Route the hash-pinned Mini V3 ring readers through ring_estimator (needs rig)
- **Issue #922** (closed): Tracked-JSON truncate-then-write remains in holdout.py (ledger + seals) and measure_m1a_volume_mapping.py
- **PR #948**: tools: write holdout ledger/seals and M1A volume-mapping records atomically
- **PR #750**: fix: keep the Mini V3 ring readers frozen with a named guard (re-scope per #1048)
- **Issue #1053** (closed): Isolate the Mini V3 freeze-defeat tests so each mutation starts from a passing copy
- **PR #1055**: test: isolate freeze-defeat mutants in function-scoped copies with a passing control
- **Issue #1050** (closed): [voice-only] Baseline Mini V4 as a new reference suite: render script, case list, board case table
- **PR #1052**: feat: baseline Mini V4 as a separate reference suite (manifest, render script, V3 freeze, board suite column)
- **Issue #1018** (closed): Confirm WORK_PLAN.md's as-of sentence survives Guide regeneration, and reconcile it with the inbound upstream in-region stamp
- **PR #1045**: fix: refuse a claim marker whose closer is not on its opener's line
- **Issue #1044** (closed): check_doc_claims.py silently drops a claim marker that spans two lines, so an unverified claim reports green

### 2026-11-18

- **PR #1042**: fix: refuse a second claim marker on one line instead of dropping it
- **Issue #1041** (closed): check_doc_claims.py silently drops a second claim marker on the same line, so an unverified claim reports green
- **Issue #1040** (closed): analyze_def_sites: read the kept DEF's extension-point census on the #1010 host (live-vs-latent, one command)
- **PR #1039**: fix: a '+' in the DEF walk ends the layer-name position
- **Issue #1037** (closed): analyze_def_sites: a skipped '+' does not end the LAYER-name position either (sibling of #1034)
- **PR #1031**: fix: consume skip-keyword name arguments so they never become phantom vias
- **Issue #1030** (closed): analyze_def_sites: a skip-keyword's NAME argument after a path's points becomes a phantom via (same class as #1013/#1021, via branch not width branch)
- **PR #1029**: docs: bind #1006 route-record figures to claim markers
- **Issue #1023** (closed): docs/tool-findings.md carries zero claim markers, so its route-record figures have nothing re-deriving them
- **PR #1025**: fix: apply the DEF path-point extension to the wire rect and count it in the record
- **Issue #1022** (closed): analyze_def_sites: the DEF path-point extension is recorded but never applied to the emitted rect, nor surfaced in the record
- **PR #1035**: fix: end the via-name position at a skipped '+' in DEF path walk
- **Issue #1034** (closed): analyze_def_sites: '+ NONDEFAULTRULE <name>' after a path's points makes the keyword a phantom via (the spelling #1030 left out of scope)
- **PR #1027**: docs: scope 'bound in-repo' claim to what route_def_manifest.json carries
- **Issue #1026** (closed): tool-findings.md:189-193 — "this chain is bound in-repo" covers two items the manifest does not
- **PR #1024**: fix: consume STYLE/MASK/WEIGHT numeric arguments with their keyword, never as the path width
- **Issue #1021** (closed): analyze_def_sites: STYLE/MASK numeric arguments are still consumed as the path width
- **PR #1019**: bind the DEF re-run evidence chain in-repo: commit route_def_manifest.json, regenerate def_site_analysis.json with totals, shape tags, overlap lower bound
- **Issue #1011** (closed): Commit route_def_manifest.json and regenerate def_site_analysis.json's record fields against the kept DEF
- **PR #1016**: docs: accept ladder_dp's 24 Metal2 PDN spacing sites as known-PDN residual
- **Issue #1009** (closed): Floorplan/PDN: the M1->M4 via-column lands leave 24 Metal2 spacing violations in ladder_dp's finished route
- **PR #1017**: parse the DEF path-point extension form ( x y ext ): record ext_start/ext_end, never consume it as width
- **Issue #1013** (closed): analyze_def_sites: parse or refuse DEF path-point extension form ( x y ext )
- **PR #1015**: docs: add static as-of instruction to WORK_PLAN.md header
- **Issue #1014** (closed): WORK_PLAN.md carries no as-of date, so a reader cannot tell when a declined regeneration left it stale
- **PR #1010**: bind the ladder_dp route-DRC claims in-repo and answer design-versus-router from the routed DEF
- **Issue #1006** (closed): ladder_dp route record: design-vs-router is undetermined for want of a DEF, and the classifier's inputs are not in the tree
- **PR #1005**: test: injected defect for the start-skip legitimacy half of the two-instant CB control
- **Issue #1004** (closed): test_verify_109_corpus_gate: the start-skip legitimacy assertion ships without the input that defeats it (rule 8)
- **PR #1001**: tools: commit the probes prose-literal census its counts were quoted from
- **PR #1002**: pnr: commit the finished ladder_dp route record (klt 0.9.0) and classify its 24 residual DRC violations
- **PR #999**: fix: NOT_QUOTED sentinel for unquoted CONCLUSION figures in the corpus-gate test
- **Issue #998** (closed): The #988/#990 prose-literal class scanner is in no tree, so its 139/159/156 counts (and "no third site") cannot be re-derived
- **Issue #997** (closed): test_verify_109_corpus_gate's CB extent parser can report a drift that does not exist (two-instant CB returns start=None)
- **Issue #808** (closed): Route ladder_dp to completion with the unpatched netlist on the build box: DRT-0305's cause is measured gone, the error is not re-observed
- **PR #1000**: tools: remove one-shot ring-ratio snapshot harness
- **PR #996**: fix: render_writes refuses out-of-record streams instead of asserting
- **PR #993**: probes: derive two summaries' quoted figures from the run's own measurements
- **PR #991**: probes: derive verify_109_claims CONCLUSION extents from the run's measurements
- **PR #987**: perf suite: drive the shipped mixer -- a synth_top backend for the full-performance suite (#338)
- **Issue #995** (closed): performance_suite: render_writes answers AssertionError, not REFUSED, for a stream past the record
- **Issue #990** (closed): Class sweep (#988): two more same-shape sites - hard-coded prose figures beside the same run's live measurements
- **Issue #988** (closed): verify_109_claims:244's CONCLUSION understates RS floor-clearing by ~13 ms (53.9 measured vs ~41 prose), and the figure is registered with no gate
- **Issue #975** (closed): Remove tools/ring_ratio_compare.py: one-shot #548 before/after harness, nothing references it
- **Issue #912** (closed): Five more JSON-refusal handlers miss UnicodeDecodeError: the #909 gap generalises past load_json_refused
- **Issue #338** (closed): Sound: full-performance suite — bass, lead, drum-only and mixed phrases with control movement
- **PR #818**: ci: commit the nightly's slow DAG records to main, not just the artifact
- **Issue #787** (closed): Carry the nightly's slow DAG evidence to main so F1, M1, D1, I1 and I2 stop reading STALE or "never run"
- **PR #986**: docs: record verify_109_claims half-2 live run against the real Fischer corpus
- **PR #985**: docs: mark §5c mpd.* bucket names as stale pending #933 regeneration
- **Issue #983** (closed): CI produced zero check-runs on PR #982: pull_request workflows stopped triggering (needs operator)
- **Issue #829** (closed): Run `make reference-integration` on the box: verify_109_claims' real-reference half has never been exercised against the real Fischer corpus
- **Issue #856** (closed): Provision the pinned build box for this checkout: no .env, no remote.env, and the dispatch worker's AWS identity has no EC2 access
- **Issue #869** (closed): Forward upstream to rjwalters/loom: post-verdict issue-body hash refuses when the body ends in a newline (#13600's fix covers one trailing newline, jq gives two)

### 2026-11-17

- **PR #980**: docs: record the posted OpenROAD#12303 correction of record in the #12307 row
- **Issue #888** (closed): Post the #12307 correction of record as a comment on OpenROAD#12303
- **Issue #826** (closed): compile_dag --only: a named node that cannot run is skipped SILENTLY (exit 0, no line)
- **PR #974**: dag: restate verdict-less evidence_file as REFUSED in docs, and forbid the silent declaration
- **PR #972**: fix: compile_dag --only refuses a named node it cannot run, instead of silence
- **PR #977**: test: pin run_evidence()'s known-key REFUSED and its non-recording path through main
- **Issue #976** (closed): compile_dag: run_evidence()'s EVIDENCE_PRECEDENCE refusal ships without the input that defeats it
- **Issue #971** (closed): compile_dag: docs/capability-dag.md and test_compile_dag_deps.py still state the pre-#791 'EXISTS ONLY returns pass' behaviour as current
- **PR #968**: test: refuse the archive fixture during a squash merge
- **PR #973**: fix: derive compile_dag's four evidence-source readers from one EVIDENCE_PRECEDENCE
- **PR #969**: fix: keep unchanged dag records byte-identical so record PRs can land (#967)
- **PR #970**: fix: share the claim-marker skip grammar between checker and #789 probe
- **PR #810**: dag: report an evidence file that declares no verdict as REFUSED, not GREEN
- **PR #944**: test: catch serialise-then-write and write-mode open spellings in the inline record-write guard
- **PR #965**: fix: refuse the 789-marker suite's archive fixture mid-merge
- **PR #953**: fix: --margins all-caught no-cliff input states the measured fact, not 'not every pair is CAUGHT'
- **PR #957**: fix: 789 marker probe derives its already-satisfied guard from the staged injection
- **PR #960**: fix: anchor the drum_kit grep= claim so the 789 probe's injection cannot collide with it
- **Issue #966** (closed): 789-marker fixture guard: `git merge --squash` reproduces the exact mid-merge split it refuses, and is not refused
- **Issue #827** (closed): compile_dag: unjudged() keys on evidence_file but run_evidence dispatches suite/verifier first, so a node with both would be REFUSED despite a passing suite
- **Issue #967** (closed): Any PR that rewrites a docs/dag-results.json record cannot land while main is busy: dag.yml re-conflicts it every merge
- **Issue #962** (closed): 789 probe: injected_state mirrors the checker's FENCE rule but not its inline-backtick rule
- **Issue #791** (closed): compile_dag renders a node GREEN when its evidence file declares no verdict: M3, S1 and S3 are green on existence alone
- **Issue #923** (closed): The source-shape guard in test_atomic_record_writers.py stays green on open(...,'w') + write(json.dumps(...))
- **Issue #964** (closed): 789 probe suite: a merged-tree measurement is silently void unless HEAD is the merge commit (git archive HEAD fixture)
- **Issue #950** (closed): print_margins' else prose says 'not every pair is CAUGHT' on a guard that does not measure it
- **Issue #936** (closed): 789 marker probe: already_satisfied() holds a hand-written copy of each claim's in=/grep=, and the two can diverge silently
- **Issue #959** (closed): main is red: the 789 marker probe's already-satisfied injection also defeats #904's grep= uniqueness refusal (tools job, since 72be3665)
- **PR #951**: probes: split the rate-fold verdict else on the dB red count it mis-states
- **Issue #946** (closed): rate_fold_db_control's verdict prose states a stronger fact than `must == []` supports
- **PR #945**: test: guard the ms.* grouping label against a silent %.0f collision
- **Issue #939** (closed): measure: the ms.* grouping label is %.0f, so two MS_WINDOWS_S entries that round to the same integer ms would silently merge buckets
- **PR #876**: ci: collect the 16 uncovered fpga/ test files in a fpga-uncovered job
- **Issue #853** (closed): 16 of 40 fpga/ test files are run by no CI job (23 more only via make verify-fast) -- the fpga half of the #404/#851 shape
- **Issue #958** (closed): 789 marker probe: the #920 already-satisfied refusal for drum_kit is shadowed by check_doc_claims' one-line uniqueness refusal — tools job red on main since 72be3665
- **Issue #956** (closed): compile_dag.py's status legend lists BLOCKED and TODO twice (merge artefact from #810)
- **Issue #937** (closed): rate_tol_sweep.py --unit <anything but db> labels its columns caught_db/cost_db, and two smaller label drifts from #895
- **PR #942**: fix: name rate_tol_sweep's per-unit columns by the swept unit
- **Issue #932** (closed): check_attested_hashes: a pathed check entry with no content_hash is a silent green, and is counted as compared
- **PR #938**: fix: REFUSE pathed check entries with no committed hash; count only compared files
- **Issue #929** (closed): measure: extra_group() merges the 10.0 ms and 5.0 ms MPD strides into one bucket (6 strides -> 5 buckets)
- **PR #934**: fix: bucket MPD attribution columns by stride, not by stride decimals
- **Issue #920** (closed): 789 marker probe: a control still reports FAIL when the tree already satisfies the value it injects
- **PR #927**: fix: 789 marker probe refuses an injection state the tree already satisfies
- **Issue #815** (closed): Re-base the 114 Refused definitions on refusal.Refused and delete the three mitigations it makes dead (follow-on to #784)
- **PR #915**: fix: re-base every Refused on refusal.Refused, delete the three dead mitigations, flip the guard to enforcing
- **Issue #904** (closed): check_doc_claims.py cannot detect a grep= pattern that matches several lines, so no marker's uniqueness is machine-checked
- **PR #916**: check_doc_claims: refuse a grep= marker matching more than one line
- **Issue #928** (closed): docs: discrimination.md:962 points at line 232 of discrimination-run-16.txt, which #778 regenerated
- **PR #931**: docs: re-anchor jit.stability figure to revision 24937ca4
- **Issue #940** (closed): The #906 roadmap gate: tags assumed in rungs.yml but asserted in nightly.yml, and nothing guards the shipped step order
- **PR #943**: ci: assert the shipped compile_dag check-before-run order; state rungs' tag precondition
- **Issue #935** (closed): the dB family's rate tolerance ships 8.0 dB of licence at the floor_db clamp with no injected-bug control on any dB column
- **Issue #921** (closed): Makefile and docs/claim-markers.md understate the 789 marker probe's runtime margin by ~6x
- **PR #926**: docs: restore measured ~0.6 s / ~6000x figures for the 789 probe runtime note
- **PR #941**: [rate-tol] measured: no dB must row is constructible for the integer-fold control at any licence (#935)
- **Issue #906** (closed): rungs.yml's roadmap gate checks the README that the line above it just rewrote
- **PR #924**: fix: the roadmap gate judges the README as committed, not the regen's own output
- **Issue #882** (closed): Tracked-JSON experiment-record writers still truncate-then-write (class sweep remainder from #881)
- **PR #918**: fix: tracked-JSON experiment records replace atomically; record readers refuse with a path
- **Issue #782** (closed): bug: the relative rate tolerance is dimensionally meaningless on the 147 dB columns
- **PR #895**: split RATE_TOL_REL into per-unit scalars; sweep the dB term to 0.1 and empty the rate quarantine
- **Issue #884** (closed): regrade: an attested-file edit fails as a bare 'exit 3' with the drift report swallowed, and nothing couples the edit to regenerating the evidence
- **PR #925**: signoff: surface klt's drift report on failure; add attested-hash coupling check to verify-fast
- **Issue #930** (closed): signoff.yml swallows the attest-check diagnostic: failing attestation prints nothing under bash -e
- **Issue #778** (closed): measure: re-derive the jit.stability +0.008 and mpd.* attributions with the exact-period fold (REFUSED in #767 -- no corpus on that host)
- **PR #919**: docs: re-derive the 5b/5c attributions with the post-#776 fold; record corpus octave incidence
- **Issue #885** (closed): pytest_collection_inventory.py:78 comment says pnr/ is 'make verify only' — false once the pnr CI job lands (#851/#865)
- **PR #914**: docs: correct GUARDED_DIRS comment — CI collects pnr/ as a third job
- **Issue #879** (closed): docs: makefile_recipe comment-filter docstrings claim a silent exit 0 for a mutation that is loud mid-recipe
- **PR #907**: docs: scope makefile_recipe comment-filter docstrings to the line position
- **Issue #905** (closed): check_doc_claims_789_marker_controls.py reports FAIL, not REFUSED, when an uncommitted edit makes a #789 claim stale
- **PR #913**: fix: 789 marker probe REFUSES a dirty tree instead of reporting FAILing controls
- **Issue #874** (closed): docs/tool-findings.md will carry contradictory claims about the core-area identity once #849 merges
- **PR #911**: docs: drop stale attribution of the 6-sf coincidence framing to PR #849's row
- **Issue #909** (closed): load_json_refused catches JSONDecodeError only: a damaged record that is also invalid UTF-8 escapes as a traceback
- **PR #910**: fix: refuse a manifest record truncated mid-UTF-8 rather than a bare UnicodeDecodeError

### 2026-11-16

- **Issue #864** (closed): classify_route_drc.py crashes where it documents exit 3 REFUSED: 3 measured inputs return rc=1 with a traceback
- **PR #908**: fix: classify_route_drc refuses (exit 3) where it documented REFUSED
- **Issue #881** (closed): Class sweep for #842: tracked JSON still written by truncate-then-write outside compile_dag
- **PR #883**: fix: the tracked bounds ledger persists through an atomic replace (#881)
- **Issue #900** (closed): Rule-8 statement for gate (c)'s narrowed header predicate: a column header with no format spec escapes the vintage pin
- **PR #902**: docs: state gate (c)'s spec-less-header bound in the rule-8 paragraph, record it with a control
- **Issue #845** (closed): estimator_defects.py section 6: nine unguarded `.value` arithmetic sites, including two that reach a check() verdict, owned by neither #792 nor #843
- **PR #898**: probes: guard estimator_defects section 6's nine .value reads behind REFUSED
- **Issue #873** (closed): spec/NUMERIC-CONTRACT.md: PR #846's repaired sites cite figures and pointers that no marker guards
- **PR #901**: docs: guard NUMERIC-CONTRACT's cited figures and pointers with 8 grep claim markers
- **Issue #863** (closed): compile_dag: stale_against() answers a content question with a commit-range walk, so a merge-shaped conflict round renders fast nodes STALE (false positive; --full-history is NOT the fix)
- **PR #903**: fix: measure DAG staleness by content, not by a commit-range walk
- **Issue #784** (closed): Give Refused a shared base class: 113 unrelated definitions make two refusals report as a crash and an execution error
- **PR #841**: fix: refusals crossing a class boundary are verdicts again (refusal.Refused base + baseline-mode guard)
- **Issue #811** (closed): Wire or document tools/probes/check_doc_claims_789_marker_controls.py: a control no target runs (the #756 shape, again)
- **PR #897**: controls: wire check_doc_claims_789_marker_controls.py as a standing control
- **Issue #877** (closed): File upstream (rjwalters/loom): loom-daemon's write guard gates issue creation on push/WRITE, refusing every public upstream where issues:write suffices
- **PR #896**: docs: index rjwalters/loom#13978 (forge create-issue refused on every foreign public repo) in the Loom pass table
- **Issue #858** (closed): compile_dag classify(): the empty-covers REFUSED guard masks a FAILING record as REFUSED when the node also carries an in-history tag
- **PR #894**: fix: answer a failing evidence record RED before the empty-covers refusal
- **Issue #870** (closed): probe_orphan_reap argv test drops true matches where a worktree path ends a longer token (--opt=<path>, <path>:<mount>)
- **PR #893**: fix: restore worktree attribution for paths that end a longer argv token
- **Issue #867** (closed): #781 residue: fold-interpolator verdict summary says 'Five' where its own runs field has four singleton groups, plus two merge-jammed lines
- **PR #892**: fix: fold-interpolator verdict counts its four singleton widths; unjam two merge-residue lines
- **Issue #852** (closed): test_probe_labels gate (c) polices filter_corner_recalibration's module docstring, and goes red if it says "pre-#150"
- **PR #891**: fix: scope the probe-label vintage gate to column headers, name the file in violations
- **Issue #890** (closed): Guide's WORK_PLAN Ready list carries forward omissions: #874 was eligible before the run and is absent on both sides of #889
- **Issue #835** (closed): File upstream: ORFS 6_report.json emits finish__design__instance__area twice with two different meanings
- **PR #849**: summarize.py refuses duplicated required metrics (OpenROAD#12303)
- **Issue #851** (closed): pnr/ test files run under make verify but under no CI job (6 of 8) -- the #404 shape, one directory over
- **PR #865**: ci: collect pnr/ as a directory in CI; assert the docker precondition in container_progress
- **Issue #868** (closed): refusal_exit_census: the sites_outside_set pin is keyed on line numbers, so an unrelated edit above cymbal_vca_drive.py:1525 reds the gate
- **PR #878**: fix: key census sites_outside_set on (file, function, status) counts, not lines
- **Issue #866** (closed): fold-interpolator-sweep.txt's n_over column no longer reproduces: #775 narrowed RATE_TOL_REL and the artefact was not regenerated
- **PR #880**: regenerate fold-interpolator n_over against RATE_TOL_REL 0.02 and record the dependency in limits
- **Issue #842** (closed): compile_dag.py --run: the per-node records write is not atomic, in the change made for kill-safety
- **PR #862**: fix: dag --run persists records through an atomic replace
- **Issue #836** (closed): sensitivity controls: nothing cross-checks `make controls` against sens.INJECTIONS (the helper already exists)
- **PR #855**: test: cross-check make controls against sens.INJECTIONS, paired with the --expect token
- **Issue #834** (closed): characterize_synth_top: the per-corner Fmax tolerance omits min-period rounding, and its boundary test's pass arm is a no-op
- **PR #872**: tools: per-corner Fmax budget absorbs the min-period rounding term
- **Issue #850** (closed): File upstream (rjwalters/loom): check-verified-corrections-preserved.sh matches only '## ', so an h3 Verified-corrections section passes silently at exit 0
- **PR #875**: docs: index rjwalters/loom#13938 (h3 blind spot in check-verified-corrections-preserved.sh) in the Loom pass table
- **Issue #843** (closed): estimator_defects.py: five unguarded `.value` arithmetic sites remain after #805, outside #792's section 6
- **PR #871**: fix: rule estimator_defects.py's five remaining unguarded .value reads per site
- **Issue #797** (closed): spec/NUMERIC-CONTRACT.md item 29 says the chip still carries drum_section_placeholder and that nothing verifies the joined chip
- **PR #846**: docs: numeric contract states the shipped drum section and exact-sum master mix
- **Issue #848** (closed): File upstream: ORFS metrics JSON serialises every float metric at 6 significant figures (hard-coded setprecision(6))
- **PR #861**: docs: ledger the OpenROAD 6-sf metrics serialisation defect (OpenROAD#12307)
- **Issue #774** (closed): bug: dominant_period picks a different octave at 44.1 kHz and 48 kHz on some clips
- **PR #781**: fix: resolve dominant_period's octave pick with a swept margin
- **Issue #804** (closed): Collapse the three exit statuses that mean REFUSED: run_all.py reports the cymbal low-tail refusal as FAIL(1)
- **PR #825**: runner: report every declared refusal status as NO-VERDICT, not FAIL
- **Issue #789** (closed): docs/milestones.md milestone 3 and the README's T1 sentence contradict the tree
- **PR #798**: docs: repair milestone 3 and the README T1 sentence, and put both under check_doc_claims
- **Issue #819** (closed): run_klt.py container-ownership test matches a path prefix without a separator boundary
- **PR #860**: fix: match prefix tests at a path-component boundary, not a string boundary
- **PR #857**: pnr: add classify_route_drc.py, the #2089-aware DRC classifier #808's route record needs
- **Issue #792** (closed): Corner probes: date the pre-#150 columns, fix `corner_shipped`'s stale docstring, guard the refusal print
- **PR #840**: probe: date the corner probes' pre-#150 columns, fix corner_shipped's docstring, guard the refusal print
- **Issue #805** (closed): The BASELINE of a perturbation comparison is read unguarded while the comparison itself is guarded (2 probe sites)
- **PR #839**: fix: assert the baseline precondition of every perturbation comparison before reading it
- **Issue #793** (closed): bug: tools/sensitivity.py raises ZeroDivisionError on an objective that reaches zero
- **PR #822**: fix: sensitivity gate refuses, never divides, across or below a zero objective
- **Issue #823** (closed): compile_dag.py --run writes docs/dag-results.json only after the whole loop, so one crashed verifier loses every node's records
- **PR #833**: DAG --run: guard the evidence loop and persist records per node
- **Issue #828** (closed): Dedupe BS.1770 K-weighted loudness: three copies of kweight/loudness in tools/
- **PR #837**: [tools] one BS.1770 K-weighting implementation: the listening packs import perceptual_gate

### 2026-11-15

- **Issue #824** (closed): stale_against() reads an unresolvable sha as 'nothing changed', so a record whose commit left the history renders GREEN instead of STALE
- **PR #844**: fix: refuse DAG evidence whose staleness cannot be measured, never render it current
- **Issue #803** (closed): A probe's identical flag has #785's absent-vs-null hole, load-bearing: balance_input_invariance reports identical on a dropped key
- **PR #831**: fix: compare balance_input_invariance images with fpga/record_compare
- **Issue #775** (closed): bug: RATE_TOL_REL is 8x larger than everything jit.cyclecorr measures
- **PR #783**: fix: narrow RATE_TOL_REL to 0.02, and record why no margin test can exist
- **PR #832**: tools: characterize synth_top from the committed route evidence
- **Issue #790** (closed): T1 item 8: build one characterization report from the committed synth_top route evidence and record the klt signoff result
- **PR #796**: fix: normalise repeat_period per overlapped window and resolve its multiple
- **Issue #780** (closed): bug: repeat_period returns four times the true repeat period on a signal that repeats exactly
- **Issue #768** (closed): measure: how many discrimination rows fall under the new jit.dphase_ar1 floor (REFUSED in #763 -- no corpus on that host)
- **PR #786**: docs: roadmap as of 2026-11-13
- **PR #799**: retire DAG nodes M5 and D4, and forbid the issue-only shape returning
- **Issue #788** (closed): DAG nodes M5 and D4 read TODO against closed issues: give each evidence or retire it
- **PR #807**: fix: assert degenerate_refused from the trace instead of by elimination
- **PR #809**: fix(pnr): retire the three klt netlist patches, and make the record attributable to an invocation
- **Issue #765** (closed): Retire the three netlist patches in pnr/klt/ladder_dp/run-klt.sh: the klt bugs they work around were fixed upstream in September
- **PR #813**: test: ground-truth corner_dc_referenced against filtered_noise, with a -6 dB control
- **Issue #794** (closed): bug: make verify is red on main -- estimator_ground_truth reports corner_dc_referenced undeclared
- **Issue #540** (closed): CI: verify the #709 cancel/duplicate-run fixes live and re-measure main job-minutes (steps 3-4 deferred)
- **PR #821**: verify_109_claims counts its verdict, refuses when the reference half is required
- **Issue #814** (closed): verify_109_claims' real-reference half is binding on no host and FAILs on a partial corpus: the cymbal-369 row-7 defect, unsearched
- **Issue #801** (closed): Remove the duplicated period grid in measure_promoted_bands.py: the unguarded copy reports 0.034 % when 9 of 12 known cases refuse
- **PR #806**: fix: refuse the period worst-case figure when any of its twelve known cases refuses

### 2026-11-14

- **Issue #772** (closed): Remove the stale pre-#109 scorecard estimator copies and the unrun apparatus command from tools/measure_partial_balance.py
- **PR #773**: feat: count dphase_ar1 floor refusals per side, and plumb the floor-off arm
- **PR #779**: refactor: drop the retired fixed-window estimators from the balance diagnostic
- **Issue #785** (closed): Consolidate 4 copies of the release staleness comparison: two generations already disagree on a record that ships with nulls
- **PR #802**: fix: one release staleness comparison, with the absent-vs-null hole closed

### 2026-11-13

- **Issue #763** (closed): bug: test_extra_features_are_rate_independent fails on main -- jit.dphase_ar1.seg0 is rate-dependent
- **PR #769**: fix: refuse jit.dphase_ar1 below a physical floor instead of a 1e-20 epsilon
- **Issue #759** (closed): Consolidate the duplicated injected-defect ladder and k-ROM builder in model/ into one copy
- **PR #764**: refactor: keep one copy of the injected-defect ladder and the k-ROM builder
- **Issue #755** (closed): Wire or remove tools/bd_pitch_predeclaration_mutants.py: unrun mutation control now REFUSES
- **PR #757**: run the BD pitch mutation controls: retarget the bool mutant, wire make controls
- **Issue #756** (closed): tools/probes/check_doc_claims_scope_control.py: a self-described control that no target runs
- **PR #758**: docs: record the claim-checker scope control as a one-time #435 record
- **Issue #767** (closed): bug: jit.cycledshape and mpd5.00ms.rowcorr are rate-dependent on the extended stimulus panel
- **PR #776**: fix: fold at the exact period so jit.cycledshape and mpd*.rowcorr stop reading the sample rate

### 2026-11-12

- **PR #754**: Bump Repo Skills 0.25.10 -> 0.26.1

### 2026-11-10

- **Issue #572** (closed): Auditor guard telemetry: sql delete no where false positive on Python del

### 2026-11-07

- **PR #752**: Bump Repo Skills 0.25.1 -> 0.25.10

### 2026-11-06

- **Issue #749** (closed): Dedupe BS.1770 K-weighted loudness: 3 hand-copied implementations, already drifted
- **Issue #570** (closed): Auditor guard telemetry: stash scope create redirect false positives
- **Issue #571** (closed): Auditor guard telemetry: worktree write confinement blocks sweep checkpoint
- **Issue #732** (closed): Consolidate duplicated compare_files/main scaffolding across three candidate comparators
- **Issue #547** (closed): reference_rigs._k_rom_for caches on id(g_rom): a freed candidate ROM can hand its k table to the next
- **PR #751**: fix(model): key substituted-ROM k cache on content, not id()

### 2026-11-01

- **PR #746**: Bump Repo Skills 0.24.7 -> 0.25.1
- **Issue #747** (closed): tools: test_rho_is_invariant_to_record_gain fails on main (rho value is None)
- **PR #748**: tools: compare refused rho depths by None-ness in the gain-invariance test

### 2026-10-30

- **Issue #745** (closed): Remove audition/physical.py: unreferenced float modal sketch

### 2026-10-29

- **Issue #742** (closed): CI regrade fails on main: committed item-10 attestation hashes a README.md the DAG bot regenerates
- **PR #743**: signoff: stop attesting README.md, which dag.yml rewrites

### 2026-10-27

- **PR #740**: ci: attest T1 item 9 (testbenches shipped) with klt signoff
- **PR #739**: ci: attest T1 item 10 with klt signoff and re-grade the committed verdict
- **Issue #738** (closed): T1 item 9 (testbenches shipped): inventory of the RTL-vs-model benches and klt signoff --attest 9
- **Issue #737** (closed): T1 item 10 (repo hygiene): klt signoff block manifest, committed verdict with CI re-grade, and --attest 10
- **Issue #744** (closed): main: item-10 attestation drifted after README regeneration (regrade red on every PR)

### 2026-10-24

- **Issue #560** (closed): [gate] a between-recording bar for #379 needs data the MARS pack cannot supply (rule v2 refused: no p satisfies seeds and stays-green together)
- **PR #535**: Single corpus-path resolver: configured_refs() reaches every reader (#522)
- **Issue #522** (closed): Four corpus-path resolvers disagree, so $GF180_TR808_REFS does not reach every reader

### 2026-10-21

- **PR #733**: Bump Repo Skills 0.24.3 -> 0.24.7

### 2026-10-20

- **PR #652**: [sound] #592 phase-aware tom pitch-drop measurement vs Fischer: REFUSED (not identifiable), no repair
- **Issue #592** (closed): [sound] toms: diode-drop magnitude and time constant against the Fischer takes (LT onset 74 vs 35 cents)
- **PR #709**: ci: stop cancelling started main runs and double-running PR commits
- **PR #727**: docs(gate-379): source-qualification report for a between-recording bar
- **Issue #726** (closed): Qualify up to three independent 808 reference-data sources for #560

### 2026-10-17

- **PR #722**: fix: run_case refuses a bool or numeric string as a measurement
- **Issue #720** (closed): Follow-on from PR #719: run_case._finite still accepts bool and numeric strings
- **PR #719**: fix: share one finite-number guard so a bool EDT10 is REFUSED
- **Issue #718** (closed): Consolidate divergent _finite() guards; cymbal_tone_render's accepts bool

### 2026-10-16

- **PR #634**: performance_suite: first model-backend run of the 7 phrases (#338)
- **Issue #673** (closed): zero_crossing_frequency moves 0.78% under polarity reversal on an asymmetric waveform (upward crossings only)
- **PR #711**: Bump Repo Skills 0.24.1 -> 0.24.3
- **PR #714**: tools: add two-SHA driver for prepare-corpus before/after probes
- **PR #690**: fix: route conga and partial-balance probes through run_case.prepare and the preparation contract
- **Issue #636** (closed): Two measurement tools carry private prepare() copies that still have the pre-#132 lead clamp (#101)

### 2026-10-15

- **PR #708**: fix: make zero_crossing_frequency polarity-symmetric and regenerate res-tuning artifacts
- **PR #704**: fix: route free-ring pitch readers through ring_estimator
- **PR #701**: ci: gate fpga publish-binding and Arty SD-top tests in verify-fast
- **PR #699**: ci: run the named R1/R2 image selection tests in verify-fast
- **PR #698**: feat: detect the two noise controls on a swept mean-residual statistic
- **PR #697**: fix: glide_cents refuses where the zero-glide known answer exceeds the floor
- **PR #696**: fix: _ring_ratio refuses non-sustained and ambiguous rings via a shared estimator
- **PR #695**: gate: match the candidate's rate path to the target's for impulse
- **PR #694**: fix: REFUSE the simulated MIDI session on an overloaded host instead of FAIL
- **PR #688**: fix(residual_dc): re-derive --verdict row labels from detail under the frozen limits
- **PR #685**: docs: record zero_crossing_frequency polarity sensitivity and pin its bound
- **Issue #684** (closed): Make zero_crossing_frequency polarity-symmetric and regenerate the res-tuning artifacts
- **Issue #703** (closed): Follow-on from PR #696: audit other ring-pitch readers still using bare am.dominant_frequency
- **Issue #692** (closed): CI-reachability gate for the remaining unreached fpga/test_*.py (split from #482)
- **Issue #654** (closed): residual_dc --verdict trusts each record's label instead of re-deriving it from detail under the frozen limits
- **Issue #602** (closed): pitch_trajectory.glide_cents answers tens of cents wrong just above its refusal threshold (and with f0 far from F_REF)
- **Issue #584** (closed): midi_session --port sim clean path reports ERR_DUE under host load (wall-clock flake, pre-existing)
- **Issue #548** (closed): ladder_headroom._ring_ratio reports a pitch for a below-onset dead-band limit cycle, and falls back silently on estimator disagreement
- **Issue #539** (closed): [probe] permitted-differences noise controls are caught in ~60% of independent seed groups; their green on VALIDATE_BASE is one draw
- **Issue #482** (closed): CI does not run fpga/test_bringup_arty.py or several other fpga/test_*.py files
- **PR #691**: fix: audit broad except handlers; dirty on unanswered git, exit red on descent errors
- **PR #689**: docs: exempt calibration-only p dial from sensitivity registry, with trigger
- **PR #687**: fix: read response_row and sound_report corners against the DC plateau
- **PR #686**: fix: refuse image_identity when published sets differ from declared populations
- **PR #683**: fix: make drum read_wav and dominant_period padding-invariant
- **PR #681**: ci: check_workflows asserts repo paths named by workflows and Makefiles exist
- **PR #680**: release: bind the R2 candidate to R2_FROZEN_AT instead of the working tree
- **Issue #682** (closed): gate-379: register or explicitly exempt the p calibration dial from the sensitivity registry
- **Issue #658** (closed): Release gates: bind R2's candidate to R2_FROZEN_AT and give the post-R2 tree its own image/verification directory (blocks #657)
- **Issue #629** (closed): check_workflows.py: assert paths named in workflows and Makefiles exist (standing guard for the #622 class)
- **Issue #617** (closed): Audit drum-facing whole-clip-mean / zero-phase conditioning for trailing-silence sensitivity (drum_verify, discrimination_features)
- **Issue #615** (closed): Audit the 57 remaining broad except handlers in tools/ and model/ (follow-up to #610)
- **Issue #582** (closed): release_manifest.image_identity: self-described membership sets can be emptied (vacuous loop, same shape as #283)
- **Issue #546** (closed): Qualify corner convention in reference_compare.response_row and sound_report._corner (no ref_db)
- **PR #504**: Reachable attack-context re-bind path for Builders (#502)
- **PR #677**: Bump Repo Skills 0.19.5 -> 0.24.1
- **PR #675**: audio_distance_floor: thread sample rate into mel_dac (#533)
- **PR #674**: spi_host: report BD attack-window width change in check() (#506)
- **Issue #502** (closed): A Builder cannot re-bind the attack-context evidence: workflow_dispatch is 403 for the agent token
- **Issue #538** (closed): Correct remaining permitted-differences detection counts after #537
- **Issue #506** (closed): spi_host: feasible() can lengthen the BD 4 ms attack window under dense hits; check() does not report it

### 2026-10-14

- **PR #671**: uart_host: blame host latency, not the device clock, at the rolling anchor unwrap (#459)
- **PR #672**: docs: DAC control pins are breakout-specific and a precondition of the audio path
- **PR #670**: verify_live_midi: refuse unmeasurable frame unwrap (#479)
- **PR #669**: external_claim: REFUSE on malformed second_route instead of crashing (#475)
- **PR #668**: Remove model/fixed_sweep.py (flat metric, unreferenced, superseded)
- **PR #667**: deadline: bind the -l2 capture to the schedule-relevant RTL (#452)
- **Issue #459** (closed): Rolling playback blames the device's clock for host latency: 'frame counter did not advance' while it ran at 48 kHz
- **Issue #460** (closed): ARTY.md's 'leave MU/FM unconnected' is Adafruit-specific and silently mutes #408's purple PCM5102
- **Issue #479** (closed): verify_live_midi._unwrap carries the same unchecked 0x8000 gap assumption #474 fixed
- **Issue #475** (closed): tools/external_claim.py crashes instead of REFUSING on a malformed second_route
- **Issue #486** (closed): check_doc_claims exits 1 on main: docs/scorecard/cymbal-369/vca-drive/vca-drive.json was committed from a dirty tree
- **Issue #663** (closed): Remove model/fixed_sweep.py: flat metric, unreferenced, superseded by ladder-rung1-audit
- **Issue #463** (closed): CI: decide whether PR branches should run each suite once (push + pull_request double run)
- **Issue #452** (closed): Deadline evidence: the -l2 capture is bound to its stimulus but not to the RTL that ran it

### 2026-10-13

- **PR #666**: Correct rungs.yml comments on non-strict compile_dag --check
- **PR #665**: fpga/ARTY.md: document --box local for on-box DSP extraction
- **PR #664**: uart_host: --hold-frames over the wrap horizon is REFUSED, not a traceback
- **PR #650**: feat(#400): production-tail diagnostic for the CY 1-2.5 kHz question (REFUSED/NO VERDICT)
- **PR #561**: gate-379: between-recording bar rule v2 (REFUSED, isolated), current-main ranking, controls
- **PR #633**: Replace unsatisfiable nightly reference-freshness check (#622)
- **PR #594**: fpga: refuse 'routed' from an incomplete nextpnr log (#565)
- **PR #624**: Nightly controls: replace blind I2S_SWAP with discriminating serializer bench
- **PR #649**: [593] tom distortion: qualification instrument, controls and preregistration
- **Issue #438** (closed): Correct rungs.yml comment about non-strict compile_dag --check behavior
- **Issue #367** (closed): fpga/ARTY.md: document --box local for the on-box DSP extraction invocation
- **Issue #458** (closed): uart_host --hold-frames raises a raw ValueError instead of REFUSED, and does so intermittently
- **Issue #379** (closed): 808 kit: automated perceptual gate calibrated against the 808's own variability — prove it, rank all 16 sounds, fix worst first
- **Issue #400** (closed): Cymbal tail: qualify and localize the remaining production-path energy/decay discrepancy
- **Issue #622** (closed): Nightly reference-freshness gate is unsatisfiable: tests a docs path that has never existed
- **Issue #565** (closed): Refuse to report 'routed' from an incomplete nextpnr log (fpga/scripts/report.sh + fpga/Makefile)
- **Issue #619** (closed): Nightly controls requires I2S_SWAP detection despite declared blind timing

### 2026-10-12

Closure events below record forge state, not a verified fix or a new sound result.
- **PR #655**: Cymbal knob-law render: kit_at delegates to the shipped constructor (#413)
- **Issue #618** (closed): Output-stage floor quantisation: does `>> 15` leave a standing sub-LSB bias and explain H_CH2's bound overshoot?
- **PR #653**: probe: measure output-stage floor quantisation against an exact round-half-even oracle

### 2026-10-11

Closure events below record forge state, not a verified fix or a new sound result.
- **Issue #403** (closed): PR #195: regenerate report.json — stale source_commit has blocked CI across 5 fallback-review passes and 11+ rebases
- **Issue #354** (closed): verify_voice --set full fails on R1's published voice_dp.v (osc2x+filter2x): an audition reference sequence and extremes
- **PR #646**: #354: gate the all-max image and pin R1's ladder-width control to its signature; current-tree closure receipts
- **Issue #361** (closed): descent_test's resample-ratio sweep never tests ratio 1.0 exactly (drops one sample at 'x1.0000')
- **PR #645**: fix(harness): descent_test identity candidate is exactly ratio 1.0 (#361)

### 2026-10-10

Closure events below record forge state, not a verified fix or a new sound result.
- **Issue #347** (closed): M5 pulse preset upper-partial deficit (4.9–5.3 dB vs Mini V3): cutoff/voicing question
- **Issue #339** (closed): Late-event policy P3 vs RTL: a late event with an earlier sent due than a draining tail is dropped (decide policy or next-image RTL)
- **PR #639**: [#324] R1 capture inputs: R1-bound references and commands; T-PHYSICAL capture-r1 refuses R0 material
- **Issue #163** (closed): Root cause: symmetry of code is not symmetry of treatment — three comparisons where both sides called the same function and were handled differently
- **PR #638**: Preparation contract at the drum pair site: refuse unlike-prepared sides (#163, first slice)

### 2026-10-09

Closure events below record forge state, not a verified fix or a new sound result.
- **Issue #631** (closed): ci: replace retired "(D5)" Blacksmith citations in workflow comments (needs workflow scope; 2am#3914)
- **PR #632**: ci: cite the Blacksmith decision by section, not the retired "(D5)"
- **Issue #609** (closed): Test and inject-control tools/r1_harvest.py: the R1 receipt_valid verdict has no test
- **PR #628**: test: cover r1_harvest receipt_valid verdict with injected controls
- **Issue #620** (closed): Vacuous-pass guard audit: standing check for verdicts that pass on an empty population
- **PR #627**: tools: vacuous_guard_audit, a standing check for verdicts that pass on an empty population
- **Issue #623** (closed): Nightly sound-report red: CP (clap) decay tau, T20 and attack outside tolerance, two of them locks
- **Issue #610** (closed): Follow-on from PR #604: preserve measurement errors and corrupt RTL anchors
- **PR #613**: Preserve corrupt anchors and measurement error types (#610)
- **PR #614**: [#152] Residual drum DC: offset-vs-skirt apparatus + dev-condition run (partial; confirm batch pending, #152 stays open)
- **Issue #608** (closed): File the Icarus Verilog generate-case-over-string-parameter bug upstream (the -g2005 workaround is unfiled)
- **PR #612**: [#608] Icarus generate-case string-param upstream report + guard
- **Issue #611** (closed): Follow-on from PR #607: qualify lead-drive refusal controls and preserve experiment limits
- **PR #616**: [#611] Lead-drive refusal controls (baseline mismatch, unchanged audio), NaN fix, README correction
- **PR #606**: Full-performance suite with seeded-defect controls (#338)
- **PR #607**: [#337] Lead ladder drive: saw 0.50 confirmed on untouched note (still failing), pulse no candidate
- **Issue #600** (closed): Measurement catches that turn bugs into 'estimator refused' (sound_report.m_noise_share, moog_probe.scan, 11 more)
- **PR #604**: Measurement catches no longer turn bugs into 'estimator refused' (#600)
- **PR #601**: [#557] BD pitch envelope: pre-tuning freeze (conditions, metric, min-improvement rule, preservation limits)
- **Issue #568** (closed): synth_count.sh prints blank cell counts and exits 0 when yosys fails
- **PR #596**: [#568] synth_count: refuse instead of printing blank cell counts
- **Issue #564** (closed): Collect the 57 in-file tests that pytest never runs (excitation_energy, dc_blocker, discrimination_*)
- **PR #590**: Collect the 57 in-file tests pytest never ran, with a collection-inventory guard (#564)
- **PR #597**: [#556] MA/RS stage-attribution instrument (measurement only, no repair)
- **PR #589**: #558 toms/congas vs the gate: negative under the frozen rule; post-hoc, a BP numerator cuts conga pitch_shape (waiting on #350); impulse moves with the gate's rate path (#588); no sound change
- **PR #587**: [#557] BD pitch-trajectory estimator, qualification and baseline script (instrument only)
- **PR #586**: Envelope response: prior-note attack shortening is not residual envelope level (#337)

### 2026-10-08

Closure events below record forge state, not a verified fix or a new sound result.
- **PR #583**: M1A phase-aware drive question; drive 0.25 confirmed at matched phase but not promoted; partial #337.
- **PR #581**: Bind trial-receipt membership to the recorded registry and refuse empty required membership.
- **PR #580**: Record revision 9 registers and the 4-bit wave code in the numeric contract.
- **PR #579**: Refuse real MIDI sessions when an internal injection set is non-empty.
- **Issue #283** (closed): Trial receipt accepted deleted or empty required-child membership; PR #581.
- **Issue #321** (closed): Revision 9 registers and wave-code width were missing from the numeric contract; PR #580.
- **Issue #288** (closed): Real MIDI sessions did not refuse an active internal injection set; PR #579.
- **Issue #426** (closed): Deadline-evidence stimulus binding; closure records forge state, not a fresh timing verdict.
- **Issue #573** (closed): Duplicate checkpoint-write guard telemetry; canonical finding #571 remains open.
- **Issue #575** (closed): Duplicate Python-deletion guard telemetry; canonical finding #572 remains open.
- **Issue #576** (closed): Duplicate literal-body-path guard telemetry; canonical finding #574 remains open.

### 2026-10-07

- **PR #554**: Shared-bus DC coupling model and contract (#551); partial #510, RTL and I2S qualification remain open.
- **PR #550**: Model-side glide boundary check; partial #247, RTL reproduction remains pending.
- **PR #549**: Model-side resonance-keyed cutoff correction; partial #257, RTL PR #555 remains open.
- **PR #545**: Reconcile filter-rolloff claims after the corner repair.
- **PR #544**: MARS calibration instrument; calibration REFUSED, existing diagnostic ranking retained; partial #379.
- **PR #543**: BD pedestal instrument and mechanism investigation; partial #220.
- **PR #542**: Pekonen-style coloration measured and declined.
- **PR #541**: Refuse unreachable attack-context source commits.
- **Issue #551** (closed): Model and numeric-contract increment of cymbal DC coupling.
- **Issue #333** (closed): Sound batch 1, unwanted-artifact repair tracking.
- **Issue #314** (closed): Stale D02A trial-release evidence documentation.
- **Issue #243** (closed): Pekonen-style oscillator coloration candidate; PR #542 records the decision to decline it.
- **Issue #215** (closed): Unreachable attack-context source-commit refusal, PR #541.
- **Issue #169** (closed): Filter-rolloff claim reconciliation, PR #545.

### 2026-10-06

- **Issue #207** (closed): Continued sound-qualification lane tracking.
- **Issue #206** (closed): Evidence-gated DSP feedback publication path.

### 2026-10-05

- **PR #537**: Restore noise slope control and record margins and fixture comparison.
- **PR #536**: Positive grammar for corpus serial provenance.
- **Issue #528** (closed): Noise slope control margin investigation.
- **Issue #527** (closed): Serial grammar accepting an unknown serial.
- **Issue #382** (closed): Cymbal Hh2 and LEVEL-corner investigation tracking.

### 2026-10-03

- **PR #532**: Reference-audio S3 route and BD take-to-take repeatability.
- **Issue #111** (closed): Machine-spread measurement, PR #532.

### 2026-10-02

- **PR #531**: Remove no-op npm scripts.
- **PR #529**: Synthetic estimator ground-truth suite and controls; partial #158.
- **PR #526**: Permitted-differences regression suite; partial #158.
- **PR #524**: Real-recording perturbation ladder.
- **PR #523**: Reference corpus lineage and qualification.
- **PR #511**: DC-blocker probe and preservation-apparatus corrections.
- **Issue #525** (closed): No-op npm scripts.
- **Issue #520** (closed): Corpus lineage documentation and qualification.
- **Issue #519** (closed): Permitted-differences regression suite.
- **Issue #518** (closed): Real-recording perturbation ladder.
- **Issue #517** (closed): Synthetic estimator ground-truth suite.
- **Issue #165** (closed): DC-blocker prototype; the joint CY/RS candidate did not trigger shipping.
- **Issue #45** (closed): Mechanical process-fix tracking.

### 2026-10-01

- **PR #509**: [dag] propagate RED/BLOCKED dependency status in classify (#140)
- **PR #508**: Read the promoted metrics' floor from the harness that measures floors (#138)
- **PR #507**: Promote cqt.0-200Hz and jit.period_ms to refusing estimators; floor unmeasurable (#138)
- **PR #505**: fpga/spi_host.py: fix hits() anchoring claim, pin 15.7.1 sequence-step movement (#496)
- **PR #503**: Wire causality battery into _Plugin.qualify for Surge and Mini V3 (#137)
- **PR #499**: Add mechanism=<status> claim modifier: effect and mechanism are separate claims
- **PR #501**: NaN fails closed at every audio-entry boundary (#134)
- **PR #500**: Qualification is a verdict per (rig, host, capability), not one boolean per rig
- **PR #495**: spec: the model's write burst is not a wire schedule — 15.7.1 gains the anchor policy
- **PR #516**: Edge-leak cause and normalization decision.
- **PR #514**: Low-band onset metric and edge-straddle precondition; partial #138.
- **PR #513**: Claim-type checks and close-time search policy.
- **Issue #515** (closed): Structural edge-leak refusal.
- **Issue #496** (closed): Unanchored SPI coefficient sequences.
- **Issue #455** (closed): Missing repo remote skill installation.
- **Issue #155** (closed): Claim-type checks and close-time search policy, PR #513.
- **Issue #143** (closed): Knob-equivalent comparison-scale tracking.
- **Issue #140** (closed): Evidence-file existence used as a DAG verdict.
- **Issue #137** (closed): Reference-driver command-effect verification.
- **Issue #136** (closed): Reference qualification by capability.
- **Issue #135** (closed): Unverified mechanism used as a decision premise.
- **Issue #134** (closed): NaN defeating comparison guards.
- **Issue #131** (closed): Physically deliverable write-sequence contract.

### 2026-09-30

- **PR #494**: docs(CLAUDE): point at the shared build-box runbook; list the .env keys
- **Issue #129** (closed): Prose verdicts live inside a hashed input, so a comment cannot be corrected without invalidating measurements
- **PR #493**: Split refprofile/profile.json's prose out of the hashed evidence
- **Issue #224** (closed): Sensitivity-sweep gate before optimisation work: define and demonstrate it (#45 item 5)
- **PR #229**: feat: gate optimisation work on a committed sensitivity sweep
- **Issue #404** (closed): The cymbal-369 gates and their must-fail controls are not run by any CI job
- **PR #427**: CI collects tools/ as a directory, and runs the cymbal gates as their own steps
- **PR #355**: R2: pulse2x (operator override), skip2xwin, #354 ladder repair, #315 -- built, bound, --image r2
- **Issue #127** (closed): The f0 tolerance cannot distinguish any two settings of the bass drum
- **PR #492**: f0 tolerance: score the bass drum against its own travel, not against 10 % of a unit-to-unit spread
- **Issue #242** (closed): sound_report.py: two locked properties (LADDER corner ratio drift, LT attack) have drifted past tolerance, unnoticed by a broken nightly gate
- **PR #269**: sound_report: two locks re-locked to the commits that moved them, and the nightly gate that should have caught them
- **Issue #484** (closed): Remove tools/compile_dag.py:mtime_commit: dead since introduction, superseded by stale_against
- **PR #490**: tools: drop compile_dag's mtime_commit, dead since the file was written
- **Issue #481** (closed): refprofile.load_clip refuses silence at 1e-9 but nothing asserts a frozen clip's LEVEL
- **PR #491**: refprofile: gate a frozen clip on its LEVEL, not on silence (#481)
- **Issue #125** (closed): Mine the prior plugin-corpus work in ~/dev before rebuilding what it already solved
- **PR #487**: pedalboard is installable on Linux; a stub bundle must REFUSE, not traceback (#124)
- **PR #489**: ci: move off Blacksmith to GitHub-hosted runners (operator decision 2026-09-30, D5)
- **Issue #432** (closed): Cymbal: the three envelope generators' peak collector voltages (Q16/Q17/Q18), read off SN p.13
- **PR #488**: Cymbal: the three envelope generators' peak collector voltages (Q16/Q17/Q18), read off SN p.13
- **Issue #483** (closed): Remove tools/trials_pilot_demo.py: superseded one-shot pilot demo, zero references
- **PR #485**: chore: remove superseded zero-reference trials pilot demo script
- **PR #480**: A pedalboard-hosted Model D rig, with the dawdreamer rig's qualification discipline and #137's causality checks
- **Issue #449** (closed): Button drum demo: Arty BTN0-3 trigger four 808 voices, no laptop
- **PR #454**: Button drum demo: Arty BTN0-3 trigger four 808 voices, no laptop (#449)
- **PR #433**: cymbal #369 step 11: the swing VCAs read off SN p.13 — there are not three drives, and the one term there cannot close the balance
- **PR #195**: Measure selected-model attack in frozen note-history contexts
- **PR #395**: ci: fork-gate every Blacksmith runner label — fork PRs to GitHub-hosted
- **PR #467**: Bring-up that asserts its preconditions, and the gate lesson from getting it wrong
- **PR #468**: Update Repo Skills to v0.16.1

### 2026-09-29

- **Issue #472** (closed): verify_sd_dac.py's silence case has no injected control
- **PR #477**: fpga: give verify_sd_dac's silence case an injected-bug control
- **Issue #474** (closed): verify_rolling_playback._unwrap assumes consecutive writes are < 0x8000 frames apart without checking
- **PR #478**: assert the frame-log unwrap's precondition instead of assuming it
- **Issue #471** (closed): effective_pins only reads single-line PACKAGE_PIN, would miss -dict form
- **PR #476**: fpga: refuse an unparseable PACKAGE_PIN line in effective_pins
- **Issue #123** (closed): Root cause: every external-tool finding we made was a property of a configuration, recorded as a property of the tool
- **PR #470**: Require environment tuple and second route for external-tool claims
- **Issue #466** (closed): play_song tests: frame unwrap breaks on long rests; note-offs checked only against the code under test
- **PR #469**: test(play_song): long-rest fixture, safe unwrap, independent note-off check
- **Issue #465** (closed): test_constant_port_evidence_matches_vivados_own_warning tests its own regex, not the build gate's
- **PR #473**: fpga: share the constant-port-evidence regex between gate and test
- **PR #409**: play_song.py: play a tune over the USB-UART link
- **Issue #406** (closed): Sigma-delta audio out on a Pmod: hear the Arty build without the PCM5102 breakout
- **PR #408**: Sigma-delta no-DAC demo output on Pmod JD (#406)
- **Issue #115** (closed): Estimators should declare their validated domain and refuse outside it
- **PR #461**: Estimators declare their validated domain and refuse outside it (#115)
- **Issue #457** (closed): CI: skip suites a PR's changes cannot affect (file filters, minimum) and stop double-running push + pull_request
- **PR #464**: CI: skip suites a change cannot affect, fail-safe, and still report (#457)
- **Issue #116** (closed): Seal the scorecard's holdout cases before they're read (F1D/F2D/F3D/F5D) — items 1-2 of this issue already landed via #141
- **PR #462**: [#116] seal the scorecard's holdout cases before they are read (F1D sealed; F2D/F3D/F5D re-justified)
- **PR #456**: Install rjwalters/repo skills (repo-remote.sh et al.)

