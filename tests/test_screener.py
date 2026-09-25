from pathlib import Path

import pytest

from src.screener import RuleEngine
from src.utils import DECISION_AMBIGUOUS, DECISION_DROP, DECISION_KEEP, DECISION_MAYBE, load_yaml


@pytest.fixture(scope="module")
def engine() -> RuleEngine:
    root = Path(__file__).resolve().parents[1]
    return RuleEngine(load_yaml(root / "config" / "screening_rules_v1.yaml"))


def paper(title: str, abstract: str, status: str = "SUCCESS") -> dict[str, object]:
    return {
        "Paper_ID（论文编号）": "TEST",
        "Title（标题）": title,
        "Authors（作者）": "A; B",
        "Abstract（摘要）": abstract,
        "Venue（会议/期刊）": "CVPR",
        "Year（年份）": 2026,
        "Track（论文轨道）": "Main Conference",
        "Official_URL（官方论文页面）": "https://openaccess.thecvf.com/test",
        "PDF_URL（官方PDF链接）": "https://openaccess.thecvf.com/test.pdf",
        "Crawl_Status（抓取状态）": status,
    }


def decision(engine: RuleEngine, title: str, abstract: str, status: str = "SUCCESS") -> str:
    return str(engine.evaluate(paper(title, abstract, status))["Decision（筛选决定）"])


def test_type_a_vlm_teacher_to_compact_student_is_keep(engine):
    value = decision(
        engine,
        "Knowledge Distillation for Compact Vision-Language Models",
        "A large VLM teacher transfers representations to a compact student using knowledge distillation.",
    )
    assert value == DECISION_KEEP


def test_type_b_world_model_policy_distillation_is_keep(engine):
    value = decision(
        engine,
        "World-to-Policy Transfer",
        "A world model forms a teacher policy and policy distillation transfers reasoning to a lightweight student.",
    )
    assert value == DECISION_KEEP


def test_type_c_pruning_with_teacher_student_distillation_not_drop(engine):
    value = decision(
        engine,
        "Structured Pruning for Diffusion Transformers",
        "We use a teacher-student alternating distillation scheme and knowledge transfer during structured pruning.",
    )
    assert value in {DECISION_KEEP, DECISION_MAYBE}


def test_type_d_compression_kd_nas_pruning_not_drop(engine):
    value = decision(
        engine,
        "A Unified Model Compression Search",
        "Our efficient compression system jointly uses KD, neural architecture search, quantization, and pruning.",
    )
    assert value in {DECISION_KEEP, DECISION_MAYBE}


def test_type_e_dataset_distillation_with_kd_is_ambiguous(engine):
    value = decision(
        engine,
        "Dataset Distillation with Auxiliary Model KD",
        "Dataset distillation is primary, while supervised learning and KD transfer knowledge from a teacher to a student.",
    )
    assert value == DECISION_AMBIGUOUS


def test_type_f_unrelated_paper_is_safe_drop(engine):
    value = decision(
        engine,
        "Geometric Reconstruction from Sparse Views",
        "We optimize camera poses and reconstruct detailed meshes from calibrated images.",
    )
    assert value == DECISION_DROP


def test_missing_abstract_is_ambiguous(engine):
    assert decision(engine, "A Paper", "", "PARTIAL") == DECISION_AMBIGUOUS


def test_lone_distill_root_is_not_silently_dropped(engine):
    assert decision(engine, "A Novel Objective", "We distill intermediate behavior with a new loss.") == DECISION_AMBIGUOUS
