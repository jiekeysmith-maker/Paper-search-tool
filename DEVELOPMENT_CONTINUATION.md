# Development continuation

Workspace: `D:\_Knowledge Distillation\worktrees\kd_manual_clean_20261004`
Branch: `manual-multivenue-next`. Starting checkpoint: `c40a76f`.
Read current Git HEAD/status/diff before resuming; do not reset WIP.

## 2026-10-06 current stage: ECCV

- Restored interrupted identity tests (previous file had invalid decorator syntax).
- ECCV-only non-greedy title/author reconciliation and abstract/DOI checks integrated into audit.
- HTML double escaping, accents, initials, author order are handled. Full-name aliases are not guessed.
- Explicit ECVA paper DOI links now provide independently traceable publication identity; large retitles without such evidence remain candidates.
- Official four-page probe found direct matching Springer links for ShapeFusion, UAV and Avatar.
- Zero-shot ECVA page links to `10.1007/978-3-031-72890-7_21`; direct publisher request returned HTTP 404. Do not infer withdrawal or exclude it. Keep ECVA-only unresolved. Evidence: `output/production_acceptance/ECCV/identity_probe_20261006/reports/`.
- Offline ECCV2024 full replay is running in attempt `eccv_identity_20261006` (started before explicit DOI enrichment patch). Check PID/lock and output; do not start a duplicate. Latest own PID was 16092; verify liveness, never assume.
- Small offline groups passed: 77 tests (ECCV + AAAI); 73 tests (identity/gate, pipeline, manual screening, ICLR); latest 72 tests (identity DOI, ECCV recovery, evidence, pipeline, acceptance reuse, AAAI). Groups overlap; do not sum as unique total.
- Frozen V1.2 SHA256 confirmed `4a94a8346cce3ab8dae5153ba9c07041371f783902d0bdb55becbc0d9c0cf514`.
- No production checkout or Paper Library writes; no push or merge.

## ECCV stage completed; TPAMI next (2026-10-06)

ECCV code checkpoints: `7c63628`, `57934d9`. Full offline enumeration/detail acceptance completed in `eccv_identity_20261006`: 89 volumes, 178 TOC pages, 2388 raw chapters, 2386 target metadata records, two proven corrections, zero network requests. It ran the initial identity module already loaded in that process and produced 293 unresolved rows.

Latest identity rules, including numeric/math safeguards and four cached explicit DOI proofs, were then applied to those completed outputs in `eccv_identity_proofs_20261006`, preserving source-file SHA256 provenance. Result: REVIEW_REQUIRED, 301 unresolved audit rows; publisher/metadata 2386, independent 2387, title/abstract present 2386, missing/invalid abstracts 0, duplicate identities 0. Formal screening was explicitly tested and correctly refused. The new unresolved total is not comparable to a missing-paper count: publisher candidates and unconfirmed independent identities remain separately visible. Full-name conflicts/insufficient publication relations require further official evidence; no guessed author aliases or deletions.

Latest targeted group: 49 passed (identity and recovery), including missing abstract preserving other chapters. `git diff --check` passed. ECCV engineering reconciliation/gate stage is complete with REVIEW_REQUIRED source data; a strict production corpus is NOT VERIFIED. No active ECCV replay remains.

## Next exact actions

1. Investigate TPAMI prototype and public source structures. Remove hardcoded 12-issue completeness assumptions. Preserve final issue year policy, Early Access identity/date separation, strict gate. Public pages only, no restricted REST or access bypass.
2. TPAMI 2024 end-to-end acceptance is required; 2025/2026 follow sequentially if sources permit. Do not claim production readiness from mocked tests.

## Native continuation

Codex heartbeat automation ID `eccv-tpami` is ACTIVE every 330 minutes, attached to this thread. It was actually created with the app tool. The native update API subsequently accepted the anchored schedule: first run 2026-10-06 10:46:03 Asia/Shanghai (T0 + 5h30m), then every 330 minutes. Stop this automation only after both ECCV and TPAMI meet the user's development completion criteria. Do not touch the unrelated paused legacy automation.

## TPAMI checkpoint after first real scheduled continuation

The automation actually fired at 2026-10-06T02:46:30Z. Git/WIP checks completed. TPAMI adapter/policy patches were present; the previous pipeline patch had failed atomically and was then completed safely.

- Added date normalization with precision preservation, first reported official date, final-issue evidence, placeholder abstract rejection, non-research official-type proof, Early Access event ledger, per-issue pagination/count evidence, partial metadata preservation, per-host access-stop/cache replay. Removed fixed twelve-issue tests from production code.
- Added static citation-tag fallback alongside existing public xplGlobal JSON parser. Mock/static contracts are not proof of actual site readiness.
- 103 targeted offline regressions passed (TPAMI, acceptance cache, AAAI, ICML/ICLR pipeline and formal screening). V1.2 SHA256 unchanged; diff check clean.
- Attempt `tpami_final_issue_20261006` replayed real HTTP snapshots: REVIEW_REQUIRED, metadata 0, unresolved 3. IMPORTANT: this still used the prototype's WRONG CSDL annual URL, not evidence of absent publications.
- Browser investigation subsequently found the real CSDL route through official navigation: `/csdl/journal/tp/past-issues/2020/2024`. `/csdl/journal/tp/2024` renders 404. The real archive rendered twelve issue links (observed, not assumed). January `/csdl/journal/tp/2024/01` renders Volume 46 Issue 1, 42 articles, `Showing 42 out of 42`, `.article-title` and `.article-authors`; article URLs carry IEEE numeric document ID plus CSDL ID.
- IEEE also renders normally in the in-app browser. Home has an All Issues link; `/xpl/issues?punumber=34&isnumber=11674301` exposes year controls and actual issue links. Raw HTTP only provides SPA shells, and one raw document request returned 202.
- Thus NOT_READY, but NOT irrecoverably source-blocked. Next: inspect IEEE 2024 issue navigation and public citation export; adapt real rendered DOM or official export ingestion (no private REST, no bypass). Correct CSDL source discovery. Preserve snapshots/evidence from normal public UI. Do not declare the mocked prototype production-ready.
- Current browser handles at this writing: CSDL tab 1 in browser 2, IEEE tab 2. Re-discover if stale. Native automation remains ACTIVE because TPAMI is unfinished.
