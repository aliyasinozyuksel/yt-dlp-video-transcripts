from __future__ import annotations

import csv
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.conftest import ct


def test_safe_csv_cell_formula_prefix():
    evil = '=HYPERLINK("http://evil.com","click")'
    assert ct.safe_csv_cell(evil) == f"'{evil}"


def test_safe_csv_cell_normal_title_unchanged():
    assert ct.safe_csv_cell("Normal Title") == "Normal Title"


def test_write_videos_csv_formula_sanitized(tmp_path: Path):
    videos = [
        {
            "index": 1,
            "id": "vid1",
            "title": '=HYPERLINK("http://evil.com","click")',
            "url": "https://www.youtube.com/watch?v=vid1",
            "upload_date": "2024-01-01",
        }
    ]
    csv_path = tmp_path / "videos.csv"
    ct.write_videos_csv(csv_path, videos)

    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))

    assert rows[0] == ["index", "id", "title", "url", "upload_date"]
    assert rows[1][2].startswith("'")


def test_parse_md_frontmatter_string_video_id(tmp_path: Path):
    md_path = tmp_path / "001_test.md"
    md_path.write_text(
        "---\n"
        'video_id: "abc"\n'
        "title: Example\n"
        "---\n\n"
        "# Example\n",
        encoding="utf-8",
    )
    meta = ct.parse_md_frontmatter(md_path)
    assert meta["video_id"] == "abc"
    assert isinstance(meta["video_id"], str)


def test_parse_md_frontmatter_boolean_timestamps(tmp_path: Path):
    md_path = tmp_path / "001_test.md"
    md_path.write_text(
        "---\n"
        "video_id: abc123\n"
        "timestamps: true\n"
        "---\n\n"
        "# Example\n",
        encoding="utf-8",
    )
    meta = ct.parse_md_frontmatter(md_path)
    assert meta["timestamps"] == "true"
    assert isinstance(meta["timestamps"], str)


def test_parse_md_frontmatter_numeric_year(tmp_path: Path):
    md_path = tmp_path / "001_test.md"
    md_path.write_text(
        "---\n"
        "video_id: abc123\n"
        "year: 2024\n"
        "---\n\n"
        "# Example\n",
        encoding="utf-8",
    )
    meta = ct.parse_md_frontmatter(md_path)
    assert meta["year"] == "2024"
    assert isinstance(meta["year"], str)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.youtube.com/@foo", "https://www.youtube.com/@foo/videos"),
        ("https://www.youtube.com/@foo/videos", "https://www.youtube.com/@foo/videos"),
        (
            "https://www.youtube.com/@foo?x=1",
            "https://www.youtube.com/@foo/videos?x=1",
        ),
        (
            "https://www.youtube.com/channel/ABC",
            "https://www.youtube.com/channel/ABC/videos",
        ),
        (
            "https://www.youtube.com/watch?v=abc123",
            "https://www.youtube.com/watch?v=abc123",
        ),
        (
            "https://www.youtube.com/playlist?list=PL123",
            "https://www.youtube.com/playlist?list=PL123",
        ),
    ],
)
def test_normalize_channel_url(url: str, expected: str):
    assert ct.normalize_channel_url(url) == expected


def test_sanitize_video_id_for_filename_safe_ids():
    assert ct.sanitize_video_id_for_filename("abcDEF_123-x") == "abcDEF_123-x"


def test_sanitize_video_id_for_filename_unsafe_chars():
    sanitized = ct.sanitize_video_id_for_filename("../evil")
    assert "/" not in sanitized
    assert ".." not in sanitized or sanitized == "--evil"
    assert ct.sanitize_video_id_for_filename("abc/def") == "abc-def"
    assert "/" not in ct.sanitize_video_id_for_filename("abc/def")


def test_make_basename_sanitizes_video_id():
    used = {"001_same-title"}
    basename = ct.make_basename(1, "Same Title", "abc/def", used)
    assert basename == "001_same-title_abc-def"


def test_atomic_write_text_writes_content(tmp_path: Path):
    path = tmp_path / "out.txt"
    ct.atomic_write_text(path, "hello\n")
    assert path.read_text(encoding="utf-8") == "hello\n"


def test_atomic_write_text_replaces_existing(tmp_path: Path):
    path = tmp_path / "out.txt"
    path.write_text("old\n", encoding="utf-8")
    ct.atomic_write_text(path, "new\n")
    assert path.read_text(encoding="utf-8") == "new\n"


def test_atomic_write_text_cleans_temp_on_failure(tmp_path: Path):
    path = tmp_path / "out.txt"
    before = {p.name for p in tmp_path.iterdir()}
    with patch.object(ct.os, "replace", side_effect=OSError("boom")):
        with pytest.raises(OSError):
            ct.atomic_write_text(path, "data\n")
    after = {p.name for p in tmp_path.iterdir()}
    assert after == before
    assert not path.exists()


def test_should_write_progress_interval_logic():
    assert ct.should_write_progress(1, 10) is True
    assert ct.should_write_progress(10, 10) is True
    assert ct.should_write_progress(5, 10) is False
    assert ct.should_write_progress(25, 10, is_last=True) is True
    assert ct.should_write_progress(5, 10, on_error=True) is True


def test_main_rejects_zero_progress_interval(monkeypatch):
    import sys

    monkeypatch.setattr(
        sys,
        "argv",
        ["channel_transcripts.py", "https://www.youtube.com/@test/videos", "--progress-interval", "0"],
    )
    with pytest.raises(SystemExit) as exc_info:
        ct.main()
    assert exc_info.value.code != 0


def test_final_report_includes_progress_interval(tmp_path: Path, monkeypatch):
    videos = [
        {
            "id": "vid1",
            "title": "Video 1",
            "url": "https://www.youtube.com/watch?v=vid1",
            "upload_date": "20240101",
        }
    ]

    monkeypatch.setattr(ct, "list_channel_videos", lambda _url: ("Test Channel", videos))
    monkeypatch.setattr(ct, "slugify", lambda text, max_length=80: "test-channel")
    monkeypatch.setattr(
        ct,
        "fetch_video_metadata",
        lambda _url: {
            "title": "Video 1",
            "upload_date": "20240101",
            "channel": "Test Channel",
            "subtitles": {"en": [{}]},
            "automatic_captions": {},
        },
    )

    def mock_download(
        _video_url: str,
        video_id: str,
        lang: str,
        _sub_type: str,
        out_dir: Path,
    ) -> Path:
        vtt_path = out_dir / f"{video_id}.{lang}.vtt"
        vtt_path.write_text(
            "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello\n",
            encoding="utf-8",
        )
        return vtt_path

    monkeypatch.setattr(ct, "download_subtitle", mock_download)

    report = ct.process_channel(
        "https://www.youtube.com/@test/videos",
        tmp_path / "output",
        delay=0,
        progress_interval=10,
    )

    assert report["progress_interval"] == 10
    progress = __import__("json").loads(
        (tmp_path / "output" / "test-channel" / "progress.json").read_text(encoding="utf-8")
    )
    assert progress["processed_count"] == 1
    assert progress["progress_interval"] == 10
