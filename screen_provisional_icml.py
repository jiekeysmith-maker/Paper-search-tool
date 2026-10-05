"""One-off provisional V1.2 screening helper for REVIEW_REQUIRED ICML corpora.

This keeps the production gate in screen_verified.py unchanged.
It explicitly screens raw/PROVISIONAL_Formal_Proceedings_Corpus.csv when
Audit_Summary.json is REVIEW_REQUIRED, without modifying the audit or creating
Formal_Proceedings_Corpus.csv.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
import uuid
from pathlib import Path

import pandas as pd

from screen_verified import MAPPING, RULES, RULES_SHA256, venue_year_path
from src.screener import RuleEngine, build_audit_sample
from src.utils import (
    load_yaml,
    SCREENING_COLUMNS,
    DECISION_KEEP,
    DECISION_MAYBE,
    DECISION_AMBIGUOUS,
    DECISION_DROP,
)
from venue_runtime import atomic_bytes, job_lock


def load_review_required_corpus(base: Path, venue: str, year: int):
    raw = base / "raw"
    audit_path = raw / "Audit_Summary.json"
    corpus_path = raw / "PROVISIONAL_Formal_Proceedings_Corpus.csv"

    if not audit_path.is_file():
        raise FileNotFoundError(f"Missing audit: {audit_path}")
    audit = json.loads(audit_path.read_text(encoding="utf-8-sig"))

    if audit.get("status") != "REVIEW_REQUIRED":
        raise ValueError(
            f"Expected Audit_Summary status REVIEW_REQUIRED, got {audit.get('status')!r}"
        )
    if audit.get("venue") != venue or str(audit.get("year")) != str(year):
        raise ValueError("Audit Venue-Year mismatch")

    unresolved = audit.get("unresolved_count")
    if type(unresolved) is not int or unresolved < 1:
        raise ValueError("REVIEW_REQUIRED audit must report unresolved_count >= 1")

    if not corpus_path.is_file():
        raise FileNotFoundError(f"Missing provisional corpus: {corpus_path}")
    if corpus_path.resolve() != corpus_path:
        raise ValueError("Linked provisional corpus is not supported")

    data = corpus_path.read_bytes()
    corpus_sha = sha256(data).hexdigest()
    frame = pd.read_csv(BytesIO(data), dtype=str, keep_default_na=False)

    required = set(MAPPING) | {"Formal_Publication_Evidence"}
    if not required.issubset(frame.columns) or frame.empty:
        raise ValueError("Missing corpus columns or empty corpus")

    for column in required - {"PDF_URL"}:
        if not frame[column].astype(str).str.strip().all():
            raise ValueError(f"Empty required field: {column}")

    if not frame["Paper_ID"].is_unique:
        raise ValueError("Duplicate Paper_ID in provisional corpus")
    if not frame["Official_URL"].is_unique:
        raise ValueError("Duplicate Official_URL in provisional corpus")
    if not frame["Venue"].eq(venue).all() or not frame["Year"].eq(str(year)).all():
        raise ValueError("Corpus Venue-Year mismatch")

    metadata_count = audit.get("metadata_count")
    publisher_count = audit.get("publisher_count")
    if type(metadata_count) is not int or len(frame) != metadata_count:
        raise ValueError(
            f"Metadata count mismatch: corpus={len(frame)} audit={metadata_count}"
        )
    if type(publisher_count) is not int or len(frame) != publisher_count:
        raise ValueError(
            f"Publisher count mismatch: corpus={len(frame)} audit={publisher_count}"
        )

    return frame, corpus_sha, audit


def screen_provisional(venue: str, year: int, library_root: Path):
    base = venue_year_path(library_root, venue, year)

    with job_lock(base, venue, year):
        rule_sha = sha256(RULES.read_bytes()).hexdigest()
        if rule_sha != RULES_SHA256:
            raise ValueError("Frozen V1.2 rules hash mismatch")

        out = base / "screening"
        if out.exists():
            raise FileExistsError(f"Screening output already exists: {out}")

        frame, corpus_sha, audit = load_review_required_corpus(base, venue, year)

        config = load_yaml(RULES)
        engine = RuleEngine(config)
        records = frame.to_dict("records")

        results = pd.DataFrame(
            [
                engine.evaluate({label: row[key] for key, label in MAPPING.items()})
                for row in records
            ],
            columns=SCREENING_COLUMNS,
        )

        tables = {"KD_Screening.csv": results}
        counts = {}
        candidates = []
        by_id = {row["Paper_ID"]: row for row in records}
        extra = [
            "Rule_Decision",
            "Rule_Evidence",
            "Rules_Version",
            "Rules_SHA256",
            "Corpus_Audit_Status",
            "Review_Status",
            "Provisional_Review_Hint",
            "Data_Issue",
        ]

        for decision in (
            DECISION_KEEP,
            DECISION_MAYBE,
            DECISION_AMBIGUOUS,
            DECISION_DROP,
        ):
            subset = results[results["Decision（筛选决定）"] == decision]
            key = decision.split("（")[0]
            counts[key] = len(subset)
            tables[f"{key}.csv"] = subset

            if decision != DECISION_DROP:
                for row in subset.to_dict("records"):
                    candidates.append(
                        {
                            **by_id[row["Paper_ID（论文编号）"]],
                            "Rule_Decision": key,
                            "Rule_Evidence": " | ".join(
                                row[c]
                                for c in (
                                    "Decision_Reason（筛选理由）",
                                    "Positive_Hits（命中的阳性规则）",
                                    "Negative_Hits（命中的负向规则）",
                                )
                            ),
                            "Rules_Version": "V1.2",
                            "Rules_SHA256": rule_sha,
                            "Corpus_Audit_Status": "REVIEW_REQUIRED",
                            "Review_Status": "PENDING_HUMAN_SECONDARY_REVIEW",
                            "Provisional_Review_Hint": (
                                "Current corpus screened before deferred audit anomalies are closed."
                            ),
                            "Data_Issue": (
                                f"DEFERRED_AUDIT_UNRESOLVED_COUNT="
                                f"{audit['unresolved_count']}"
                            ),
                        }
                    )

        if sum(counts.values()) != len(frame):
            raise ValueError("Screening partition mismatch")

        tables["Needs_Secondary_Review.csv"] = pd.DataFrame(
            candidates, columns=list(frame.columns) + extra
        )

        drops = results[
            results["Decision（筛选决定）"] == DECISION_DROP
        ].copy()
        drops["Rule_Score（规则分数）"] = pd.to_numeric(
            drops["Rule_Score（规则分数）"]
        )
        sample = build_audit_sample(drops, config)
        tables["SAFE_DROP_Audit_Sample.csv"] = sample

        manifest = {
            "utc": datetime.now(timezone.utc).isoformat(),
            "venue": venue,
            "year": year,
            "status": "SCREENED_CSV_READY",
            "counts": counts,
            "candidate_count": len(candidates),
            "safe_drop_sample_count": len(sample),
            "rules_sha256": rule_sha,
            "corpus_sha256": corpus_sha,
            "corpus_source": "PROVISIONAL_Formal_Proceedings_Corpus.csv",
            "corpus_audit_status": "REVIEW_REQUIRED",
            "unresolved_count": audit["unresolved_count"],
            "provisional_screening": True,
            "note": (
                "Deferred audit anomalies remain open; this run does not change "
                "Audit_Summary.json or create Formal_Proceedings_Corpus.csv."
            ),
            "screening_entry": Path(__file__).name,
            "screening_entry_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        }

        staging = base / "runtime" / ("screening-" + uuid.uuid4().hex + ".partial")
        staging.mkdir(parents=True)

        for name, table in tables.items():
            table.to_csv(
                staging / name,
                index=False,
                lineterminator="\n",
                encoding="utf-8-sig",
            )

        manifest["output_sha256"] = {
            name: sha256((staging / name).read_bytes()).hexdigest()
            for name in tables
        }
        atomic_bytes(
            staging / "Run_Manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        staging.rename(out)

        return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("venue", choices=("ICML",))
    parser.add_argument("year", type=int, choices=(2025, 2026))
    parser.add_argument("--library-root", type=Path, required=True)
    args = parser.parse_args()

    result = screen_provisional(
        args.venue, args.year, args.library_root.resolve()
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
