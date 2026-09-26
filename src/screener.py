"""Config-driven, deterministic, high-recall KD screening engine."""

from __future__ import annotations

import math
import random
import re
from pathlib import Path
from typing import Iterable

import pandas as pd

from .utils import (
    DECISION_AMBIGUOUS,
    DECISION_DROP,
    DECISION_KEEP,
    DECISION_MAYBE,
    RAW_COLUMNS,
    SCREENING_COLUMNS,
    ProjectPaths,
    clean_cell,
    load_yaml,
    setup_logger,
    write_csv,
    write_xlsx,
)


def _find_phrases(text: str, phrases: Iterable[str], cap: int) -> list[str]:
    hits: list[str] = []
    for phrase in phrases:
        pattern = rf"(?<!\w){re.escape(str(phrase).casefold())}(?!\w)"
        if re.search(pattern, text):
            hits.append(str(phrase))
            if len(hits) >= cap:
                break
    return hits


class RuleEngine:
    """Transparent rule scorer whose vocabulary and weights live in YAML."""

    def __init__(self, config: dict) -> None:
        self.config = config
        self.phrases = config["phrases"]
        self.weights = config["weights"]
        self.caps = config.get("caps", {})
        self.thresholds = config["thresholds"]
        self.ambiguous = config.get("ambiguous", {})
        self.distill_regex = re.compile(config["regex"]["distill_root"], re.IGNORECASE)
        self.kd_regex = re.compile(config["regex"]["kd_acronym"], re.IGNORECASE)
        self.kd_tree_regex = re.compile(config["regex"]["kd_tree"], re.IGNORECASE)

    def evaluate(self, paper: dict[str, object]) -> dict[str, object]:
        title = clean_cell(paper.get("Title（标题）"))
        abstract = clean_cell(paper.get("Abstract（摘要）"))
        text = f"{title}\n{abstract}".casefold()
        phrase_cap = int(self.caps.get("phrase_hits_per_group", 4))
        strong_hits = _find_phrases(text, self.phrases["strong_positive"], int(self.caps.get("strong_positive_hits", 2)))
        teacher_hits = _find_phrases(text, self.phrases["teacher"], phrase_cap)
        student_hits = _find_phrases(text, self.phrases["student"], phrase_cap)
        split_transfer = "strong_transfer" in self.phrases or "generic_transfer" in self.phrases
        if split_transfer:
            strong_transfer_hits = _find_phrases(text, self.phrases.get("strong_transfer", []), phrase_cap)
            generic_transfer_hits = _find_phrases(text, self.phrases.get("generic_transfer", []), phrase_cap)
        else:
            # Backward compatibility for V1.1 rule files.
            strong_transfer_hits = []
            generic_transfer_hits = _find_phrases(text, self.phrases.get("knowledge_transfer", []), phrase_cap)
        transfer_hits = list(dict.fromkeys(strong_transfer_hits + generic_transfer_hits))
        large_hits = _find_phrases(text, self.phrases["large_foundation_model"], phrase_cap)
        efficient_hits = _find_phrases(text, self.phrases["efficient_compression"], phrase_cap)
        negative_hits = _find_phrases(
            text, self.phrases["negative_boundary"], int(self.caps.get("negative_boundary_hits", 2))
        )
        distill_hit = bool(self.distill_regex.search(text))
        # Remove spatial-index terms before looking for the KD acronym. This prevents
        # KD-tree, KD tree, and k-d tree from becoming Knowledge Distillation evidence.
        kd_search_text = self.kd_tree_regex.sub(" ", text)
        kd_hit = bool(self.kd_regex.search(kd_search_text))
        kd_context_hits = _find_phrases(kd_search_text, self.phrases["kd_acronym_context"], phrase_cap)

        score = len(strong_hits) * float(self.weights["strong_positive_each"])
        positive_components: list[str] = [f"strong:{term}" for term in strong_hits]
        if distill_hit:
            score += float(self.weights["distill_root"])
            positive_components.append("regex:distill*")
        if kd_hit:
            score += float(self.weights["kd_acronym"])
            positive_components.append("weak:KD")
        if kd_hit and kd_context_hits:
            score += float(self.weights["kd_context_combo"])
            positive_components.append(f"combo:KD+context:{' | '.join(kd_context_hits)}")
        for hits, label, weight_key in (
            (teacher_hits, "teacher", "teacher"),
            (student_hits, "student", "student"),
            (large_hits, "large_model", "large_foundation_model"),
            (efficient_hits, "efficient", "efficient_compression"),
        ):
            if hits:
                score += float(self.weights[weight_key])
                positive_components.append(f"{label}:{' | '.join(hits)}")

        if split_transfer:
            if strong_transfer_hits:
                score += float(self.weights["strong_transfer"])
                positive_components.append(f"transfer_strong:{' | '.join(strong_transfer_hits)}")
            if generic_transfer_hits:
                score += float(self.weights["generic_transfer"])
                positive_components.append(f"transfer_generic:{' | '.join(generic_transfer_hits)}")
        elif transfer_hits:
            score += float(self.weights["knowledge_transfer"])
            positive_components.append(f"transfer:{' | '.join(transfer_hits)}")

        teacher_combo = bool(teacher_hits and student_hits and transfer_hits)
        large_combo = bool(
            large_hits
            and (
                distill_hit
                or (teacher_hits and student_hits)
                or (strong_transfer_hits if split_transfer else transfer_hits)
            )
        )
        efficient_combo = bool(efficient_hits and (distill_hit or (teacher_hits and student_hits)))
        if teacher_combo:
            score += float(self.weights["teacher_student_transfer_combo"])
            positive_components.append("combo:teacher+student+transfer")
        if large_combo:
            score += float(self.weights["large_kd_combo"])
            positive_components.append("combo:large/foundation+KD-transfer")
        if efficient_combo:
            score += float(self.weights["efficient_kd_combo"])
            positive_components.append("combo:efficient/compression+KD")

        positive_score_before_negative = score
        if negative_hits:
            score += len(negative_hits) * float(self.weights["negative_boundary_each"])
        score = round(score, 2)

        status = clean_cell(paper.get("Crawl_Status（抓取状态）"))
        keep_threshold = float(self.thresholds["keep"])
        maybe_threshold = float(self.thresholds["maybe"])
        near_floor = maybe_threshold - float(self.thresholds.get("near_maybe_margin", 2))
        conflict_floor = float(self.thresholds.get("positive_negative_conflict_positive_score", maybe_threshold))
        has_positive_signal = bool(positive_components)
        conflict = bool(negative_hits and positive_score_before_negative >= conflict_floor)
        notes: list[str] = []

        contextual_kd = bool(kd_hit and kd_context_hits)
        distill_with_context = bool(
            distill_hit
            and (teacher_hits or student_hits or strong_transfer_hits or large_hits or efficient_hits)
        )
        explicit_kd_evidence = bool(strong_hits or contextual_kd or distill_with_context)

        if (not abstract and self.ambiguous.get("missing_abstract", True)) or (
            status not in {"", "SUCCESS"} and self.ambiguous.get("incomplete_crawl", True)
        ):
            decision = DECISION_AMBIGUOUS
            reason = "摘要缺失或抓取/解析不完整，不能安全排除。"
            notes.append("INCOMPLETE_METADATA")
        elif conflict and self.ambiguous.get("positive_negative_conflict", True):
            decision = DECISION_AMBIGUOUS
            reason = "阳性KD证据与Dataset Distillation/边界概念同时较强，需人工判别。"
            notes.append("POSITIVE_NEGATIVE_CONFLICT")
        elif negative_hits and distill_hit and self.ambiguous.get("negative_with_distill_signal", True):
            decision = DECISION_AMBIGUOUS
            reason = "出现Dataset/Data Distillation等边界概念且含distill词根，保守进入人工复核。"
            notes.append("BOUNDARY_DISTILLATION")
        elif score >= keep_threshold and explicit_kd_evidence:
            decision = DECISION_KEEP
            reason = "存在明确KD表达、上下文化KD缩写或有模型上下文的distill证据，达到KEEP阈值。"
        elif score >= maybe_threshold or teacher_combo or large_combo or efficient_combo:
            decision = DECISION_MAYBE
            reason = "存在组合式KD/知识迁移迹象，达到MAYBE保护条件。"
        elif distill_hit and self.ambiguous.get("lone_distill_signal", True):
            decision = DECISION_AMBIGUOUS
            reason = "出现distill词根但上下文不足以确认模型知识蒸馏，保守进入人工复核。"
            notes.append("LONE_DISTILL_SIGNAL")
        elif near_floor <= score < maybe_threshold and has_positive_signal and self.ambiguous.get("near_threshold_with_signal", True):
            decision = DECISION_AMBIGUOUS
            reason = "含KD相关信号且分数接近MAYBE阈值，避免静默漏检。"
            notes.append("NEAR_MAYBE_THRESHOLD")
        else:
            decision = DECISION_DROP
            reason = "未发现足以进入候选池的KD强证据、关键组合或规则冲突；原始记录仍完整保留。"

        result = {column: clean_cell(paper.get(column)) for column in RAW_COLUMNS if column in SCREENING_COLUMNS}
        result.update(
            {
                "Rule_Score（规则分数）": score,
                "Positive_Hits（命中的阳性规则）": "; ".join(positive_components),
                "Negative_Hits（命中的负向规则）": "; ".join(negative_hits),
                "Teacher_Evidence（Teacher证据）": "; ".join(teacher_hits),
                "Student_Evidence（Student证据）": "; ".join(student_hits),
                "Transfer_Evidence（知识迁移证据）": "; ".join(transfer_hits),
                "Large_Model_Evidence（大模型相关证据）": "; ".join(large_hits),
                "Efficient_AI_Evidence（高效AI相关证据）": "; ".join(efficient_hits),
                "Decision（筛选决定）": decision,
                "Decision_Reason（筛选理由）": reason,
                "Need_Manual_Check（是否需要人工复核）": "是" if decision == DECISION_AMBIGUOUS else "否",
                "Download_Status（下载状态）": "NOT_REQUESTED",
                "Local_PDF_Path（本地PDF路径）": "",
                "Rule_Version（规则版本）": clean_cell(self.config.get("rule_version", "unknown")),
                "Notes（备注）": "; ".join(notes),
            }
        )
        return {column: result.get(column, "") for column in SCREENING_COLUMNS}


