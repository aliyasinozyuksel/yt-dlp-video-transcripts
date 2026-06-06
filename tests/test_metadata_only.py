from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from tests.conftest import ct


def sample_videos(count: int = 5) -> list[dict]:
    return [
        {
            "id": f"vid{i}",
            "title": f"Video {i}",
            "url": f"https://www.youtube.com/watch?v=vid{i}",
            "upload_date": f"20260{min(i, 3)}0{i}",
        }
        for i in range(1, count + 1)
    ]


@pytest.fixture
def mock_channel(monkeypatch):
    videos = sample_videos()

    def _list(_url: str) -> tuple[str, list[dict]]:
        return ("Test Channel", videos)

    monkeypatch.setattr(ct, "list_channel_videos", _list)
    monkeypatch.setattr(ct, "slugify", lambda text, max_length=80: "test-channel")
    return videos


def test_metadata_only_writes_videos_json_and_csv(
    tmp_path: Path,
    mock_channel,
    monkeypatch,
):
    def fail_fetch(*_args, **_kwargs):
        raise AssertionError("fetch_video_metadata should not be called")

    monkeypatch.setattr(ct, "fetch_video_metadata", fail_fetch)
    monkeypatch.setattr(ct, "download_subtitle", fail_fetch)

    output_dir = tmp_path / "output"
    report = ct.process_channel(
        "https://www.youtube.com/@test/videos",
        output_dir,
        delay=0,
        metadata_only=True,
        max_videos=3,
    )

    base = output_dir / "test-channel"
    videos_json = base / "videos.json"
    videos_csv = base / "videos.csv"

    assert videos_json.exists()
    assert videos_csv.exists()
    assert report["metadata_only"] is True
    assert report["selected_count"] == 3
    assert report["total_videos"] == 5
    assert len(report["videos"]) == 3
    assert list((base / "txt").glob("*.txt")) == []
    assert list((base / "md").glob("*.md")) == []
    assert not (base / "progress.json").exists()
    assert not (base / "report.json").exists()


def test_metadata_only_videos_json_structure(tmp_path: Path, mock_channel):
    output_dir = tmp_path / "output"
    ct.process_channel(
        "https://www.youtube.com/@test/videos",
        output_dir,
        delay=0,
        metadata_only=True,
        max_videos=2,
    )

    payload = json.loads(
        (output_dir / "test-channel" / "videos.json").read_text(encoding="utf-8")
    )
    assert payload["channel_name"] == "Test Channel"
    assert payload["total_videos"] == 5
    assert payload["selected_count"] == 2
    assert payload["max_videos"] == 2
    assert len(payload["videos"]) == 2
    assert payload["videos"][0]["index"] == 1
    assert payload["videos"][0]["id"] == "vid1"
    assert payload["videos"][0]["upload_date"] == "2026-01-01"


def test_metadata_only_videos_csv_headers(tmp_path: Path, mock_channel):
    output_dir = tmp_path / "output"
    ct.process_channel(
        "https://www.youtube.com/@test/videos",
        output_dir,
        delay=0,
        metadata_only=True,
        max_videos=2,
    )

    csv_path = output_dir / "test-channel" / "videos.csv"
    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert rows[0].keys() == {"index", "id", "title", "url", "upload_date"}
    assert rows[0]["id"] == "vid1"
    assert rows[1]["id"] == "vid2"


def test_metadata_only_respects_max_videos(tmp_path: Path, mock_channel):
    output_dir = tmp_path / "output"
    report = ct.process_channel(
        "https://www.youtube.com/@test/videos",
        output_dir,
        delay=0,
        metadata_only=True,
        max_videos=2,
    )
    assert report["selected_count"] == 2
    assert len(report["videos"]) == 2


def test_metadata_only_respects_index_range(tmp_path: Path, mock_channel):
    output_dir = tmp_path / "output"
    report = ct.process_channel(
        "https://www.youtube.com/@test/videos",
        output_dir,
        delay=0,
        metadata_only=True,
        start_index=2,
        end_index=4,
    )
    assert report["selected_count"] == 3
    assert [video["index"] for video in report["videos"]] == [2, 3, 4]
    assert report["start_index"] == 2
    assert report["end_index"] == 4


def test_metadata_only_dry_run_writes_no_files(
    tmp_path: Path,
    mock_channel,
    monkeypatch,
):
    output_dir = tmp_path / "output"

    report = ct.process_channel(
        "https://www.youtube.com/@test/videos",
        output_dir,
        delay=0,
        metadata_only=True,
        dry_run=True,
        max_videos=3,
    )

    base = output_dir / "test-channel"
    assert report["dry_run"] is True
    assert report["would_write_metadata_files"] is False
    assert not (base / "videos.json").exists()
    assert not (base / "videos.csv").exists()
    assert not base.exists() or list(base.rglob("*")) == []


def test_metadata_only_dry_run_returns_report_dict(tmp_path: Path, mock_channel):
    report = ct.process_channel(
        "https://www.youtube.com/@test/videos",
        tmp_path / "output",
        delay=0,
        metadata_only=True,
        dry_run=True,
        max_videos=2,
    )
    assert report["metadata_only"] is True
    assert report["selected_count"] == 2
    assert len(report["videos"]) == 2


def test_transcript_behavior_unchanged_without_metadata_only(
    tmp_path: Path,
    monkeypatch,
):
    videos = sample_videos(1)

    monkeypatch.setattr(ct, "list_channel_videos", lambda _url: ("Test Channel", videos))
    monkeypatch.setattr(ct, "slugify", lambda text, max_length=80: "test-channel")
    monkeypatch.setattr(
        ct,
        "fetch_video_metadata",
        lambda _url: {
            "title": "Video 1",
            "upload_date": "20260101",
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

    output_dir = tmp_path / "output"
    report = ct.process_channel(
        "https://www.youtube.com/@test/videos",
        output_dir,
        delay=0,
        metadata_only=False,
    )

    base = output_dir / "test-channel"
    assert "metadata_only" not in report or report.get("metadata_only") is not True
    assert len(list((base / "txt").glob("*.txt"))) == 1
    assert len(list((base / "md").glob("*.md"))) == 1
    assert not (base / "videos.json").exists()
