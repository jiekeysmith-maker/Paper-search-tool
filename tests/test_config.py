from pathlib import Path

from src.utils import load_yaml


def test_v11_default_track_scope_is_main_only():
    root = Path(__file__).resolve().parents[1]
    config = load_yaml(root / "config" / "source_config.yaml")
    tracks = config["venues"]["CVPR"]["tracks"]
    assert tracks["Main Conference"]["enabled"] is True
    assert tracks["Findings"]["enabled"] is False
    assert tracks["Workshop"]["enabled"] is False


def test_v11_and_v12_rule_files_are_both_preserved():
    root = Path(__file__).resolve().parents[1]
    v11 = load_yaml(root / "config" / "screening_rules_v1.yaml")
    v12 = load_yaml(root / "config" / "screening_rules_v1_2.yaml")
    assert v11["rule_version"] == "V1.1"
    assert v12["rule_version"] == "V1.2"
