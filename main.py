"""Command-line entry point for Paper Search Tool V1."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.crawler import crawl_cvf
from src.downloader import download_candidates, download_secondary_candidates
from src.reporter import generate_report
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
    for command in ("crawl", "screen", "download", "download-secondary", "report", "all"):
        sub = subparsers.add_parser(command)
        sub.add_argument("--venue", default="CVPR", help="Venue，V1支持CVPR")
        sub.add_argument("--year", required=True, type=int, help="四位年份，例如2026")
        sub.add_argument("--rules", type=Path, default=Path("config/screening_rules_v1.yaml"))
        sub.add_argument("--source-config", type=Path, default=Path("config/source_config.yaml"))
        sub.add_argument("--limit", type=positive_int, help="抓取/下载小样本上限；抓取时在轨道间轮转")
        sub.add_argument("--pdf-limit", type=positive_int, help="all/download阶段的PDF下载上限")
        sub.add_argument("--force", action="store_true", help="忽略缓存重新请求，或覆盖已有PDF")
        sub.add_argument("--title-query", help="仅在CVF列表中按标题过滤，供定向Smoke Test使用")
    return parser


def _resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path.cwd().resolve()
    rules_path = _resolve(root, args.rules)
    source_path = _resolve(root, args.source_config)
    try:
        if args.command in {"crawl", "all"}:
            crawl_cvf(
                root=root,
                venue=args.venue,
                year=args.year,
                source_config_path=source_path,
                limit=args.limit,
                force=args.force,
                title_query=args.title_query,
            )
        if args.command in {"screen", "all"}:
            screen_papers(root, args.venue, args.year, rules_path)
        if args.command in {"download", "all"}:
            download_limit = args.pdf_limit if args.pdf_limit is not None else (args.limit if args.command == "download" else None)
            download_candidates(
                root=root,
                venue=args.venue,
                year=args.year,
                rules_path=rules_path,
                source_config_path=source_path,
                limit=download_limit,
                force=args.force,
            )
        if args.command == "download-secondary":
            download_limit = args.pdf_limit if args.pdf_limit is not None else args.limit
            download_secondary_candidates(
                root=root,
                venue=args.venue,
                year=args.year,
                source_config_path=source_path,
                limit=download_limit,
                force=args.force,
            )
        if args.command in {"report", "all"}:
            generate_report(root, args.venue, args.year)
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
