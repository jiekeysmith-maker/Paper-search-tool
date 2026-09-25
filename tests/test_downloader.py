from src.downloader import download_pdf_file


class FakePdfResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        yield b"%PDF-1.4\n"
        yield b"1 0 obj\n<<>>\nendobj\n%%EOF"


class FakeSession:
    def get(self, *args, **kwargs):
        return FakePdfResponse()


def test_pdf_download_and_existing_file_skip(tmp_path):
    destination = tmp_path / "paper.pdf"
    status, size = download_pdf_file(FakeSession(), "https://example.test/paper.pdf", destination, timeout=1)
    assert status == "DOWNLOADED"
    assert size == destination.stat().st_size
    status2, size2 = download_pdf_file(FakeSession(), "https://example.test/paper.pdf", destination, timeout=1)
    assert (status2, size2) == ("ALREADY_EXISTS", size)


def test_corrupt_existing_file_is_replaced(tmp_path):
    destination = tmp_path / "paper.pdf"
    destination.write_bytes(b"not a pdf")
    status, _ = download_pdf_file(FakeSession(), "https://example.test/paper.pdf", destination, timeout=1)
    assert status == "DOWNLOADED"
    assert destination.read_bytes().startswith(b"%PDF-")
