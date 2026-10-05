# ECCV identity and metadata validation

The original 789 unresolved audit rows were not 789 missing papers. They arose mainly from strict string comparisons between publisher and ECVA titles/authors. Publisher metadata remains authoritative for the corpus.

## Automatic identity evidence

- Unicode/HTML/math/hyphen-normalized title plus a complete compatible author assignment.
- Close publication-title variant plus complete compatible authors, unique bidirectional selection and a competition margin.
- Substantial shared title tokens with a complete exact author team of at least three, also subject to competition checks.
- Official ECVA paper-page DOI link to the publisher chapter, with corroborating authors. This stronger explicit relation can explain a name variant without adding a general name alias.

Initial expansion requires matching family names and compatible given names. Missing middle names are allowed; conflicting full given names are not aliases. Author order is immaterial, but duplicate people and ambiguous person assignments are not accepted. High author overlap, mutual-best title ranking, or radical retitling alone do not authorize a match.

The audit stores both titles, both author lists, scores, evidence category and competing URLs. Independent occurrences remain distinguishable from unique URL identities. Unconfirmed independent records are retained; unresolved rows are not a missing-paper count.

## Four targeted public-page checks, 2026-10-06

ECVA's explicitly labeled DOI links confirm these publication relationships:

- ShapeFusion → `10.1007/978-3-031-72630-9_5` (Michail/Michael remains a recorded source spelling discrepancy).
- UAV → `10.1007/978-3-031-73030-6_6`.
- Avatar → `10.1007/978-3-031-73223-2_23`, despite a large title change.
- Zero-shot → `10.1007/978-3-031-72890-7_21`. The chapter was absent from the saved publisher inventory and its official URL returned HTTP 404 during the targeted check. This does not prove withdrawal or non-publication; it remains unresolved and must not be removed to equalize counts.

Raw snapshots and probe reports are under `output/production_acceptance/ECCV/identity_probe_20261006/`. No PDF was downloaded. A failed DOI destination is never converted to publisher metadata.

## Metadata and gate

ECCV audit additionally checks actual nonempty title/abstract, known placeholder/navigation signatures, unique Paper_ID/DOI/URL and Springer DOI/URL agreement. Summary fields include formal_count, title_present_count, abstract_present_count, abstract_missing_count, abstract_invalid_count and duplicate_identity_count. Heuristics cannot universally certify prose semantics; the parser also requires the official chapter abstract container and publication metadata.

Only complete enumeration, complete metadata and zero unresolved audit records publish Formal_Proceedings_Corpus. The existing screen_verified gate and frozen RuleEngine are unchanged. REVIEW_REQUIRED does not run formal screening.
