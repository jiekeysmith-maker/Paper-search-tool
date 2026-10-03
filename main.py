"""Command-line entry point for Paper Search Tool V1."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.corpus_audit import COMPLETENESS_VERIFIED, audit_corpus
from src.crawler import crawl_cvf
from src.downloader import (
    download_candidates,
    download_reserve_candidates,
    download_secondary_candidates,
    organize_existing_secondary_pdfs,
)
from src.organizer import organize_read_first_assignments
from src.kd_assignment import validate_assignment
from src.paths import DEFAULT_LIBRARY_ROOT, ProjectPaths
from src.reclassification import execute_reclassification, plan_reclassification
from src.status_reporter import generate_report
from src.screener import screen_papers


def positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="论文搜索工具（Paper Search Tool）",
        description="CVF论文抓取、确定性KD高召回初筛、官方PDF下载与审计报告",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in (
        "crawl",
        "audit-corpus",
        "screen",
        "download",
        "download-secondary",
        "download-reserve",
        "validate-assignment",
        "apply-reclassification",
        "organize-read-first",
        "report",
        "all",
    ):
        sub = subparsers.add_parser(command)
        sub.add_argument("--venue", default="CVPR", help="Venue，V1支持CVPR")
        sub.add_argument("--year", required=True, type=int, help="四位年份，例如2026")
        sub.add_argument(
            "--library-root",
            type=Path,
            default=DEFAULT_LIBRARY_ROOT,
            help=r"论文库根目录，默认 D:\_Knowledge Distillation\Paper Library",
        )
        sub.add_argument("--rules", type=Path, default=Path("config/screening_rules_v1_2.yaml"))
        sub.add_argument("--source-config", type=Path, default=Path("config/source_config.yaml"))
        sub.add_argument("--limit", type=positive_int, help="抓取/下载小样本上限；抓取时在轨道间轮转")
        sub.add_argument("--pdf-limit", type=positive_int, help="显式PDF下载命令的下载上限")
        sub.add_argument("--force", action="store_true", help="忽略缓存重新请求，或覆盖已有PDF")
        if command in {"crawl", "all"}:
            sub.add_argument(
                "--refresh-listing",
                action="store_true",
                help="只强制刷新CVF列表页；复用已有SUCCESS详情，仅抓新增或失败论文",
            )
        sub.add_argument("--title-query", help="仅在CVF列表中按标题过滤，供定向Smoke Test使用")
        if command in {"download-secondary", "download-reserve"}:
            mode = sub.add_mutually_exclusive_group()
            mode.add_argument(
                "--retry-failed",
                action="store_true",
                help="只重试对应Manifest中状态为FAILED的记录",
            )
            if command == "download-secondary":
                mode.add_argument(
                    "--organize-existing",
                    action="store_true",
                    help="legacy：仅按Read_Order移动历史PDF并更新Manifest，不联网",
                )
        if command == "apply-reclassification":
            sub.add_argument("--csv", required=True, type=Path, help="重分类CSV路径")
            action = sub.add_mutually_exclusive_group()
            action.add_argument("--dry-run", action="store_true", help="只验证和规划（默认）")
            action.add_argument("--execute", action="store_true", help="验证READY后执行本地移动")
    return parser


def _resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project_root = Path.cwd().resolve()
    library_root = args.library_root.resolve()
    rules_path = _resolve(project_root, args.rules)
    source_path = _resolve(project_root, args.source_config)
    try:
        if args.command in {"crawl", "all"}:
            crawl_cvf(
                root=library_root,
                venue=args.venue,
                year=args.year,
                source_config_path=source_path,
                limit=args.limit,
                force=args.force,
                refresh_listing=args.refresh_listing,
                title_query=args.title_query,
            )
        if args.command in {"audit-corpus", "all"}:
            audit_result = audit_corpus(
                root=library_root,
                venue=args.venue,
                year=args.year,
                source_config_path=source_path,
                force=args.force,
            )
            print(
                f"Corpus completeness: {audit_result.status}; "
                f"report: {audit_result.report_path}"
            )
            if args.command == "all" and audit_result.status != COMPLETENESS_VERIFIED:
                print("Formal proceedings corpus is not verified; all stopped before screening.")
                return 0
        if args.command in {"screen", "all"}:
            screen_papers(library_root, args.venue, args.year, rules_path)
        if args.command == "download":
            download_limit = args.pdf_limit if args.pdf_limit is not None else args.limit
            download_candidates(
                root=library_root,
                venue=args.venue,
                year=args.year,
                rules_path=rules_path,
                source_config_path=source_path,
                limit=download_limit,
                force=args.force,
            )
        if args.command == "download-secondary":
            if args.organize_existing:
                counts = organize_existing_secondary_pdfs(library_root, args.venue, args.year)
                print(f"Organized FULL_READ PDFs: {counts}")
            else:
                download_limit = args.pdf_limit if args.pdf_limit is not None else args.limit
                download_secondary_candidates(
                    root=library_root,
                    venue=args.venue,
                    year=args.year,
                    source_config_path=source_path,
                    limit=download_limit,
                    force=args.force,
                    retry_failed=args.retry_failed,
                )
        if args.command == "download-reserve":
            download_limit = args.pdf_limit if args.pdf_limit is not None else args.limit
            download_reserve_candidates(
                root=library_root,
                venue=args.venue,
                year=args.year,
                source_config_path=source_path,
                limit=download_limit,
                force=args.force,
                retry_failed=args.retry_failed,
            )
        if args.command == "validate-assignment":
            result = validate_assignment(library_root, args.venue, args.year)
            print(f"Assignment validation: {'OK' if result.valid else 'ERROR'}; QC: {result.report_path}")
        if args.command == "apply-reclassification":
            csv_path = _resolve(project_root, args.csv)
            expected_counts = (
                {"KD_Method_Centric": 70, "Other_KD": 55}
                if args.venue.upper() == "CVPR"
                and args.year == 2026
                and csv_path.name == "CVPR2026_PDF_Reclassification.csv"
                else None
            )
            if expected_counts:
                research_map = ProjectPaths(
                    library_root, args.venue.upper(), args.year
                ).research_map_xlsx
                if not research_map.exists():
                    raise FileNotFoundError(
                        f"Required Research Map not found; migration stopped: {research_map}"
                    )
            if args.execute:
                result = execute_reclassification(
                    library_root,
                    args.venue,
                    args.year,
                    csv_path,
                    expected_counts=expected_counts,
                )
                print(f"Reclassification execute: OK; report: {result.report_path}")
            else:
                result = plan_reclassification(
                    library_root,
                    args.venue,
                    args.year,
                    csv_path,
                    expected_counts=expected_counts,
                )
                print(f"Reclassification dry-run: READY; report: {result.report_path}")
        if args.command == "organize-read-first":
            counts = organize_read_first_assignments(library_root, args.venue, args.year)
            print(f"Organized Read_First assignments: {counts}")
        if args.command == "report":
            generate_report(library_root, args.venue, args.year)
        if args.command == "all":
            print("Secondary screening is required before KD review Assignment and download.")
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