def _take_random(frame: pd.DataFrame, count: int, rng: random.Random) -> list[int]:
    choices = list(frame.index)
    if count <= 0 or not choices:
        return []
    return rng.sample(choices, min(count, len(choices)))


def build_audit_sample(drop_frame: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Create a reproducible, deduplicated stratified SAFE_DROP audit sample."""
    audit = config.get("audit", {})
    target = min(int(audit.get("sample_size", 50)), len(drop_frame))
    if target == 0:
        return drop_frame.head(0).copy()
    rng = random.Random(int(audit.get("random_seed", 0)))
    quotas = audit.get("quotas", {})
    chosen: list[int] = []
    reasons: dict[int, list[str]] = {}

    categories = [
        ("near_threshold", drop_frame.sort_values("Rule_Score（规则分数）", ascending=False)),
        (
            "teacher_or_student",
            drop_frame[(drop_frame["Teacher_Evidence（Teacher证据）"] != "") | (drop_frame["Student_Evidence（Student证据）"] != "")],
        ),
        ("foundation_model", drop_frame[drop_frame["Large_Model_Evidence（大模型相关证据）"] != ""]),
        ("efficient_compression", drop_frame[drop_frame["Efficient_AI_Evidence（高效AI相关证据）"] != ""]),
        ("random", drop_frame),
    ]
    for category, candidates in categories:
        quota = int(math.ceil(target * float(quotas.get(category, 0))))
        available = candidates.loc[~candidates.index.isin(chosen)]
        if category == "near_threshold":
            picks = list(available.head(quota).index)
        else:
            picks = _take_random(available, quota, rng)
        for idx in picks:
            chosen.append(idx)
            reasons.setdefault(idx, []).append(category)

    remaining = drop_frame.loc[~drop_frame.index.isin(chosen)]
    for idx in _take_random(remaining, target - len(chosen), rng):
        chosen.append(idx)
        reasons.setdefault(idx, []).append("random_fill")
    chosen = chosen[:target]
    sample = drop_frame.loc[chosen].copy()
    sample.insert(len(sample.columns), "Audit_Stratum（审计分层）", ["; ".join(reasons.get(idx, [])) for idx in chosen])
    return sample


def screen_papers(root: Path, venue: str, year: int, rules_path: Path) -> pd.DataFrame:
    """Screen the persisted raw pool without any web access."""
    paths = ProjectPaths(root, venue.upper(), year)
    paths.ensure()
    logger = setup_logger(f"screen.{venue}.{year}", paths.logs / "screening.log")
    if not paths.raw_csv.exists():
        raise FileNotFoundError(f"Raw paper pool not found: {paths.raw_csv}")
    config = load_yaml(rules_path)
    engine = RuleEngine(config)
    raw = pd.read_csv(paths.raw_csv, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    results = pd.DataFrame([engine.evaluate(row) for row in raw.to_dict(orient="records")], columns=SCREENING_COLUMNS)

    write_csv(results, paths.screening_csv, SCREENING_COLUMNS)
    write_xlsx(results, paths.screening_xlsx, SCREENING_COLUMNS)
    decision_files = {
        DECISION_KEEP: "RULE_KEEP.csv",
        DECISION_MAYBE: "RULE_MAYBE.csv",
        DECISION_AMBIGUOUS: "RULE_AMBIGUOUS.csv",
        DECISION_DROP: "SAFE_DROP.csv",
    }
    for decision, filename in decision_files.items():
        subset = results[results["Decision（筛选决定）"] == decision]
        write_csv(subset, paths.screening / filename, SCREENING_COLUMNS)
        logger.info("Decision %s: %d", decision, len(subset))

    manual = results[
        (results["Decision（筛选决定）"] == DECISION_AMBIGUOUS)
        | (results["Need_Manual_Check（是否需要人工复核）"] == "是")
    ].copy()
    write_csv(manual, paths.screening / f"{paths.stem}_Manual_Review.csv", SCREENING_COLUMNS)

    drops = results[results["Decision（筛选决定）"] == DECISION_DROP].copy()
    drops["Rule_Score（规则分数）"] = pd.to_numeric(drops["Rule_Score（规则分数）"], errors="coerce").fillna(0)
    audit = build_audit_sample(drops, config)
    audit_columns = SCREENING_COLUMNS + ["Audit_Stratum（审计分层）"]
    write_csv(audit, paths.screening / f"{paths.stem}_SAFE_DROP_Audit_Sample.csv", audit_columns)
    logger.info("Screening complete: total=%d manual=%d audit=%d rules=%s", len(results), len(manual), len(audit), rules_path)
    return results
