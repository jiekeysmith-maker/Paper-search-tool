from pathlib import Path

import pytest

from src.screener import RuleEngine
from src.utils import DECISION_AMBIGUOUS, DECISION_DROP, DECISION_KEEP, DECISION_MAYBE, load_yaml


@pytest.fixture(scope="module")
def engine() -> RuleEngine:
    root = Path(__file__).resolve().parents[1]
    return RuleEngine(load_yaml(root / "config" / "screening_rules_v1_2.yaml"))


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


def test_masters_large_vlm_to_compact_vlm_is_keep(engine):
    value = decision(
        engine,
        "Masking Teacher and Reinforcing Student for Distilling Vision-Language Models",
        (
            "This raises the need for compact yet capable VLMs that can efficiently learn from powerful large teacher. "
            "However, distilling knowledge from large teacher to small student remains challenging due to their large "
            "size gap: the student often fails to reproduce the teacher's complex, high-dimensional representations. "
            "We propose Masters, a mask-progressive reinforcement learning distillation framework that progressively "
            "restores teacher capacity and refines knowledge transfer with a distillation reward."
        ),
    )
    assert value == DECISION_KEEP


def test_wpt_policy_and_world_reward_distillation_is_keep(engine):
    value = decision(
        engine,
        "WPT: World-to-Policy Transfer via Online World Model Distillation",
        (
            "We introduce WPT, a World-to-Policy Transfer training paradigm that enables online distillation under "
            "the guidance of an end-to-end world model. A trainable reward model infuses world knowledge into a "
            "teacher policy. Policy distillation and world reward distillation then transfer the teacher's reasoning "
            "ability into a lightweight student policy while preserving real-time deployability."
        ),
    )
    assert value == DECISION_KEEP


def test_test_time_distillation_is_not_dropped(engine):
    value = decision(
        engine,
        "Test-Time Distillation for Continual Model Adaptation",
        (
            "Continual Test-Time Adaptation addresses distribution shifts without labels, but self-supervision can "
            "amplify prediction errors. We propose Test-Time Distillation, reframing adaptation as a distillation "
            "process guided by a frozen Vision-Language Model as an external signal. CoDiRe constructs a robust "
            "blended teacher and aligns predictions with that teacher for stable adaptation."
        ),
    )
    assert value != DECISION_DROP


def test_fast_foundation_stereo_kd_nas_pruning_is_not_dropped(engine):
    value = decision(
        engine,
        "Fast-FoundationStereo: Real-Time Zero-Shot Stereo Matching",
        (
            "Stereo foundation models generalize well but are too expensive for real-time use. Fast-FoundationStereo "
            "uses knowledge distillation to compress the hybrid backbone into an efficient student, blockwise neural "
            "architecture search under latency budgets, and structured pruning of the refinement module. The model "
            "runs over ten times faster while closely matching zero-shot accuracy."
        ),
    )
    assert value != DECISION_DROP


def test_pluggable_pruning_contiguous_layer_distillation_is_not_dropped(engine):
    value = decision(
        engine,
        "Pluggable Pruning with Contiguous Layer Distillation for Diffusion Transformers",
        (
            "We propose Pluggable Pruning with Contiguous Layer Distillation, a flexible structured pruning framework "
            "for Diffusion Transformers. A plug-and-play teacher-student alternating distillation scheme integrates "
            "depth-wise and width-wise pruning in one training phase and enables knowledge transfer across diverse "
            "pruning ratios without per-configuration retraining."
        ),
    )
    assert value != DECISION_DROP


def test_rethinking_dataset_distillation_is_ambiguous(engine):
    value = decision(
        engine,
        "Rethinking Dataset Distillation: Hard Truths about Soft Labels",
        (
            "Large-scale dataset distillation methods can perform on par with random image baselines because soft "
            "labels are used during downstream training. We examine label regimes ranging from abundant soft labels, "
            "termed the SL+KD regime, to fixed soft labels and hard labels. High-quality coresets do not convincingly "
            "outperform random baselines in the SL and SL+KD regimes, motivating evaluation of dataset distillation "
            "and data-efficient learning under hard labels."
        ),
    )
    assert value == DECISION_AMBIGUOUS


