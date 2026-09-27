"""Central path policy for the paper library.

All persistent Venue-Year paths must be constructed here.  The project source
tree and the paper data library are intentionally separate.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


DEFAULT_LIBRARY_ROOT = Path(r"D:\_Knowledge Distillation\Paper Library")


@dataclass(frozen=True)
class ProjectPaths:
    """Canonical paths for one ``<library>/<venue>/<year>`` collection."""

    library_root: Path
    venue: str
    year: int

    @property
    def normalized_venue(self) -> str:
        return self.venue.upper()

    @property
    def stem(self) -> str:
        return f"{self.normalized_venue}{self.year}"

    @property
    def venue_root(self) -> Path:
        return self.library_root / self.normalized_venue

    @property
    def year_root(self) -> Path:
        return self.venue_root / str(self.year)

    @property
    def raw_dir(self) -> Path:
        return self.year_root / "raw"

    @property
    def cache_dir(self) -> Path:
        return self.raw_dir / "cache"

    @property
    def cache_lists_dir(self) -> Path:
        return self.cache_dir / "lists"

    @property
    def cache_details_dir(self) -> Path:
        return self.cache_dir / "details"

    @property
    def screening_dir(self) -> Path:
        return self.year_root / "screening"

    @property
    def secondary_screening_dir(self) -> Path:
        return self.year_root / "secondary_screening"

    @property
    def pdf_dir(self) -> Path:
        return self.year_root / "PDFs"

    @property
    def full_read_pdf_dir(self) -> Path:
        """Legacy CVPR 2026 path; new workflows must not create it."""
        return self.pdf_dir / "FULL_READ"

    @property
    def reserve_pdf_dir(self) -> Path:
        """Legacy optional pool; new workflows must not create it by default."""
        return self.pdf_dir / "RESERVE"

    @property
    def kd_method_centric_pdf_dir(self) -> Path:
        return self.pdf_dir / "KD_Method_Centric"

    @property
    def other_kd_pdf_dir(self) -> Path:
        return self.pdf_dir / "Other_KD"

    @property
    def assignments_dir(self) -> Path:
        return self.year_root / "assignments"

    @property
    def manifests_dir(self) -> Path:
        return self.year_root / "manifests"

    @property
    def reports_dir(self) -> Path:
        return self.year_root / "reports"

    @property
    def logs_dir(self) -> Path:
        return self.year_root / "logs"

    @property
    def raw_csv(self) -> Path:
        return self.raw_dir / "All_Papers.csv"

    @property
    def raw_xlsx(self) -> Path:
        return self.raw_dir / "All_Papers.xlsx"

    @property
    def crawl_exception_csv(self) -> Path:
        return self.raw_dir / "Crawl_Exception_Queue.csv"

    @property
    def screening_csv(self) -> Path:
        return self.screening_dir / "KD_Screening.csv"

    @property
    def screening_xlsx(self) -> Path:
        return self.screening_dir / "KD_Screening.xlsx"

    @property
    def rule_keep_csv(self) -> Path:
        return self.screening_dir / "RULE_KEEP.csv"

    @property
    def rule_maybe_csv(self) -> Path:
        return self.screening_dir / "RULE_MAYBE.csv"

    @property
    def rule_ambiguous_csv(self) -> Path:
        return self.screening_dir / "RULE_AMBIGUOUS.csv"

    @property
    def safe_drop_csv(self) -> Path:
        return self.screening_dir / "SAFE_DROP.csv"

    @property
    def manual_review_csv(self) -> Path:
        return self.screening_dir / "Manual_Review.csv"

    @property
    def safe_drop_audit_csv(self) -> Path:
        return self.screening_dir / "SAFE_DROP_Audit_Sample.csv"

    @property
    def secondary_screening_csv(self) -> Path:
        return self.secondary_screening_dir / "Secondary_Screening.csv"

    @property
    def secondary_screening_xlsx(self) -> Path:
        return self.secondary_screening_dir / "Secondary_Screening.xlsx"

    @property
    def full_read_csv(self) -> Path:
        """Legacy secondary-screening file."""
        return self.secondary_screening_dir / "FULL_READ.csv"

    @property
    def reserve_csv(self) -> Path:
        """Legacy secondary-screening file."""
        return self.secondary_screening_dir / "RESERVE.csv"

    @property
    def exclude_csv(self) -> Path:
        return self.secondary_screening_dir / "EXCLUDE.csv"

    @property
    def kd_method_centric_csv(self) -> Path:
        return self.secondary_screening_dir / "KD_METHOD_CENTRIC.csv"

    @property
    def other_kd_csv(self) -> Path:
        return self.secondary_screening_dir / "OTHER_KD.csv"

    @property
    def full_read_manifest_csv(self) -> Path:
        """Legacy FULL_READ manifest."""
        return self.manifests_dir / f"{self.stem}_FULL_READ_PDF_Manifest.csv"

    @property
    def kd_review_manifest_csv(self) -> Path:
        return self.manifests_dir / f"{self.stem}_KD_REVIEW_PDF_Manifest.csv"

    @property
    def reserve_manifest_csv(self) -> Path:
        return self.manifests_dir / f"{self.stem}_RESERVE_PDF_Manifest.csv"

    @property
    def legacy_manifest_csv(self) -> Path:
        return self.manifests_dir / f"{self.stem}_PDF_Manifest.csv"

    @property
    def read_first_assignment_csv(self) -> Path:
        return self.assignments_dir / f"{self.stem}_Read_First_Assignment.csv"

    @property
    def full_read_assignment_csv(self) -> Path:
        """Legacy FULL_READ Assignment."""
        return self.assignments_dir / f"{self.stem}_FULL_READ_Assignment.csv"

    @property
    def kd_review_assignment_csv(self) -> Path:
        return self.assignments_dir / f"{self.stem}_KD_REVIEW_Assignment.csv"

    @property
    def status_report(self) -> Path:
        return self.reports_dir / f"{self.stem}_Status.md"

    @property
    def assignment_qc_report(self) -> Path:
        return self.reports_dir / f"{self.stem}_Assignment_QC.md"

    @property
    def full_read_download_report(self) -> Path:
        return self.reports_dir / f"{self.stem}_FULL_READ_Download.md"

    @property
    def kd_review_download_report(self) -> Path:
        return self.reports_dir / f"{self.stem}_KD_REVIEW_Download.md"

    @property
    def pdf_migration_report(self) -> Path:
        return self.reports_dir / f"{self.stem}_PDF_Migration.md"

    @property
    def research_map_xlsx(self) -> Path:
        return self.pdf_dir / f"{self.stem}_KD_Research_Map.xlsx"

    @property
    def crawl_log(self) -> Path:
        return self.logs_dir / "crawl.log"

    @property
    def screening_log(self) -> Path:
        return self.logs_dir / "screening.log"

    @property
    def legacy_download_log(self) -> Path:
        return self.logs_dir / "download_legacy.log"

    @property
    def full_read_download_log(self) -> Path:
        return self.logs_dir / "download_secondary.log"

    @property
    def reserve_download_log(self) -> Path:
        return self.logs_dir / "download_reserve.log"

    @property
    def organize_log(self) -> Path:
        return self.logs_dir / "organize_read_first.log"

    @property
    def report_log(self) -> Path:
        return self.logs_dir / "report.log"

    def listing_cache(self, filename: str) -> Path:
        return self.cache_lists_dir / filename

    def detail_cache(self, paper_id: str) -> Path:
        return self.cache_details_dir / f"{paper_id}.html"

    def full_read_order_dir(self, folder: str) -> Path:
        return self.full_read_pdf_dir / folder

    def full_read_assistant_dir(self, read_tier: str, assistant: str) -> Path:
        """Legacy Read_Tier path."""
        return self.full_read_pdf_dir / read_tier / assistant

    def kd_review_assistant_dir(self, secondary_class: str, assistant: str) -> Path:
        if secondary_class == "KD_Method_Centric":
            category = self.kd_method_centric_pdf_dir
        elif secondary_class == "Other_KD":
            category = self.other_kd_pdf_dir
        else:
            raise ValueError(f"Invalid Secondary_Class: {secondary_class}")
        return category / assistant

    def read_first_assistant_dir(self, assistant: str) -> Path:
        return self.full_read_pdf_dir / "Read_First" / assistant

    def legacy_pdf_dir(self, decision_folder: str) -> Path:
        return self.pdf_dir / decision_folder

    def ensure_stage(self, stage: str) -> None:
        """Create only directories required by the requested stage."""
        stages = {
            "crawl": (self.raw_dir, self.cache_lists_dir, self.cache_details_dir, self.logs_dir),
            "screen": (self.screening_dir, self.logs_dir),
            "download-secondary": (
                self.manifests_dir,
                self.reports_dir,
                self.logs_dir,
            ),
            "download-reserve": (self.reserve_pdf_dir, self.manifests_dir, self.logs_dir),
            "download-legacy": (self.manifests_dir, self.logs_dir),
            "organize": (self.logs_dir,),
            "validate-assignment": (self.reports_dir, self.logs_dir),
            "apply-reclassification": (self.reports_dir, self.logs_dir, self.manifests_dir),
            "report": (self.reports_dir, self.logs_dir),
        }
        try:
            directories = stages[stage]
        except KeyError as exc:
            raise ValueError(f"Unknown path stage: {stage}") from exc
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
