# ECCV code review — 2026-10-06

Workspace: `D:\_Knowledge Distillation\worktrees\kd_manual_clean_20261004`.
Branch: `manual-multivenue-next`. Code is ready for **manual acceptance**, not a claim that either annual corpus is VERIFIED. No real crawl or full pytest suite was run for this change.

## Existing WIP retained and corrected

- Keep official AcceptedPapers as independent acceptance evidence. Preserve repeated event rows and IDs. Existing reconciliation only resolves identical identity/title/author events; conflicting identities remain unresolved. Acceptance evidence always carries `final_publication_inventory=false` and cannot alone authorize formal screening.
- Keep chapter citation metadata as the final Title/Authors/Paper_ID/DOI source, retaining TOC title and original chapter enumeration. Add Part and LNCS volume to corpus rows.
- Keep read-only acceptance snapshot reuse and `has_snapshot()`. Availability is not integrity proof: `get()` still validates bytes/hash/scope. Add explicit `cache_only` to prevent misses or refresh mode from falling through to network.
- Keep successful cached chapters after volume failure. Extend stopping to HTTP 401/403/429, RetryDeferred, Springer authentication redirects and explicit access challenge titles. Further requests to the affected host become cache-only; errors remain evidence, not missing-paper assertions.

## Inventory and non-paper safeguards

- A discovered book set matching its seed URLs is insufficient completeness evidence. Require publisher volume-count corroboration or agreement with the official conference Part inventory. Inventory failures, duplicate Part/LNCS identities, untrusted links and count mismatches remain unresolved.
- Preserve all parsed volume/chapter evidence and successful TOC page URLs. Book DOI and target conference title/year must agree. Follow canonical and ISBN-alias TOC pagination. Workshop book titles are rejected by the exact conference-title contract.
- Corrections/errata need official citation identity, C-page numbering and an explicit original-chapter link. Front/back matter chapter exclusion requires publisher section/type and Roman pagination; publisher TOC-labelled non-chapter material is separately retained. Titles alone never silently remove chapters. Unknown candidates remain unresolved.
- ECVA records retain official year-scoped URLs, Title and Authors. ECVA failure does not discard publisher metadata. Missing independent evidence prevents VERIFIED.

Outputs use existing raw/audit conventions, plus `Publisher_All_Chapter_Records.csv`, `Volume_Enumeration.json` (including `TOC_Page_URLs`), `Volume_Failures.json` when applicable, `Access_Restrictions.json` when applicable, and `Accepted_Program_Evidence.json` for the fallback. Exclusions retain evidence in `Non_Target_Records.csv` and the audit. `difference_rows` is not a missing-paper count.

## Validation and limitations

35 targeted offline/mock tests passed in 2.14 seconds, covering access stops, cache-only misses/corruption, volume discovery, incomplete pagination, real correction fixtures, non-paper proof and acceptance-list gate rejection. This was a small selected subset, not the full suite or a real annual integration run. The initial sandbox could not create its test directory; the same scoped tests subsequently ran with permitted worktree access.

Static comparison against `940091b` confirms AAAI/ICML/ICLR pipeline functions unchanged. `screen_verified.py`, `venue_audit.py`, and `metadata_transport.py` are unchanged. Frozen V1.2 SHA256 remains `4a94a8346cce3ab8dae5153ba9c07041371f783902d0bdb55becbc0d9c0cf514`.

Real 2024/2026 source changes, complete volume discovery and remaining publisher-versus-independent differences still require user-run acceptance. The historical 2388/2387 discrepancy is not declared resolved by this code review. No PDF downloads, production checkout edits, Paper Library writes, main merge or push were performed.

During development HEAD advanced externally to `c4c86df` (`wip: sync current ECCV development state`). That commit is preserved; subsequent fixes are committed on top without rewriting history.