def test_thermal_det_synthetic_dataset_is_not_negative(engine):
    result = engine.evaluate(
        paper(
            "Thermal-Det: Language-Guided Cross-Modal Distillation for Open-Vocabulary Thermal Object Detection",
            (
                "Thermal-Det is an LLM-supervised open-vocabulary detector for thermal images. To enable large-scale "
                "training, we construct a synthetic dataset with thermally aligned samples. The model jointly optimizes "
                "detection, captioning, and cross-modal distillation objectives. A frozen RGB teacher provides geometric "
                "and semantic pseudo-supervision, transferring open-vocabulary knowledge without manual annotation."
            ),
        )
    )
    assert result["Decision（筛选决定）"] == DECISION_KEEP
    assert "synthetic dataset" not in result["Negative_Hits（命中的负向规则）"]


def test_plain_vlm_alignment_does_not_trigger_large_combo_or_maybe(engine):
    result = engine.evaluate(
        paper(
            "Cross-Modal Alignment for Vision-Language Models",
            "We improve a vision-language model using image-text alignment on paired web data and a contrastive objective.",
        )
    )
    assert result["Decision（筛选决定）"] == DECISION_DROP
    assert "combo:large/foundation+KD-transfer" not in result["Positive_Hits（命中的阳性规则）"]


def test_llm_supervision_without_kd_does_not_trigger_large_combo(engine):
    result = engine.evaluate(
        paper(
            "Supervised Adaptation of a Large Language Model",
            "A large language model is optimized with task supervision and human annotations for visual question answering.",
        )
    )
    assert result["Decision（筛选决定）"] == DECISION_DROP
    assert "combo:large/foundation+KD-transfer" not in result["Positive_Hits（命中的阳性规则）"]


def test_foundation_model_with_strong_knowledge_transfer_is_protected(engine):
    result = engine.evaluate(
        paper(
            "Knowledge Transfer from a Vision Foundation Model",
            "We study knowledge transfer from a vision foundation model into a compact task-specific representation.",
        )
    )
    assert result["Decision（筛选决定）"] in {DECISION_MAYBE, DECISION_AMBIGUOUS}
    assert "combo:large/foundation+KD-transfer" in result["Positive_Hits（命中的阳性规则）"]


def test_teacher_student_generic_alignment_is_maybe_not_keep(engine):
    result = engine.evaluate(
        paper(
            "Teacher-Student Alignment for Robust Recognition",
            "A teacher network guides a compact student through representation alignment and supervision.",
        )
    )
    assert result["Decision（筛选决定）"] == DECISION_MAYBE
    assert "combo:teacher+student+transfer" in result["Positive_Hits（命中的阳性规则）"]


@pytest.mark.parametrize(
    ("title", "abstract"),
    [
        (
            "Fast Nearest-Neighbor Matching with a KD-tree",
            "We build a KD-tree spatial index for efficient nearest-neighbor lookup in a large 3D point cloud.",
        ),
        (
            "Scalable Registration Using a k-d tree",
            "A k-d tree accelerates geometric correspondence search without model compression or teacher supervision.",
        ),
    ],
)
def test_kd_tree_terms_do_not_trigger_keep(engine, title, abstract):
    result = engine.evaluate(paper(title, abstract))
    assert result["Decision（筛选决定）"] != DECISION_KEEP
    assert "weak:KD" not in result["Positive_Hits（命中的阳性规则）"]


def test_kd_acronym_with_teacher_student_context_is_protected(engine):
    value = decision(
        engine,
        "Compact Recognition with KD",
        (
            "Our KD objective transfers feature and logit knowledge from a pretrained teacher to a compact student, "
            "reducing model compression error while retaining response quality."
        ),
    )
    assert value in {DECISION_KEEP, DECISION_MAYBE, DECISION_AMBIGUOUS}


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
