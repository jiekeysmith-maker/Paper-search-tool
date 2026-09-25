from src.downloader import build_pdf_filename, sanitize_filename


def test_windows_illegal_characters_are_removed():
    value = sanitize_filename('A:B<C>"D/E\\F|G?H*')
    assert not any(char in value for char in '<>:"/\\|?*')


def test_reserved_name_and_length():
    assert sanitize_filename("CON") == "_CON"
    filename = build_pdf_filename("CVPR2026_MAIN_123", "x" * 500)
    assert filename.endswith(".pdf")
    assert len(filename) <= 180

