from pathlib import Path

import pandas as pd

from src.crawler import CachedHttpClient
from src.utils import write_csv, write_xlsx


class FakeResponse:
    text = "<html>中文摘要</html>"
    apparent_encoding = "utf-8"
    encoding = "utf-8"

    def raise_for_status(self):
        return None


def test_cache_prevents_second_request(tmp_path, monkeypatch):
    client = CachedHttpClient({"request_interval_seconds": 0, "retries": 0}, logger=__import__("logging").getLogger("test"))
    calls = {"count": 0}

    def fake_get(*args, **kwargs):
        calls["count"] += 1
        return FakeResponse()

    monkeypatch.setattr(client.session, "get", fake_get)
    cache = tmp_path / "cache" / "page.html"
    first, source1 = client.get_text("https://example.test", cache)
    second, source2 = client.get_text("https://example.test", cache)
    assert first == second
    assert calls["count"] == 1
    assert (source1, source2) == ("NETWORK", "CACHE")


def test_csv_xlsx_keep_bilingual_header_and_utf8(tmp_path):
    columns = ["Title（标题）", "Abstract（摘要）"]
    rows = [{"Title（标题）": "知识蒸馏", "Abstract（摘要）": "中文摘要可读"}]
    csv_path = tmp_path / "test.csv"
    xlsx_path = tmp_path / "test.xlsx"
    write_csv(rows, csv_path, columns)
    write_xlsx(rows, xlsx_path, columns)
    assert csv_path.read_bytes().startswith(b"\xef\xbb\xbf")
    csv_frame = pd.read_csv(csv_path, encoding="utf-8-sig")
    xlsx_frame = pd.read_excel(xlsx_path)
    assert csv_frame.iloc[0, 0] == "知识蒸馏"
    assert xlsx_frame.iloc[0, 1] == "中文摘要可读"

