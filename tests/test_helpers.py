from __future__ import annotations

from tests.conftest import ct


def test_slugify_basic():
    assert ct.slugify("Hello World!") == "hello-world"


def test_slugify_turkish_chars():
    # NFKD + ASCII encoding drops some Turkish letters (e.g. ı, ş).
    assert ct.slugify("Türkçe Başlık") == "turkce-baslk"


def test_yaml_str_quotes_and_special_chars():
    value = ct.yaml_str('Title: "quotes" & colons')
    assert value.startswith('"')
    assert '\\"' in value


def test_md_table_cell_escapes_pipe():
    assert ct.md_table_cell("Part A | Part B") == r"Part A \| Part B"


def test_md_table_cell_replaces_newlines():
    assert ct.md_table_cell("line1\nline2") == "line1 line2"


def test_select_videos_range():
    videos = [{"id": f"id{i}", "title": f"v{i}"} for i in range(1, 11)]
    selected = ct.select_videos(videos, max_videos=2, start_index=3, end_index=5)
    assert [(index, video["title"]) for index, video in selected] == [
        (3, "v3"),
        (4, "v4"),
        (5, "v5"),
    ]


def test_select_videos_max_when_no_range():
    videos = [{"id": f"id{i}", "title": f"v{i}"} for i in range(1, 6)]
    selected = ct.select_videos(videos, max_videos=2, start_index=None, end_index=None)
    assert len(selected) == 2
    assert selected[0][0] == 1


def test_run_report_filename_chunk():
    name = ct.run_report_filename(1, 100, None, 650)
    assert name == "report_001_100.json"


def test_run_report_filename_max_videos():
    name = ct.run_report_filename(None, None, 5, 650)
    assert name == "report_001_005.json"
