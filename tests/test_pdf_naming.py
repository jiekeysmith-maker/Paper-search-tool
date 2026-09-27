from src.pdf_naming import plan_title_pdf_filename, sanitize_pdf_filename


def test_normal_title_is_preserved_without_paper_id(tmp_path):
    title = "Masking Teacher and Reinforcing Student for Distilling Vision-Language Models"
    filename, collision = plan_title_pdf_filename(title, "CVPR2025_MAIN_001", tmp_path, set())
    assert filename == f"{title}.pdf"
    assert "CVPR2025_MAIN_001" not in filename
    assert collision is False


def test_windows_invalid_characters_and_reserved_names_are_safe():
    cleaned = sanitize_pdf_filename('A:B<C>"D/E\\F|G?H* ')
    assert cleaned == "A-B-C--D-E-F-G-H-"
    assert not any(character in cleaned for character in '<>:"/\\|?*')
    assert sanitize_pdf_filename("CON") == "_CON"
    assert not sanitize_pdf_filename("Title. ").endswith((".", " "))


def test_filename_collision_appends_only_second_paper_id(tmp_path):
    used: set[str] = set()
    first, first_collision = plan_title_pdf_filename("A/B", "P1", tmp_path, used)
    second, second_collision = plan_title_pdf_filename("A\\B", "P2", tmp_path, used)
    assert first == "A-B.pdf"
    assert first_collision is False
    assert second == "A-B__P2.pdf"
    assert second_collision is True


def test_overlong_title_falls_back_to_shortened_title_plus_paper_id(tmp_path):
    filename, collision = plan_title_pdf_filename("Long Title " * 80, "P-LONG", tmp_path, set())
    assert filename.endswith("__P-LONG.pdf")
    assert collision is False
    assert len(str((tmp_path / filename).resolve())) <= 240

