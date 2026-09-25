from pathlib import Path

from src.utils import load_yaml


def test_v11_default_track_scope_is_main_only():
    root = Path(__file__).resolve().parents[1]
    config = load_yaml(root / "config" / "source_config.yaml")
    tracks = config["venues"]["CVPR"]["tracks"]
    assert tracks["Main Conference"]["enabled"] is True
    assert tracks["Findings"]["enabled"] is False
    assert tracks["Workshop"]["enabled"] is False

