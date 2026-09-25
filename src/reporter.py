"""Chinese Markdown summary generation."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from .utils import DECISION_AMBIGUOUS, DECISION_DROP, DECISION_KEEP, DECISION_MAYBE, ProjectPaths, clean_cell, setup_logger


def _count(frame: pd.DataFrame, column: str, value: str) -> int:
    if column not in frame.columns:
        return 0
    return int((frame[column].astype(str) == value).sum())


def generate_report(root: Path, venue: str, year: int) -> Path:
    """Summarize crawl, screening, audit, review, and download outcomes."""
    paths = ProjectPaths(root, venue.upper(), year)
    paths.ensure()
    logger = setup_logger(f"report.{venue}.{year}", paths.logs / "report.log")
    if not paths.raw_csv.exists():
        raise FileNotFoundError(f"Raw paper pool not found: {paths.raw_csv}")
    if not paths.screening_csv.exists():
        raise FileNotFoundError(f"Screening results not found: {paths.screening_csv}")

    raw = pd.read_csv(paths.raw_csv, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    screening = pd.read_csv(paths.screening_csv, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    manual_path = paths.screening / f"{paths.stem}_Manual_Review.csv"
    audit_path = paths.screening / f"{paths.stem}_SAFE_DROP_Audit_Sample.csv"
    manifest_path = paths.manifests / f"{paths.stem}_PDF_Manifest.csv"
    manual_count = len(pd.read_csv(manual_path, encoding="utf-8-sig")) if manual_path.exists() else 0
    audit_count = len(pd.read_csv(audit_path, encoding="utf-8-sig")) if audit_path.exists() else 0
    manifest = pd.read_csv(manifest_path, encoding="utf-8-sig", dtype=str, keep_default_na=False) if manifest_path.exists() else pd.DataFrame()
    pdf_success = 0 if manifest.empty else int(manifest["Download_Status（下载状态）"].isin(["DOWNLOADED", "ALREADY_EXISTS"]).sum())
    pdf_failed = 0 if manifest.empty else int(manifest["Download_Status（下载状态）"].isin(["FAILED", "MISSING_URL"]).sum())
    rule_versions = sorted({clean_cell(value) for value in screening.get("Rule_Version（规则版本）", []) if clean_cell(value)})
    errors = raw[raw["Crawl_Status（抓取状态）"] != "SUCCESS"]

    hit_counts: dict[str, int] = {}
    for cell in screening.get("Positive_Hits（命中的阳性规则）", []):
        for hit in str(cell).split("; "):
            if hit:
                hit_counts[hit] = hit_counts.get(hit, 0) + 1
    top_hits = sorted(hit_counts.items(), key=lambda item: (-item[1], item[0]))[:15]
    hit_lines = "\n".join(f"- `{hit}`：{count}" for hit, count in top_hits) or "- 暂无阳性命中。"
    exception_lines = "\n".join(
        f"- {clean_cell(row['Paper_ID（论文编号）'])}：{clean_cell(row['Crawl_Error（抓取错误）']) or clean_cell(row['Crawl_Status（抓取状态）'])}"
        for _, row in errors.head(20).iterrows()
    ) or "- 本次记录中无抓取异常。"

    report = f"""# {paths.stem} Knowledge Distillation 初筛报告

> 本报告仅反映确定性规则对 Title + Abstract 的高召回初筛结果，不代表 P0/P1/P2/P3 科研价值判断，也不宣称覆盖了所有重要 KD 论文。

## 运行概览

| 指标 | 数量/值 |
|---|---:|
| 官方抓取论文总数 | {len(raw)} |
| Main Conference | {_count(raw, 'Track（论文轨道）', 'Main Conference')} |
| Findings | {_count(raw, 'Track（论文轨道）', 'Findings')} |
| Workshop（仅配置开启时） | {int(raw['Track（论文轨道）'].astype(str).str.startswith('Workshop').sum())} |
| Abstract 成功 | {int((raw['Abstract（摘要）'].astype(str).str.strip() != '').sum())} |
| Abstract 失败/缺失 | {int((raw['Abstract（摘要）'].astype(str).str.strip() == '').sum())} |
| RULE_KEEP | {_count(screening, 'Decision（筛选决定）', DECISION_KEEP)} |
| RULE_MAYBE | {_count(screening, 'Decision（筛选决定）', DECISION_MAYBE)} |
| RULE_AMBIGUOUS | {_count(screening, 'Decision（筛选决定）', DECISION_AMBIGUOUS)} |
| SAFE_DROP | {_count(screening, 'Decision（筛选决定）', DECISION_DROP)} |
| PDF 成功/已存在 | {pdf_success} |
| PDF 失败/缺少链接 | {pdf_failed} |
| 人工复核队列 | {manual_count} |
| SAFE_DROP 审计样本 | {audit_count} |
| 规则版本 | {', '.join(rule_versions) or '未知'} |
| 报告生成时间 | {datetime.now().astimezone().isoformat(timespec='seconds')} |

## 阳性规则命中频次（Top 15）

{hit_lines}

## 异常情况

{exception_lines}

## 解释与后续

- `RULE_KEEP` 与 `RULE_MAYBE` 默认进入官方 PDF 下载阶段。
- `RULE_AMBIGUOUS` 包含摘要缺失、解析不完整、正负规则冲突和阈值边界案例，默认不下载但不会删除。
- `SAFE_DROP` 仍保留在原始池和筛选表中，并通过分层审计样本检查潜在漏检。
- 修改规则后，可直接对原始 CSV 重新运行 `screen`，无需重新访问 CVF。
"""
    report_path = paths.reports / f"{paths.stem}_KD_Screening_Report.md"
    report_path.write_text(report, encoding="utf-8")
    logger.info("Report written: %s", report_path)
    return report_path
